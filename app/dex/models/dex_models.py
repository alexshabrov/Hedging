"""
DEX models
Date: 2026-02-08
Version: 1.0
"""
from enum import Enum
from typing import List
from dex.lib.strict_model import StrictModel

# Enums
class DexEventType(Enum):
    SWAP = 'swap'

# ABI models
class SwapEventAbiInput(StrictModel):
    name: str
    type: str
    indexed: bool

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'name': self.name,
            'type': self.type,
            'indexed': self.indexed,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'SwapEventAbiInput':
        return cls(
            name=str(data['name']),
            type=str(data['type']),
            indexed=bool(data['indexed']),
        )

class SwapEventAbi(StrictModel):
    name: str
    type: str
    inputs: List[SwapEventAbiInput]

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'name': self.name,
            'type': self.type,
            'inputs': [item.model_dump() for item in self.inputs],
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'SwapEventAbi':
        inputs = data['inputs']
        return cls(
            name=str(data['name']),
            type=str(data['type']),
            inputs=[SwapEventAbiInput.from_dict(item) for item in list(inputs)],
        )

# Runtime config
class SwapsRealtimeConfig(StrictModel):
    ws_url: str
    pool_address: str
    swap_topic0: str
    swap_abi: SwapEventAbi

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'ws_url': self.ws_url,
            'pool_address': self.pool_address,
            'swap_topic0': self.swap_topic0,
            'swap_abi': self.swap_abi.model_dump(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'SwapsRealtimeConfig':
        return cls(
            ws_url=str(data['ws_url']),
            pool_address=str(data['pool_address']),
            swap_topic0=str(data['swap_topic0']),
            swap_abi=SwapEventAbi.from_dict(data['swap_abi']),
        )

# Swap event model
class SwapEvent(StrictModel):
    event_type: DexEventType
    pool_address: str
    block_number: int
    tx_hash: str
    log_index: int
    sender: str
    recipient: str
    amount0: int
    amount1: int
    sqrt_price_x96: int
    liquidity: int
    tick: int
    data: str
    topics: List[str]

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'event_type': self.event_type.value,
            'pool_address': self.pool_address,
            'block_number': self.block_number,
            'tx_hash': self.tx_hash,
            'log_index': self.log_index,
            'sender': self.sender,
            'recipient': self.recipient,
            'amount0': self.amount0,
            'amount1': self.amount1,
            'sqrt_price_x96': self.sqrt_price_x96,
            'liquidity': self.liquidity,
            'tick': self.tick,
            'data': self.data,
            'topics': list(self.topics),
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'SwapEvent':
        return cls(
            event_type=DexEventType(str(data['event_type'])),
            pool_address=str(data['pool_address']),
            block_number=int(data['block_number']),
            tx_hash=str(data['tx_hash']),
            log_index=int(data['log_index']),
            sender=str(data['sender']),
            recipient=str(data['recipient']),
            amount0=int(data['amount0']),
            amount1=int(data['amount1']),
            sqrt_price_x96=int(data['sqrt_price_x96']),
            liquidity=int(data['liquidity']),
            tick=int(data['tick']),
            data=str(data['data']),
            topics=list(data['topics']),
        )