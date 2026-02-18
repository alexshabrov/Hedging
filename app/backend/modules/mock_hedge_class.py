"""
Mock hedge module
Date: 2026-02-18
Version: 1.0
"""
import threading
import time
from typing import Callable, Dict

from live.lib.logger import get_logger
from live.exchanges.exchange_factory import get_realtime_class
from live.exchanges.exchange_models import Rule

from dex.contract.contract_wrapper import ContractWrapper
from dex.models.realtime_models import SwapEvent, SwapEventAbi, SwapEventAbiInput, SwapsRealtimeConfig
from dex.realtime.swaps_realtime import SwapsRealtime

from backend.models.hedger_models import HedgerConfig, MockRealtimeSource
from backend.models.mock_hedge_models import MockHedgeBoundary, MockHedgeSession, MockHedgeTriggerEvent


class MockHedge:
    SWAP_TOPIC0 = '0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67'

    def __init__(self, config: HedgerConfig, cw: ContractWrapper):
        self._config = config
        self._cw = cw
        self._logger = get_logger('mock_hedge')
        self._lock = threading.Lock()

        self._is_started = False
        self._stream_error = None

        self._last_price_by_symbol = {}
        self._sessions_by_uid = {}
        self._callbacks_by_uid = {}

        self._live_rt = None
        self._live_uid_by_symbol = {}
        self._live_threads_by_symbol = {}

        self._dex_rt = None

    def start(self) -> None:
        if self._is_started:
            raise RuntimeError('MockHedge.start: already started')
        if self._config is None:
            raise RuntimeError('MockHedge.start: config is None')
        if not isinstance(self._config, HedgerConfig):
            raise RuntimeError(f'MockHedge.start: config is not HedgerConfig: {type(self._config)}')
        if self._cw is None:
            raise RuntimeError('MockHedge.start: cw is None')
        if not isinstance(self._cw, ContractWrapper):
            raise RuntimeError(f'MockHedge.start: cw is not ContractWrapper: {type(self._cw)}')

        if self._config.mock_realtime_source == MockRealtimeSource.LIVE:
            self._start_live()
        elif self._config.mock_realtime_source == MockRealtimeSource.DEX:
            self._start_dex()
        else:
            raise RuntimeError(f'MockHedge.start: unsupported source: {self._config.mock_realtime_source}')

        # Keep an immediate baseline price before the first realtime tick.
        bootstrap_price = float(self._cw.get_current_traditional_price())
        self._on_price(symbol=str(self._config.symbol), price=float(bootstrap_price))

        self._is_started = True
        self._logger.info(f'mock_hedge_started source={self._config.mock_realtime_source.value} symbol={self._config.symbol}')

    def stop(self) -> None:
        if not self._is_started:
            return

        stop_errors = []

        try:
            if self._config.mock_realtime_source == MockRealtimeSource.LIVE:
                self._stop_live()
            elif self._config.mock_realtime_source == MockRealtimeSource.DEX:
                self._stop_dex()
            else:
                raise RuntimeError(f'MockHedge.stop: unsupported source: {self._config.mock_realtime_source}')
        except Exception as exc:
            stop_errors.append(exc)

        with self._lock:
            self._sessions_by_uid = {}
            self._callbacks_by_uid = {}
            self._is_started = False

        if len(stop_errors) > 0:
            if len(stop_errors) == 1:
                raise stop_errors[0]
            raise RuntimeError(f'MockHedge.stop: cleanup errors: {stop_errors}')

        self._logger.info('mock_hedge_stopped')

    def check(self) -> None:
        with self._lock:
            stream_error = self._stream_error
        if stream_error is not None:
            raise stream_error

    def get_last_price(self, symbol: str) -> float:
        if not isinstance(symbol, str) or len(symbol) == 0:
            raise RuntimeError('MockHedge.get_last_price: symbol is empty')

        with self._lock:
            if symbol not in self._last_price_by_symbol:
                raise RuntimeError(f'MockHedge.get_last_price: price is not available for symbol={symbol}')
            price = self._last_price_by_symbol[symbol]

        return float(price)

    def start_hedge(
        self,
        uid: str,
        symbol: str,
        target_lower_price: float,
        target_upper_price: float,
        callback: Callable[[MockHedgeTriggerEvent], None],
    ) -> None:
        if not self._is_started:
            raise RuntimeError('MockHedge.start_hedge: source is not started')
        if not isinstance(uid, str) or len(uid) == 0:
            raise RuntimeError('MockHedge.start_hedge: uid is empty')
        if not isinstance(symbol, str) or len(symbol) == 0:
            raise RuntimeError('MockHedge.start_hedge: symbol is empty')
        if not callable(callback):
            raise RuntimeError(f'MockHedge.start_hedge: callback is not callable: {type(callback)}')
        if float(target_lower_price) <= 0:
            raise RuntimeError(f'MockHedge.start_hedge: target_lower_price must be > 0: {target_lower_price}')
        if float(target_upper_price) <= 0:
            raise RuntimeError(f'MockHedge.start_hedge: target_upper_price must be > 0: {target_upper_price}')
        if float(target_lower_price) >= float(target_upper_price):
            raise RuntimeError(
                'MockHedge.start_hedge: target_lower_price must be lower than target_upper_price '
                f'got lower={target_lower_price} upper={target_upper_price}'
            )

        with self._lock:
            if uid in self._sessions_by_uid:
                raise RuntimeError(f'MockHedge.start_hedge: uid already exists: {uid}')
            if symbol not in self._last_price_by_symbol:
                raise RuntimeError(f'MockHedge.start_hedge: start price is missing for symbol={symbol}')
            start_price = float(self._last_price_by_symbol[symbol])

            session = MockHedgeSession(
                uid=str(uid),
                symbol=str(symbol),
                start_price=float(start_price),
                target_lower_price=float(target_lower_price),
                target_upper_price=float(target_upper_price),
                started_at_ms=int(time.time() * 1000),
            )
            self._sessions_by_uid[uid] = session
            self._callbacks_by_uid[uid] = callback

        self._logger.info(
            f'mock_hedge_session_started uid={uid} symbol={symbol} '
            f'start_price={start_price} target_lower={target_lower_price} target_upper={target_upper_price}'
        )

    def stop_hedge(self, uid: str) -> None:
        if not isinstance(uid, str) or len(uid) == 0:
            raise RuntimeError('MockHedge.stop_hedge: uid is empty')

        with self._lock:
            if uid not in self._sessions_by_uid:
                raise RuntimeError(f'MockHedge.stop_hedge: uid not found: {uid}')
            del self._sessions_by_uid[uid]
            if uid in self._callbacks_by_uid:
                del self._callbacks_by_uid[uid]

        self._logger.info(f'mock_hedge_session_stopped uid={uid}')

    def _set_stream_error(self, exc: Exception) -> None:
        with self._lock:
            if self._stream_error is None:
                self._stream_error = RuntimeError(f'MockHedge stream failed: {exc}')

    def _on_price(self, symbol: str, price: float) -> None:
        if not isinstance(symbol, str) or len(symbol) == 0:
            raise RuntimeError('MockHedge._on_price: symbol is empty')
        if float(price) <= 0:
            raise RuntimeError(f'MockHedge._on_price: price must be > 0: {price}')

        events = []
        callbacks = []

        with self._lock:
            self._last_price_by_symbol[symbol] = float(price)

            sessions = list(self._sessions_by_uid.values())
            for session in sessions:
                if str(session.symbol) != str(symbol):
                    continue

                boundary = None
                if float(price) <= float(session.target_lower_price):
                    boundary = MockHedgeBoundary.LOWER
                elif float(price) >= float(session.target_upper_price):
                    boundary = MockHedgeBoundary.UPPER

                if boundary is None:
                    continue

                uid = str(session.uid)
                if uid not in self._callbacks_by_uid:
                    raise RuntimeError(f'MockHedge._on_price: callback is missing for uid={uid}')
                callback = self._callbacks_by_uid[uid]

                event = MockHedgeTriggerEvent(
                    uid=str(uid),
                    symbol=str(symbol),
                    boundary=boundary,
                    start_price=float(session.start_price),
                    target_lower_price=float(session.target_lower_price),
                    target_upper_price=float(session.target_upper_price),
                    trigger_price=float(price),
                    triggered_at_ms=int(time.time() * 1000),
                )
                events.append(event)
                callbacks.append(callback)

                del self._sessions_by_uid[uid]
                del self._callbacks_by_uid[uid]

        index = 0
        while index < len(events):
            event = events[index]
            callback = callbacks[index]
            try:
                callback(event)
            except Exception as exc:
                self._set_stream_error(exc)
                raise

            self._logger.info(
                f'mock_hedge_triggered uid={event.uid} symbol={event.symbol} '
                f'boundary={event.boundary.value} trigger_price={event.trigger_price}'
            )
            index += 1

    def _start_live(self) -> None:
        RealtimeClass = get_realtime_class('Binance')
        live_rt = RealtimeClass()
        live_rt.start()
        live_rt.wait_for_connect(timeout=60)

        rules = live_rt.get_rules()
        symbol = str(self._config.symbol)
        if symbol not in rules:
            live_rt.stop()
            raise RuntimeError(f'MockHedge._start_live: symbol rule is missing: {symbol}')
        rule = rules[symbol]

        uid = f'mock_hedge_{symbol}'
        channel = live_rt.subscribe(uid=uid, symbol=symbol)
        worker = threading.Thread(
            target=self._run_live_channel,
            args=(symbol, rule, channel),
            name=f'mock_hedge_live_{symbol}',
            daemon=True,
        )
        worker.start()

        self._live_rt = live_rt
        self._live_uid_by_symbol[symbol] = str(uid)
        self._live_threads_by_symbol[symbol] = worker

    def _stop_live(self) -> None:
        if self._live_rt is None:
            raise RuntimeError('MockHedge._stop_live: live_rt is None')

        stop_errors = []
        symbols = list(self._live_uid_by_symbol.keys())
        for symbol in symbols:
            uid = self._live_uid_by_symbol[symbol]
            try:
                self._live_rt.unsubscribe(uid=uid)
            except Exception as exc:
                stop_errors.append(exc)

        try:
            self._live_rt.stop()
        except Exception as exc:
            stop_errors.append(exc)

        for symbol in symbols:
            worker = self._live_threads_by_symbol[symbol]
            worker.join(timeout=5.0)
            if worker.is_alive():
                stop_errors.append(RuntimeError(f'MockHedge._stop_live: worker is still alive for symbol={symbol}'))

        self._live_rt = None
        self._live_uid_by_symbol = {}
        self._live_threads_by_symbol = {}

        if len(stop_errors) > 0:
            if len(stop_errors) == 1:
                raise stop_errors[0]
            raise RuntimeError(f'MockHedge._stop_live: stop errors: {stop_errors}')

    def _run_live_channel(self, symbol: str, rule: Rule, channel) -> None:
        try:
            for item in channel:
                bid_price = int(item.bid_price)
                ask_price = int(item.ask_price)
                mid_price_units = int((int(bid_price) + int(ask_price)) // 2)
                if int(mid_price_units) <= 0:
                    raise RuntimeError(f'MockHedge._run_live_channel: mid_price_units must be > 0: {mid_price_units}')

                mid_price = float(rule.price_from_units(int(mid_price_units)))
                self._on_price(symbol=str(symbol), price=float(mid_price))
        except Exception as exc:
            self._set_stream_error(exc)
            self._logger.error(f'mock_hedge_live_channel_failed symbol={symbol} error={exc}')

    def _start_dex(self) -> None:
        symbol = str(self._config.symbol)
        pool_address = str(self._config.pool_address)
        ws_url = str(self._config.dex_ws_url)
        if len(ws_url) == 0:
            raise RuntimeError('MockHedge._start_dex: dex_ws_url is empty')

        config = SwapsRealtimeConfig(
            ws_url=str(ws_url),
            pool_address=str(pool_address),
            swap_topic0=str(self.SWAP_TOPIC0),
            swap_abi=self._build_swap_abi(),
        )
        dex_rt = SwapsRealtime(config=config)

        def on_swap(event: SwapEvent) -> None:
            if event is None:
                raise RuntimeError('MockHedge._start_dex.on_swap: event is None')
            if not isinstance(event, SwapEvent):
                raise RuntimeError(f'MockHedge._start_dex.on_swap: event is not SwapEvent: {type(event)}')

            price_event = self._cw.convet_to_price_event(event)
            self._on_price(symbol=str(symbol), price=float(price_event.traditional_price))

        dex_rt.set_on_swap(on_swap)
        dex_rt.start()
        self._dex_rt = dex_rt

    def _stop_dex(self) -> None:
        if self._dex_rt is None:
            raise RuntimeError('MockHedge._stop_dex: dex_rt is None')

        self._dex_rt.stop()
        self._dex_rt = None

    def _build_swap_abi(self) -> SwapEventAbi:
        return SwapEventAbi(
            name='Swap',
            type='event',
            inputs=[
                SwapEventAbiInput(name='sender', type='address', indexed=True),
                SwapEventAbiInput(name='recipient', type='address', indexed=True),
                SwapEventAbiInput(name='amount0', type='int256', indexed=False),
                SwapEventAbiInput(name='amount1', type='int256', indexed=False),
                SwapEventAbiInput(name='sqrtPriceX96', type='uint160', indexed=False),
                SwapEventAbiInput(name='liquidity', type='uint128', indexed=False),
                SwapEventAbiInput(name='tick', type='int24', indexed=False),
            ],
        )
