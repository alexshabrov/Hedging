import time, orjson, requests
from threading import Lock, Event
from queue import Queue, Full, Empty
from typing import Dict, List, Optional

from ...lib.logger import get_logger
from ...lib.ws_client import WsClient
from ..exchange_models import Rule, BookTicker
from ..exchange_interface import RealtimeInterface

"""
Binance realtime connector for BookTicker stream.

Maintains per-subscriber channels keyed by uid, converts raw prices/volumes to strict integer units using exchange rules,
and delivers BookTicker updates through RealtimeChannel iterators.
"""


### Constants ###
HTTP_ENDPOINT = "https://fapi.binance.com"
WS_ENDPOINT = "wss://fstream.binance.com/ws"

CHAN_SIZE = 100_000


### Realtime channel ###
class RealtimeChannel:
    def __init__(self, maxsize: int):
        if maxsize is None:
            raise RuntimeError('RealtimeChannel: maxsize is None')

        if not isinstance(maxsize, int):
            raise RuntimeError(f'RealtimeChannel: maxsize is not int: {type(maxsize)}')

        if maxsize <= 0:
            raise RuntimeError(f'RealtimeChannel: maxsize must be > 0, got: {maxsize}')

        self.maxsize = maxsize

        self.lock = Lock()
        self.closed = False

        self.q = Queue(maxsize=maxsize)
        self._close_item = object()

    def push(self, item):
        if item is None:
            raise RuntimeError('RealtimeChannel.push: item is None')

        if not isinstance(item, BookTicker):
            raise RuntimeError(f'RealtimeChannel.push: item is not BookTicker: {type(item)}')

        with self.lock:
            if self.closed:
                raise RuntimeError('RealtimeChannel.push: channel is closed')

        try:
            self.q.put_nowait(item)
        except Full:
            raise RuntimeError('RealtimeChannel.push: channel is full')

    def close(self):
        with self.lock:
            if self.closed:
                return

            self.closed = True

        while True:
            try:
                self.q.put_nowait(self._close_item)
                return
            except Full:
                try:
                    _ = self.q.get_nowait()
                except Empty:
                    continue

    def __iter__(self):
        return self

    def __next__(self):
        item = self.q.get()
        if item is self._close_item:
            raise StopIteration
        return item


