import time
from threading import Thread, Lock
from queue import Queue
from typing import Optional, List

from ..lib.logger import get_logger
from ..lib.strict_model import StrictModel

from .exchange_interface import ExchangeInterface, RealtimeInterface
from .exchange_models import Fill, BookTicker
from .exchange_models import OrderSide, OrderType, OrderError, PositionSide
from .exchange_models import ChaseCommand, PositionStopCommand, PositionCommandType, PositionEvent, PositionEventType, ChaseResponse
from .fills_aggregator import AggregatedFill, FillsAggregator
from .fills_router import FillsRouter

"""
Chase-only position engine (int-units only).

Runs a single unified event loop that consumes user commands, realtime BookTicker updates and exchange Fill events.
Maintains per-order fill accounting with deduplication and terminal flags to avoid double counting.
Implements a maker "chase" using post-only LIMIT orders, reacting to book prices, exchange errors and aggregated fills,
and emits PositionEvent / ChaseResponse objects to the caller.
"""


### Models (internal state) ###
class _ChaseState(StrictModel):
    cmd_id: str
    started_ms: int

    symbol: str
    side: OrderSide

    target_units: int
    order_ids: List[str] = []

    active_order_id: Optional[str] = None
    active_price_units: int = 0
    active_volume_units: int = 0

    cooldown_until_ms: int = 0
    gtx_violations: int = 0

    # If True, chase should be finalized after the current AggregatedFill is applied
    # to accounting (order_fills). This avoids "filled=0" when finishing on an agg
    # that has not been stored yet.
    finish_pending: bool = False


class _OrderFillsState(StrictModel):
    # Per-order fills storage and terminal flags to avoid double accounting.
    # When order is finalized (filled or filled-by-deletion), we must ignore and drop any further fills for that order id.
    order_id: str
    side: OrderSide

    filled_by_deletion: bool = False
    is_filled: bool = False
    filled_units: int = 0

    parts: List[AggregatedFill] = []


