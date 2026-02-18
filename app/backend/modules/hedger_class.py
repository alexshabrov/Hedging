"""
Hedger module
Date: 2026-02-11
Version: 1.0
"""
import sys, time, threading
from typing import Optional

from pymongo import MongoClient  # type: ignore[import-not-found]

from live.lib.logger import get_logger
from live.exchanges.exchange_factory import get_exchange_class, get_realtime_class
from live.exchanges.exchange_models import Rule
from live.logic.hedging import HedgeEngine
from live.logic.models import (
    HedgeConfig,
    HedgeMode,
    HedgeStatus,
    HedgeExecutionParams,
    HedgeOffsetsPctX10000,
    HedgeVolumeRequest,
    HedgeLeg,
    HedgeSnapshot,
    HedgeMetrics,
    HedgeStats as LiveHedgeStats,
    HedgeChaseMetrics,
    HedgeChaseKind,
)

from dex.contract.contract_wrapper import ContractWrapper
from dex.swappers.swapper_factory import SwapperFactory
from dex.models.swapper_models import CowSwapConfig, SwapRequest, SwapperType, SwapResult
from dex.models.contract_models import MintResult, DecreaseLiquidityResult, CollectFeesResult, DexRunStats

from backend.models.hedger_models import (
    HedgerConfig,
    HedgeCalcStats,
    LiveStats,
    HedgerStats,
    HedgeRunStatus,
    CexTriggerMode,
    MockRealtimeSource,
)
from backend.modules.mock_hedge_class import MockHedge
from backend.models.mock_hedge_models import MockHedgeTriggerEvent, MockHedgeBoundary


