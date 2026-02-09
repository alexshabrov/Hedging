import time
from threading import Thread, Lock, Event

from .exchange_models import Fill

"""
Fills router and deduplication.

Buffers raw Fill events by order_id until an order is attached, deduplicates by trade_id,
and flushes fills into a downstream callback. Also prunes old buffered fills by TTL.
"""


### Fills router ###
class FillsRouter:
    def __init__(self):
        """
        Router for raw exchange fills (buffer + dedup + flush).

        Responsibilities:
         - Buffer Fill items by order_id until attach_order() is called.
         - Deduplicate Fill by trade_id with TTL (bounded memory).
         - Flush buffered fills into downstream callback in FIFO order.
         - Periodically prune expired buffers and expired trade_id marks.

        Threading:
         - add()/attach_order()/try_flush_for_order()/flush_all() are thread-safe.
         - Background thread only prunes; any exception is latched into last_error.

        Notes:
         - This router does not aggregate or interpret fills; it only routes.
        """
        self.lock = Lock()
        self.stop_event = Event()

        self.by_order = {}
        self.attached = set()
        
        # trade_id -> last_seen_ms (for TTL-based dedup and bounded memory)
        self.seen_trade_ids = {}

        self.ttl_ms = 5 * 60 * 1000
        self.prune_tick_s = 60

        self.push = None

        self.thread = None
        self.should_run = False

        self.last_error = None

    def set_push(self, callback):
        """
        Set downstream callback used to push fills out of the router.

        Input:
         - callback: callable(Fill) -> None

        Contract:
         - Must be set before any flush operation.
        """
        if callback is None:
            raise RuntimeError('FillsRouter.set_push: callback is None')

        self.push = callback

    def start(self):
        """
        Start background pruning loop.

        Idempotency:
         - Forbidden: calling start() twice raises.
        """
        if self.thread is not None:
            raise RuntimeError('FillsRouter.start: already started')

        self.should_run = True
        self.stop_event.clear()
        self.thread = Thread(target=self._thread, daemon=True)
        self.thread.start()

    def stop(self):
        """
        Stop background pruning loop and validate background errors.

        Contract:
         - stop() must terminate the thread quickly (join timeout).
         - Any background exception is re-raised via check().
        """
        if self.thread is None:
            raise RuntimeError('FillsRouter.stop: not started')

        self.should_run = False
        self.stop_event.set()
        self.thread.join(timeout=10)

        if self.thread.is_alive():
            raise RuntimeError('FillsRouter.stop: failed to stop thread')

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
        Add a raw Fill to the router.

        Behavior:
         - Deduplicate by fill.trade_id (TTL-based).
         - Store by order_id until attached, then flush immediately.

        Thread-safety:
         - Protected by self.lock.
        """
        if fill is None:
            raise RuntimeError('FillsRouter.add: fill is None')

        if not isinstance(fill, Fill):
            raise RuntimeError(f'FillsRouter.add: fill is not Fill: {type(fill)}')

        if not fill.order_id:
            raise RuntimeError('FillsRouter.add: empty order_id')

        if not fill.trade_id:
            raise RuntimeError('FillsRouter.add: empty trade_id')

        with self.lock:
            self._prune_unsafe()

            now_ms = int(time.time() * 1000)
            last_seen_ms = self.seen_trade_ids.get(fill.trade_id)
            
            if last_seen_ms is not None:
                if now_ms - int(last_seen_ms) <= int(self.ttl_ms):
                    return
            
            self.seen_trade_ids[fill.trade_id] = int(now_ms)

            arr = self.by_order.get(fill.order_id)
            if arr is None:
                arr = []
                self.by_order[fill.order_id] = arr

            arr.append({
                'fill': fill,
                'added_ms': int(time.time() * 1000),
            })

            attached = fill.order_id in self.attached

        if attached:
            self.try_flush_for_order(fill.order_id)

    def attach_order(self, order_id):
        """
        Mark an order_id as attached.

        Behavior:
         - Adds order_id into attached set.
         - Triggers immediate flush of buffered fills for this order_id (if any).
        """
        if not order_id:
            raise RuntimeError('FillsRouter.attach_order: empty order_id')

        with self.lock:
            self.attached.add(order_id)

        self.try_flush_for_order(order_id)

    def try_flush_for_order(self, order_id):
        """
        Flush buffered fills for a specific order_id into downstream callback.

        Guarantees:
         - One-shot: buffered list for order_id is removed under lock.
         - FIFO: fill order is preserved per append order.

        Errors:
         - Raises if push callback is not set.
        """
        if not order_id:
            raise RuntimeError('FillsRouter.try_flush_for_order: empty order_id')

        if self.push is None:
            raise RuntimeError('FillsRouter.try_flush_for_order: push is not set')

        with self.lock:
            self._prune_unsafe()

            items = self.by_order.get(order_id)
            if items is None or len(items) == 0:
                return

            del self.by_order[order_id]

        for it in items:
            f = it.get('fill')
            if f is None:
                raise RuntimeError('FillsRouter.try_flush_for_order: stored fill is None')

            self.push(f)

    def flush_all(self):
        """
        Flush buffered fills for all order_ids.

        Used during shutdown to avoid losing already received fills.
        """
        if self.push is None:
            raise RuntimeError('FillsRouter.flush_all: push is not set')

        with self.lock:
            keys = list(self.by_order.keys())

        for oid in keys:
            self.try_flush_for_order(oid)

    def _thread(self):
        """
        Background loop: periodically prunes expired buffered fills and trade_id dedup marks.

        Any exception is stored into last_error and stops the loop.
        """
        try:
            while self.should_run:
                if self.stop_event.wait(timeout=self.prune_tick_s):
                    return
                with self.lock:
                    self._prune_unsafe()
        except Exception as e:
            self.last_error = e
            self.should_run = False

    def _prune_unsafe(self):
        """
        Prune expired buffered fills and expired trade_id dedup marks.

        Caller must hold self.lock.
        """
        now_ms = int(time.time() * 1000)

        rm_orders = []

        for order_id, arr in self.by_order.items():
            w = []

            for it in arr:
                added_ms = it.get('added_ms')
                if added_ms is None:
                    raise RuntimeError('FillsRouter._prune_unsafe: added_ms is None')

                if now_ms - int(added_ms) <= self.ttl_ms:
                    w.append(it)

            if len(w) == 0:
                rm_orders.append(order_id)
            else:
                self.by_order[order_id] = w

        for order_id in rm_orders:
            if order_id in self.by_order:
                del self.by_order[order_id]
        
        # Prune seen_trade_ids by TTL to keep bounded memory usage.
        rm_trades = []
        
        for trade_id, last_seen_ms in self.seen_trade_ids.items():
            if now_ms - int(last_seen_ms) > int(self.ttl_ms):
                rm_trades.append(trade_id)
        
        for trade_id in rm_trades:
            if trade_id in self.seen_trade_ids:
                del self.seen_trade_ids[trade_id]