### Binance realtime ###
class BinanceRealtime(RealtimeInterface):
    def __init__(self):
        self.logger = get_logger('binance_realtime')

        self.lock = Lock()
        self.stop_event = Event()

        self.should_run = False
        self.last_error = None

        self.rules = None

        self.ws_client = None

        self.subs_by_symbol = {}
        self.uid_to_symbols = {}

    def start(self) -> None:
        if self.ws_client is not None:
            raise RuntimeError('BinanceRealtime.start: already started')

        self.should_run = True
        self.stop_event.clear()

        self.rules = self._get_rules()

        def on_message(message) -> None:
            try:
                self._on_message(message)
            except Exception as e:
                with self.lock:
                    self.last_error = e
                    self.should_run = False
                    self.stop_event.set()

                try:
                    self.stop()
                except Exception as stop_err:
                    self.logger.error(f'BinanceRealtime.on_message: stop failed: {stop_err}')

        self.ws_client = WsClient(WS_ENDPOINT, threaded=False, retries=True)
        self.ws_client.set_on_message(on_message)
        self.ws_client.start()

        with self.lock:
            symbols = list(self.subs_by_symbol.keys())

        for sym in symbols:
            self._send_subscribe(sym)

    def wait_for_connect(self, timeout: int = 60) -> None:
        if self.ws_client is None:
            raise RuntimeError('BinanceRealtime.wait_for_connect: ws_client is None')

        self.ws_client.wait_for_connect(timeout=timeout)

    def stop(self) -> None:
        if self.ws_client is None:
            raise RuntimeError('BinanceRealtime.stop: not started')

        self.should_run = False
        self.stop_event.set()

        ws = self.ws_client
        self.ws_client = None

        ws.stop()

        self._close_all_channels()

        self.check()

    def check(self):
        if self.last_error is not None:
            raise self.last_error

    def get_rules(self) -> Dict[str, Rule]:
        if self.rules is None:
            raise RuntimeError('BinanceRealtime.get_rules: rules is None')

        return dict(self.rules)

    def get_symbols(self) -> List[str]:
        if self.rules is None:
            raise RuntimeError('BinanceRealtime.get_symbols: rules is None')

        return list(self.rules.keys())

    def subscribe(self, uid: str, symbol: str):
        if not self.should_run:
            raise RuntimeError('BinanceRealtime.subscribe: not started')

        if not uid:
            raise RuntimeError('BinanceRealtime.subscribe: empty uid')

        if not symbol:
            raise RuntimeError('BinanceRealtime.subscribe: empty symbol')

        if self.rules is None:
            raise RuntimeError('BinanceRealtime.subscribe: rules is None')

        if symbol not in self.rules:
            raise RuntimeError(f'BinanceRealtime.subscribe: rule not found for symbol: {symbol}')

        with self.lock:
            m = self.subs_by_symbol.get(symbol)
            if m is None:
                m = {}
                self.subs_by_symbol[symbol] = m

            if uid in m:
                return m[uid]

            ch = RealtimeChannel(CHAN_SIZE)
            m[uid] = ch

            arr = self.uid_to_symbols.get(uid)
            if arr is None:
                arr = []
                self.uid_to_symbols[uid] = arr
            arr.append(symbol)

            first = len(m) == 1

        if first:
            self._send_subscribe(symbol)

        return ch

    def unsubscribe(self, uid: str) -> None:
        if not self.should_run:
            raise RuntimeError('BinanceRealtime.unsubscribe: not started')

        if not uid:
            raise RuntimeError('BinanceRealtime.unsubscribe: empty uid')

        with self.lock:
            symbols = self.uid_to_symbols.get(uid)
            if symbols is None or len(symbols) == 0:
                raise RuntimeError(f'BinanceRealtime.unsubscribe: uid not found: {uid}')

            del self.uid_to_symbols[uid]

        to_unsub = []

        for sym in symbols:
            ch = None
            empty = False

            with self.lock:
                m = self.subs_by_symbol.get(sym)
                if m is None:
                    continue

                ch = m.get(uid)
                if uid in m:
                    del m[uid]

                empty = len(m) == 0

                if empty:
                    del self.subs_by_symbol[sym]

            if ch is not None:
                ch.close()

            if empty:
                to_unsub.append(sym)

        for sym in to_unsub:
            self._send_unsubscribe(sym)

    def _close_all_channels(self):
        with self.lock:
            m = self.subs_by_symbol
            self.subs_by_symbol = {}
            self.uid_to_symbols = {}

        for _, mm in m.items():
            for _, ch in mm.items():
                if ch is None:
                    continue
                ch.close()

    def _send_subscribe(self, symbol: str):
        if not symbol:
            raise RuntimeError('BinanceRealtime._send_subscribe: empty symbol')

        if self.ws_client is None:
            raise RuntimeError('BinanceRealtime._send_subscribe: ws_client is None')

        stream = f'{symbol.lower()}@bookTicker'

        msg = {
            'method': 'SUBSCRIBE',
            'params': [stream],
            'id': int(time.time() * 1000),
        }

        raw = orjson.dumps(msg)
        self.ws_client.send(raw)

    def _send_unsubscribe(self, symbol: str):
        if not symbol:
            raise RuntimeError('BinanceRealtime._send_unsubscribe: empty symbol')

        if self.ws_client is None:
            raise RuntimeError('BinanceRealtime._send_unsubscribe: ws_client is None')

        stream = f'{symbol.lower()}@bookTicker'

        msg = {
            'method': 'UNSUBSCRIBE',
            'params': [stream],
            'id': int(time.time() * 1000),
        }

        raw = orjson.dumps(msg)
        self.ws_client.send(raw)

    def _on_message(self, message) -> None:
        if not self.should_run:
            return

        if message is None:
            raise RuntimeError('BinanceRealtime._on_message: message is None')

        payload = orjson.loads(message)

        if not isinstance(payload, dict):
            raise RuntimeError(f'BinanceRealtime._on_message: payload is not dict: {type(payload)}')

        if 'e' not in payload:
            return

        if payload['e'] != 'bookTicker':
            return

        if 's' not in payload:
            raise RuntimeError(f'BinanceRealtime._on_message: missing s in bookTicker: {payload}')

        symbol = payload['s']
        if not symbol:
            raise RuntimeError('BinanceRealtime._on_message: empty symbol in bookTicker')

        if self.rules is None:
            raise RuntimeError('BinanceRealtime._on_message: rules is None')

        if symbol not in self.rules:
            raise RuntimeError(f'BinanceRealtime._on_message: rule not found for symbol: {symbol}')

        rule = self.rules[symbol]

        if 'E' not in payload:
            raise RuntimeError(f'BinanceRealtime._on_message: missing E in bookTicker: {payload}')

        time_ms = payload['E']
        if time_ms is None:
            raise RuntimeError('BinanceRealtime._on_message: E is None')

        if not isinstance(time_ms, int):
            raise RuntimeError(f'BinanceRealtime._on_message: E is not int: {type(time_ms)}')

        bid_price_str = payload.get('b')
        bid_volume_str = payload.get('B')
        ask_price_str = payload.get('a')
        ask_volume_str = payload.get('A')

        if bid_price_str is None or bid_volume_str is None or ask_price_str is None or ask_volume_str is None:
            raise RuntimeError(f'BinanceRealtime._on_message: missing bookTicker fields: {payload}')

        if not isinstance(bid_price_str, str) or not isinstance(ask_price_str, str):
            raise RuntimeError('BinanceRealtime._on_message: bad bookTicker price types')

        if not isinstance(bid_volume_str, str) or not isinstance(ask_volume_str, str):
            raise RuntimeError('BinanceRealtime._on_message: bad bookTicker volume types')

        if bid_price_str.strip() == '' or ask_price_str.strip() == '':
            raise RuntimeError('BinanceRealtime._on_message: empty bookTicker price string')

        if bid_volume_str.strip() == '' or ask_volume_str.strip() == '':
            raise RuntimeError('BinanceRealtime._on_message: empty bookTicker volume string')

        if 'e' in bid_price_str.lower() or 'e' in ask_price_str.lower():
            raise RuntimeError('BinanceRealtime._on_message: exponent is not allowed for price')

        if 'e' in bid_volume_str.lower() or 'e' in ask_volume_str.lower():
            raise RuntimeError('BinanceRealtime._on_message: exponent is not allowed for volume')

        bid_price = rule.price_to_units(bid_price_str)
        bid_volume = rule.volume_to_units(bid_volume_str)
        ask_price = rule.price_to_units(ask_price_str)
        ask_volume = rule.volume_to_units(ask_volume_str)

        item = BookTicker(
            symbol=symbol,
            time_ms=time_ms,
            bid_price=bid_price,
            bid_volume=bid_volume,
            ask_price=ask_price,
            ask_volume=ask_volume,
        )

        with self.lock:
            m = self.subs_by_symbol.get(symbol)
            if m is None or len(m) == 0:
                return

            chans = list(m.values())

        for ch in chans:
            if ch is None:
                raise RuntimeError('BinanceRealtime._on_message: channel is None')

            ch.push(item)

    def _get_rules(self) -> Dict[str, Rule]:
        endpoint = f'{HTTP_ENDPOINT}/fapi/v1/exchangeInfo'

        try:
            response = requests.get(endpoint, timeout=30)
        except Exception as e:
            raise RuntimeError(f'BinanceRealtime._get_rules: request failed: {e}')

        if response.status_code != 200:
            raise RuntimeError(f'BinanceRealtime._get_rules: bad status: {response.status_code}')

        try:
            payload = response.json()
        except Exception as e:
            raise RuntimeError(f'BinanceRealtime._get_rules: bad json: {e}')

        if not isinstance(payload, dict):
            raise RuntimeError(f'BinanceRealtime._get_rules: payload is not dict: {type(payload)}')

        if 'symbols' not in payload:
            raise RuntimeError('BinanceRealtime._get_rules: symbols is missing')

        rules: Dict[str, Rule] = {}

        for item in payload['symbols']:
            if item is None:
                raise RuntimeError('BinanceRealtime._get_rules: item is None')

            if item.get('status') != 'TRADING':
                continue

            if item.get('contractType') != 'PERPETUAL':
                continue

            symbol = item.get('symbol')
            if not symbol:
                continue

            if '_' in symbol:
                continue

            price_step = None
            lot_step = None
            min_base_volume = None
            max_base_volume = None
            min_quote_volume = None

            filters = item.get('filters')
            if filters is None:
                raise RuntimeError(f'BinanceRealtime._get_rules: filters is None for symbol={symbol}')

            for f in filters:
                if f is None:
                    raise RuntimeError(f'BinanceRealtime._get_rules: filter is None for symbol={symbol}')

                if f.get('filterType') == 'PRICE_FILTER':
                    price_step = f.get('tickSize')

                if f.get('filterType') == 'LOT_SIZE':
                    lot_step = f.get('stepSize')
                    min_base_volume = f.get('minQty')
                    max_base_volume = f.get('maxQty')

                if f.get('filterType') == 'MIN_NOTIONAL':
                    min_quote_volume = f.get('notional')

            if price_step is None or lot_step is None or min_base_volume is None or max_base_volume is None:
                continue

            rule = Rule(
                price_step=price_step,
                lot_step=lot_step,
                min_base_volume=min_base_volume,
                max_base_volume=max_base_volume,
                min_quote_volume=min_quote_volume,
            )

            rules[symbol] = rule

        if len(rules) == 0:
            raise RuntimeError('BinanceRealtime._get_rules: empty rules')

        return rules