### Hedger ###
class Hedger:
    def __init__(self, config, binance_key, binance_secret, private_key, wallet_address):
        if config is None:
            raise RuntimeError('Hedger: config is None')
        if not isinstance(config, HedgerConfig):
            raise RuntimeError(f'Hedger: config is not HedgerConfig: {type(config)}')
        if not isinstance(private_key, str) or len(private_key) == 0:
            raise RuntimeError('Hedger: private_key is empty')
        if wallet_address is not None and (not isinstance(wallet_address, str) or len(wallet_address) == 0):
            raise RuntimeError('Hedger: wallet_address is empty')

        if not bool(config.dex_only):
            if not isinstance(binance_key, str) or len(binance_key) == 0:
                raise RuntimeError('Hedger: binance_key is empty')
            if not isinstance(binance_secret, str) or len(binance_secret) == 0:
                raise RuntimeError('Hedger: binance_secret is empty')

        self.config = config
        self._binance_key = None if binance_key is None else str(binance_key)
        self._binance_secret = None if binance_secret is None else str(binance_secret)
        self._private_key = str(private_key)
        self._wallet_address = str(wallet_address) if wallet_address is not None else None
        
        self._logger = get_logger('hedger')
        
        self._exchange = None
        self._rt = None
        self._cw = None
        self._rule = None
        self._stop_requested_evt = threading.Event()
        
        self.last_stats = None
        self._is_running = False
        
        self._validate_config()
        self._init_clients()
        self._reset_run_state()
    
    def _init_clients(self) -> None:
        exchange = None
        rt = None
        cw = None
        
        try:
            cw = ContractWrapper(
                rpc_url=str(self.config.rpc_url),
                pool_address=str(self.config.pool_address),
                network=str(self.config.network),
                private_key=str(self._private_key),
                wallet_address=str(self._wallet_address) if self._wallet_address is not None else None,
            )

            self._cw = cw

            if bool(self.config.dex_only):
                self._logger.info(
                    f'hedger_clients_ready_dex_only symbol={self.config.symbol} '
                    f'pool={self.config.pool_address} network={self.config.network}'
                )
                return

            if self._binance_key is None or len(self._binance_key) == 0:
                raise RuntimeError('Hedger: binance_key is empty')
            if self._binance_secret is None or len(self._binance_secret) == 0:
                raise RuntimeError('Hedger: binance_secret is empty')

            ExchangeClass = get_exchange_class('Binance')
            RealtimeClass = get_realtime_class('Binance')

            exchange = ExchangeClass(
                key=str(self._binance_key),
                secret=str(self._binance_secret),
                hedge_mode=False,
                is_realtime=True,
            )

            rt = RealtimeClass()
            rt.start()

            exchange.wait_for_connect(timeout=60)
            rt.wait_for_connect(timeout=60)

            rules = exchange.get_rules()
            if self.config.symbol not in rules:
                raise RuntimeError(f'Hedger: rule not found for symbol: {self.config.symbol}')

            rule = rules[self.config.symbol]

            if float(rule.price_step) <= 0:
                raise RuntimeError(f'Hedger: bad price_step for symbol={self.config.symbol}: {rule.price_step}')
            if float(rule.lot_step) <= 0:
                raise RuntimeError(f'Hedger: bad lot_step for symbol={self.config.symbol}: {rule.lot_step}')

            self._exchange = exchange
            self._rt = rt
            self._rule = rule

            self._logger.info(f'hedger_clients_ready symbol={self.config.symbol} pool={self.config.pool_address} network={self.config.network}')
        
        except Exception:
            init_exc = sys.exc_info()
            
            cleanup_errors = []
            
            if rt is not None:
                try:
                    rt.stop()
                except Exception as e:
                    cleanup_errors.append(e)
            
            if exchange is not None:
                try:
                    exchange.stop()
                except Exception as e:
                    cleanup_errors.append(e)
            
            _t, exc, tb = init_exc
            
            if len(cleanup_errors) > 0:
                raise RuntimeError(f'Hedger: init failed and cleanup failed too: cleanup_errors={cleanup_errors}') from exc
            
            raise exc.with_traceback(tb)
    
    def _reset_run_state(self) -> None:
        self.last_stats = None
        self._stop_requested_evt.clear()
        
        self._run_hedge = None
        self._run_mock_hedge = None
        self._run_mock_hedge_uid = None
        self._run_mock_trigger_event = None
        self._run_mock_trigger_evt = threading.Event()
        self._run_token_id = None
        
        self._run_mint_res = None
        self._run_pos_state = None
        self._run_dec_res = None
        self._run_col_res = None
        self._run_reb_res = None
        
        self._run_last_snapshot = None
        
        self._run_init_balance0_raw = 0
        self._run_init_balance1_raw = 0
        self._run_final_balance0_raw = 0
        self._run_final_balance1_raw = 0
        self._run_mint_tx_timestamp_ms = 0
        self._run_decrease_tx_timestamp_ms = 0
        self._run_finished_at_ms = 0
        
        self._run_calc_stats = None
        self._run_status = HedgeRunStatus.INITIALIZED
        self._run_error = None
        self._run_main_exc = None

    def _is_benign_stop_error(self, e: Exception) -> bool:
        msg = str(e).lower()
        if 'not started' in msg:
            return True
        if 'already closed' in msg:
            return True
        return False

    def request_stop(self) -> None:
        self._stop_requested_evt.set()
    
    def stop(self) -> None:
        cleanup_errors = []

        # While run() is active, stop is only a cooperative stop request.
        if bool(self._is_running):
            self.request_stop()
            return
        
        if self._rt is not None:
            try:
                self._logger.info('hedger_rt_stop')
                self._rt.stop()
            except Exception as e:
                if not self._is_benign_stop_error(e):
                    cleanup_errors.append(e)
        
        if self._exchange is not None:
            try:
                self._logger.info('hedger_exchange_stop')
                self._exchange.stop()
            except Exception as e:
                if not self._is_benign_stop_error(e):
                    cleanup_errors.append(e)
        
        self._rt = None
        self._exchange = None
        if not bool(self._is_running):
            self._cw = None
            self._rule = None
        
        if len(cleanup_errors) > 0 and (not bool(self._is_running)):
            if len(cleanup_errors) == 1:
                raise cleanup_errors[0]
            raise RuntimeError(f'Hedger.stop failed: cleanup_errors={cleanup_errors}')
    
    def run(self) -> HedgerStats:
        self._reset_run_state()
        self._is_running = True

        if self._cw is None:
            raise RuntimeError('Hedger: contract wrapper is not initialized')
        if not bool(self.config.dex_only):
            if self._exchange is None:
                raise RuntimeError('Hedger: exchange is not initialized')
            if self._rt is None:
                raise RuntimeError('Hedger: realtime is not initialized')
            if self._rule is None:
                raise RuntimeError('Hedger: symbol rule is not initialized')
        
        self._logger.info(f'hedger_start symbol={self.config.symbol} pool={self.config.pool_address} network={self.config.network}')
        
        try:
            price_now = float(self._cw.get_current_traditional_price())
            if float(price_now) <= 0:
                raise RuntimeError(f'Hedger: bad current price: {price_now}')
            
            price_lower_pct = self.config.price_lower_pct
            price_upper_pct = self.config.price_upper_pct
            
            if (price_lower_pct is None) != (price_upper_pct is None):
                raise RuntimeError('Hedger: price_lower_pct and price_upper_pct must be set together')
            
            if price_lower_pct is not None and price_upper_pct is not None:
                if self.config.price_lower is not None or self.config.price_upper is not None:
                    raise RuntimeError('Hedger: price bounds and price pct cannot be set together')
                
                if float(price_lower_pct) <= 0 or float(price_upper_pct) <= 0:
                    raise RuntimeError('Hedger: price pct must be > 0')
                
                price_lower = float(price_now) * (1.0 - float(price_lower_pct) / 100.0)
                price_upper = float(price_now) * (1.0 + float(price_upper_pct) / 100.0)
                
                self._logger.info(f'hedger_prices price_now={price_now} price_lower={price_lower} price_upper={price_upper} price_lower_pct={price_lower_pct} price_upper_pct={price_upper_pct}')
            else:
                if self.config.price_lower is None or self.config.price_upper is None:
                    raise RuntimeError('Hedger: price_lower and price_upper must be set')
                
                price_lower = float(self.config.price_lower)
                price_upper = float(self.config.price_upper)
                
                self._logger.info(f'hedger_prices price_now={price_now} price_lower={price_lower} price_upper={price_upper}')
            
            if float(price_lower) <= 0 or float(price_upper) <= 0:
                raise RuntimeError('Hedger: price bounds must be > 0')
            if float(price_lower) >= float(price_upper):
                raise RuntimeError('Hedger: price_lower must be < price_upper')
            if float(price_now) <= float(price_lower) or float(price_now) >= float(price_upper):
                raise RuntimeError('Hedger: current price must be inside bounds')
            
            delta_up = float(price_upper) - float(price_now)
            delta_dn = float(price_now) - float(price_lower)
            if float(delta_up) <= 0 or float(delta_dn) <= 0:
                raise RuntimeError('Hedger: bad price deltas')
            
            avg_delta = (float(delta_up) + float(delta_dn)) / 2.0
            if float(avg_delta) <= 0:
                raise RuntimeError('Hedger: bad average delta')
            
            if abs(float(delta_up) - float(delta_dn)) > float(avg_delta) * 1e-6:
                raise RuntimeError('Hedger: bounds are not symmetric around current price')
            
            target_offset_pct_x10000 = int(round((float(delta_up) / float(price_now)) * 1_000_000.0))
            if int(target_offset_pct_x10000) <= 0:
                raise RuntimeError('Hedger: target_offset_pct_x10000 must be > 0')
            
            hedge_quote = float(self.config.total_quote) * float(self.config.cex_ratio)
            if float(hedge_quote) <= 0:
                raise RuntimeError('Hedger: hedge_quote must be > 0')
            
            self._logger.info(f'hedger_quote total_quote={self.config.total_quote} cex_ratio={self.config.cex_ratio} hedge_quote={hedge_quote}')
            
            trigger_mode = self.config.trigger_mode
            if trigger_mode == CexTriggerMode.UNITS:
                trigger_units = int(self.config.trigger_units)
                if int(trigger_units) <= 0:
                    raise RuntimeError('Hedger: trigger_units must be > 0 for units mode')

                if bool(self.config.dex_only):
                    price_step = float(price_now) * 1e-4
                else:
                    if self._rule is None:
                        raise RuntimeError('Hedger: symbol rule is not initialized for units mode')
                    price_step = float(self._rule.price_step)
                if float(price_step) <= 0:
                    raise RuntimeError(f'Hedger: bad price_step for units mode: {price_step}')

                trigger_delta = float(price_step) * float(trigger_units)
                if float(trigger_delta) <= 0:
                    raise RuntimeError('Hedger: trigger_delta must be > 0 for units mode')

                trigger_offset_pct_x10000 = int(round((float(trigger_delta) / float(price_now)) * 1_000_000.0))
                if int(trigger_offset_pct_x10000) <= 0:
                    raise RuntimeError('Hedger: units mode produced non-positive trigger_offset_pct_x10000')
            elif trigger_mode == CexTriggerMode.PCT:
                trigger_offset_pct_x10000 = int(round(float(self.config.trigger_pct) * 10_000.0))
                if int(trigger_offset_pct_x10000) <= 0:
                    raise RuntimeError('Hedger: pct mode produced non-positive trigger_offset_pct_x10000')
            else:
                raise RuntimeError(f'Hedger: unsupported trigger_mode: {trigger_mode}')
            
            if int(trigger_offset_pct_x10000) >= int(target_offset_pct_x10000):
                raise RuntimeError(f'Hedger: trigger_offset_pct_x10000 must be < target_offset_pct_x10000, got trigger={trigger_offset_pct_x10000} target={target_offset_pct_x10000}')
            
            self._logger.info(f'hedger_trigger trigger_mode={trigger_mode.value} trigger_pct={self.config.trigger_pct} trigger_units={self.config.trigger_units} trigger_offset_pct_x10000={trigger_offset_pct_x10000} target_offset_pct_x10000={target_offset_pct_x10000}')
            
            self._run_calc_stats = HedgeCalcStats(
                base_price=float(price_now),
                price_lower=float(price_lower),
                price_upper=float(price_upper),
                total_quote=float(self.config.total_quote),
                cex_ratio=float(self.config.cex_ratio),
                trigger_mode=trigger_mode,
                trigger_offset_pct_x10000=int(trigger_offset_pct_x10000),
                target_offset_pct_x10000=int(target_offset_pct_x10000),
                hedge_quote=float(hedge_quote),
            )
            
            if bool(self.config.dex_only):
                self._run_mock_hedge = MockHedge(config=self.config, cw=self._cw)
                self._run_mock_hedge.start()

                wait_started_at_ms = int(time.time() * 1000)
                while True:
                    try:
                        _ = self._run_mock_hedge.get_last_price(str(self.config.symbol))
                        break
                    except Exception:
                        self._run_mock_hedge.check()

                    waited_ms = int(time.time() * 1000) - int(wait_started_at_ms)
                    if int(waited_ms) > int(self.config.entrance_timeout_ms):
                        raise RuntimeError(
                            'Hedger: mock realtime price is not ready in time '
                            f'waited_ms={waited_ms} timeout_ms={self.config.entrance_timeout_ms}'
                        )
                    time.sleep(0.05)

                self._run_mock_hedge_uid = str(int(time.time() * 1000))

                def on_mock_trigger(event: MockHedgeTriggerEvent) -> None:
                    if event is None:
                        raise RuntimeError('Hedger.on_mock_trigger: event is None')
                    if not isinstance(event, MockHedgeTriggerEvent):
                        raise RuntimeError(f'Hedger.on_mock_trigger: event is not MockHedgeTriggerEvent: {type(event)}')
                    self._run_mock_trigger_event = event
                    self._run_mock_trigger_evt.set()

                self._run_mock_hedge.start_hedge(
                    uid=str(self._run_mock_hedge_uid),
                    symbol=str(self.config.symbol),
                    target_lower_price=float(price_lower),
                    target_upper_price=float(price_upper),
                    callback=on_mock_trigger,
                )
                self._logger.info(
                    f'hedger_mock_hedge_started uid={self._run_mock_hedge_uid} '
                    f'source={self.config.mock_realtime_source.value}'
                )
            else:
                def on_volume(req: HedgeVolumeRequest) -> int:
                    if req is None:
                        raise RuntimeError('on_volume: req is None')
                    if not isinstance(req, HedgeVolumeRequest):
                        raise RuntimeError(f'on_volume: req is not HedgeVolumeRequest: {type(req)}')
                    if req.symbol != self.config.symbol:
                        raise RuntimeError(f'on_volume: symbol mismatch: req.symbol={req.symbol} config.symbol={self.config.symbol}')
                    if req.leg != HedgeLeg.LONG and req.leg != HedgeLeg.SHORT:
                        raise RuntimeError(f'on_volume: bad leg: {req.leg}')
                    
                    price_units = int(req.price_units)
                    if int(price_units) <= 0:
                        raise RuntimeError(f'on_volume: bad price_units: {price_units}')

                    if self._rule is None:
                        raise RuntimeError('on_volume: symbol rule is not initialized')
                    
                    price_step = float(self._rule.price_step)
                    lot_step = float(self._rule.lot_step)
                    
                    if float(price_step) <= 0:
                        raise RuntimeError(f'on_volume: bad price_step: {self._rule.price_step}')
                    if float(lot_step) <= 0:
                        raise RuntimeError(f'on_volume: bad lot_step: {self._rule.lot_step}')
                    
                    price_float = float(price_units) * float(price_step)
                    if float(price_float) <= 0:
                        raise RuntimeError(f'on_volume: bad price_float: {price_float}')
                    
                    base_volume = float(hedge_quote) / float(price_float)
                    base_units = int(base_volume / float(lot_step))
                    
                    if int(base_units) <= 0:
                        raise RuntimeError(f'on_volume: bad base_units: {base_units} from hedge_quote={hedge_quote} price_float={price_float}')
                    
                    return int(base_units)
                
                cfg = HedgeConfig(
                    hedge_id=str(int(time.time() * 1000)),
                    symbol=str(self.config.symbol),
                    hedge_mode=HedgeMode.BOTH,
                    trigger_offset_pct_x10000=HedgeOffsetsPctX10000(
                        long=int(trigger_offset_pct_x10000),
                        short=int(trigger_offset_pct_x10000),
                    ),
                    target_offset_pct_x10000=HedgeOffsetsPctX10000(
                        long=int(target_offset_pct_x10000),
                        short=int(target_offset_pct_x10000),
                    ),
                    execution_params=HedgeExecutionParams(
                        tick_ms=int(self.config.tick_ms),
                        gtx_cooldown_ms=int(self.config.gtx_cooldown_ms),
                        entrance_timeout_ms=int(self.config.entrance_timeout_ms),
                    ),
                )
                
                self._run_hedge = HedgeEngine(config=cfg, exchange=self._exchange, realtime=self._rt, on_volume=on_volume)
                self._run_hedge.start()
                
                self._logger.info(f'hedger_hedge_started hedge_id={cfg.hedge_id} symbol={self.config.symbol}')
            
            token0_address = self._cw.get_token0_address()
            token1_address = self._cw.get_token1_address()
            
            self._run_init_balance0_raw = int(self._cw.get_balance(str(token0_address)))
            self._run_init_balance1_raw = int(self._cw.get_balance(str(token1_address)))
            
            self._logger.info(f'hedger_init_balances token0={token0_address} token1={token1_address} balance0_raw={self._run_init_balance0_raw} balance1_raw={self._run_init_balance1_raw}')
            
            self._run_status = HedgeRunStatus.RUNNING
            
            self._run_mint_res = self._cw.add_liquidity_traditional(
                fee_pct=float(self.config.fee_pct),
                total_quote=float(self.config.total_quote),
                price_lower=float(price_lower),
                price_upper=float(price_upper),
            )
            
            if self._run_mint_res is None:
                raise RuntimeError('Hedger: mint_res is None')
            if not isinstance(self._run_mint_res, MintResult):
                raise RuntimeError(f'Hedger: mint_res is not MintResult: {type(self._run_mint_res)}')
            if not bool(self._run_mint_res.ok):
                raise RuntimeError('Hedger: add_liquidity_traditional failed')
            if self._run_mint_res.token_id is None or int(self._run_mint_res.token_id) <= 0:
                raise RuntimeError('Hedger: mint token_id is missing')
            
            self._run_token_id = int(self._run_mint_res.token_id)
            self._run_mint_tx_timestamp_ms = int(self._cw.get_tx_timestamp_ms(str(self._run_mint_res.tx_hash)))
            if int(self._run_mint_tx_timestamp_ms) <= 0:
                raise RuntimeError(f'Hedger: bad mint_tx_timestamp_ms: {self._run_mint_tx_timestamp_ms}')
            
            self._logger.info(f'hedger_minted token_id={self._run_token_id} amount_base={self._run_mint_res.amount_base} amount_quote={self._run_mint_res.amount_quote} tx={self._run_mint_res.tx_hash}')
            
            self._run_pos_state = self._cw.get_position_state(int(self._run_token_id))
            
            self._logger.info(f'hedger_position token_id={self._run_token_id} price_current={self._run_pos_state.price_current} price_lower={self._run_pos_state.price_lower} price_upper={self._run_pos_state.price_upper}')
            
            if bool(self.config.dex_only):
                while True:
                    if bool(self._stop_requested_evt.is_set()):
                        self._logger.info('hedger_stop_requested')
                        self._run_status = HedgeRunStatus.FINISHED
                        self._run_last_snapshot = self._build_mock_snapshot(
                            price=float(self._cw.get_current_traditional_price()),
                            close_reason='manual_stop',
                            status=HedgeStatus.CLOSED,
                        )
                        break

                    if self._run_mock_hedge is None:
                        raise RuntimeError('Hedger: mock hedge is not initialized')
                    self._run_mock_hedge.check()

                    if bool(self._run_mock_trigger_evt.is_set()):
                        if self._run_mock_trigger_event is None:
                            raise RuntimeError('Hedger: mock trigger event flag is set but event is missing')
                        boundary = self._run_mock_trigger_event.boundary
                        if boundary == MockHedgeBoundary.LOWER:
                            close_reason = 'mock_lower'
                        elif boundary == MockHedgeBoundary.UPPER:
                            close_reason = 'mock_upper'
                        else:
                            raise RuntimeError(f'Hedger: unsupported mock boundary: {boundary}')

                        self._run_status = HedgeRunStatus.FINISHED
                        self._run_last_snapshot = self._build_mock_snapshot(
                            price=float(self._run_mock_trigger_event.trigger_price),
                            close_reason=close_reason,
                            status=HedgeStatus.CLOSED,
                        )
                        self._logger.info(
                            f'hedger_mock_closed uid={self._run_mock_trigger_event.uid} '
                            f'boundary={self._run_mock_trigger_event.boundary.value} '
                            f'price={self._run_mock_trigger_event.trigger_price}'
                        )
                        break

                    time.sleep(0.05)
            else:
                last_mutation_counter = -1
                
                while True:
                    if bool(self._stop_requested_evt.is_set()):
                        self._logger.info('hedger_stop_requested')
                        if self._run_hedge is not None and bool(self._run_hedge.started):
                            self._run_hedge.stop()
                        self._run_status = HedgeRunStatus.FINISHED
                        break

                    self._run_hedge.check()
                    snap = self._run_hedge.status()
                    
                    mc = int(snap.mutation_counter)
                    if int(mc) != int(last_mutation_counter):
                        last_mutation_counter = int(mc)
                        self._run_last_snapshot = snap
                        self._logger.info(f'hedger_snapshot_update mutation_counter={mc} status={snap.status.value}')
                    
                    if snap.status == HedgeStatus.CLOSED:
                        self._run_status = HedgeRunStatus.FINISHED
                        self._logger.info(f'hedger_closed hedge_id={cfg.hedge_id} symbol={self.config.symbol}')
                        break
                    
                    if snap.status == HedgeStatus.FAILED:
                        self._run_status = HedgeRunStatus.FAILED
                        self._logger.info(f'hedger_failed hedge_id={cfg.hedge_id} symbol={self.config.symbol}')
                        break
                    
                    time.sleep(0.05)
        
        except Exception:
            self._run_status = HedgeRunStatus.FAILED
            self._run_main_exc = sys.exc_info()
        
        finally:
            cleanup_errors = []
            cleanup_lock = threading.Lock()
            
            def _add_cleanup_error(e):
                with cleanup_lock:
                    cleanup_errors.append(e)
            
            def _cleanup_dex():
                if self._run_token_id is None:
                    return
                
                try:
                    self._logger.info(f'hedger_decrease_start token_id={self._run_token_id}')
                    self._run_dec_res = self._cw.decrease_liquidity(int(self._run_token_id), liquidity_percent=100)
                    self._run_decrease_tx_timestamp_ms = int(self._cw.get_tx_timestamp_ms(str(self._run_dec_res.tx_hash)))
                    if int(self._run_decrease_tx_timestamp_ms) <= 0:
                        raise RuntimeError(f'Hedger: bad decrease_tx_timestamp_ms: {self._run_decrease_tx_timestamp_ms}')
                    self._logger.info(f'hedger_decrease_done token_id={self._run_token_id} ok={self._run_dec_res.ok} amount_base={self._run_dec_res.amount_base} amount_quote={self._run_dec_res.amount_quote}')
                except Exception as e:
                    _add_cleanup_error(e)
                
                try:
                    self._logger.info(f'hedger_collect_start token_id={self._run_token_id}')
                    self._run_col_res = self._cw.collect_fees(int(self._run_token_id))
                    self._logger.info(f'hedger_collect_done token_id={self._run_token_id} ok={self._run_col_res.ok} amount_base={self._run_col_res.amount_base} amount_quote={self._run_col_res.amount_quote}')
                except Exception as e:
                    _add_cleanup_error(e)
                
                try:
                    self._logger.info('hedger_rebalance_start')
                    self._run_reb_res = self._rebalance(
                        cw=self._cw,
                        init_balance0_raw=int(self._run_init_balance0_raw),
                        init_balance1_raw=int(self._run_init_balance1_raw),
                    )
                    if self._run_reb_res is None:
                        self._logger.info('hedger_rebalance_skipped')
                    else:
                        self._logger.info(f'hedger_rebalance_done ok={self._run_reb_res.ok} status={self._run_reb_res.order.status.value if self._run_reb_res.order is not None else None}')
                except Exception as e:
                    _add_cleanup_error(e)
                
                try:
                    token0_address = self._cw.get_token0_address()
                    token1_address = self._cw.get_token1_address()
                    self._run_final_balance0_raw = int(self._cw.get_balance(str(token0_address)))
                    self._run_final_balance1_raw = int(self._cw.get_balance(str(token1_address)))
                    self._logger.info(f'hedger_final_balances token0={token0_address} token1={token1_address} balance0_raw={self._run_final_balance0_raw} balance1_raw={self._run_final_balance1_raw}')
                except Exception as e:
                    _add_cleanup_error(e)
            
            def _cleanup_live():
                try:
                    if self._run_hedge is not None and bool(self._run_hedge.started):
                        self._logger.info('hedger_hedge_stop')
                        self._run_hedge.stop()
                    if self._run_mock_hedge is not None:
                        self._logger.info('hedger_mock_hedge_stop')
                        self._run_mock_hedge.stop()
                except Exception as e:
                    if not self._is_benign_stop_error(e):
                        _add_cleanup_error(e)
            
            dex_cleanup_thread = threading.Thread(target=_cleanup_dex, name='hedger_dex_cleanup')
            live_cleanup_thread = threading.Thread(target=_cleanup_live, name='hedger_live_cleanup')
            
            dex_cleanup_thread.start()
            live_cleanup_thread.start()
            
            dex_cleanup_thread.join()
            live_cleanup_thread.join()
            
            if len(cleanup_errors) > 0:
                self._logger.error(f'hedger_cleanup_errors errors={cleanup_errors}')

            self._run_finished_at_ms = int(time.time() * 1000)
            
            if self._run_calc_stats is not None:
                uniswap_stats = DexRunStats(
                    token_id=int(self._run_token_id) if self._run_token_id is not None else None,
                    mint=self._run_mint_res,
                    mint_tx_timestamp_ms=int(self._run_mint_tx_timestamp_ms),
                    position=self._run_pos_state,
                    decrease=self._run_dec_res,
                    decrease_tx_timestamp_ms=int(self._run_decrease_tx_timestamp_ms),
                    collect=self._run_col_res,
                    rebalance=self._run_reb_res,
                    initial_balance0_raw=int(self._run_init_balance0_raw),
                    initial_balance1_raw=int(self._run_init_balance1_raw),
                    final_balance0_raw=int(self._run_final_balance0_raw),
                    final_balance1_raw=int(self._run_final_balance1_raw),
                )

                if bool(self.config.dex_only) and self._run_last_snapshot is None:
                    if self._cw is None:
                        raise RuntimeError('Hedger: contract wrapper is not initialized for mock snapshot fallback')

                    fallback_status = HedgeStatus.CLOSED
                    fallback_reason = 'mock_closed'
                    if self._run_status == HedgeRunStatus.FAILED:
                        fallback_status = HedgeStatus.FAILED
                        fallback_reason = 'mock_failed'

                    self._run_last_snapshot = self._build_mock_snapshot(
                        price=float(self._cw.get_current_traditional_price()),
                        close_reason=fallback_reason,
                        status=fallback_status,
                    )
                
                live_stats = LiveStats(
                    last_snapshot=self._run_last_snapshot,
                )
                
                if self._run_main_exc is not None:
                    _t, exc, _tb = self._run_main_exc
                    self._run_error = str(exc)
                
                stats = HedgerStats(
                    status=self._run_status,
                    calc=self._run_calc_stats,
                    uniswap=uniswap_stats,
                    live=live_stats,
                    finished_at_ms=int(self._run_finished_at_ms),
                    error=self._run_error,
                )
                
                self.last_stats = stats
            
            if self.last_stats is not None:
                try:
                    self._write_stats(self.last_stats)
                except Exception as e:
                    cleanup_errors.append(e)

            self._is_running = False
            
            if self._run_main_exc is not None and len(cleanup_errors) > 0:
                _t, exc, _tb = self._run_main_exc
                raise RuntimeError(f'Hedger failed and cleanup failed too: cleanup_errors={cleanup_errors}') from exc
            
            if self._run_main_exc is not None:
                _t, exc, tb = self._run_main_exc
                raise exc.with_traceback(tb)
            
            if len(cleanup_errors) > 0:
                if len(cleanup_errors) == 1:
                    raise cleanup_errors[0]
                raise RuntimeError(f'Hedger cleanup failed: cleanup_errors={cleanup_errors}')
        
        if self.last_stats is None:
            raise RuntimeError('Hedger: last_stats is None after run')
        
        return self.last_stats

    def _build_mock_snapshot(self, price: float, close_reason: str, status: HedgeStatus) -> HedgeSnapshot:
        if float(price) <= 0:
            raise RuntimeError(f'Hedger._build_mock_snapshot: price must be > 0: {price}')
        if not isinstance(close_reason, str) or len(close_reason) == 0:
            raise RuntimeError('Hedger._build_mock_snapshot: close_reason is empty')
        if status != HedgeStatus.CLOSED and status != HedgeStatus.FAILED:
            raise RuntimeError(f'Hedger._build_mock_snapshot: unsupported status: {status}')

        step = '0.00000001'
        rule = Rule(
            price_step=str(step),
            lot_step='0.0001',
            min_base_volume='0.0001',
            max_base_volume='1000000000',
            min_quote_volume='0.0001',
            max_quote_volume='1000000000',
        )
        last_mid_price_units = int(round(float(price) / float(step)))
        if int(last_mid_price_units) <= 0:
            raise RuntimeError(f'Hedger._build_mock_snapshot: bad last_mid_price_units: {last_mid_price_units}')

        now_ms = int(time.time() * 1000)
        started_ms = int(self._run_mint_tx_timestamp_ms)
        if int(started_ms) <= 0:
            started_ms = int(now_ms)

        chase = HedgeChaseMetrics(
            cmd_id='mock_open',
            kind=HedgeChaseKind.OPEN,
            started_ms=int(started_ms),
            finished_ms=int(now_ms),
            order_side='BUY',
            intended_price_units=int(last_mid_price_units),
            target_base_units=1,
            filled_base_units=1,
            filled_quote_units=1,
            orders_created=1,
            fills=1,
            gtx_violations=0,
            exchange_errors=0,
            ok=True,
            error=None,
            avg_price_units=int(last_mid_price_units),
            slippage_pct_x10000=0,
        )

        metrics = HedgeMetrics(
            last_mid_price_units=int(last_mid_price_units),
            base_balance_units=1,
            quote_balance_units=1,
            quote_turnover_units=1,
            realized_pnl_quote_units=0,
            unrealized_pnl_quote_units=0,
            trigger_ms=int(started_ms),
            opened_ms=int(started_ms),
            close_trigger_ms=int(now_ms),
            closed_ms=int(now_ms),
            chases=[chase],
            neutral_excursions_pct_x10000=[],
        )

        return HedgeSnapshot(
            hedge_id=f'mock_{int(now_ms)}',
            symbol=str(self.config.symbol),
            status=status,
            started_ms=int(started_ms),
            updated_ms=int(now_ms),
            mutation_counter=1,
            base_price_units=int(last_mid_price_units),
            lines=None,
            symbol_rule=rule,
            opened_leg=HedgeLeg.LONG,
            opened_base_units=1,
            close_reason=str(close_reason),
            last_error=None,
            stats=LiveHedgeStats(
                chases_started=1,
                chases_done=1,
                position_events=0,
            ),
            metrics=metrics,
        )
    
    def _validate_config(self) -> None:
        cfg = self.config
        
        if not isinstance(cfg.symbol, str) or len(cfg.symbol) == 0:
            raise RuntimeError('HedgerConfig.symbol is empty')
        if not isinstance(cfg.rpc_url, str) or len(cfg.rpc_url) == 0:
            raise RuntimeError('HedgerConfig.rpc_url is empty')
        if not isinstance(cfg.network, str) or len(cfg.network) == 0:
            raise RuntimeError('HedgerConfig.network is empty')
        if not isinstance(cfg.pool_address, str) or len(cfg.pool_address) == 0:
            raise RuntimeError('HedgerConfig.pool_address is empty')
        if float(cfg.fee_pct) <= 0:
            raise RuntimeError('HedgerConfig.fee_pct must be > 0')
        if (cfg.price_lower_pct is None) != (cfg.price_upper_pct is None):
            raise RuntimeError('HedgerConfig.price_lower_pct and price_upper_pct must be set together')
        
        if cfg.price_lower_pct is not None and cfg.price_upper_pct is not None:
            if cfg.price_lower is not None or cfg.price_upper is not None:
                raise RuntimeError('HedgerConfig.price bounds and price pct cannot be set together')
            if float(cfg.price_lower_pct) <= 0 or float(cfg.price_upper_pct) <= 0:
                raise RuntimeError('HedgerConfig.price pct must be > 0')
        else:
            if cfg.price_lower is None or cfg.price_upper is None:
                raise RuntimeError('HedgerConfig.price_lower and price_upper must be set')
            if float(cfg.price_lower) <= 0 or float(cfg.price_upper) <= 0:
                raise RuntimeError('HedgerConfig.price bounds must be > 0')
            if float(cfg.price_lower) >= float(cfg.price_upper):
                raise RuntimeError('HedgerConfig.price_lower must be < price_upper')
        if float(cfg.total_quote) <= 0:
            raise RuntimeError('HedgerConfig.total_quote must be > 0')
        if float(cfg.cex_ratio) <= 0:
            raise RuntimeError('HedgerConfig.cex_ratio must be > 0')
        if not isinstance(cfg.trigger_mode, CexTriggerMode):
            raise RuntimeError(f'HedgerConfig.trigger_mode is not CexTriggerMode: {type(cfg.trigger_mode)}')
        if cfg.trigger_mode == CexTriggerMode.PCT:
            if float(cfg.trigger_pct) <= 0:
                raise RuntimeError('HedgerConfig.trigger_pct must be > 0 for pct mode')
            if int(cfg.trigger_units) != 0:
                raise RuntimeError('HedgerConfig.trigger_units must be 0 for pct mode')
        elif cfg.trigger_mode == CexTriggerMode.UNITS:
            if int(cfg.trigger_units) <= 0:
                raise RuntimeError('HedgerConfig.trigger_units must be > 0 for units mode')
            if float(cfg.trigger_pct) != 0.0:
                raise RuntimeError('HedgerConfig.trigger_pct must be 0 for units mode')
        else:
            raise RuntimeError(f'HedgerConfig.trigger_mode is unsupported: {cfg.trigger_mode}')
        if not isinstance(cfg.mongo_uri, str) or len(cfg.mongo_uri) == 0:
            raise RuntimeError('HedgerConfig.mongo_uri is empty')
        if not isinstance(cfg.mongo_db, str) or len(cfg.mongo_db) == 0:
            raise RuntimeError('HedgerConfig.mongo_db is empty')
        if not isinstance(cfg.mongo_collection, str) or len(cfg.mongo_collection) == 0:
            raise RuntimeError('HedgerConfig.mongo_collection is empty')
        if int(cfg.tick_ms) <= 0:
            raise RuntimeError('HedgerConfig.tick_ms must be > 0')
        if int(cfg.gtx_cooldown_ms) <= 0:
            raise RuntimeError('HedgerConfig.gtx_cooldown_ms must be > 0')
        if int(cfg.entrance_timeout_ms) <= 0:
            raise RuntimeError('HedgerConfig.entrance_timeout_ms must be > 0')
        if int(cfg.cowswap_api_timeout_sec) <= 0:
            raise RuntimeError('HedgerConfig.cowswap_api_timeout_sec must be > 0')
        if int(cfg.cowswap_wait_timeout_sec) <= 0:
            raise RuntimeError('HedgerConfig.cowswap_wait_timeout_sec must be > 0')
        if int(cfg.cowswap_poll_interval_sec) <= 0:
            raise RuntimeError('HedgerConfig.cowswap_poll_interval_sec must be > 0')
        if not isinstance(cfg.dex_only, bool):
            raise RuntimeError(f'HedgerConfig.dex_only is not bool: {type(cfg.dex_only)}')
        if not isinstance(cfg.mock_realtime_source, MockRealtimeSource):
            raise RuntimeError(f'HedgerConfig.mock_realtime_source is invalid: {type(cfg.mock_realtime_source)}')
        if not isinstance(cfg.dex_ws_url, str):
            raise RuntimeError(f'HedgerConfig.dex_ws_url is not str: {type(cfg.dex_ws_url)}')
        if not bool(cfg.dex_only) and cfg.mock_realtime_source != MockRealtimeSource.LIVE:
            raise RuntimeError('HedgerConfig.mock_realtime_source must be live when dex_only=false')
        if cfg.mock_realtime_source.value == 'dex':
            if len(cfg.dex_ws_url) == 0:
                raise RuntimeError('HedgerConfig.dex_ws_url is empty for dex mock source')
            if not cfg.dex_ws_url.startswith('wss://'):
                raise RuntimeError('HedgerConfig.dex_ws_url must start with wss:// for dex mock source')
    
    def _rebalance(self, cw, init_balance0_raw, init_balance1_raw) -> Optional[SwapResult]:
        if cw is None:
            raise RuntimeError('Hedger._rebalance: cw is None')
        if not isinstance(cw, ContractWrapper):
            raise RuntimeError(f'Hedger._rebalance: cw is not ContractWrapper: {type(cw)}')
        if int(init_balance0_raw) <= 0 and int(init_balance1_raw) <= 0:
            raise RuntimeError('Hedger._rebalance: initial balances are empty')
        
        token0_address = cw.get_token0_address()
        token1_address = cw.get_token1_address()
        quote_address = cw.get_quote_token_address()
        
        token0_decimals = cw.get_token0_decimals()
        token1_decimals = cw.get_token1_decimals()
        
        balance0_raw = cw.get_balance(str(token0_address))
        balance1_raw = cw.get_balance(str(token1_address))
        
        if int(balance0_raw) <= 0 and int(balance1_raw) <= 0:
            raise RuntimeError('Hedger._rebalance: balances are empty')
        
        current_price = cw.get_current_traditional_price()
        if float(current_price) <= 0:
            raise RuntimeError('Hedger._rebalance: current_price must be > 0')
        
        token0_address = str(token0_address).lower()
        token1_address = str(token1_address).lower()
        quote_address = str(quote_address).lower()
        
        if quote_address == token0_address:
            quote_decimals = int(token0_decimals)
            base_decimals = int(token1_decimals)
            quote_now_raw = int(balance0_raw)
            base_now_raw = int(balance1_raw)
            quote_init_raw = int(init_balance0_raw)
            base_init_raw = int(init_balance1_raw)
            quote_address_now = str(token0_address)
            base_address_now = str(token1_address)
        elif quote_address == token1_address:
            quote_decimals = int(token1_decimals)
            base_decimals = int(token0_decimals)
            quote_now_raw = int(balance1_raw)
            base_now_raw = int(balance0_raw)
            quote_init_raw = int(init_balance1_raw)
            base_init_raw = int(init_balance0_raw)
            quote_address_now = str(token1_address)
            base_address_now = str(token0_address)
        else:
            raise RuntimeError('Hedger._rebalance: quote token does not match pool tokens')
        
        quote_now = float(quote_now_raw) / float(10 ** quote_decimals)
        base_now = float(base_now_raw) / float(10 ** base_decimals)
        quote_init = float(quote_init_raw) / float(10 ** quote_decimals)
        base_init = float(base_init_raw) / float(10 ** base_decimals)
        
        delta_quote = float(quote_now) - float(quote_init)
        delta_base = float(base_now) - float(base_init)
        
        if float(delta_quote) == 0.0 and float(delta_base) == 0.0:
            return None
        
        if float(delta_quote) > 0.0 and float(delta_base) < 0.0:
            base_needed = abs(float(delta_base))
            quote_needed = float(base_needed) * float(current_price)
            if float(quote_needed) <= 0:
                raise RuntimeError('Hedger._rebalance: quote_needed must be > 0')
            if float(quote_now) < float(quote_needed):
                raise RuntimeError('Hedger._rebalance: insufficient quote balance for rebalance')
            
            sell_token = str(quote_address_now)
            buy_token = str(base_address_now)
            sell_amount = float(quote_needed)
        
        elif float(delta_base) > 0.0 and float(delta_quote) < 0.0:
            quote_needed = abs(float(delta_quote))
            base_needed = float(quote_needed) / float(current_price)
            if float(base_needed) <= 0:
                raise RuntimeError('Hedger._rebalance: base_needed must be > 0')
            if float(base_now) < float(base_needed):
                raise RuntimeError('Hedger._rebalance: insufficient base balance for rebalance')
            
            sell_token = str(base_address_now)
            buy_token = str(quote_address_now)
            sell_amount = float(base_needed)
        
        else:
            self._logger.info(
                'hedger_rebalance_skip_non_opposite '
                f'delta_quote={delta_quote} delta_base={delta_base}'
            )
            return None
        
        swapper_config = CowSwapConfig(
            swapper_type=SwapperType.COW_SWAP,
            network=str(self.config.network),
            rpc_url=str(self.config.rpc_url),
            private_key=str(self._private_key),
            wallet_address=str(self._wallet_address) if self._wallet_address is not None else None,
            api_timeout_sec=int(self.config.cowswap_api_timeout_sec),
            wait_timeout_sec=int(self.config.cowswap_wait_timeout_sec),
            poll_interval_sec=int(self.config.cowswap_poll_interval_sec),
        )
        
        swapper = SwapperFactory(swapper_config).create()
        
        req = SwapRequest(
            sell_token=str(sell_token),
            buy_token=str(buy_token),
            amount=float(sell_amount),
            wait_timeout_sec=int(self.config.cowswap_wait_timeout_sec),
            poll_interval_sec=int(self.config.cowswap_poll_interval_sec),
        )
        
        res = swapper.swap_sync(req)
        if res is None:
            raise RuntimeError('Hedger._rebalance: swap result is None')
        if not isinstance(res, SwapResult):
            raise RuntimeError(f'Hedger._rebalance: res is not SwapResult: {type(res)}')
        if not bool(res.ok):
            raise RuntimeError(f'Hedger._rebalance: swap failed: {res.error}')
        
        return res
    
    def _write_stats(self, stats: HedgerStats) -> None:
        if stats is None:
            raise RuntimeError('Hedger._write_stats: stats is None')
        if not isinstance(stats, HedgerStats):
            raise RuntimeError(f'Hedger._write_stats: stats is not HedgerStats: {type(stats)}')
        
        uri = str(self.config.mongo_uri)
        db_name = str(self.config.mongo_db)
        collection_name = str(self.config.mongo_collection)
        
        if len(uri) == 0:
            raise RuntimeError('Hedger._write_stats: mongo_uri is empty')
        if len(db_name) == 0:
            raise RuntimeError('Hedger._write_stats: mongo_db is empty')
        if len(collection_name) == 0:
            raise RuntimeError('Hedger._write_stats: mongo_collection is empty')
        
        self._logger.info(f'hedger_mongo_write uri={uri} db={db_name} collection={collection_name}')
        
        client = MongoClient(str(uri), serverSelectionTimeoutMS=5000)
        try:
            _ = client.server_info()
            db = client[str(db_name)]
            col = db[str(collection_name)]
            doc = stats.model_dump()
            res = col.insert_one(doc)
            if res is None or res.inserted_id is None:
                raise RuntimeError('Hedger._write_stats: insert failed')
            self._logger.info(f'hedger_mongo_written id={res.inserted_id}')
        finally:
            client.close()