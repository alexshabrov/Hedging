"""
Hedger models
Date: 2026-02-11
Version: 1.0
"""
from enum import Enum
from typing import Optional
import orjson

from live.lib.strict_model import StrictModel
from live.logic.models import HedgeSnapshot

from dex.models.contract_models import DexRunStats


### Enums ###
class HedgeRunStatus(str, Enum):
    INITIALIZED = 'initialized'
    RUNNING = 'running'
    FINISHED = 'finished'
    FAILED = 'failed'


class CexTriggerMode(str, Enum):
    PCT = 'pct'
    UNITS = 'units'


### Models ###
class HedgerConfig(StrictModel):
    symbol: str
    rpc_url: str
    network: str
    pool_address: str
    fee_pct: float
    price_lower: Optional[float]
    price_upper: Optional[float]
    price_lower_pct: Optional[float] = None
    price_upper_pct: Optional[float] = None
    total_quote: float
    cex_ratio: float = 0.255
    trigger_mode: CexTriggerMode
    trigger_pct: float = 0.0
    trigger_units: int = 0
    mongo_uri: str
    mongo_db: str
    mongo_collection: str
    tick_ms: int = 5
    gtx_cooldown_ms: int = 5
    entrance_timeout_ms: int = 60_000
    cowswap_api_timeout_sec: int = 10
    cowswap_wait_timeout_sec: int = 300
    cowswap_poll_interval_sec: int = 3

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'symbol': self.symbol,
            'rpc_url': self.rpc_url,
            'network': self.network,
            'pool_address': self.pool_address,
            'fee_pct': float(self.fee_pct),
            'price_lower': None if self.price_lower is None else float(self.price_lower),
            'price_upper': None if self.price_upper is None else float(self.price_upper),
            'price_lower_pct': None if self.price_lower_pct is None else float(self.price_lower_pct),
            'price_upper_pct': None if self.price_upper_pct is None else float(self.price_upper_pct),
            'total_quote': float(self.total_quote),
            'cex_ratio': float(self.cex_ratio),
            'trigger_mode': self.trigger_mode.value,
            'trigger_pct': float(self.trigger_pct),
            'trigger_units': int(self.trigger_units),
            'mongo_uri': self.mongo_uri,
            'mongo_db': self.mongo_db,
            'mongo_collection': self.mongo_collection,
            'tick_ms': int(self.tick_ms),
            'gtx_cooldown_ms': int(self.gtx_cooldown_ms),
            'entrance_timeout_ms': int(self.entrance_timeout_ms),
            'cowswap_api_timeout_sec': int(self.cowswap_api_timeout_sec),
            'cowswap_wait_timeout_sec': int(self.cowswap_wait_timeout_sec),
            'cowswap_poll_interval_sec': int(self.cowswap_poll_interval_sec),
        }


class HedgeCalcStats(StrictModel):
    base_price: float
    price_lower: float
    price_upper: float
    total_quote: float
    cex_ratio: float
    trigger_mode: CexTriggerMode
    trigger_offset_pct_x10000: int
    target_offset_pct_x10000: int
    hedge_quote: float

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'base_price': float(self.base_price),
            'price_lower': float(self.price_lower),
            'price_upper': float(self.price_upper),
            'total_quote': float(self.total_quote),
            'cex_ratio': float(self.cex_ratio),
            'trigger_mode': self.trigger_mode.value,
            'trigger_offset_pct_x10000': int(self.trigger_offset_pct_x10000),
            'target_offset_pct_x10000': int(self.target_offset_pct_x10000),
            'hedge_quote': float(self.hedge_quote),
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'HedgeCalcStats':
        if data is None:
            raise RuntimeError('HedgeCalcStats.from_dict: data is None')
        if not isinstance(data, dict):
            raise RuntimeError(f'HedgeCalcStats.from_dict: data is not dict: {type(data)}')

        return cls(
            base_price=float(data['base_price']),
            price_lower=float(data['price_lower']),
            price_upper=float(data['price_upper']),
            total_quote=float(data['total_quote']),
            cex_ratio=float(data['cex_ratio']),
            trigger_mode=CexTriggerMode(str(data['trigger_mode'])),
            trigger_offset_pct_x10000=int(data['trigger_offset_pct_x10000']),
            target_offset_pct_x10000=int(data['target_offset_pct_x10000']),
            hedge_quote=float(data['hedge_quote']),
        )


class LiveStats(StrictModel):
    last_snapshot: Optional[HedgeSnapshot]

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'last_snapshot': None if self.last_snapshot is None else self.last_snapshot.model_dump(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'LiveStats':
        if data is None:
            raise RuntimeError('LiveStats.from_dict: data is None')
        if not isinstance(data, dict):
            raise RuntimeError(f'LiveStats.from_dict: data is not dict: {type(data)}')

        raw = data['last_snapshot']
        return cls(
            last_snapshot=None if raw is None else HedgeSnapshot.from_dict(raw),
        )


class HedgerStats(StrictModel):
    status: HedgeRunStatus
    calc: HedgeCalcStats
    uniswap: DexRunStats
    live: LiveStats
    finished_at_ms: Optional[int]
    error: Optional[str]

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'status': self.status.value,
            'calc': self.calc.model_dump(),
            'uniswap': self.uniswap.model_dump(),
            'live': self.live.model_dump(),
            'finished_at_ms': None if self.finished_at_ms is None else int(self.finished_at_ms),
            'error': self.error,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'HedgerStats':
        if data is None:
            raise RuntimeError('HedgerStats.from_dict: data is None')
        if not isinstance(data, dict):
            raise RuntimeError(f'HedgerStats.from_dict: data is not dict: {type(data)}')
        
        status = HedgeRunStatus(str(data['status']))
        
        calc = HedgeCalcStats.from_dict(data['calc'])
        uniswap = DexRunStats.from_dict(data['uniswap'])
        live = LiveStats.from_dict(data['live'])
        
        return cls(
            status=status,
            calc=calc,
            uniswap=uniswap,
            live=live,
            finished_at_ms=None if data['finished_at_ms'] is None else int(data['finished_at_ms']),
            error=None if data['error'] is None else str(data['error']),
        )
    
    @classmethod
    def from_json(cls, raw) -> 'HedgerStats':
        if raw is None:
            raise RuntimeError('HedgerStats.from_json: raw is None')
        
        if isinstance(raw, bytes):
            payload = orjson.loads(raw)
        elif isinstance(raw, bytearray):
            payload = orjson.loads(bytes(raw))
        elif isinstance(raw, str):
            payload = orjson.loads(raw.encode('utf-8'))
        else:
            raise RuntimeError(f'HedgerStats.from_json: raw must be bytes/bytearray/str, got: {type(raw)}')
        
        if not isinstance(payload, dict):
            raise RuntimeError(f'HedgerStats.from_json: payload is not dict: {type(payload)}')
        
        return cls.from_dict(payload)