### Position (chase-only, int-only) ###
class Position:
    def __init__(self, symbol, exchange, realtime, uid, tick_ms: int = 5, gtx_cooldown_ms: int = 5, entrance_timeout_ms: int = 0):
        """
        Chase-only position executor (maker-only, int-units only).

        Responsibilities:
         - Provide a single unified loop() that consumes commands, BookTicker and Fill events.
         - Execute a single active maker chase at a time via GTX LIMIT orders (create/modify/delete).
         - Aggregate and deduplicate fills, maintain per-order terminal states and base balance.
         - Emit PositionEvent and ChaseResponse objects to the caller.

        Input:
         - symbol: str
         - exchange: ExchangeInterface (hedge mode must be disabled)
         - realtime: RealtimeInterface
         - uid: str subscription id
         - tick_ms: int, main tick cadence for aggregation and routing
         - gtx_cooldown_ms: int, cooldown after GTX violation or inferred deletion
         - entrance_timeout_ms: int, optional (0 disables) time limit for "no entrance" use-case

        Errors:
         - All invalid inputs raise immediately (no fallback).
        """
        # Validate inputs
        if not symbol:
            raise RuntimeError('Position: empty symbol')

        if tick_ms is None:
            raise RuntimeError('Position: tick_ms is None')

        if not isinstance(tick_ms, int):
            raise RuntimeError(f'Position: tick_ms is not int: {type(tick_ms)}')

        if tick_ms <= 0:
            raise RuntimeError(f'Position: tick_ms must be > 0, got: {tick_ms}')

        if gtx_cooldown_ms is None:
            raise RuntimeError('Position: gtx_cooldown_ms is None')
        
        if not isinstance(gtx_cooldown_ms, int):
            raise RuntimeError(f'Position: gtx_cooldown_ms is not int: {type(gtx_cooldown_ms)}')
        
        if int(gtx_cooldown_ms) <= 0:
            raise RuntimeError(f'Position: gtx_cooldown_ms must be > 0, got: {gtx_cooldown_ms}')

        if entrance_timeout_ms is None:
            raise RuntimeError('Position: entrance_timeout_ms is None')

        if not isinstance(entrance_timeout_ms, int):
            raise RuntimeError(f'Position: entrance_timeout_ms is not int: {type(entrance_timeout_ms)}')

        if int(entrance_timeout_ms) < 0:
            raise RuntimeError(f'Position: entrance_timeout_ms must be >= 0, got: {entrance_timeout_ms}')
        
        if exchange is None:
            raise RuntimeError('Position: exchange is None')

        if not isinstance(exchange, ExchangeInterface):
            raise RuntimeError(f'Position: exchange is not ExchangeInterface: {type(exchange)}')

        if realtime is None:
            raise RuntimeError('Position: realtime is None')

        if not isinstance(realtime, RealtimeInterface):
            raise RuntimeError(f'Position: realtime is not RealtimeInterface: {type(realtime)}')

        if not uid:
            raise RuntimeError('Position: empty uid')

        # Public identity
        self.symbol = symbol
        self.uid = uid

        # External dependencies
        self.exchange = exchange
        self.realtime = realtime

        # Force one-way mode (hedge mode MUST be disabled)
        if self.exchange.is_hedge_mode():
            raise RuntimeError('Position: hedge_mode must be False')

        # Logger
        self.logger = get_logger('position')

        # Tick configuration
        self.tick_ms = int(tick_ms)

        # Unified inbox queue (everything goes here)
        self.q = Queue()

        # Realtime subscription (external channel)
        self.rt_ch = None

        # Realtime bridge thread (moves BookTicker into self.q)
        self.rt_thread = None

        # Fills router and aggregator (exactly like exchange_example.py)
        self.fills_agg = None
        self.fills_rt = None

        # Cached book ticker (latest)
        self.book = None
        self.book_lock = Lock()
        self.book_update_pending = False

        # Current chase (single active chase only)
        self.chase = None

        # Fatal error latch (set by callbacks, raised in loop)
        self.last_error = None

        # Fills accounting (per-order, no global counters)
        self.order_fills = {}

        # Settings
        # - gtx_cooldown_ms: cooldown after post-only (GTX) violation / inferred deletion to allow trailing fills to arrive.
        self.gtx_cooldown_ms = int(gtx_cooldown_ms)

        # Entrance timeout (Position-level event, used by HedgeEngine for LONG_ONLY / SHORT_ONLY only)
        self.entrance_timeout_ms = int(entrance_timeout_ms)
        self.entrance_started_ms = 0
        self.entrance_timeout_fired = False

        # Lifecycle
        self.started = False

    def _log_balance_mutation(self, prev_units, new_units, ctx, data):
        """
        Log base balance mutations (int units) for debugging and post-mortem.

        Input:
         - prev_units: int previous base balance (units)
         - new_units: int new base balance (units)
         - ctx: str short context tag
         - data: dict extra structured fields for log line

        Output:
         - None (no-op when balance did not change)

        Notes:
         - This method does not change any state, only logs.
        """
        if ctx is None:
            raise RuntimeError('Position._log_balance_mutation: ctx is None')
        
        if not isinstance(prev_units, int) or not isinstance(new_units, int):
            raise RuntimeError('Position._log_balance_mutation: prev_units and new_units must be int')
        
        if data is None:
            data = {}
        
        if not isinstance(data, dict):
            raise RuntimeError(f'Position._log_balance_mutation: data is not dict: {type(data)}')
        
        if int(prev_units) == int(new_units):
            return
        
        delta = int(new_units) - int(prev_units)
        
        self.logger.info(f'position_balance_mutation uid={self.uid} symbol={self.symbol} prev_units={int(prev_units)} new_units={int(new_units)} delta_units={int(delta)} ctx={ctx} data={data}')

    def add_command(self, cmd):
        """
        Public entrypoint: enqueue a chase command into the unified loop inbox.

        Input:
         - cmd: ChaseCommand (PositionCommandType.CHASE), symbol must match.

        Contract:
         - Thread-safe: may be called from any thread.
         - Does not execute command immediately; execution happens inside loop().
        """
        # Public entrypoint: add user command to the unified inbox queue
        if cmd is None:
            raise RuntimeError('Position.add_command: cmd is None')

        if not isinstance(cmd, ChaseCommand):
            raise RuntimeError(f'Position.add_command: cmd is not ChaseCommand: {type(cmd)}')

        if cmd.cmd != PositionCommandType.CHASE:
            raise RuntimeError(f'Position.add_command: bad cmd: {cmd.cmd}')

        if cmd.symbol != self.symbol:
            raise RuntimeError(f'Position.add_command: symbol mismatch: cmd.symbol={cmd.symbol} position.symbol={self.symbol}')

        self._push(cmd, 'Position.add_command')

    def get_base_balance_units(self):
        """
        Read-only helper: current base balance in int units.

        Contract:
         - Value is derived strictly from aggregated fills stored in order_fills.
         - Safe to call at any time; does not require loop() iteration.
        """
        # Read-only helper (updated strictly in the unified loop)
        return int(self._sum_position_base_balance_units())

    def loop(self):
        """
        Unified blocking generator loop.

        Behavior:
         - Starts internal wiring on first call (realtime subscription, fills router/aggregator).
         - Blocks on self.q.get() and dispatches by item type.
         - Yields PositionEvent and ChaseResponse to the caller.

        Errors:
         - Any fatal error from background callbacks is latched into last_error and raised here.
         - Unknown queue item types raise immediately.
        """
        # Unified loop: a single blocking consumer of the unified inbox queue.
        # Everything is pushed into self.q, then handled by isinstance.
        #
        # Yields only Position-level outputs (StrictModel): PositionEvent and ChaseResponse.

        if not self.started:
            self._start()

        while True:
            # Raise fatal errors from callback threads (don’t ignore)
            if self.last_error is not None:
                raise self.last_error

            # Blocking receive from the unified inbox queue
            it = self.q.get()

            if it is None:
                raise RuntimeError('Position.loop: queue item is None')

            # Output types (yield to caller)
            if isinstance(it, PositionEvent):
                yield it
                continue

            if isinstance(it, ChaseResponse):
                yield it
                continue

            # Command types
            if isinstance(it, ChaseCommand):
                self._handle_cmd(it)
                continue
            
            if isinstance(it, PositionStopCommand):
                self._handle_stop(it)
                return

            # Exchange fill types
            if isinstance(it, Fill):
                if self.fills_rt is None:
                    raise RuntimeError('Position.loop: fills_rt is None')
                if self._is_order_finalized(it.order_id):
                    continue
                self.fills_rt.add(it)
                continue

            # Realtime types
            if isinstance(it, BookTicker):
                with self.book_lock:
                    self.book_update_pending = False
                
                self._manage_chase()
                continue

            # Aggregated fills come from FillsAggregator handler
            if isinstance(it, AggregatedFill):
                prev_bal = int(self._sum_position_base_balance_units())
                self._on_chase_agg_fill(it)
                self._apply_aggregate(it)

                # If chase became satisfied on this agg, finalize only after accounting was updated.
                ch = self.chase
                if ch is not None and bool(ch.finish_pending):
                    # Re-check using accounting (must include the current agg now).
                    if int(self._sum_chase_remaining_units()) == 0:
                        self._finish_chase(ok=True, err=None)
                    else:
                        ch.finish_pending = False
                new_bal = int(self._sum_position_base_balance_units())
                
                self._log_balance_mutation(prev_bal, new_bal, 'agg_fill', {
                    'order_id': str(it.order_id),
                    'side': it.side.value if it.side is not None else None,
                    'base_units': int(it.base_volume),
                    'price_units': int(it.price),
                    'time_ms': int(it.time_ms),
                    'arrived_ms': int(it.arrived_ms),
                    'from_api': bool(it.from_api),
                    'is_filled': bool(it.is_filled),
                })
                continue

            raise RuntimeError(f'Position.loop: unexpected queue item type: {type(it)}')

    def _start(self):
        """
        Internal one-time wiring of realtime and fills pipeline.

        Responsibilities:
         - Subscribe to realtime and start bridge thread (BookTicker -> unified queue).
         - Create and start FillsRouter and FillsAggregator (Fill -> AggregatedFill -> unified queue).
         - Attach exchange fill callback (Fill -> unified queue).
         - Arm optional entrance timeout timer.

        Idempotency:
         - Forbidden: calling _start() twice raises.
        """
        # Wire components exactly like exchange_example.py:
        # - exchange.set_fill_callback -> pushes Fill into unified queue
        # - fills_rt.set_push(fills_agg.add)
        # - fills_agg handler pushes AggregatedFill into unified queue
        if self.started:
            raise RuntimeError('Position._start: already started')

        # Subscribe to realtime
        self.rt_ch = self.realtime.subscribe(self.uid, self.symbol)
        if self.rt_ch is None:
            raise RuntimeError('Position._start: realtime channel is None')

        # Start realtime bridge (bookTicker -> unified queue)
        self.rt_thread = Thread(target=self._rt_thread, daemon=True)
        self.rt_thread.start()

        # Create router + aggregator (reuse project implementations)
        self.fills_agg = FillsAggregator(handler=self._on_agg_fill, tick_ms=int(self.tick_ms))
        self.fills_rt = FillsRouter()

        self.fills_rt.set_push(self.fills_agg.add)

        self.fills_agg.start()
        self.fills_rt.start()

        # Attach exchange fill callback (fills -> unified queue)
        self.exchange.set_fill_callback(self._on_exchange_fill)

        # Arm entrance timer (only if enabled)
        if int(self.entrance_timeout_ms) > 0:
            self.entrance_started_ms = int(time.time() * 1000)
            self.entrance_timeout_fired = False

        self.started = True
    
    def stop(self):
        """
        Request Position shutdown via a STOP command into the unified loop.

        Contract:
         - stop() is asynchronous with respect to loop(); loop() will process STOP and cleanup.
         - stop() is intended to be called by the owner thread (e.g. HedgeEngine.stop()).
        """
        if not bool(self.started):
            raise RuntimeError('Position.stop: not started')
        
        now_ms = int(time.time() * 1000)
        
        self._push(PositionStopCommand(
            cmd=PositionCommandType.STOP,
            symbol=self.symbol,
            time_ms=int(now_ms),
        ), 'Position.stop')

    def check(self):
        """
        Health check: validate Position is running and raise any latched background errors.

        Checks:
         - last_error from callback threads
         - realtime bridge thread liveness
         - fills router/aggregator background errors

        Errors:
         - Raises on any inconsistency (no fallback).
        """
        if self.last_error is not None:
            raise self.last_error

        if not bool(self.started):
            raise RuntimeError('Position.check: not started')

        if self.rt_ch is None:
            raise RuntimeError('Position.check: rt_ch is None')

        if self.rt_thread is None:
            raise RuntimeError('Position.check: rt_thread is None')

        if not self.rt_thread.is_alive():
            raise RuntimeError('Position.check: rt_thread is not alive')

        if self.fills_agg is None:
            raise RuntimeError('Position.check: fills_agg is None')

        if self.fills_rt is None:
            raise RuntimeError('Position.check: fills_rt is None')

        self.fills_agg.check()
        self.fills_rt.check()

    def _push(self, item, ctx):
        """
        Push an item into unified inbox queue.

        Contract:
         - Non-blocking put is allowed (put_nowait).
         - Non-blocking pop is forbidden: loop() must be a blocking consumer.
        """
        # Push into unified queue (non-blocking push is OK; non-blocking pop is forbidden)
        if item is None:
            raise RuntimeError(f'{ctx}: item is None')

        self.q.put_nowait(item)

    def _on_exchange_fill(self, fill):
        """
        Exchange fill callback (WS/API): validate and push Fill into unified queue.

        Threading:
         - Called by exchange implementation thread.
         - Any exception is latched into last_error to be raised by loop().
        """
        # Exchange WS callback: validate + push into unified queue
        try:
            if fill is None:
                raise RuntimeError('Position._on_exchange_fill: fill is None')

            if not isinstance(fill, Fill):
                raise RuntimeError(f'Position._on_exchange_fill: fill is not Fill: {type(fill)}')

            if fill.symbol != self.symbol:
                raise RuntimeError(f'Position._on_exchange_fill: symbol mismatch: fill.symbol={fill.symbol} position.symbol={self.symbol}')

            if not fill.order_id:
                raise RuntimeError('Position._on_exchange_fill: empty order_id')

            if not fill.trade_id:
                raise RuntimeError('Position._on_exchange_fill: empty trade_id')

            self._push(fill, 'Position._on_exchange_fill')

        except Exception as e:
            self.last_error = e

    def _on_agg_fill(self, agg):
        """
        FillsAggregator handler: validate and push AggregatedFill into unified queue.

        Threading:
         - Called by FillsAggregator background thread.
         - Any exception is latched into last_error to be raised by loop().
        """
        # FillsAggregator handler: push AggregatedFill into unified queue
        try:
            if agg is None:
                raise RuntimeError('Position._on_agg_fill: agg is None')

            if not isinstance(agg, AggregatedFill):
                raise RuntimeError(f'Position._on_agg_fill: agg is not AggregatedFill: {type(agg)}')

            if agg.symbol != self.symbol:
                raise RuntimeError(f'Position._on_agg_fill: symbol mismatch: agg.symbol={agg.symbol} position.symbol={self.symbol}')

            self._push(agg, 'Position._on_agg_fill')

        except Exception as e:
            self.last_error = e

    def _rt_thread(self):
        """
        Realtime bridge thread: consume BookTicker stream and push a coalesced update into unified queue.

        Contract:
         - Stores latest BookTicker into self.book under lock.
         - Ensures at most one pending BookTicker item in the unified queue (book_update_pending flag).
         - Any exception is latched into last_error to be raised by loop().
        """
        # Realtime bridge: block on realtime channel iterator and update cached prices immediately.
        try:
            ch = self.rt_ch
            if ch is None:
                raise RuntimeError('Position._rt_thread: rt_ch is None')

            for it in ch:
                if it is None:
                    raise RuntimeError('Position._rt_thread: item is None')

                if not isinstance(it, BookTicker):
                    raise RuntimeError(f'Position._rt_thread: item is not BookTicker: {type(it)}')

                if it.symbol != self.symbol:
                    raise RuntimeError(f'Position._rt_thread: symbol mismatch: it.symbol={it.symbol} position.symbol={self.symbol}')

                should_push = False
                
                with self.book_lock:
                    self.book = it
                    
                    if not bool(self.book_update_pending):
                        self.book_update_pending = True
                        should_push = True
                
                if should_push:
                    self._push(it, 'Position._rt_thread')

        except Exception as e:
            self.last_error = e

    def _handle_cmd(self, cmd):
        """
        Handle CHASE command inside unified loop.

        Behavior:
         - Validates command and initializes a new _ChaseState (single active chase only).
         - Emits PositionEvent(CHASE_STARTED).
         - Immediately triggers _manage_chase() to place the first order if book is available.

        Notes:
         - Also disarms entrance timeout after the first chase starts.
        """
        # Handle CHASE command: init chase state and emit CHASE_STARTED into unified queue.
        if cmd is None:
            raise RuntimeError('Position._handle_cmd: cmd is None')

        if not isinstance(cmd, ChaseCommand):
            raise RuntimeError(f'Position._handle_cmd: cmd is not ChaseCommand: {type(cmd)}')

        if cmd.cmd != PositionCommandType.CHASE:
            raise RuntimeError(f'Position._handle_cmd: bad cmd: {cmd.cmd}')

        if cmd.base_volume is None:
            raise RuntimeError('Position._handle_cmd: base_volume is None')

        if not isinstance(cmd.base_volume, int):
            raise RuntimeError(f'Position._handle_cmd: base_volume is not int: {type(cmd.base_volume)}')

        if cmd.base_volume <= 0:
            raise RuntimeError(f'Position._handle_cmd: base_volume must be > 0, got: {cmd.base_volume}')

        # Force one-way: position_side must be BOTH
        if cmd.position_side != PositionSide.BOTH:
            raise RuntimeError(f'Position._handle_cmd: position_side must be BOTH, got: {cmd.position_side}')

        if self.chase is not None:
            raise RuntimeError('Position._handle_cmd: chase already running')

        # Disarm entrance timeout on first chase command (entrance is no longer applicable).
        if int(self.entrance_timeout_ms) > 0 and not bool(self.entrance_timeout_fired):
            self.entrance_timeout_fired = True

        # Initialize chase state (StrictModel, no dicts)
        now_ms = int(time.time() * 1000)

        self.chase = _ChaseState(
            cmd_id=cmd.cmd_id,
            started_ms=now_ms,
            symbol=self.symbol,
            side=cmd.side,
            target_units=int(cmd.base_volume),
        )

        # Emit started event
        self._push(PositionEvent(
            event_type=PositionEventType.CHASE_STARTED,
            symbol=self.symbol,
            time_ms=now_ms,
            data={
                'cmd_id': cmd.cmd_id,
                'base_units': int(cmd.base_volume),
                'side': cmd.side.value,
            },
        ), 'Position._handle_cmd')

        # Drive first decision immediately (requires book ticker)
        self._manage_chase()

    def _handle_stop(self, cmd):
        """
        Handle STOP command inside unified loop and perform full cleanup.

        Responsibilities:
         - Delete active chase order if present (ignore DOES_NOT_EXIST as terminal).
         - Force-close any remaining base balance via reduce-only MARKET order.
         - Unsubscribe from realtime and stop/flush fills router + aggregator.

        Contract:
         - After successful stop, Position becomes not started and loop() terminates.
         - Cleanup errors are collected and raised (no silent ignores).
        """
        if cmd is None:
            raise RuntimeError('Position._handle_stop: cmd is None')
        
        if not isinstance(cmd, PositionStopCommand):
            raise RuntimeError(f'Position._handle_stop: cmd is not PositionStopCommand: {type(cmd)}')
        
        if cmd.cmd != PositionCommandType.STOP:
            raise RuntimeError(f'Position._handle_stop: bad cmd: {cmd.cmd}')
        
        if cmd.symbol != self.symbol:
            raise RuntimeError(f'Position._handle_stop: symbol mismatch: cmd.symbol={cmd.symbol} position.symbol={self.symbol}')
        
        if self.chase is not None:
            ch = self.chase
            
            if ch.active_order_id is not None:
                oid = str(ch.active_order_id)
                
                ok, order_error, err = self.exchange.delete_order(
                    symbol=self.symbol,
                    order_id=str(oid),
                    order_type=OrderType.LIMIT,
                )
                
                if order_error is not None and order_error != OrderError.DOES_NOT_EXIST:
                    raise RuntimeError(f'Position._handle_stop: delete_order failed: order_error={order_error} err={err}')
                
                if err is not None and order_error != OrderError.DOES_NOT_EXIST:
                    raise RuntimeError(f'Position._handle_stop: delete_order failed: err={err}')
                
                self._push(PositionEvent(
                    event_type=PositionEventType.CHASE_ORDER_DELETED,
                    symbol=self.symbol,
                    time_ms=int(time.time() * 1000),
                    data={
                        'cmd_id': str(ch.cmd_id),
                        'order_id': str(oid),
                        'ok': bool(ok),
                        'order_error': order_error.value if order_error is not None else None,
                        'error': str(err) if err is not None else None,
                        'stage': 'stop',
                    },
                ), 'Position._handle_stop')
                
                ch.active_order_id = None
                ch.active_price_units = 0
                ch.active_volume_units = 0
        
        bal = int(self._sum_position_base_balance_units())
        if bal != 0:
            if bal > 0:
                side = OrderSide.SELL
            else:
                side = OrderSide.BUY
            
            base_units = int(bal)
            if base_units < 0:
                base_units = int(-base_units)
            
            order_id, can_repeat, order_error, err = self.exchange.create_order(
                position_side=PositionSide.BOTH,
                order_side=side,
                order_type=OrderType.MARKET,
                symbol=self.symbol,
                base_volume=int(base_units),
                price=None,
                reduce_only=True,
            )
            
            if order_error is not None:
                raise RuntimeError(f'Position._handle_stop: reduce_only market close failed: order_error={order_error} can_repeat={can_repeat} err={err}')
            
            if err is not None:
                raise RuntimeError(f'Position._handle_stop: reduce_only market close failed: can_repeat={can_repeat} err={err}')
            
            if not order_id:
                raise RuntimeError('Position._handle_stop: reduce_only market close returned empty order_id')
            
            self.logger.info(f'position_stop_reduce_only uid={self.uid} symbol={self.symbol} side={side.value} base_units={int(base_units)} order_id={order_id}')
        
        cleanup_errors = []
        
        try:
            self.realtime.unsubscribe(self.uid)
        except Exception as e:
            cleanup_errors.append(e)
        
        if self.fills_rt is None:
            cleanup_errors.append(RuntimeError('Position._handle_stop: fills_rt is None'))
        else:
            try:
                self.fills_rt.flush_all()
            except Exception as e:
                cleanup_errors.append(e)
        
        if self.fills_agg is None:
            cleanup_errors.append(RuntimeError('Position._handle_stop: fills_agg is None'))
        else:
            try:
                self.fills_agg.stop_and_flush()
            except Exception as e:
                cleanup_errors.append(e)
        
        if self.fills_rt is not None:
            try:
                self.fills_rt.stop()
            except Exception as e:
                cleanup_errors.append(e)
        
        if self.fills_agg is not None:
            try:
                self.fills_agg.check()
            except Exception as e:
                cleanup_errors.append(e)
        
        if self.fills_rt is not None:
            try:
                self.fills_rt.check()
            except Exception as e:
                cleanup_errors.append(e)
        
        if len(cleanup_errors) > 0:
            if len(cleanup_errors) == 1:
                raise cleanup_errors[0]
            
            raise RuntimeError(f'Position._handle_stop: cleanup_errors={cleanup_errors}')
        
        self.started = False

    def _sum_chase_filled_units(self):
        """
        Sum filled base units for the current chase across all chase order_ids.

        Output:
         - int filled_units (>= 0)

        Preconditions:
         - self.chase must be set.
        """
        ch = self.chase
        if ch is None:
            raise RuntimeError('Position._sum_chase_filled_units: chase is None')

        if not isinstance(ch, _ChaseState):
            raise RuntimeError(f'Position._sum_chase_filled_units: chase is not _ChaseState: {type(ch)}')

        s = 0

        for oid in ch.order_ids:
            if not oid:
                raise RuntimeError('Position._sum_chase_filled_units: empty order_id')

            s += int(self._sum_order_filled_units(str(oid)))

        return int(s)

    def _sum_chase_remaining_units(self):
        """
        Remaining base units to fill for current chase.

        Output:
         - int remaining_units = max(target_units - filled_units, 0)

        Preconditions:
         - self.chase must be set.
        """
        ch = self.chase
        if ch is None:
            raise RuntimeError('Position._sum_chase_remaining_units: chase is None')

        if not isinstance(ch, _ChaseState):
            raise RuntimeError(f'Position._sum_chase_remaining_units: chase is not _ChaseState: {type(ch)}')

        filled = int(self._sum_chase_filled_units())
        remaining = int(ch.target_units) - int(filled)

        if remaining < 0:
            remaining = 0

        return int(remaining)
   
    def _apply_aggregate(self, agg):
        """
        Apply an AggregatedFill into per-order storage (order_fills).

        Contract:
         - Skips finalized orders (filled or filled-by-deletion).
         - Stores the aggregate and finalizes order as filled when agg.is_filled is True.
        """
        # Store aggregated fill per order id (no global counters).
        if agg is None:
            raise RuntimeError('Position._apply_aggregate: agg is None')

        if not isinstance(agg, AggregatedFill):
            raise RuntimeError(f'Position._apply_aggregate: agg is not AggregatedFill: {type(agg)}')

        if self._is_order_finalized(agg.order_id):
            return

        self._store_order_fill(agg)

    def _store_order_fill(self, agg):
        """
        Store an AggregatedFill part for a specific order_id.

        Responsibilities:
         - Initialize _OrderFillsState on first sight of order_id.
         - Append aggregated fill part while order is not terminal.
         - Finalize order when agg.is_filled is True.

        Notes:
         - Terminal orders (filled/filled-by-deletion) ignore further parts (avoid double accounting).
        """
        if agg is None:
            raise RuntimeError('Position._store_order_fill: agg is None')

        if not isinstance(agg, AggregatedFill):
            raise RuntimeError(f'Position._store_order_fill: agg is not AggregatedFill: {type(agg)}')

        if not agg.order_id:
            raise RuntimeError('Position._store_order_fill: empty order_id')

        if agg.side is None:
            raise RuntimeError('Position._store_order_fill: agg.side is None')

        if not isinstance(agg.base_volume, int):
            raise RuntimeError(f'Position._store_order_fill: agg.base_volume is not int: {type(agg.base_volume)}')

        if int(agg.base_volume) <= 0:
            raise RuntimeError(f'Position._store_order_fill: agg.base_volume must be > 0, got: {agg.base_volume}')

        st = self.order_fills.get(str(agg.order_id))
        if st is None:
            st = _OrderFillsState(
                order_id=str(agg.order_id),
                side=agg.side,
            )
            self.order_fills[str(agg.order_id)] = st
        else:
            if not isinstance(st, _OrderFillsState):
                raise RuntimeError(f'Position._store_order_fill: order_fills state is not _OrderFillsState: {type(st)}')

            if st.order_id != str(agg.order_id):
                raise RuntimeError('Position._store_order_fill: state.order_id mismatch')

            if st.side != agg.side:
                raise RuntimeError(f'Position._store_order_fill: side mismatch: state.side={st.side} agg.side={agg.side}')

            if bool(st.filled_by_deletion) or bool(st.is_filled):
                return

        st.parts.append(agg)
        
        if bool(agg.is_filled):
            self._finalize_order_as_filled(st)
    
    def _finalize_order_as_filled(self, st):
        """
        Finalize an order as filled using currently stored AggregatedFill parts.

        Contract:
         - Computes filled_units as sum of base_volume across parts.
         - Marks st.is_filled=True and clears st.parts to stop further accounting.
         - If order is already terminal (filled/filled-by-deletion) this is a no-op.
        """
        if st is None:
            raise RuntimeError('Position._finalize_order_as_filled: st is None')

        if not isinstance(st, _OrderFillsState):
            raise RuntimeError(f'Position._finalize_order_as_filled: st is not _OrderFillsState: {type(st)}')

        if bool(st.filled_by_deletion):
            return

        if bool(st.is_filled):
            return

        s = 0

        for agg in st.parts:
            if agg is None:
                raise RuntimeError('Position._finalize_order_as_filled: agg is None')

            if not isinstance(agg, AggregatedFill):
                raise RuntimeError(f'Position._finalize_order_as_filled: agg is not AggregatedFill: {type(agg)}')

            if agg.order_id != st.order_id:
                raise RuntimeError('Position._finalize_order_as_filled: order_id mismatch')

            if agg.side != st.side:
                raise RuntimeError('Position._finalize_order_as_filled: side mismatch')

            s += int(agg.base_volume)

        if int(s) <= 0:
            raise RuntimeError(f'Position._finalize_order_as_filled: non-positive filled_units: {s}')

        st.is_filled = True
        st.filled_units = int(s)
        st.parts = []

    def _is_order_finalized(self, order_id):
        """
        Check if an order_id is terminal for accounting.

        Output:
         - True when order is filled or filled-by-deletion.
         - False when order has no state or is still collecting parts.
        """
        if not order_id:
            raise RuntimeError('Position._is_order_finalized: empty order_id')

        st = self.order_fills.get(str(order_id))
        if st is None:
            return False

        if not isinstance(st, _OrderFillsState):
            raise RuntimeError(f'Position._is_order_finalized: state is not _OrderFillsState: {type(st)}')

        if bool(st.filled_by_deletion):
            return True
        
        if bool(st.is_filled):
            return True

        return False

    def _sum_order_filled_units(self, order_id):
        """
        Sum filled base units for a single order_id.

        Output:
         - int filled_units (>= 0)

        Contract:
         - For terminal orders returns cached st.filled_units.
         - For non-terminal orders sums base_volume across stored parts.
        """
        if not order_id:
            raise RuntimeError('Position._sum_order_filled_units: empty order_id')

        st = self.order_fills.get(str(order_id))
        if st is None:
            return 0

        if not isinstance(st, _OrderFillsState):
            raise RuntimeError(f'Position._sum_order_filled_units: state is not _OrderFillsState: {type(st)}')

        if bool(st.filled_by_deletion) or bool(st.is_filled):
            return int(st.filled_units)

        s = 0

        for agg in st.parts:
            if agg is None:
                raise RuntimeError('Position._sum_order_filled_units: agg is None')

            if not isinstance(agg, AggregatedFill):
                raise RuntimeError(f'Position._sum_order_filled_units: agg is not AggregatedFill: {type(agg)}')

            if agg.order_id != str(order_id):
                raise RuntimeError('Position._sum_order_filled_units: order_id mismatch')

            if agg.side != st.side:
                raise RuntimeError('Position._sum_order_filled_units: side mismatch')

            s += int(agg.base_volume)

        return int(s)

    def _sum_position_base_balance_units(self):
        """
        Sum current base balance across all stored order fills.

        Output:
         - int base_balance_units:
           BUY fills add, SELL fills subtract.

        Notes:
         - Only counted when per-order filled_units > 0.
         - Intended to end at 0 after a complete open+close sequence.
        """
        s = 0

        for order_id, st in self.order_fills.items():
            if not order_id:
                raise RuntimeError('Position._sum_position_base_balance_units: empty order_id key')

            if st is None:
                raise RuntimeError('Position._sum_position_base_balance_units: state is None')

            if not isinstance(st, _OrderFillsState):
                raise RuntimeError(f'Position._sum_position_base_balance_units: state is not _OrderFillsState: {type(st)}')

            filled = self._sum_order_filled_units(order_id)
            if int(filled) <= 0:
                continue

            if st.side == OrderSide.BUY:
                s += int(filled)
            elif st.side == OrderSide.SELL:
                s -= int(filled)
            else:
                raise RuntimeError(f'Position._sum_position_base_balance_units: bad side: {st.side}')

        return int(s)

    def _mark_order_filled_by_deletion(self, order_id, side, full_units):
        """
        Finalize an order as fully filled inferred from deletion / DOES_NOT_EXIST.

        Motivation:
         - Some exchanges cancel/replace during modify and later report DOES_NOT_EXIST.
           In this case we conservatively treat the order as filled up to its full intended size.

        Contract:
         - Marks order as terminal (filled_by_deletion=True).
         - Wipes stored parts to avoid double accounting.
         - Returns previously counted filled units for this order_id.
        """
        # Finalize order as fully filled inferred from deletion / does_not_exist.
        # Important: to avoid double accounting, we wipe stored fills and ignore any future fills for this order id.
        if not order_id:
            raise RuntimeError('Position._mark_order_filled_by_deletion: empty order_id')

        if side is None:
            raise RuntimeError('Position._mark_order_filled_by_deletion: side is None')

        if not isinstance(full_units, int):
            raise RuntimeError(f'Position._mark_order_filled_by_deletion: full_units is not int: {type(full_units)}')

        if int(full_units) <= 0:
            raise RuntimeError(f'Position._mark_order_filled_by_deletion: full_units must be > 0, got: {full_units}')

        prev = self._sum_order_filled_units(str(order_id))

        st = self.order_fills.get(str(order_id))
        if st is None:
            st = _OrderFillsState(
                order_id=str(order_id),
                side=side,
            )
            self.order_fills[str(order_id)] = st
        else:
            if not isinstance(st, _OrderFillsState):
                raise RuntimeError(f'Position._mark_order_filled_by_deletion: state is not _OrderFillsState: {type(st)}')

            if st.side != side:
                raise RuntimeError(f'Position._mark_order_filled_by_deletion: side mismatch: state.side={st.side} side={side}')

        st.filled_by_deletion = True
        st.filled_units = int(full_units)
        st.parts = []

        return int(prev)

    def _manage_chase(self):
        """
        Main chase decision step driven by BookTicker updates.

        Responsibilities:
         - Fire entrance timeout when enabled and no chase started in time.
         - For active chase:
            - Compute remaining target units.
            - Select maker price (BUY -> bid, SELL -> ask).
            - Create first order or modify active order to follow book and remaining volume.
         - Respect cooldown after GTX violation or inferred deletion.
        """
        # Entrance timeout is checked on every book ticker update.
        # This is a Position-level timeout event used by HedgeEngine in LONG_ONLY / SHORT_ONLY:
        # close hedge if no fills happened and no chase was ever started.
        if int(self.entrance_timeout_ms) > 0 and not bool(self.entrance_timeout_fired):
            ch = self.chase
            if ch is None:
                if int(self.entrance_started_ms) <= 0:
                    raise RuntimeError('Position._manage_chase: entrance_started_ms is not set')

                now_ms = int(time.time() * 1000)
                dt_ms = int(now_ms) - int(self.entrance_started_ms)

                if dt_ms > int(self.entrance_timeout_ms):
                    bal = int(self._sum_position_base_balance_units())
                    if bal != 0:
                        raise RuntimeError(f'Position._manage_chase: entrance timeout fired but base balance is non-zero: {bal}')

                    self.entrance_timeout_fired = True

                    self._push(PositionEvent(
                        event_type=PositionEventType.ENTRANCE_TIMEOUT,
                        symbol=self.symbol,
                        time_ms=int(now_ms),
                        data={
                            'entrance_timeout_ms': int(self.entrance_timeout_ms),
                            'started_ms': int(self.entrance_started_ms),
                            'dt_ms': int(dt_ms),
                        },
                    ), 'Position._manage_chase')

                    return

        # Chase decision is made only on book ticker updates
        ch = self.chase
        if ch is None:
            return

        remaining = int(self._sum_chase_remaining_units())

        # Finish fast when remaining is already satisfied
        if remaining <= 0:
            self._finish_chase(ok=True, err=None)
            return

        # Book ticker is mandatory for chase
        with self.book_lock:
            book = self.book
        if book is None:
            return

        # Cooldown after GTX/price violation or after deletion
        now_ms = int(time.time() * 1000)
        if ch.cooldown_until_ms > now_ms:
            return

        # Maker price selection: BUY -> bid, SELL -> ask
        if ch.side == OrderSide.BUY:
            desired_price = int(book.bid_price)
        elif ch.side == OrderSide.SELL:
            desired_price = int(book.ask_price)
        else:
            raise RuntimeError(f'Position._manage_chase: bad side: {ch.side}')

        if desired_price <= 0:
            raise RuntimeError(f'Position._manage_chase: bad desired_price: {desired_price}')

        # No active order -> create
        if ch.active_order_id is None:
            self._chase_create(desired_price, int(remaining))
            return

        # Active exists -> requote when price/volume differs
        if ch.active_price_units != desired_price or ch.active_volume_units != int(remaining):
            self._chase_modify(desired_price, int(remaining))

    def _chase_create(self, price_units, volume_units):
        """
        Create a new post-only (GTX) LIMIT order for the current chase.

        Input:
         - price_units: int maker price in exchange units
         - volume_units: int base size in exchange units (remaining target)

        Behavior:
         - Calls exchange.create_order(LIMIT, reduce_only=False).
         - On PRICE_VIOLATION: increments gtx_violations, sets cooldown and emits CHASE_PRICE_VIOLATION.
         - On other exchange errors: emits CHASE_EXCHANGE_ERROR and returns (retry on next tick).
         - On success: attaches order_id to fills router and emits CHASE_ORDER_CREATED.
        """
        # Create GTX LIMIT order and attach it for fills routing
        ch = self.chase
        if ch is None:
            raise RuntimeError('Position._chase_create: chase is None')

        if not isinstance(price_units, int) or not isinstance(volume_units, int):
            raise RuntimeError('Position._chase_create: price_units and volume_units must be int')

        if price_units <= 0:
            raise RuntimeError(f'Position._chase_create: bad price_units: {price_units}')

        if volume_units <= 0:
            raise RuntimeError(f'Position._chase_create: bad volume_units: {volume_units}')

        order_id, can_repeat, order_error, err = self.exchange.create_order(
            position_side=PositionSide.BOTH,
            order_side=ch.side,
            order_type=OrderType.LIMIT,
            symbol=self.symbol,
            base_volume=int(volume_units),
            price=int(price_units),
            reduce_only=False,
        )

        if order_error is not None:
            if order_error == OrderError.PRICE_VIOLATION:
                ch.gtx_violations = int(ch.gtx_violations) + 1
                ch.cooldown_until_ms = int(time.time() * 1000) + int(self.gtx_cooldown_ms)

                self._push(PositionEvent(
                    event_type=PositionEventType.CHASE_PRICE_VIOLATION,
                    symbol=self.symbol,
                    time_ms=int(time.time() * 1000),
                    data={
                        'cmd_id': ch.cmd_id,
                        'can_repeat': bool(can_repeat),
                        'price_units': int(price_units),
                        'volume_units': int(volume_units),
                        'gtx_violations': int(ch.gtx_violations),
                        'error': str(err) if err is not None else None,
                        'stage': 'create',
                    },
                ), 'Position._chase_create')
                return

            self._push(PositionEvent(
                event_type=PositionEventType.CHASE_EXCHANGE_ERROR,
                symbol=self.symbol,
                time_ms=int(time.time() * 1000),
                data={
                    'cmd_id': ch.cmd_id,
                    'can_repeat': bool(can_repeat),
                    'order_error': order_error.value,
                    'error': str(err) if err is not None else None,
                    'price_units': int(price_units),
                    'volume_units': int(volume_units),
                    'stage': 'create',
                },
            ), 'Position._chase_create')

            return

        # Exchange errors are non-fatal for the unified loop; emit and retry on next tick if allowed.
        if err is not None:
            self._push(PositionEvent(
                event_type=PositionEventType.CHASE_EXCHANGE_ERROR,
                symbol=self.symbol,
                time_ms=int(time.time() * 1000),
                data={
                    'cmd_id': ch.cmd_id,
                    'can_repeat': bool(can_repeat),
                    'order_error': None,
                    'error': str(err),
                    'price_units': int(price_units),
                    'volume_units': int(volume_units),
                    'stage': 'create',
                },
            ), 'Position._chase_create')

            return

        if not order_id:
            raise RuntimeError(f'Position._chase_create: empty order_id can_repeat={can_repeat}')

        # Attach order id for fills router (fills may arrive before order_id)
        if self.fills_rt is None:
            raise RuntimeError('Position._chase_create: fills_rt is None')
        self.fills_rt.attach_order(order_id)

        # Update chase active order
        ch.active_order_id = str(order_id)
        ch.active_price_units = int(price_units)
        ch.active_volume_units = int(volume_units)

        if str(order_id) not in ch.order_ids:
            ch.order_ids.append(str(order_id))

        # Emit created event
        self._push(PositionEvent(
            event_type=PositionEventType.CHASE_ORDER_CREATED,
            symbol=self.symbol,
            time_ms=int(time.time() * 1000),
            data={
                'cmd_id': ch.cmd_id,
                'order_id': str(order_id),
                'price_units': int(price_units),
                'volume_units': int(volume_units),
            },
        ), 'Position._chase_create')

    def _chase_modify(self, price_units, volume_units):
        """
        Modify active post-only (GTX) LIMIT order to follow desired price and volume.

        Input:
         - price_units: int desired maker price in exchange units
         - volume_units: int desired base size in exchange units

        Behavior:
         - Calls exchange.modify_order() for active_order_id.
         - On PRICE_VIOLATION: treat as cancellation during modify, set cooldown, clear active id and emit CHASE_PRICE_VIOLATION.
         - On DOES_NOT_EXIST: infer filled-by-deletion, finalize order accounting, clear active id, emit CHASE_FILLED_BY_DELETION.
         - On other exchange errors: emit CHASE_EXCHANGE_ERROR and return (retry on next tick).
         - On success: updates active_price_units and active_volume_units snapshot.
        """
        # Modify active GTX LIMIT order at desired price and desired volume.
        ch = self.chase
        if ch is None:
            raise RuntimeError('Position._chase_modify: chase is None')

        if ch.active_order_id is None:
            raise RuntimeError('Position._chase_modify: active_order_id is None')

        active_id = str(ch.active_order_id)

        can_repeat, order_error, err = self.exchange.modify_order(
            symbol=self.symbol,
            order_id=active_id,
            position_side=PositionSide.BOTH,
            order_side=ch.side,
            order_type=OrderType.LIMIT,
            base_volume=int(volume_units),
            price=int(price_units),
        )

        if order_error is not None:
            # PRICE_VIOLATION on modify implies exchange cancels the order during modification.
            # We must drop the active id and wait for a short cooldown to allow any trailing fills to arrive.
            if order_error == OrderError.PRICE_VIOLATION:
                ch.gtx_violations = int(ch.gtx_violations) + 1
                ch.cooldown_until_ms = int(time.time() * 1000) + int(self.gtx_cooldown_ms)

                self._push(PositionEvent(
                    event_type=PositionEventType.CHASE_PRICE_VIOLATION,
                    symbol=self.symbol,
                    time_ms=int(time.time() * 1000),
                    data={
                        'cmd_id': ch.cmd_id,
                        'order_id': active_id,
                        'can_repeat': bool(can_repeat),
                        'price_units': int(price_units),
                        'volume_units': int(volume_units),
                        'gtx_violations': int(ch.gtx_violations),
                        'error': str(err) if err is not None else None,
                        'stage': 'modify',
                    },
                ), 'Position._chase_modify')

                ch.active_order_id = None
                ch.active_price_units = 0
                ch.active_volume_units = 0

                return

            # DOES_NOT_EXIST => mark order as filled-by-deletion and ignore all its fills.
            if order_error == OrderError.DOES_NOT_EXIST:
                prev_bal = int(self._sum_position_base_balance_units())
                
                full_units = int(ch.active_volume_units)
                if full_units <= 0:
                    raise RuntimeError(f'Position._chase_modify: bad active_volume_units: {full_units}')

                prev_filled = self._mark_order_filled_by_deletion(active_id, ch.side, int(full_units))
                delta = int(full_units) - int(prev_filled)
                
                new_bal = int(self._sum_position_base_balance_units())
                
                self._log_balance_mutation(prev_bal, new_bal, 'filled_by_deletion', {
                    'cmd_id': ch.cmd_id,
                    'order_id': str(active_id),
                    'side': ch.side.value if ch.side is not None else None,
                    'filled_units': int(delta) if int(delta) > 0 else 0,
                    'filled_prev_units': int(prev_filled),
                    'filled_total_units': int(full_units),
                })

                if delta < 0:
                    delta = 0

                filled_total = int(self._sum_chase_filled_units())
                remaining_total = int(self._sum_chase_remaining_units())

                self._push(PositionEvent(
                    event_type=PositionEventType.CHASE_FILLED_BY_DELETION,
                    symbol=self.symbol,
                    time_ms=int(time.time() * 1000),
                    data={
                        'cmd_id': ch.cmd_id,
                        'order_id': active_id,
                        'filled_units': int(delta),
                        'filled_prev_units': int(prev_filled),
                        'filled_total_units': int(full_units),
                        'price_units': int(ch.active_price_units),
                        'chase_filled_units': int(filled_total),
                        'chase_remaining_units': int(remaining_total),
                        'can_repeat': bool(can_repeat),
                        'error': str(err) if err is not None else None,
                        'stage': 'modify',
                    },
                ), 'Position._chase_modify')

                ch.active_order_id = None
                ch.active_price_units = 0
                ch.active_volume_units = 0

                if remaining_total == 0:
                    self._finish_chase(ok=True, err=None)
                return

            self._push(PositionEvent(
                event_type=PositionEventType.CHASE_EXCHANGE_ERROR,
                symbol=self.symbol,
                time_ms=int(time.time() * 1000),
                data={
                    'cmd_id': ch.cmd_id,
                    'order_id': active_id,
                    'can_repeat': bool(can_repeat),
                    'order_error': order_error.value,
                    'error': str(err) if err is not None else None,
                    'price_units': int(price_units),
                    'volume_units': int(volume_units),
                    'stage': 'modify',
                },
            ), 'Position._chase_modify')

            return

        if err is not None:
            self._push(PositionEvent(
                event_type=PositionEventType.CHASE_EXCHANGE_ERROR,
                symbol=self.symbol,
                time_ms=int(time.time() * 1000),
                data={
                    'cmd_id': ch.cmd_id,
                    'order_id': active_id,
                    'can_repeat': bool(can_repeat),
                    'order_error': None,
                    'error': str(err),
                    'price_units': int(price_units),
                    'volume_units': int(volume_units),
                    'stage': 'modify',
                },
            ), 'Position._chase_modify')

            return

        # Modify succeeded: update active snapshot
        ch.active_price_units = int(price_units)
        ch.active_volume_units = int(volume_units)

    def _on_chase_agg_fill(self, agg):
        """
        Handle AggregatedFill for the current chase (routing and progress tracking).

        Behavior:
         - Ignores when no chase is active.
         - Ignores finalized orders and orders not belonging to current chase.
         - Validates side consistency and updates chase progress (filled/remaining).
         - Emits PositionEvent(CHASE_AGG_FILL).
         - Clears active_order_id when agg.is_filled is True for that order.
         - Finishes chase immediately when remaining_total reaches 0.
        """
        # Apply aggregated fills to chase state when they belong to current chase (any order id).
        ch = self.chase
        if ch is None:
            return

        if agg is None:
            raise RuntimeError('Position._on_chase_agg_fill: agg is None')

        if not isinstance(agg, AggregatedFill):
            raise RuntimeError(f'Position._on_chase_agg_fill: agg is not AggregatedFill: {type(agg)}')

        if self._is_order_finalized(agg.order_id):
            return

        if agg.order_id not in ch.order_ids:
            return

        if agg.side != ch.side:
            raise RuntimeError(f'Position._on_chase_agg_fill: side mismatch: agg.side={agg.side} chase.side={ch.side}')

        base_units = int(agg.base_volume)
        if base_units <= 0:
            raise RuntimeError(f'Position._on_chase_agg_fill: bad base_units: {base_units}')

        # Totals should include the current agg even before it's stored.
        filled_prev = int(self._sum_chase_filled_units())
        filled_total = int(filled_prev) + int(base_units)
        remaining_total = int(ch.target_units) - int(filled_total)

        if remaining_total < 0:
            remaining_total = 0

        # Emit agg fill event for chase
        self._push(PositionEvent(
            event_type=PositionEventType.CHASE_AGG_FILL,
            symbol=self.symbol,
            time_ms=int(time.time() * 1000),
            data={
                'cmd_id': ch.cmd_id,
                'order_id': str(agg.order_id),
                'base_units': int(base_units),
                'price_units': int(agg.price),
                'filled_units': int(filled_total),
                'remaining_units': int(remaining_total),
            },
        ), 'Position._on_chase_agg_fill')
        
        if bool(agg.is_filled):
            if ch.active_order_id == str(agg.order_id):
                ch.active_order_id = None
                ch.active_price_units = 0
                ch.active_volume_units = 0

        # Finish when satisfied
        if remaining_total == 0:
            # Defer finalization until after this agg is applied to accounting,
            # otherwise _finish_chase() may see filled_total=0.
            ch.finish_pending = True

    def _finish_chase(self, ok, err):
        """
        Finalize current chase and emit terminal outputs.

        Output (into unified queue):
         - ChaseResponse(cmd_id, ok, target_base_volume, filled_base_volume, error, timestamps)
         - PositionEvent(CHASE_DONE)

        Contract:
         - After this call self.chase is cleared (Position becomes idle).
        """
        # Finalize chase and emit response + done event
        ch = self.chase
        if ch is None:
            raise RuntimeError('Position._finish_chase: chase is None')

        now_ms = int(time.time() * 1000)
        filled_total = int(self._sum_chase_filled_units())

        self._push(ChaseResponse(
            cmd_id=ch.cmd_id,
            symbol=self.symbol,
            ok=bool(ok),
            started_ms=int(ch.started_ms),
            finished_ms=int(now_ms),
            target_base_volume=int(ch.target_units),
            filled_base_volume=int(filled_total),
            error=err,
        ), 'Position._finish_chase')

        self._push(PositionEvent(
            event_type=PositionEventType.CHASE_DONE,
            symbol=self.symbol,
            time_ms=int(now_ms),
            data={
                'cmd_id': ch.cmd_id,
                'ok': bool(ok),
                'error': err,
                'target_units': int(ch.target_units),
                'filled_units': int(filled_total),
            },
        ), 'Position._finish_chase')

        self.chase = None

