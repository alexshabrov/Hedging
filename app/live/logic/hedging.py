import time
import orjson
from queue import Queue
from threading import Thread, Lock, Event

from ..lib.logger import get_logger

from ..exchanges.exchange_interface import ExchangeInterface, RealtimeInterface
from ..exchanges.exchange_models import BookTicker
from ..exchanges.exchange_models import OrderSide, PositionSide
from ..exchanges.exchange_models import ChaseCommand, PositionCommandType
from ..exchanges.exchange_models import PositionEventType
from ..exchanges.exchange_models import PositionEvent, ChaseResponse
from ..exchanges.exchange_models import Rule
from ..exchanges.position import Position

from .helpers import pct_x10000_mul_price, mid_price_units
from .helpers import validate_offsets, validate_optional_reason
from .models import HedgeMode, HedgeStatus, HedgeLeg
from .models import HedgeConfig, HedgeLines, HedgeVolumeRequest
from .models import HedgeSnapshot, _HedgeState
from .models import HedgeMetrics, HedgeChaseMetrics, HedgeChaseKind
from .models import HedgeCloseReason

"""
Live hedge engine (isolated module).

Implements the same high-level threshold/target/neutral-close logic as backtest `app/lib/logic.py`,
but runs against live BookTicker + maker-chase execution (`Position`) and supports background operation.

Differences vs the task spec:
 - All schemas are StrictModel + Enum only.
 - When opening threshold is touched, the engine calls a user callback to obtain the base volume (units).

Important:
 - Uses strict integer units only (price_units, base_units).
 - Uses only orjson for JSON.
 - No fallback logic and no silent ignores: all critical errors are raised or transition hedge into FAILED.

Example (minimal):

    def on_volume(req: HedgeVolumeRequest) -> int:
        # Return base volume in exchange lot-step units (int).
        # You own exposure math; engine just asks at threshold touch.
        if req.leg == HedgeLeg.LONG:
            return 10
        return 10

    cfg = HedgeConfig(
        hedge_id='h1',
        symbol='BTCUSDT',
        hedge_mode=HedgeMode.BOTH,
        trigger_offset_pct_x10000=HedgeOffsetsPctX10000(long=2500, short=2500),   # 0.25%
        target_offset_pct_x10000=HedgeOffsetsPctX10000(long=5000, short=5000),    # 0.50%
        execution_params=HedgeExecutionParams(
            tick_ms=5,
            gtx_cooldown_ms=5,
            entrance_timeout_ms=60_000,
        ),
    )

    hedge = HedgeEngine(config=cfg, exchange=exchange, realtime=realtime, on_volume=on_volume)
    hedge.start()
    # ...
    snap = hedge.status()
    raw = hedge.status_json()
    # ...
    hedge.close(reason='manual')
"""

