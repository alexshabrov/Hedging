"""
Uniswap V3 swaps realtime
Date: 2026-02-08
Version: 1.0
"""
import time, traceback, json
from typing import Callable, Optional, List
from dex.lib.logger import get_logger
from dex.lib.ws_client import WsClient
from dex.models.dex_models import DexEventType, SwapEvent, SwapsRealtimeConfig

# Swaps realtime
class SwapsRealtime:
    def __init__(self, config: SwapsRealtimeConfig) -> None:
        # Params
        self._config = config

        # Properties
        self._logger = get_logger('swaps_realtime')
        self._ws = WsClient(url=self._config.ws_url, threaded=False, retries=True)
        self._subscription_id: Optional[str] = None # use old typing
        self._pending_req_id: Optional[int] = None
        self._on_swap_cb: Optional[Callable[[SwapEvent], None]] = None
        self._req_id = int(time.time() * 1000)

        # Validate
        self._validate_config()

        # Callbacks
        self._ws.set_on_message(self._on_message)

    def set_on_swap(self, callback: Callable[[SwapEvent], None]) -> None:
        self._on_swap_cb = callback

    def start(self) -> None:
        if self._on_swap_cb is None:
            raise RuntimeError("on_swap callback is not set")

        self._ws.start()
        self._ws.wait_for_connect(timeout=60)
        self._subscribe()

    def stop(self) -> None:
        self._ws.stop()

    # ----------------------------------------------------------------------
    def _validate_config(self) -> None:
        if not isinstance(self._config.ws_url, str) or len(self._config.ws_url) == 0:
            raise RuntimeError("ws_url is empty")
        if not self._config.ws_url.startswith("wss://"):
            raise RuntimeError("ws_url must start with wss://")

        self._validate_hex(self._config.pool_address, 42, "pool_address")
        self._validate_hex(self._config.swap_topic0, 66, "swap_topic0")
        self._validate_swap_abi()

    def _validate_swap_abi(self) -> None:
        abi = self._config.swap_abi
        if str(abi.type) != "event":
            raise RuntimeError("swap_abi.type must be 'event'")
        if str(abi.name) != "Swap":
            raise RuntimeError("swap_abi.name must be 'Swap'")

        inputs = list(abi.inputs)
        if len(inputs) != 7:
            raise RuntimeError(f"swap_abi.inputs must have 7 items, got: {len(inputs)}")

        expected = [
            ("address", True),
            ("address", True),
            ("int256", False),
            ("int256", False),
            ("uint160", False),
            ("uint128", False),
            ("int24", False),
        ]

        for i in range(len(expected)):
            exp_type, exp_indexed = expected[i]
            inp = inputs[i]
            if str(inp.type) != exp_type:
                raise RuntimeError(f"swap_abi.inputs[{i}].type must be {exp_type}")
            if bool(inp.indexed) != bool(exp_indexed):
                raise RuntimeError(f"swap_abi.inputs[{i}].indexed must be {exp_indexed}")

    def _validate_hex(self, value: str, length: int, name: str) -> None:
        if not isinstance(value, str):
            raise RuntimeError(f"{name} must be str")
        if not value.startswith("0x"):
            raise RuntimeError(f"{name} must start with 0x")
        if len(value) != length:
            raise RuntimeError(f"{name} must be {length} chars, got: {len(value)}")
        try:
            int(value[2:], 16)
        except Exception as e:
            raise RuntimeError(f"{name} is not hex: {e}")

    # ----------------------------------------------------------------------
    def _subscribe(self) -> None:
        if self._pending_req_id is not None:
            raise RuntimeError("Subscription request already pending")

        self._req_id += 1
        self._pending_req_id = int(self._req_id)

        req = {
            "jsonrpc": "2.0",
            "id": int(self._pending_req_id),
            "method": "eth_subscribe",
            "params": [
                "logs",
                {
                    "address": self._config.pool_address,
                    "topics": [self._config.swap_topic0],
                }
            ],
        }

        payload = json.dumps(req, ensure_ascii=False)
        self._ws.send(payload)

    def _on_message(self, message: str) -> None:
        try:
            data = json.loads(message)
        except Exception as e:
            raise RuntimeError(f"Invalid JSON: {e}")

        if not isinstance(data, dict):
            raise RuntimeError("JSON-RPC message must be a dict")

        if "method" in data:
            self._handle_subscription_event(data)
            return

        if "id" in data:
            self._handle_response(data)
            return

        raise RuntimeError("Unknown JSON-RPC message type")

    def _handle_response(self, data: dict) -> None:
        if str(data['jsonrpc']) != "2.0":
            raise RuntimeError("jsonrpc must be 2.0")

        if self._pending_req_id is None:
            raise RuntimeError("No pending request for response")

        if int(data['id']) != int(self._pending_req_id):
            raise RuntimeError("Response id mismatch")

        if "error" in data:
            err = data['error']
            raise RuntimeError(f"RPC error: {err}")

        result = data['result']
        if not isinstance(result, str):
            raise RuntimeError("Subscription result must be string")

        self._subscription_id = str(result)
        self._pending_req_id = None
        self._logger.info(f"Subscribed: {self._subscription_id}")

    def _handle_subscription_event(self, data: dict) -> None:
        if str(data['method']) != "eth_subscription":
            raise RuntimeError("Unexpected method")

        params = data['params']
        if not isinstance(params, dict):
            raise RuntimeError("params must be dict")

        sub_id = params['subscription']
        if self._subscription_id is None:
            raise RuntimeError("Subscription not initialized")
        if str(sub_id) != str(self._subscription_id):
            raise RuntimeError("Subscription id mismatch")

        result = params['result']
        if not isinstance(result, dict):
            raise RuntimeError("result must be dict")

        event = self._parse_swap_event(result)
        if self._on_swap_cb is None:
            raise RuntimeError("on_swap callback is not set")

        try:
            self._on_swap_cb(event)
        except Exception as e:
            self._logger.error(f"Error in on_swap callback: {e} {traceback.format_exc()}")
            raise

    # ----------------------------------------------------------------------
    def _parse_swap_event(self, log: dict) -> SwapEvent:
        address = str(log['address']).lower()
        if address != self._config.pool_address.lower():
            raise RuntimeError("Pool address mismatch")

        topics = list(log['topics'])
        if len(topics) != 3:
            raise RuntimeError(f"Swap topics must be 3 items, got: {len(topics)}")

        topic0 = str(topics[0])
        if topic0 != self._config.swap_topic0:
            raise RuntimeError("Swap topic0 mismatch")

        sender = self._topic_to_address(str(topics[1]))
        recipient = self._topic_to_address(str(topics[2]))

        data = str(log['data'])
        words = self._split_words(data, 5)

        amount0 = self._decode_int256(words[0])
        amount1 = self._decode_int256(words[1])
        sqrt_price_x96 = self._decode_uint(words[2], 160)
        liquidity = self._decode_uint(words[3], 128)
        tick = self._decode_int(words[4], 24)

        block_number = self._hex_to_int(str(log['blockNumber']))
        log_index = self._hex_to_int(str(log['logIndex']))
        tx_hash = str(log['transactionHash'])

        return SwapEvent(
            event_type=DexEventType.SWAP,
            pool_address=address,
            block_number=int(block_number),
            tx_hash=tx_hash,
            log_index=int(log_index),
            sender=sender,
            recipient=recipient,
            amount0=int(amount0),
            amount1=int(amount1),
            sqrt_price_x96=int(sqrt_price_x96),
            liquidity=int(liquidity),
            tick=int(tick),
            data=data,
            topics=[str(t) for t in list(topics)],
        )

    def _topic_to_address(self, topic: str) -> str:
        self._validate_hex(topic, 66, "topic")
        addr = "0x" + topic[-40:]
        self._validate_hex(addr, 42, "address")
        return str(addr).lower()

    def _split_words(self, data: str, count: int) -> List[str]:
        self._validate_hex(data, 2 + 64 * count, "data")
        out: List[str] = []
        i = 0
        while i < count:
            start = 2 + 64 * i
            end = start + 64
            out.append("0x" + data[start:end])
            i += 1
        return out

    def _hex_to_int(self, value: str) -> int:
        self._validate_hex(value, len(value), "hex")
        return int(value, 16)

    def _decode_int256(self, value: str) -> int:
        self._validate_hex(value, 66, "int256")
        v = int(value, 16)
        if v >= 2 ** 255:
            v = v - 2 ** 256
        return int(v)

    def _decode_uint(self, value: str, bits: int) -> int:
        self._validate_hex(value, 66, "uint")
        v = int(value, 16)
        if v >= 2 ** bits:
            raise RuntimeError(f"uint overflow: {v}")
        return int(v)

    def _decode_int(self, value: str, bits: int) -> int:
        v = self._decode_int256(value)
        min_v = -1 * (2 ** (bits - 1))
        max_v = (2 ** (bits - 1)) - 1
        if v < min_v or v > max_v:
            raise RuntimeError(f"int overflow: {v}")
        return int(v)