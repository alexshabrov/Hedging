import time
from threading import Thread, Lock
from typing import List

from .exchange_models import Fill, OrderSide
from ..lib.strict_model import StrictModel

"""
Per-order fills aggregation.

Collects raw Fill items (single trades) into AggregatedFill buckets by order_id with a short tick window,
then emits AggregatedFill into the position loop. Aggregation is int-units only (base_volume, price).
"""


### Models ###
class AggregatedFill(StrictModel):
    symbol: str
    order_id: str
    time_ms: int
    arrived_ms: int
    price: int
    base_volume: int
    from_api: bool
    side: OrderSide
    is_filled: bool
    trades: List[Fill] = []

    def model_dump(self):
        """
        Convert AggregatedFill into a plain dict (JSON-ready).

        Contract:
         - Preserves integer units (price, base_volume, timestamps).
         - Serializes side as enum value (string).
         - Serializes nested trades via Fill.model_dump().
        """
        return {
            'symbol': self.symbol,
            'order_id': self.order_id,
            'time_ms': self.time_ms,
            'arrived_ms': self.arrived_ms,
            'price': self.price,
            'base_volume': self.base_volume,
            'from_api': self.from_api,
            'side': self.side.value,
            'is_filled': bool(self.is_filled),
            'trades': [t.model_dump() for t in self.trades],
        }

    @classmethod
    def from_dict(cls, data):
        """
        Build AggregatedFill from a plain dict (inverse of model_dump).

        Input:
         - data: dict with keys compatible with AggregatedFill.model_dump()

        Output:
         - AggregatedFill (StrictModel)

        Notes:
         - Side is reconstructed via OrderSide(value).
         - Nested trades are reconstructed via Fill.from_dict().
        """
        return cls(
            symbol=data['symbol'],
            order_id=data['order_id'],
            time_ms=data['time_ms'],
            arrived_ms=data['arrived_ms'],
            price=data['price'],
            base_volume=data['base_volume'],
            from_api=data['from_api'],
            side=OrderSide(data['side']),
            is_filled=bool(data['is_filled']),
            trades=[Fill.from_dict(x) for x in data.get('trades', [])],
        )