### HedgeEngine ###
class HedgeEngine:
    def __init__(self, config, exchange, realtime, on_volume):
        """
        Create live hedge engine instance.

        Responsibilities:
         - Own state machine (HedgeStatus) and metrics (HedgeMetrics).
         - Subscribe to realtime BookTicker and drive open/close decisions.
         - Execute maker chases via Position and consume PositionEvent / ChaseResponse.
         - Provide thread-safe status snapshots and JSON serialization (orjson).

        Input:
         - config: HedgeConfig (StrictModel)
         - exchange: ExchangeInterface
         - realtime: RealtimeInterface
         - on_volume: callable(HedgeVolumeRequest) -> int base_units

        Errors:
         - Raises on any invalid input and config mismatch (no fallback).
        """
        if config is None:
            raise RuntimeError('HedgeEngine: config is None')

        if not isinstance(config, HedgeConfig):
            raise RuntimeError(f'HedgeEngine: config is not HedgeConfig: {type(config)}')

        if exchange is None:
            raise RuntimeError('HedgeEngine: exchange is None')

        if not isinstance(exchange, ExchangeInterface):
            raise RuntimeError(f'HedgeEngine: exchange is not ExchangeInterface: {type(exchange)}')

        if realtime is None:
            raise RuntimeError('HedgeEngine: realtime is None')

        if not isinstance(realtime, RealtimeInterface):
            raise RuntimeError(f'HedgeEngine: realtime is not RealtimeInterface: {type(realtime)}')

        if on_volume is None:
            raise RuntimeError('HedgeEngine: on_volume is None')

        if not callable(on_volume):
            raise RuntimeError(f'HedgeEngine: on_volume is not callable: {type(on_volume)}')

        if not config.hedge_id:
            raise RuntimeError('HedgeEngine: empty hedge_id')

        if not config.symbol:
            raise RuntimeError('HedgeEngine: empty symbol')

        if config.execution_params is None:
            raise RuntimeError('HedgeEngine: execution_params is None')

        if int(config.execution_params.tick_ms) <= 0:
            raise RuntimeError(f'HedgeEngine: tick_ms must be > 0, got: {config.execution_params.tick_ms}')

        if int(config.execution_params.gtx_cooldown_ms) <= 0:
            raise RuntimeError(f'HedgeEngine: gtx_cooldown_ms must be > 0, got: {config.execution_params.gtx_cooldown_ms}')

        if int(config.execution_params.entrance_timeout_ms) <= 0:
            raise RuntimeError(f'HedgeEngine: entrance_timeout_ms must be > 0, got: {config.execution_params.entrance_timeout_ms}')

        validate_offsets('trigger_offset_pct_x10000', config.trigger_offset_pct_x10000)
        validate_offsets('target_offset_pct_x10000', config.target_offset_pct_x10000)

        self.config = config
        self.exchange = exchange
        self.realtime = realtime
        self.on_volume = on_volume

        self.logger = get_logger(f'hedge_{self.config.hedge_id}')

        self.lock = Lock()
        self.stop_event = Event()

        self.q = Queue()
        self._close_item = object()

        self.rt_ch = None
        self.rt_thread = None
        self.pos_thread = None
        self.main_thread = None

        self.pos = None

        self.state = _HedgeState()

        self.last_error = None
        self.started = False
        
        # Endless mode (single-side only):
        # - Keeps the same base_price_units/lines (single trace).
        # - Automatically re-arms WAITING_TRIGGER after each round close or waiting timeout.
        self._cycle_single_side = False
        self._graceful_stop_requested = False
        self._run_endless_on_snapshot = None
        
        # Optional callback: called when a new endless loop iteration begins (single-side only).
        # Signature: fn(iteration: int, reason: str, time_ms: int) -> None
        self._on_loop_iteration_started = None
        self._loop_iteration = 0

    def start(self):
        """
        Start hedge engine background operation.

        Behavior:
         - Initializes in-memory state and metrics.
         - Creates Position instance for maker chase execution.
         - Subscribes to realtime BookTicker (separate from Position subscription).
         - Starts 3 threads:
            - _rt_thread: BookTicker -> internal queue
            - _pos_thread: Position outputs -> internal queue
            - _main_thread: unified dispatcher and state machine

        Idempotency:
         - Forbidden: calling start() twice raises.
        """
        if self.started:
            raise RuntimeError('HedgeEngine.start: already started')

        self.started = True
        self.stop_event.clear()

        now_ms = int(time.time() * 1000)
        self.state.started_ms = int(now_ms)
        self.state.updated_ms = int(now_ms)
        self.state.mutation_counter = int(self.state.mutation_counter) + 1
        self.state.phase_started_ms = int(now_ms)
        self.state.status = HedgeStatus.INITIALIZED
        self.state.metrics = HedgeMetrics()
        self.state.current_chase = None
        self.state.neutral_excursion_threshold_units = 0
        self.state.neutral_excursion_max_units = 0

        rules = self.exchange.get_rules()
        if rules is None:
            raise RuntimeError('HedgeEngine.start: exchange rules are None')
        if not isinstance(rules, dict):
            raise RuntimeError(f'HedgeEngine.start: exchange rules are not dict: {type(rules)}')
        if self.config.symbol not in rules:
            raise RuntimeError(f'HedgeEngine.start: rule not found for symbol: {self.config.symbol}')

        rule = rules[self.config.symbol]
        if rule is None:
            raise RuntimeError(f'HedgeEngine.start: rule is None for symbol: {self.config.symbol}')
        if not isinstance(rule, Rule):
            raise RuntimeError(f'HedgeEngine.start: rule is not Rule for symbol={self.config.symbol}: {type(rule)}')

        price_step = str(rule.price_step)
        lot_step = str(rule.lot_step)
        if len(price_step) == 0:
            raise RuntimeError(f'HedgeEngine.start: empty price_step for symbol: {self.config.symbol}')
        if len(lot_step) == 0:
            raise RuntimeError(f'HedgeEngine.start: empty lot_step for symbol: {self.config.symbol}')

        self.state.symbol_rule = rule

        # Position (chase executor)
        uid = f'hedge_{self.config.hedge_id}'

        if self.config.hedge_mode == HedgeMode.BOTH:
            entrance_timeout_ms = 0
        else:
            entrance_timeout_ms = int(self.config.execution_params.entrance_timeout_ms)

        self.pos = Position(
            symbol=self.config.symbol,
            exchange=self.exchange,
            realtime=self.realtime,
            uid=f'pos_{uid}',
            tick_ms=int(self.config.execution_params.tick_ms),
            gtx_cooldown_ms=int(self.config.execution_params.gtx_cooldown_ms),
            entrance_timeout_ms=int(entrance_timeout_ms),
        )

        # Realtime subscription (separate from Position subscription)
        self.rt_ch = self.realtime.subscribe(uid=uid, symbol=self.config.symbol)
        if self.rt_ch is None:
            raise RuntimeError('HedgeEngine.start: rt_ch is None')

        # Threads: realtime -> q, position consumer -> q, hedge main loop (background)
        self.rt_thread = Thread(target=self._rt_thread, daemon=True)
        self.rt_thread.start()

        self.pos_thread = Thread(target=self._pos_thread, daemon=True)
        self.pos_thread.start()

        self.main_thread = Thread(target=self._main_thread, daemon=True)
        self.main_thread.start()

    def run_endless(self, sleep_s: float = 0.05):
        """
        Convenience helper: run single-side hedge endlessly inside one HedgeEngine instance.

        Contract:
         - Enables internal cycle mode (single-side only).
         - Starts the engine (if not started).
         - Blocks until the engine finishes (graceful_stop / close / failure), then performs stop() cleanup.
        """
        if sleep_s is None:
            raise RuntimeError('HedgeEngine.run_endless: sleep_s is None')
        if not isinstance(sleep_s, float):
            raise RuntimeError(f'HedgeEngine.run_endless: sleep_s is not float: {type(sleep_s)}')
        if float(sleep_s) <= 0:
            raise RuntimeError(f'HedgeEngine.run_endless: sleep_s must be > 0, got: {sleep_s}')

        self._cycle_single_side = True

        if not bool(self.started):
            self.start()

        try:
            while True:
                self.check()
                snap = self.status()
                cb = self._run_endless_on_snapshot
                if cb is not None:
                    cb(snap)
                if snap.status == HedgeStatus.CLOSED or snap.status == HedgeStatus.FAILED:
                    return snap
                time.sleep(float(sleep_s))
        finally:
            if bool(self.started):
                self.stop()

    def graceful_stop(self, reason=None):
        """
        Request a graceful stop:
         - Stops endless cycling after the current round ends.
         - If no exposure exists (WAITING_TRIGGER/INITIALIZED), closes immediately.

        Notes:
         - Does NOT force-close an active position (use close()/stop() for force).
        """
        if not bool(self.started):
            raise RuntimeError('HedgeEngine.graceful_stop: not started')

        _ = validate_optional_reason(reason, 'HedgeEngine.graceful_stop')

        self._graceful_stop_requested = True
        self._push({'type': 'graceful_stop', 'reason': str(reason) if reason is not None else None}, 'HedgeEngine.graceful_stop')
    
    def set_on_snapshot(self, callback):
        """
        Optional hook for run_endless(): called with HedgeSnapshot on every polling tick.
        """
        if callback is None:
            self._run_endless_on_snapshot = None
            return
        
        if not callable(callback):
            raise RuntimeError(f'HedgeEngine.set_on_snapshot: callback is not callable: {type(callback)}')
        
        self._run_endless_on_snapshot = callback
    
    def set_on_loop_iteration_started(self, callback):
        if callback is None:
            self._on_loop_iteration_started = None
            return
        
        if not callable(callback):
            raise RuntimeError(f'HedgeEngine.set_on_loop_iteration_started: callback is not callable: {type(callback)}')
        
        self._on_loop_iteration_started = callback

    def close(self, reason=None):
        """
        Request hedge closure and perform full stop/cleanup.

        Behavior:
         - Latches close_requested + optional close_reason into state under lock.
         - Enqueues a close request item into main loop.
         - Calls stop() to cleanup threads and subscriptions.

        Notes:
         - close() is a hard lifecycle end for this HedgeEngine instance.
        """
        if not self.started:
            raise RuntimeError('HedgeEngine.close: not started')

        _ = validate_optional_reason(reason, 'HedgeEngine.close')

        with self.lock:
            self.state.close_requested = True
            self.state.close_reason = str(reason) if reason is not None else None

        self._push({'type': 'close'}, 'HedgeEngine.close')
        
        # close() must fully cleanup threads, subscriptions and Position state.
        # Position.stop() also performs its own cleanup (including reducing any remaining exposure).
        self.stop()

    def stop(self):
        """
        Stop background threads and cleanup external resources.

        Responsibilities:
         - Unsubscribe realtime subscription owned by hedge engine.
         - Stop Position (which also performs its own cleanup).
         - Terminate threads and raise any cleanup errors (no silent ignores).

        Idempotency:
         - Forbidden: calling stop() when not started raises.
        """
        if not self.started:
            raise RuntimeError('HedgeEngine.stop: not started')

        self.started = False
        self.stop_event.set()

        cleanup_errors = []

        try:
            if self.rt_ch is not None:
                try:
                    self.realtime.unsubscribe(uid=f'hedge_{self.config.hedge_id}')
                except Exception as e:
                    cleanup_errors.append(e)
        except Exception as e:
            cleanup_errors.append(e)

        try:
            if self.pos is not None and bool(self.pos.started):
                self.pos.stop()
        except Exception as e:
            cleanup_errors.append(e)

        try:
            self._push(self._close_item, 'HedgeEngine.stop')
        except Exception as e:
            cleanup_errors.append(e)

        for th in [self.rt_thread, self.pos_thread]:
            try:
                if th is not None:
                    th.join(timeout=10)
                    if th.is_alive():
                        cleanup_errors.append(RuntimeError('HedgeEngine.stop: failed to stop thread'))
            except Exception as e:
                cleanup_errors.append(e)
        
        try:
            if self.main_thread is not None:
                self.main_thread.join(timeout=10)
                if self.main_thread.is_alive():
                    cleanup_errors.append(RuntimeError('HedgeEngine.stop: failed to stop main_thread'))
        except Exception as e:
            cleanup_errors.append(e)

        if len(cleanup_errors) > 0:
            if len(cleanup_errors) == 1:
                raise cleanup_errors[0]
            raise RuntimeError(f'HedgeEngine.stop: cleanup failed: cleanup_errors={cleanup_errors}')

    def check(self):
        """
        Health check: raise latched fatal error if any.

        Contract:
         - If Position is running, delegates to Position.check() as well.
        """
        if self.last_error is not None:
            raise self.last_error

        if self.pos is not None and bool(self.pos.started):
            self.pos.check()

    def status(self):
        """
        Get a point-in-time snapshot of current hedge state.

        Output:
         - HedgeSnapshot (StrictModel) with status, lines, opened leg, metrics and last_error.

        Thread-safety:
         - Snapshot is created under self.lock.
        """
        with self.lock:
            st = self.state

            return HedgeSnapshot(
                hedge_id=self.config.hedge_id,
                symbol=self.config.symbol,
                status=st.status,
                started_ms=int(st.started_ms),
                updated_ms=int(st.updated_ms),
                mutation_counter=int(st.mutation_counter),
                base_price_units=int(st.base_price_units),
                lines=st.lines,
                symbol_rule=st.symbol_rule,
                opened_leg=st.opened_leg,
                opened_base_units=int(st.opened_base_units),
                close_reason=st.closing_reason.value if st.closing_reason is not None else st.close_reason,
                last_error=st.last_error,
                stats=st.stats,
                metrics=st.metrics,
            )

    def status_json(self):
        """
        Serialize status snapshot to JSON using orjson.

        Output:
         - bytes with JSON payload (HedgeSnapshot.model_dump()).
        """
        snap = self.status()
        raw = orjson.dumps(snap.model_dump())
        return raw

    def _main_thread(self):
        """
        Hedge main dispatcher loop (background).

        Behavior:
         - Blocks on internal queue and dispatches by item type:
            - BookTicker -> _on_book
            - PositionEvent -> _on_position_event
            - ChaseResponse -> _on_chase_response
            - {'type': 'close'} -> _on_close_requested
         - Terminates on _close_item sentinel or when last_error is set.

        Errors:
         - Any exception transitions hedge into FAILED via _fail().
        """
        try:
            while True:
                if self.last_error is not None:
                    return

                it = self.q.get()

                if it is self._close_item:
                    return

                if it is None:
                    raise RuntimeError('HedgeEngine._main_thread: queue item is None')

                if isinstance(it, BookTicker):
                    self._on_book(it)
                elif isinstance(it, PositionEvent):
                    self._on_position_event(it)
                elif isinstance(it, ChaseResponse):
                    self._on_chase_response(it)
                elif isinstance(it, dict):
                    t = it['type']
                    if t == 'close':
                        self._on_close_requested()
                    elif t == 'graceful_stop':
                        if 'reason' not in it:
                            raise RuntimeError('HedgeEngine._main_thread: graceful_stop missing reason')
                        self._on_graceful_stop(it['reason'])
                    else:
                        raise RuntimeError(f'HedgeEngine._main_thread: bad dict item type: {t}')
                else:
                    raise RuntimeError(f'HedgeEngine._main_thread: unexpected item: {type(it)}')
        except Exception as e:
            self._fail(e)

    def _on_graceful_stop(self, reason=None):
        """
        Handle graceful_stop request inside the hedge main loop.

        Behavior:
         - If no exposure exists, closes immediately.
         - Otherwise, latches stop-after-round and lets the current round finish naturally.
        """
        with self.lock:
            st = self.state

            st.updated_ms = int(time.time() * 1000)
            st.mutation_counter = int(st.mutation_counter) + 1

            if reason is not None:
                st.close_reason = str(reason)

            if st.status == HedgeStatus.WAITING_TRIGGER or st.status == HedgeStatus.INITIALIZED:
                st.status = HedgeStatus.CLOSED
                self.logger.info(f'hedge_graceful_stopped_waiting hedge_id={self.config.hedge_id} symbol={self.config.symbol} reason={st.close_reason}')
                self._push(self._close_item, 'HedgeEngine._on_graceful_stop')
                return

            # EXECUTING / ACTIVE / CLOSING: do nothing here; _on_chase_response will stop cycling.
            if st.status == HedgeStatus.EXECUTING or st.status == HedgeStatus.ACTIVE or st.status == HedgeStatus.CLOSING:
                self.logger.info(f'hedge_graceful_stop_requested hedge_id={self.config.hedge_id} symbol={self.config.symbol} status={st.status.value} reason={st.close_reason}')
                return

            if st.status == HedgeStatus.CLOSED or st.status == HedgeStatus.FAILED:
                self._push(self._close_item, 'HedgeEngine._on_graceful_stop')
                return

            raise RuntimeError(f'HedgeEngine._on_graceful_stop: bad status: {st.status}')
    
    def _emit_loop_iteration_started(self, reason: str, time_ms: int):
        if reason is None:
            raise RuntimeError('HedgeEngine._emit_loop_iteration_started: reason is None')
        if not isinstance(time_ms, int):
            raise RuntimeError('HedgeEngine._emit_loop_iteration_started: time_ms must be int')
        if int(time_ms) <= 0:
            raise RuntimeError(f'HedgeEngine._emit_loop_iteration_started: bad time_ms: {time_ms}')
        
        cb = self._on_loop_iteration_started
        if cb is None:
            self._loop_iteration = int(self._loop_iteration) + 1
            return
        
        self._loop_iteration = int(self._loop_iteration) + 1
        cb(int(self._loop_iteration), str(reason), int(time_ms))

    def _push(self, item, ctx):
        """
        Push item into hedge internal queue.

        Input:
         - item: non-None payload (BookTicker, PositionEvent, ChaseResponse or internal dict/sentinel)
         - ctx: str context for error messages

        Contract:
         - Queue is the only cross-thread boundary for hedge logic.
        """
        if ctx is None:
            raise RuntimeError('HedgeEngine._push: ctx is None')

        if item is None:
            raise RuntimeError(f'HedgeEngine._push: item is None ctx={ctx}')

        self.q.put(item)

    def _fail(self, err):
        """
        Transition hedge into FAILED state and latch fatal error.

        Behavior:
         - Sets self.last_error for check() and for terminating threads.
         - Updates state.status=FAILED and state.last_error under lock.
         - Logs the failure and attempts to stop main loop by pushing _close_item.
        """
        if err is None:
            raise RuntimeError('HedgeEngine._fail: err is None')

        if not isinstance(err, Exception):
            raise RuntimeError(f'HedgeEngine._fail: err is not Exception: {type(err)}')

        self.last_error = err

        with self.lock:
            self.state.status = HedgeStatus.FAILED
            self.state.updated_ms = int(time.time() * 1000)
            self.state.mutation_counter = int(self.state.mutation_counter) + 1
            self.state.last_error = str(err)

        self.logger.error(f'hedge_failed hedge_id={self.config.hedge_id} symbol={self.config.symbol} err={err}')

        try:
            self._push(self._close_item, 'HedgeEngine._fail')
        except Exception:
            pass

    def _rt_thread(self):
        """
        Realtime consumer thread: forward BookTicker into hedge queue.

        Contract:
         - Stops when stop_event is set.
         - Any exception transitions hedge into FAILED via _fail().
        """
        try:
            if self.rt_ch is None:
                raise RuntimeError('HedgeEngine._rt_thread: rt_ch is None')

            for book in self.rt_ch:
                if self.stop_event.is_set():
                    return

                if book is None:
                    raise RuntimeError('HedgeEngine._rt_thread: book is None')

                if not isinstance(book, BookTicker):
                    raise RuntimeError(f'HedgeEngine._rt_thread: book is not BookTicker: {type(book)}')

                self._push(book, 'HedgeEngine._rt_thread')
        except Exception as e:
            self._fail(e)

    def _pos_thread(self):
        """
        Position consumer thread: forward Position outputs into hedge queue.

        Input stream:
         - Position.loop() yields PositionEvent and ChaseResponse.

        Contract:
         - Stops when stop_event is set.
         - Any exception transitions hedge into FAILED via _fail().
        """
        try:
            if self.pos is None:
                raise RuntimeError('HedgeEngine._pos_thread: pos is None')

            for it in self.pos.loop():
                if self.stop_event.is_set():
                    return

                if it is None:
                    raise RuntimeError('HedgeEngine._pos_thread: item is None')

                if not isinstance(it, PositionEvent) and not isinstance(it, ChaseResponse):
                    raise RuntimeError(f'HedgeEngine._pos_thread: bad item type: {type(it)}')

                self._push(it, 'HedgeEngine._pos_thread')
        except Exception as e:
            self._fail(e)

    def _init_lines_if_needed(self, book):
        """
        Initialize hedge trigger/target lines once, using first observed mid price.

        Behavior:
         - Computes base_price_units from BookTicker mid.
         - Converts trigger/target offsets (pct_x10000) into integer deltas.
         - Builds HedgeLines absolute price levels and stores them into state.
         - Transitions status into WAITING_TRIGGER.

        Idempotency:
         - Safe: if lines are already initialized, returns immediately.
        """
        if book is None:
            raise RuntimeError('HedgeEngine._init_lines_if_needed: book is None')

        with self.lock:
            if self.state.lines is not None:
                return

        base_price_units = int(mid_price_units(book))
        if base_price_units <= 0:
            raise RuntimeError(f'HedgeEngine._init_lines_if_needed: bad base_price_units: {base_price_units}')

        tr = self.config.trigger_offset_pct_x10000
        tg = self.config.target_offset_pct_x10000

        d_top_threshold = int(pct_x10000_mul_price(base_price_units, int(tr.long)))
        d_btm_threshold = int(pct_x10000_mul_price(base_price_units, int(tr.short)))

        d_top_target = int(pct_x10000_mul_price(base_price_units, int(tg.long)))
        d_btm_target = int(pct_x10000_mul_price(base_price_units, int(tg.short)))

        if d_top_threshold <= 0 or d_btm_threshold <= 0 or d_top_target <= 0 or d_btm_target <= 0:
            raise RuntimeError(f'HedgeEngine._init_lines_if_needed: non-positive offsets: tt={d_top_threshold} bt={d_btm_threshold} tT={d_top_target} bT={d_btm_target}')

        lines = HedgeLines(
            top_target_units=int(base_price_units + d_top_target),
            btm_target_units=int(base_price_units - d_btm_target),
            top_threshold_units=int(base_price_units + d_top_threshold),
            btm_threshold_units=int(base_price_units - d_btm_threshold),
        )

        if lines.btm_threshold_units <= 0 or lines.btm_target_units <= 0:
            raise RuntimeError('HedgeEngine._init_lines_if_needed: btm levels must be > 0')

        should_emit = False
        emit_ts = 0
        
        with self.lock:
            self.state.base_price_units = int(base_price_units)
            self.state.lines = lines
            self.state.status = HedgeStatus.WAITING_TRIGGER
            self.state.updated_ms = int(time.time() * 1000)
            self.state.mutation_counter = int(self.state.mutation_counter) + 1
            
            # First loop iteration starts once lines are initialized.
            if self._cycle_single_side and (self.config.hedge_mode == HedgeMode.LONG_ONLY or self.config.hedge_mode == HedgeMode.SHORT_ONLY):
                should_emit = True
                emit_ts = int(self.state.updated_ms)
        
        if bool(should_emit):
            self._emit_loop_iteration_started('init', int(emit_ts))

        self.logger.info(f'hedge_lines hedge_id={self.config.hedge_id} symbol={self.config.symbol} base={int(base_price_units)} top_tg={int(lines.top_target_units)} btm_tg={int(lines.btm_target_units)} top_th={int(lines.top_threshold_units)} btm_th={int(lines.btm_threshold_units)}')

    def _on_book(self, book):
        """
        Handle realtime BookTicker update.

        Responsibilities:
         - Initialize lines on first tick.
         - Update last_mid_price_units and derived unrealized PnL metric.
         - Track neutral excursion while in CLOSING(NEUTRAL).
         - Apply close_requested semantics (close wins over triggers/targets).
         - Drive state machine transitions:
            - WAITING_TRIGGER -> open on threshold touch
            - ACTIVE -> close on target or neutral threshold touch
        """
        if book is None:
            raise RuntimeError('HedgeEngine._on_book: book is None')

        if not isinstance(book, BookTicker):
            raise RuntimeError(f'HedgeEngine._on_book: book is not BookTicker: {type(book)}')

        if book.symbol != self.config.symbol:
            raise RuntimeError(f'HedgeEngine._on_book: symbol mismatch: book.symbol={book.symbol} config.symbol={self.config.symbol}')

        self._init_lines_if_needed(book)

        price_units = int(mid_price_units(book))
        now_ms = int(time.time() * 1000)

        with self.lock:
            st = self.state
            st.updated_ms = int(now_ms)
            st.metrics.last_mid_price_units = int(price_units)

            lines = st.lines
            if lines is None:
                raise RuntimeError('HedgeEngine._on_book: lines is None')

            base_bal = int(st.metrics.base_balance_units)
            quote_bal = int(st.metrics.quote_balance_units)
            st.metrics.unrealized_pnl_quote_units = int(quote_bal + (base_bal * int(price_units)))

            if st.status == HedgeStatus.CLOSING and st.closing_reason == HedgeCloseReason.NEUTRAL:
                if st.opened_leg is None:
                    raise RuntimeError('HedgeEngine._on_book: opened_leg is None in CLOSING neutral')

                th = int(st.neutral_excursion_threshold_units)
                if th <= 0:
                    raise RuntimeError('HedgeEngine._on_book: neutral_excursion_threshold_units is not set')

                if st.opened_leg == HedgeLeg.LONG:
                    if int(price_units) < int(th):
                        d = int(th) - int(price_units)
                        if d > int(st.neutral_excursion_max_units):
                            st.neutral_excursion_max_units = int(d)
                elif st.opened_leg == HedgeLeg.SHORT:
                    if int(price_units) > int(th):
                        d = int(price_units) - int(th)
                        if d > int(st.neutral_excursion_max_units):
                            st.neutral_excursion_max_units = int(d)
                else:
                    raise RuntimeError(f'HedgeEngine._on_book: bad opened_leg: {st.opened_leg}')

            if bool(st.close_requested):
                # Close should win over any trigger/target decision.
                if st.status == HedgeStatus.WAITING_TRIGGER:
                    st.status = HedgeStatus.CLOSED
                    self.logger.info(f'hedge_closed_waiting hedge_id={self.config.hedge_id} symbol={self.config.symbol} reason={st.close_reason}')
                    self._push(self._close_item, 'HedgeEngine._on_book')
                    return
                
                if st.status == HedgeStatus.ACTIVE:
                    self._close_position_unsafe(price_units=0, now_ms=int(time.time() * 1000), reason=HedgeCloseReason.FORCED)
                    return
                
                # EXECUTING/CLOSING are handled by response path or timeout.
                if st.status == HedgeStatus.EXECUTING or st.status == HedgeStatus.CLOSING:
                    return
                
                if st.status == HedgeStatus.CLOSED or st.status == HedgeStatus.FAILED:
                    self._push(self._close_item, 'HedgeEngine._on_book')
                    return
                
                raise RuntimeError(f'HedgeEngine._on_book: bad status on close_requested: {st.status}')

            # WAITING_TRIGGER: detect threshold touch and open
            if st.status == HedgeStatus.WAITING_TRIGGER:
                # Single-side endless cycle: re-arm waiting timeout without tearing down the engine.
                if self._cycle_single_side and (self.config.hedge_mode == HedgeMode.LONG_ONLY or self.config.hedge_mode == HedgeMode.SHORT_ONLY):
                    timeout_ms = int(self.config.execution_params.entrance_timeout_ms)
                    if timeout_ms > 0:
                        dt_ms = int(now_ms) - int(st.phase_started_ms)
                        if dt_ms > int(timeout_ms):
                            # Graceful stop: terminate when no exposure exists.
                            if bool(self._graceful_stop_requested):
                                st.status = HedgeStatus.CLOSED
                                st.updated_ms = int(now_ms)
                                st.mutation_counter = int(st.mutation_counter) + 1
                                self.logger.info(f'hedge_graceful_stopped_timeout hedge_id={self.config.hedge_id} symbol={self.config.symbol} dt_ms={dt_ms} timeout_ms={timeout_ms}')
                                self._push(self._close_item, 'HedgeEngine._on_book')
                                return

                            # Continue: reset phase timer and keep waiting on the same lines.
                            st.phase_started_ms = int(now_ms)
                            st.mutation_counter = int(st.mutation_counter) + 1
                            self.logger.info(f'hedge_waiting_timeout_continue hedge_id={self.config.hedge_id} symbol={self.config.symbol} dt_ms={dt_ms} timeout_ms={timeout_ms}')
                            
                            # Emit iteration start outside the lock to avoid deadlocks in user callbacks.
                            emit_reason = 'waiting_timeout'
                            emit_ts = int(now_ms)
                            should_emit = True
                        else:
                            should_emit = False
                            emit_reason = ''
                            emit_ts = 0
                    else:
                        should_emit = False
                        emit_reason = ''
                        emit_ts = 0
                else:
                    should_emit = False
                    emit_reason = ''
                    emit_ts = 0
                
                if bool(should_emit):
                    self._emit_loop_iteration_started(str(emit_reason), int(emit_ts))

                if self.config.hedge_mode == HedgeMode.BOTH or self.config.hedge_mode == HedgeMode.LONG_ONLY:
                    if price_units > int(lines.top_threshold_units):
                        self._open_leg_unsafe(HedgeLeg.LONG, int(price_units), int(now_ms))
                        return

                if self.config.hedge_mode == HedgeMode.BOTH or self.config.hedge_mode == HedgeMode.SHORT_ONLY:
                    if price_units < int(lines.btm_threshold_units):
                        self._open_leg_unsafe(HedgeLeg.SHORT, int(price_units), int(now_ms))
                        return

                return

            # ACTIVE: detect close conditions
            if st.status == HedgeStatus.ACTIVE:
                if st.opened_leg is None:
                    raise RuntimeError('HedgeEngine._on_book: opened_leg is None in ACTIVE')

                if int(st.opened_base_units) <= 0:
                    raise RuntimeError(f'HedgeEngine._on_book: bad opened_base_units: {st.opened_base_units}')

                if st.opened_leg == HedgeLeg.LONG:
                    if price_units >= int(lines.top_target_units):
                        self._close_position_unsafe(price_units, now_ms, HedgeCloseReason.TARGET)
                        return

                    if price_units <= int(lines.top_threshold_units):
                        self._close_position_unsafe(price_units, now_ms, HedgeCloseReason.NEUTRAL)
                        return

                    return

                if st.opened_leg == HedgeLeg.SHORT:
                    if price_units <= int(lines.btm_target_units):
                        self._close_position_unsafe(price_units, now_ms, HedgeCloseReason.TARGET)
                        return

                    if price_units >= int(lines.btm_threshold_units):
                        self._close_position_unsafe(price_units, now_ms, HedgeCloseReason.NEUTRAL)
                        return

                    return

                raise RuntimeError(f'HedgeEngine._on_book: bad opened_leg: {st.opened_leg}')

    def _open_leg_unsafe(self, leg, price_units, now_ms):
        """
        Start OPEN chase for a given leg (LONG/SHORT) and transition into EXECUTING.

        Behavior:
         - Builds HedgeVolumeRequest and calls on_volume callback to obtain base_units (int).
         - Converts leg into order side (LONG->BUY, SHORT->SELL).
         - Enqueues Position chase command and updates state (opening_cmd_id, current_chase metrics).

        Preconditions:
         - st.lines initialized
         - self.pos initialized
         - price_units > 0

        Notes:
         - Called only from within _on_book() under self.lock.
        """
        if leg is None:
            raise RuntimeError('HedgeEngine._open_leg_unsafe: leg is None')

        if leg != HedgeLeg.LONG and leg != HedgeLeg.SHORT:
            raise RuntimeError(f'HedgeEngine._open_leg_unsafe: bad leg: {leg}')

        if not isinstance(price_units, int):
            raise RuntimeError('HedgeEngine._open_leg_unsafe: price_units must be int')

        if int(price_units) <= 0:
            raise RuntimeError(f'HedgeEngine._open_leg_unsafe: bad price_units: {price_units}')

        if self.pos is None:
            raise RuntimeError('HedgeEngine._open_leg_unsafe: pos is None')

        st = self.state
        lines = st.lines
        if lines is None:
            raise RuntimeError('HedgeEngine._open_leg_unsafe: lines is None')
        
        base_price_units = int(st.base_price_units)
        if base_price_units <= 0:
            raise RuntimeError(f'HedgeEngine._open_leg_unsafe: bad base_price_units: {base_price_units}')

        req = HedgeVolumeRequest(
            hedge_id=self.config.hedge_id,
            symbol=self.config.symbol,
            leg=leg,
            price_units=int(price_units),
            time_ms=int(now_ms),
            base_price_units=int(base_price_units),
            lines=lines,
        )

        base_units = self.on_volume(req)
        if base_units is None:
            raise RuntimeError('HedgeEngine._open_leg_unsafe: on_volume returned None')

        if not isinstance(base_units, int):
            raise RuntimeError(f'HedgeEngine._open_leg_unsafe: on_volume returned non-int: {type(base_units)}')

        if int(base_units) <= 0:
            raise RuntimeError(f'HedgeEngine._open_leg_unsafe: on_volume returned non-positive base_units: {base_units}')

        cmd_id = str(int(now_ms))

        if leg == HedgeLeg.LONG:
            side = OrderSide.BUY
        else:
            side = OrderSide.SELL

        cmd = ChaseCommand(
            cmd=PositionCommandType.CHASE,
            symbol=self.config.symbol,
            position_side=PositionSide.BOTH,
            side=side,
            base_volume=int(base_units),
            cmd_id=str(cmd_id),
            time_ms=int(now_ms),
        )

        st.status = HedgeStatus.EXECUTING
        st.opened_leg = leg
        st.opening_cmd_id = str(cmd_id)
        st.phase_started_ms = int(now_ms)
        st.stats.chases_started = int(st.stats.chases_started) + 1
        st.updated_ms = int(now_ms)
        st.mutation_counter = int(st.mutation_counter) + 1
        
        if int(st.metrics.trigger_ms) == 0:
            st.metrics.trigger_ms = int(now_ms)

        ch = HedgeChaseMetrics(
            cmd_id=str(cmd_id),
            kind=HedgeChaseKind.OPEN,
            started_ms=int(now_ms),
            order_side=side.value,
            intended_price_units=int(price_units),
        )

        st.current_chase = ch
        st.metrics.chases.append(ch)

        self.logger.info(f'hedge_open_trigger hedge_id={self.config.hedge_id} symbol={self.config.symbol} leg={leg.value} price_units={int(price_units)} base_units={int(base_units)} cmd_id={cmd_id}')

        self.pos.add_command(cmd)

    def _close_position_unsafe(self, price_units, now_ms, reason):
        """
        Start CLOSE chase for the currently opened leg and transition into CLOSING.

        Input:
         - price_units: int intended price (0 allowed -> will use last mid price snapshot)
         - now_ms: int current time (ms)
         - reason: HedgeCloseReason (TARGET / NEUTRAL / FORCED)

        Behavior:
         - Maps opened leg into opposite order side (LONG->SELL, SHORT->BUY).
         - Enqueues Position chase command and updates close-related state fields.
         - Initializes neutral excursion tracking when reason is NEUTRAL.

        Notes:
         - Called only from within _on_book() / _on_close_requested() under self.lock.
        """
        if self.pos is None:
            raise RuntimeError('HedgeEngine._close_position_unsafe: pos is None')

        if reason is None:
            raise RuntimeError('HedgeEngine._close_position_unsafe: reason is None')

        if not isinstance(reason, HedgeCloseReason):
            raise RuntimeError(f'HedgeEngine._close_position_unsafe: reason is not HedgeCloseReason: {type(reason)}')

        st = self.state

        if st.opened_leg is None:
            raise RuntimeError('HedgeEngine._close_position_unsafe: opened_leg is None')

        base_units = int(st.opened_base_units)
        if base_units <= 0:
            raise RuntimeError(f'HedgeEngine._close_position_unsafe: bad opened_base_units: {base_units}')

        if st.opened_leg == HedgeLeg.LONG:
            side = OrderSide.SELL
        elif st.opened_leg == HedgeLeg.SHORT:
            side = OrderSide.BUY
        else:
            raise RuntimeError(f'HedgeEngine._close_position_unsafe: bad opened_leg: {st.opened_leg}')

        cmd_id = str(int(now_ms))

        cmd = ChaseCommand(
            cmd=PositionCommandType.CHASE,
            symbol=self.config.symbol,
            position_side=PositionSide.BOTH,
            side=side,
            base_volume=int(base_units),
            cmd_id=str(cmd_id),
            time_ms=int(now_ms),
        )

        st.status = HedgeStatus.CLOSING
        st.closing_cmd_id = str(cmd_id)
        st.closing_reason = reason
        st.phase_started_ms = int(now_ms)
        st.stats.chases_started = int(st.stats.chases_started) + 1
        st.updated_ms = int(now_ms)
        st.mutation_counter = int(st.mutation_counter) + 1
        
        if int(st.metrics.close_trigger_ms) == 0:
            st.metrics.close_trigger_ms = int(now_ms)

        intended_price_units = int(price_units)
        if intended_price_units <= 0:
            intended_price_units = int(st.metrics.last_mid_price_units)

        ch = HedgeChaseMetrics(
            cmd_id=str(cmd_id),
            kind=HedgeChaseKind.CLOSE,
            started_ms=int(now_ms),
            order_side=side.value,
            intended_price_units=int(intended_price_units),
        )

        st.current_chase = ch
        st.metrics.chases.append(ch)

        if st.closing_reason == HedgeCloseReason.NEUTRAL:
            if st.lines is None:
                raise RuntimeError('HedgeEngine._close_position_unsafe: lines is None')

            if st.opened_leg == HedgeLeg.LONG:
                st.neutral_excursion_threshold_units = int(st.lines.top_threshold_units)
            else:
                st.neutral_excursion_threshold_units = int(st.lines.btm_threshold_units)

            st.neutral_excursion_max_units = 0
        else:
            st.neutral_excursion_threshold_units = 0
            st.neutral_excursion_max_units = 0

        self.logger.info(f'hedge_close_trigger hedge_id={self.config.hedge_id} symbol={self.config.symbol} leg={st.opened_leg.value} reason={reason.value} price_units={int(price_units)} base_units={int(base_units)} cmd_id={cmd_id}')

        self.pos.add_command(cmd)

    def _find_chase_metrics_unsafe(self, cmd_id):
        """
        Find HedgeChaseMetrics by cmd_id in reverse chronological order.

        Input:
         - cmd_id: str

        Output:
         - HedgeChaseMetrics instance (from state.metrics.chases)

        Errors:
         - Raises when cmd_id is not found (no fallback).
        """
        if not cmd_id:
            raise RuntimeError('HedgeEngine._find_chase_metrics_unsafe: empty cmd_id')

        st = self.state
        ms = st.metrics.chases
        if ms is None:
            raise RuntimeError('HedgeEngine._find_chase_metrics_unsafe: metrics.chases is None')

        for i in range(len(ms) - 1, -1, -1):
            ch = ms[i]
            if ch is None:
                raise RuntimeError('HedgeEngine._find_chase_metrics_unsafe: ch is None')

            if ch.cmd_id == str(cmd_id):
                return ch

        raise RuntimeError(f'HedgeEngine._find_chase_metrics_unsafe: chase not found: cmd_id={cmd_id}')

    def _on_position_event(self, ev):
        """
        Handle PositionEvent emitted by Position loop.

        Responsibilities:
         - Update counters and timestamps.
         - Update chase metrics (orders_created, violations, exchange_errors, fills).
         - Update internal balances (base_balance_units / quote_balance_units / turnover) from fill events.
         - Handle Position entrance timeout event for single-side hedge modes.

        Contract:
         - All required fields must be present; missing data raises (no silent ignore).
        """
        if ev is None:
            raise RuntimeError('HedgeEngine._on_position_event: ev is None')

        if not isinstance(ev, PositionEvent):
            raise RuntimeError(f'HedgeEngine._on_position_event: ev is not PositionEvent: {type(ev)}')

        if ev.symbol != self.config.symbol:
            raise RuntimeError(f'HedgeEngine._on_position_event: symbol mismatch: ev.symbol={ev.symbol} config.symbol={self.config.symbol}')

        with self.lock:
            self.state.stats.position_events = int(self.state.stats.position_events) + 1
            self.state.updated_ms = int(time.time() * 1000)
            self.state.mutation_counter = int(self.state.mutation_counter) + 1

        if ev.event_type == PositionEventType.ENTRANCE_TIMEOUT:
            with self.lock:
                st = self.state

                if st.lines is None:
                    raise RuntimeError('HedgeEngine._on_position_event: lines is None on entrance timeout')

                if st.status != HedgeStatus.WAITING_TRIGGER:
                    raise RuntimeError(f'HedgeEngine._on_position_event: entrance timeout in bad status: {st.status}')

                if self.config.hedge_mode != HedgeMode.LONG_ONLY and self.config.hedge_mode != HedgeMode.SHORT_ONLY:
                    raise RuntimeError(f'HedgeEngine._on_position_event: entrance timeout in bad hedge_mode: {self.config.hedge_mode}')

                if st.opened_leg is not None:
                    raise RuntimeError('HedgeEngine._on_position_event: opened_leg is not None on entrance timeout')

                if int(st.opened_base_units) != 0:
                    raise RuntimeError(f'HedgeEngine._on_position_event: opened_base_units is not 0 on entrance timeout: {st.opened_base_units}')

                st.status = HedgeStatus.CLOSED
                st.updated_ms = int(time.time() * 1000)
                st.mutation_counter = int(st.mutation_counter) + 1

            self.logger.info(f'hedge_closed_entrance_timeout hedge_id={self.config.hedge_id} symbol={self.config.symbol}')

            if self.pos is None:
                raise RuntimeError('HedgeEngine._on_position_event: pos is None on entrance timeout')
            
            if bool(self.pos.started):
                self.pos.stop()

            self._push(self._close_item, 'HedgeEngine._on_position_event')
            return

        if ev.data is None:
            raise RuntimeError('HedgeEngine._on_position_event: data is None')

        data = ev.data

        if 'cmd_id' not in data:
            raise RuntimeError('HedgeEngine._on_position_event: missing cmd_id')

        cmd_id = str(data['cmd_id'])

        if ev.event_type == PositionEventType.CHASE_STARTED:
            if 'base_units' not in data:
                raise RuntimeError('HedgeEngine._on_position_event: missing base_units (CHASE_STARTED)')

            base_units = int(data['base_units'])

            with self.lock:
                ch = self._find_chase_metrics_unsafe(cmd_id)
                ch.started_ms = int(ev.time_ms)
                ch.target_base_units = int(base_units)

            return

        if ev.event_type == PositionEventType.CHASE_ORDER_CREATED:
            with self.lock:
                ch = self._find_chase_metrics_unsafe(cmd_id)
                ch.orders_created = int(ch.orders_created) + 1

            return

        if ev.event_type == PositionEventType.CHASE_PRICE_VIOLATION:
            with self.lock:
                ch = self._find_chase_metrics_unsafe(cmd_id)
                ch.gtx_violations = int(ch.gtx_violations) + 1

            return

        if ev.event_type == PositionEventType.CHASE_EXCHANGE_ERROR:
            with self.lock:
                ch = self._find_chase_metrics_unsafe(cmd_id)
                ch.exchange_errors = int(ch.exchange_errors) + 1

            return

        if ev.event_type == PositionEventType.CHASE_AGG_FILL:
            if 'base_units' not in data:
                raise RuntimeError('HedgeEngine._on_position_event: missing base_units (CHASE_AGG_FILL)')

            if 'price_units' not in data:
                raise RuntimeError('HedgeEngine._on_position_event: missing price_units (CHASE_AGG_FILL)')

            base_units = int(data['base_units'])
            price_units = int(data['price_units'])

            if base_units <= 0:
                raise RuntimeError(f'HedgeEngine._on_position_event: non-positive base_units (CHASE_AGG_FILL): {base_units}')

            if price_units <= 0:
                raise RuntimeError(f'HedgeEngine._on_position_event: non-positive price_units (CHASE_AGG_FILL): {price_units}')

            with self.lock:
                st = self.state
                ch = self._find_chase_metrics_unsafe(cmd_id)

                if ch.order_side == OrderSide.BUY.value:
                    side = 1
                elif ch.order_side == OrderSide.SELL.value:
                    side = -1
                else:
                    raise RuntimeError(f'HedgeEngine._on_position_event: bad order_side: {ch.order_side}')

                quote_units = int(base_units) * int(price_units)

                ch.filled_base_units = int(ch.filled_base_units) + int(base_units)
                ch.filled_quote_units = int(ch.filled_quote_units) + int(quote_units)
                ch.fills = int(ch.fills) + 1

                st.metrics.base_balance_units = int(st.metrics.base_balance_units) + int(side * int(base_units))
                st.metrics.quote_balance_units = int(st.metrics.quote_balance_units) + int(-side * int(quote_units))

                qd = int(-side * int(quote_units))
                if qd < 0:
                    qd = int(-qd)
                st.metrics.quote_turnover_units = int(st.metrics.quote_turnover_units) + int(qd)

            return

        if ev.event_type == PositionEventType.CHASE_FILLED_BY_DELETION:
            if 'filled_units' not in data:
                raise RuntimeError('HedgeEngine._on_position_event: missing filled_units (CHASE_FILLED_BY_DELETION)')

            if 'price_units' not in data:
                raise RuntimeError('HedgeEngine._on_position_event: missing price_units (CHASE_FILLED_BY_DELETION)')

            filled_units = int(data['filled_units'])
            price_units = int(data['price_units'])

            if filled_units <= 0:
                return

            if price_units <= 0:
                raise RuntimeError(f'HedgeEngine._on_position_event: non-positive price_units (CHASE_FILLED_BY_DELETION): {price_units}')

            with self.lock:
                st = self.state
                ch = self._find_chase_metrics_unsafe(cmd_id)

                if ch.order_side == OrderSide.BUY.value:
                    side = 1
                elif ch.order_side == OrderSide.SELL.value:
                    side = -1
                else:
                    raise RuntimeError(f'HedgeEngine._on_position_event: bad order_side: {ch.order_side}')

                quote_units = int(filled_units) * int(price_units)

                ch.filled_base_units = int(ch.filled_base_units) + int(filled_units)
                ch.filled_quote_units = int(ch.filled_quote_units) + int(quote_units)
                ch.fills = int(ch.fills) + 1

                st.metrics.base_balance_units = int(st.metrics.base_balance_units) + int(side * int(filled_units))
                st.metrics.quote_balance_units = int(st.metrics.quote_balance_units) + int(-side * int(quote_units))

                qd = int(-side * int(quote_units))
                if qd < 0:
                    qd = int(-qd)
                st.metrics.quote_turnover_units = int(st.metrics.quote_turnover_units) + int(qd)

            return

        return

    def _on_chase_response(self, resp):
        """
        Handle ChaseResponse emitted by Position when a chase finishes.

        Behavior:
         - Updates chase metrics (ok/error/finished_ms, filled/target volumes, avg price, slippage).
         - If currently EXECUTING: validates it matches opening_cmd_id and transitions to ACTIVE.
         - If currently CLOSING: validates it matches closing_cmd_id and transitions to CLOSED
           (or to WAITING_TRIGGER on neutral close in BOTH mode).
         - On any mismatch or non-ok response: raises -> hedge becomes FAILED.
        """
        if resp is None:
            raise RuntimeError('HedgeEngine._on_chase_response: resp is None')

        if not isinstance(resp, ChaseResponse):
            raise RuntimeError(f'HedgeEngine._on_chase_response: resp is not ChaseResponse: {type(resp)}')

        if resp.symbol != self.config.symbol:
            raise RuntimeError(f'HedgeEngine._on_chase_response: symbol mismatch: resp.symbol={resp.symbol} config.symbol={self.config.symbol}')

        with self.lock:
            st = self.state
            st.stats.chases_done = int(st.stats.chases_done) + 1
            st.updated_ms = int(time.time() * 1000)
            st.mutation_counter = int(st.mutation_counter) + 1
            
            ch = self._find_chase_metrics_unsafe(resp.cmd_id)
            ch.finished_ms = int(resp.finished_ms)
            ch.ok = bool(resp.ok)
            ch.error = str(resp.error) if resp.error is not None else None
            ch.target_base_units = int(resp.target_base_volume)
            ch.filled_base_units = int(resp.filled_base_volume)

            if int(ch.filled_base_units) > 0:
                ch.avg_price_units = int(int(ch.filled_quote_units) // int(ch.filled_base_units))

            intended = int(ch.intended_price_units)
            avg = int(ch.avg_price_units)

            if intended > 0 and avg > 0:
                if ch.order_side == OrderSide.BUY.value:
                    ch.slippage_pct_x10000 = int(((avg - intended) * 10000) // intended)
                elif ch.order_side == OrderSide.SELL.value:
                    ch.slippage_pct_x10000 = int(((intended - avg) * 10000) // intended)
                else:
                    raise RuntimeError(f'HedgeEngine._on_chase_response: bad order_side: {ch.order_side}')

            # Opening response
            if st.status == HedgeStatus.EXECUTING:
                if st.opening_cmd_id is None:
                    raise RuntimeError('HedgeEngine._on_chase_response: opening_cmd_id is None in EXECUTING')

                if resp.cmd_id != st.opening_cmd_id:
                    raise RuntimeError(f'HedgeEngine._on_chase_response: cmd_id mismatch in EXECUTING: got={resp.cmd_id} expected={st.opening_cmd_id}')

                if not bool(resp.ok):
                    raise RuntimeError(f'HedgeEngine: open chase failed: cmd_id={resp.cmd_id} error={resp.error}')

                filled = int(resp.filled_base_volume)
                target = int(resp.target_base_volume)
                
                if target <= 0:
                    raise RuntimeError(f'HedgeEngine: bad target_base_volume on open: {target}')
                
                if filled <= 0:
                    raise RuntimeError(f'HedgeEngine: bad filled_base_volume on open: {filled}')
                
                if filled != target:
                    raise RuntimeError(f'HedgeEngine: open filled mismatch: filled={filled} target={target}')

                st.opened_base_units = int(filled)
                st.status = HedgeStatus.ACTIVE
                
                if int(st.metrics.opened_ms) == 0:
                    st.metrics.opened_ms = int(resp.finished_ms)

                self.logger.info(f'hedge_opened hedge_id={self.config.hedge_id} symbol={self.config.symbol} leg={st.opened_leg.value if st.opened_leg is not None else None} filled_base_units={filled} cmd_id={resp.cmd_id}')
                st.mutation_counter = int(st.mutation_counter) + 1

                # If close was requested during EXECUTING, close immediately after opening
                if bool(st.close_requested):
                    self._close_position_unsafe(price_units=0, now_ms=int(time.time() * 1000), reason=HedgeCloseReason.FORCED)

                return

            # Closing response
            if st.status == HedgeStatus.CLOSING:
                if st.closing_cmd_id is None:
                    raise RuntimeError('HedgeEngine._on_chase_response: closing_cmd_id is None in CLOSING')
                
                if st.closing_reason is None:
                    raise RuntimeError('HedgeEngine._on_chase_response: closing_reason is None in CLOSING')

                if resp.cmd_id != st.closing_cmd_id:
                    raise RuntimeError(f'HedgeEngine._on_chase_response: cmd_id mismatch in CLOSING: got={resp.cmd_id} expected={st.closing_cmd_id}')

                if not bool(resp.ok):
                    raise RuntimeError(f'HedgeEngine: close chase failed: cmd_id={resp.cmd_id} error={resp.error}')

                filled = int(resp.filled_base_volume)
                if filled <= 0:
                    raise RuntimeError(f'HedgeEngine: bad filled_base_volume on close: {filled}')

                if int(st.opened_base_units) <= 0:
                    raise RuntimeError('HedgeEngine._on_chase_response: opened_base_units is not set on close')

                if filled != int(st.opened_base_units):
                    raise RuntimeError(f'HedgeEngine: close filled mismatch: filled={filled} opened={st.opened_base_units}')
                
                if st.closing_reason == HedgeCloseReason.NEUTRAL:
                    th = int(st.neutral_excursion_threshold_units)
                    if th <= 0:
                        raise RuntimeError('HedgeEngine._on_chase_response: neutral_excursion_threshold_units is not set')

                    d = int(st.neutral_excursion_max_units)
                    if d < 0:
                        d = 0

                    pct = int((int(d) * 10000) // int(th))
                    st.metrics.neutral_excursions_pct_x10000.append(int(pct))
                
                # Single-side endless cycle: continue after ANY normal close reason (NEUTRAL/TARGET),
                # keeping the same base_price_units/lines and a single hedge_id trace.
                if (
                    self._cycle_single_side
                    and (self.config.hedge_mode == HedgeMode.LONG_ONLY or self.config.hedge_mode == HedgeMode.SHORT_ONLY)
                    and not bool(st.close_requested)
                    and not bool(self._graceful_stop_requested)
                    and (st.closing_reason == HedgeCloseReason.NEUTRAL or st.closing_reason == HedgeCloseReason.TARGET)
                ):
                    base_bal = int(st.metrics.base_balance_units)
                    if base_bal != 0:
                        raise RuntimeError(f'HedgeEngine._on_chase_response: base_balance_units is not flat on cycle: {base_bal}')

                    self.logger.info(
                        f'hedge_cycle_continue hedge_id={self.config.hedge_id} symbol={self.config.symbol} '
                        f'leg={st.opened_leg.value if st.opened_leg is not None else None} '
                        f'reason={st.closing_reason.value if st.closing_reason is not None else None} '
                        f'filled_base_units={filled} cmd_id={resp.cmd_id}'
                    )

                    st.status = HedgeStatus.WAITING_TRIGGER
                    st.phase_started_ms = int(time.time() * 1000)
                    st.mutation_counter = int(st.mutation_counter) + 1
                    
                    emit_reason = f'close_{st.closing_reason.value}'
                    emit_ts = int(st.phase_started_ms)
                    should_emit = True
                    # Preserve the last close reason for snapshots/UI after we reset cycle state.
                    st.close_reason = st.closing_reason.value if st.closing_reason is not None else st.close_reason

                    st.opened_leg = None
                    st.opened_base_units = 0

                    st.opening_cmd_id = None
                    st.closing_cmd_id = None
                    st.closing_reason = None
                    st.neutral_excursion_threshold_units = 0
                    st.neutral_excursion_max_units = 0
                    st.current_chase = None

                    # Round-local timestamps should reset, balances and chase history remain cumulative.
                    st.metrics.trigger_ms = 0
                    st.metrics.opened_ms = 0
                    st.metrics.close_trigger_ms = 0
                    st.metrics.closed_ms = 0

                    # Emit iteration start outside the lock to avoid deadlocks in user callbacks.
                    if bool(should_emit):
                        if int(emit_ts) <= 0:
                            raise RuntimeError('HedgeEngine._on_chase_response: bad emit_ts on cycle')
                        self._emit_loop_iteration_started(str(emit_reason), int(emit_ts))

                    return

                # Neutral close: in BOTH mode keep running (like backtest logic), otherwise exit.
                if st.closing_reason == HedgeCloseReason.NEUTRAL and self.config.hedge_mode == HedgeMode.BOTH and not bool(st.close_requested):
                    self.logger.info(f'hedge_neutral_closed_continue hedge_id={self.config.hedge_id} symbol={self.config.symbol} leg={st.opened_leg.value if st.opened_leg is not None else None} filled_base_units={filled} cmd_id={resp.cmd_id}')
                    
                    st.status = HedgeStatus.WAITING_TRIGGER
                    st.phase_started_ms = int(time.time() * 1000)
                    st.mutation_counter = int(st.mutation_counter) + 1
                    # Preserve the last close reason for snapshots/UI after we reset round state.
                    st.close_reason = st.closing_reason.value if st.closing_reason is not None else st.close_reason
                    
                    st.opened_leg = None
                    st.opened_base_units = 0
                    
                    st.opening_cmd_id = None
                    st.closing_cmd_id = None
                    st.closing_reason = None
                    st.neutral_excursion_threshold_units = 0
                    st.neutral_excursion_max_units = 0
                    st.current_chase = None
                    
                    return

                st.status = HedgeStatus.CLOSED
                st.metrics.closed_ms = int(resp.finished_ms)
                st.mutation_counter = int(st.mutation_counter) + 1

                base_bal = int(st.metrics.base_balance_units)
                if base_bal != 0:
                    raise RuntimeError(f'HedgeEngine._on_chase_response: base_balance_units is not flat: {base_bal}')

                st.metrics.realized_pnl_quote_units = int(st.metrics.quote_balance_units)

                self.logger.info(f'hedge_closed hedge_id={self.config.hedge_id} symbol={self.config.symbol} leg={st.opened_leg.value if st.opened_leg is not None else None} reason={st.closing_reason.value if st.closing_reason is not None else None} filled_base_units={filled} cmd_id={resp.cmd_id}')

                self._push(self._close_item, 'HedgeEngine._on_chase_response')
                return

            raise RuntimeError(f'HedgeEngine._on_chase_response: unexpected status for chase response: {st.status}')

    def _on_close_requested(self):
        """
        Handle internal close request (close_requested latch).

        Behavior:
         - CLOSED/FAILED: terminates main loop via _close_item.
         - WAITING_TRIGGER: closes immediately (no exposure exists).
         - ACTIVE: triggers forced close via _close_position_unsafe().
         - EXECUTING/CLOSING: waits for chase response path to finish cleanup.
        """
        with self.lock:
            st = self.state

            if not bool(st.close_requested):
                raise RuntimeError('HedgeEngine._on_close_requested: close_requested is False')

            st.updated_ms = int(time.time() * 1000)
            st.mutation_counter = int(st.mutation_counter) + 1

            if st.status == HedgeStatus.CLOSED:
                self._push(self._close_item, 'HedgeEngine._on_close_requested')
                return

            if st.status == HedgeStatus.FAILED:
                self._push(self._close_item, 'HedgeEngine._on_close_requested')
                return

            if st.status == HedgeStatus.WAITING_TRIGGER:
                st.status = HedgeStatus.CLOSED
                self.logger.info(f'hedge_closed_waiting hedge_id={self.config.hedge_id} symbol={self.config.symbol} reason={st.close_reason}')
                self._push(self._close_item, 'HedgeEngine._on_close_requested')
                return

            # ACTIVE: close now
            if st.status == HedgeStatus.ACTIVE:
                self._close_position_unsafe(price_units=0, now_ms=int(time.time() * 1000), reason=HedgeCloseReason.FORCED)
                return

            # EXECUTING/CLOSING: will be handled by response path or timeout
            if st.status == HedgeStatus.EXECUTING or st.status == HedgeStatus.CLOSING:
                return

            raise RuntimeError(f'HedgeEngine._on_close_requested: bad status: {st.status}')