### Fills aggregator ###
class FillsAggregator:
    def __init__(self, handler, tick_ms):
        """
        Aggregate raw Fill events into AggregatedFill buckets by order_id.

        Responsibilities:
         - Collect Fill objects per order_id.
         - Flush an aggregated snapshot after a short inactivity window (tick_ms).
         - Emit AggregatedFill via the provided handler callback.

        Threading:
         - Uses a background thread that periodically calls flush().
         - add() is thread-safe (protected by lock).
         - Any background exception is latched into last_error and raised via check().
        """
        if handler is None:
            raise RuntimeError('FillsAggregator: handler is None')

        if tick_ms is None:
            raise RuntimeError('FillsAggregator: tick_ms is None')

        if tick_ms <= 0:
            raise RuntimeError(f'FillsAggregator: tick_ms must be > 0, got: {tick_ms}')

        self.handler = handler
        self.tick_ms = tick_ms

        self.lock = Lock()
        self.parts = {}

        self.thread = None
        self.should_run = False

        self.last_error = None

    def start(self):
        """
        Start background aggregation loop.

        Idempotency:
         - Forbidden: calling start() twice raises.
        """
        if self.thread is not None:
            raise RuntimeError('FillsAggregator.start: already started')

        self.should_run = True
        self.thread = Thread(target=self._thread, daemon=True)
        self.thread.start()

    def stop_and_flush(self):
        """
        Stop background loop, flush remaining buckets, then raise background error if any.

        Contract:
         - stop_and_flush() joins the thread (with timeout) and then flushes once.
         - Caller should not expect any further handler calls after return.
        """
        if self.thread is None:
            raise RuntimeError('FillsAggregator.stop_and_flush: not started')

        self.should_run = False
        self.thread.join(timeout=10)

        if self.thread.is_alive():
            raise RuntimeError('FillsAggregator.stop_and_flush: failed to stop thread')

        self.flush()

        self.check()

    def check(self):
        """
        Raise the last background error if any.

        Used by owners during shutdown and health checks.
        """
        if self.last_error is not None:
            raise self.last_error

    def add(self, fill):
        """
        Add raw Fill into aggregation state.

        Behavior:
         - Buckets fills by fill.order_id.
         - Updates last_tick_at per bucket to extend the tick window.

        Thread-safety:
         - Protected by self.lock.
        """
        if fill is None:
            raise RuntimeError('FillsAggregator.add: fill is None')

        if not isinstance(fill, Fill):
            raise RuntimeError(f'FillsAggregator.add: fill is not Fill: {type(fill)}')

        if not fill.order_id:
            raise RuntimeError('FillsAggregator.add: empty order_id')

        if not fill.trade_id:
            raise RuntimeError('FillsAggregator.add: empty trade_id')

        if not isinstance(fill.base_volume, int):
            raise RuntimeError(f'FillsAggregator.add: base_volume is not int: {type(fill.base_volume)}')

        if fill.base_volume <= 0:
            raise RuntimeError(f'FillsAggregator.add: non-positive base_volume: {fill.base_volume}')

        with self.lock:
            party = self.parts.get(fill.order_id)

            if party is None:
                party = {
                    'symbol': fill.symbol,
                    'order_id': fill.order_id,
                    'side': fill.side,
                    'trades': [],
                    'last_tick_at': time.time(),
                }
                self.parts[fill.order_id] = party

            party['trades'].append(fill)
            party['last_tick_at'] = time.time()

    def flush(self):
        """
        Flush ready buckets into handler as AggregatedFill objects.

        A bucket becomes ready when:
         - It has at least one trade, and
         - No new trades were added within the last tick_ms window.

        Notes:
         - flush() is safe to call from both background thread and owner shutdown path.
         - Any AggregatedFill emitted is removed from internal storage (one-shot).
        """
        now = time.time()
        tick_s = self.tick_ms / 1000

        ready = []

        with self.lock:
            rm = []

            for order_id, party in self.parts.items():
                if len(party['trades']) == 0:
                    rm.append(order_id)
                    continue

                if now - party['last_tick_at'] < tick_s:
                    continue

                agg = self._build_aggregate(party)
                if agg is None:
                    rm.append(order_id)
                    continue

                ready.append(agg)
                rm.append(order_id)

            for k in rm:
                if k in self.parts:
                    del self.parts[k]

        for agg in ready:
            self.handler(agg)

    def _thread(self):
        """
        Background loop: sleep tick_ms and flush().

        Any exception is stored in last_error and stops the loop.
        """
        try:
            while self.should_run:
                time.sleep(self.tick_ms / 1000)
                self.flush()
        except Exception as e:
            self.last_error = e
            self.should_run = False

    @staticmethod
    def _build_aggregate(party):
        """
        Build AggregatedFill from a single order_id party bucket.

        Input:
         - party: dict with keys: symbol, order_id, side, trades[]

        Output:
         - AggregatedFill when bucket has at least one valid trade and sum_base > 0
         - None when bucket is empty or sum_base <= 0 (drop silently by caller)

        Contract:
         - Uses last trade price as aggregate price (int units).
         - time_ms/arrived_ms are max across trades.
         - from_api/is_filled are OR across trades.
        """
        trades = party.get('trades')
        if trades is None or len(trades) == 0:
            return None

        symbol = party.get('symbol')
        order_id = party.get('order_id')
        side = party.get('side')

        if not symbol:
            raise RuntimeError('FillsAggregator._build_aggregate: empty symbol')

        if not order_id:
            raise RuntimeError('FillsAggregator._build_aggregate: empty order_id')

        if side is None:
            raise RuntimeError('FillsAggregator._build_aggregate: side is None')

        sum_base = 0
        last_px = 0

        max_time_ms = 0
        max_arrived_ms = 0

        from_api = False
        is_filled = False

        unique = []

        for t in trades:
            if t is None:
                raise RuntimeError('FillsAggregator._build_aggregate: trade is None')

            if not isinstance(t, Fill):
                raise RuntimeError(f'FillsAggregator._build_aggregate: trade is not Fill: {type(t)}')

            if not isinstance(t.base_volume, int):
                raise RuntimeError(f'FillsAggregator._build_aggregate: base_volume is not int: {type(t.base_volume)}')

            if not isinstance(t.price, int):
                raise RuntimeError(f'FillsAggregator._build_aggregate: price is not int: {type(t.price)}')

            if t.base_volume > 0:
                sum_base += int(t.base_volume)
                last_px = int(t.price)

            if t.time_ms > max_time_ms:
                max_time_ms = t.time_ms

            if t.arrived_ms > max_arrived_ms:
                max_arrived_ms = t.arrived_ms

            if t.from_api:
                from_api = True
            
            if bool(t.is_filled):
                is_filled = True

            unique.append(t)

        if len(unique) == 0:
            return None

        if int(sum_base) <= 0:
            return None

        if int(last_px) <= 0:
            raise RuntimeError('FillsAggregator._build_aggregate: last_px is non-positive')

        return AggregatedFill(
            symbol=symbol,
            order_id=order_id,
            time_ms=max_time_ms,
            arrived_ms=max_arrived_ms,
            price=last_px,
            base_volume=int(sum_base),
            from_api=from_api,
            side=side,
            is_filled=bool(is_filled),
            trades=unique,
        )
