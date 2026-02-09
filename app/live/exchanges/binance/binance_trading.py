import time, json, orjson, hmac, requests, hashlib, random
from urllib.parse import urlencode
from threading import Thread
from typing import Dict, Optional, Callable, List, Tuple, Any

from ..exchange_interface import *
from ...lib.logger import get_logger
from ...lib.ws_client import WsClient
from ..exchange_models import OrderError, Order, AccountData, Balance, Position

"""
Binance trading connector (REST + user-data websocket).

Provides order create/modify/delete APIs in strict integer units, maps exchange error codes into OrderError,
and emits Fill updates (and account updates) via callbacks.
"""

### Constants ###
HTTP_ENDPOINT = "https://fapi.binance.com"
WS_ENDPOINT = "wss://fstream.binance.com/ws"

# Binance error codes
BINANCE_CODE_NO_SUCH_ORDER = -2011
BINANCE_CODE_ORDER_DOES_NOT_EXIST = -2013
BINANCE_CODE_TIMESTAMP_OUTSIDE_WINDOW = -1021
BINANCE_CODE_REDUCE_ONLY_REJECTED = -2022
BINANCE_CODE_GTX_REJECTED = -5022
BINANCE_CODE_WOULD_IMMEDIATELY_TRIGGER = -2021
BINANCE_CODE_UNKNOWN_RESULT = -1007

### Binance ###
class Binance(ExchangeInterface):
    def __init__(self, key: str, secret: str, hedge_mode: Optional[bool] = True, is_realtime: Optional[bool] = False) -> None:
        # Params
        self.hedge_mode = hedge_mode

        # API
        assert key, 'BINANCE_KEY is empty'
        assert secret, 'BINANCE_SECRET is empty'

        self.key = key
        self.secret = secret

        # Logger
        self.logger = get_logger('binance')

        # Callbacks
        self.on_fill = None
        self.on_update = None

        # Update rate limits
        self._update_rate_limits()

        # Check status
        self._check_status()

        # Rules
        self.rules = self._get_rules()

        if is_realtime:
            # Start rules updater
            Thread(target=self._rules_updater_thread, daemon=True).start()

            # Start WS
            self._start_ws()

    def is_hedge_mode(self) -> bool:
        return bool(self.hedge_mode)

    def wait_for_connect(self, timeout: int = 60) -> None:
        if self.ws_client is None:
            raise RuntimeError('wait_for_connect: ws_client is None')

        self.ws_client.wait_for_connect(timeout=timeout)

    def stop(self) -> None:
        if self.ws_client is None:
            raise RuntimeError('stop: ws_client is None')

        self.ws_client.stop()

    ### Auth request ###
    def _auth_request(self, endpoint: str, method: str = "GET", data: Optional[Dict[str, Any]] = None,
                      make_it_binance_way: bool = False, n_recursion: int = 0) -> Tuple[Any, int, bool, Optional[Exception]]:
        endpoint_path = endpoint

        if data is None:
            data = {}
        else:
            data = data.copy()

        base_data = data.copy()

        self.logger.info(f'Sending request: {endpoint_path}, method: {method}, data: {json.dumps(data, indent=4)}')

        # Add a timestamp to the data and recvWindow
        timestamp = int(time.time() * 1000)

        data['timestamp'] = timestamp
        data['recvWindow'] = 5000
        
        # Create a URL-encoded query string from the data
        query_string = urlencode(data)

        # Replace single quote with double quote in the query string
        query_string = query_string.replace('%27', '%22')
    
        # Create the signature
        signature = hmac.new(bytes(self.secret, 'utf-8'), msg=bytes(query_string, 'utf-8'), digestmod=hashlib.sha256).hexdigest()
    
        # Adjust URL and data for GET and non-GET methods
        if method == "GET":
            endpoint = f"{endpoint_path}?{query_string}&signature={signature}"
            data = None  # Clear the data since it's now in the URL
        else:
            # POST, PUT, etc. will have body data
            # EXCEPT the special way
            if make_it_binance_way:
                data = None
                endpoint = f"{endpoint_path}?{query_string}&signature={signature}" # special way
            else:
                endpoint = f"{endpoint_path}?signature={signature}" # sane way
        
        # Add or update headers with the API key
        headers = {
            'X-MBX-APIKEY': self.key,
            'Content-Type': 'application/json',
        }
        
        # Send the request
        try:
            _response = requests.request(method, f"{HTTP_ENDPOINT}{endpoint}", headers=headers, json=data)
            response = _response.json()
        except Exception as e:
            self.logger.error(f'Failed to send request: {e}')
            return None, 0, True, e
        
        # Response headers
        response_headers = dict(_response.headers)
        
        if 'x-mbx-used-weight-1m' in response_headers:
            value = int(response_headers["x-mbx-used-weight-1m"])
            self.logger.info(f'Headers -> Used weight: {value} / {self.request_weight_minute} ({value / self.request_weight_minute * 100:.2f}%)')

            if value > self.request_weight_minute * 0.5:
                self.logger.warning(f'Used weight is more than 50%: {value}')

            if value > self.request_weight_minute * 0.95:
                self.logger.warning(f'Used weight is more than 95%: {value}')

                # Get the rest of the minute
                minute_ts = int(time.time() / 60) * 60 + 60
                left_seconds = minute_ts - time.time()

                # Sleep until the next minute
                self.logger.warning(f'Sleeping for {left_seconds + 1} seconds')
                time.sleep(left_seconds + 1)

        if 'x-mbx-order-count-10s' in response_headers:
            value = int(response_headers["x-mbx-order-count-10s"])
            self.logger.info(f'Headers -> Order count (10s): {value} / {self.order_weight_10_seconds} ({value / self.order_weight_10_seconds * 100:.2f}%)')

            if value > self.order_weight_10_seconds * 0.5:
                self.logger.warning(f'Order count is more than 50%: {value}')

            if value > self.order_weight_10_seconds * 0.95:
                self.logger.warning(f'Order count is more than 95%: {value}')

                # Sleep for 10 seconds
                self.logger.warning(f'Sleeping for 10 seconds')
                time.sleep(10)

        if 'x-mbx-order-count-1m' in response_headers:
            value = int(response_headers["x-mbx-order-count-1m"])
            self.logger.info(f'Headers -> Order count (1m): {value} / {self.orders_weight_minute} ({value / self.orders_weight_minute * 100:.2f}%)')

            if value > self.orders_weight_minute * 0.5:
                self.logger.warning(f'Order count is more than 50%: {value}')

            if value > self.orders_weight_minute * 0.95:
                self.logger.warning(f'Order count is more than 95%: {value}')

                # Get the rest of the minute
                minute_ts = int(time.time() / 60) * 60 + 60
                left_seconds = minute_ts - time.time()

                # Sleep until the next minute
                self.logger.warning(f'Sleeping for {left_seconds + 1} seconds')
                time.sleep(left_seconds + 1)
            
        # Check code: return raw exchange code like Go connector does.
        if isinstance(response, dict) and 'code' in response and 'msg' in response:
            code = int(response['code'])

            if code == BINANCE_CODE_TIMESTAMP_OUTSIDE_WINDOW and n_recursion < 3:
                self.logger.warning('Timestamp for this request is outside of the recvWindow, trying again')
                return self._auth_request(endpoint_path, method, base_data, make_it_binance_way, n_recursion + 1)

            self.logger.error(f'Bad response: {response}')
            return response, int(code), False, None

        self.logger.info(f'Completed request: {endpoint_path}, method: {method}, data: {json.dumps(data, indent=4)}')

        return response, 0, True, None

    def _raise_on_bad_code(self, code, response, ctx):
        if not isinstance(code, int):
            raise RuntimeError(f'{ctx}: code is not int: {type(code)}')

        if int(code) != 0 and int(code) != 200:
            raise RuntimeError(f'{ctx}: bad response: {response}')

    def _map_order_op_error(self, op, code, response):
        # Centralized code->OrderError mapping; no duplication.
        if not op:
            raise RuntimeError('Binance._map_order_op_error: empty op')

        if not isinstance(code, int):
            raise RuntimeError(f'Binance._map_order_op_error: code is not int: {type(code)}')

        if int(code) == 0:
            return None, None

        # Delete in Go treats -2011 as OK (no such order).
        if op == 'delete':
            if int(code) == BINANCE_CODE_NO_SUCH_ORDER:
                return None, None

            if int(code) == BINANCE_CODE_ORDER_DOES_NOT_EXIST:
                return OrderError.DOES_NOT_EXIST, RuntimeError(f'Order does not exist: {response}')

            return OrderError.EXCHANGE, RuntimeError(f'Bad response: {response}')

        # Shared codes for create/modify in Go.
        if int(code) == BINANCE_CODE_UNKNOWN_RESULT:
            return OrderError.NEEDS_REPAIR, RuntimeError(f'exchange unknown result (-1007): {response}')

        if int(code) == BINANCE_CODE_REDUCE_ONLY_REJECTED:
            return OrderError.VOLUME_VIOLATION, RuntimeError(f'ReduceOnly order rejected: {response}')

        if int(code) == BINANCE_CODE_GTX_REJECTED:
            return OrderError.PRICE_VIOLATION, RuntimeError(f'GTX rejected: {response}')

        if int(code) == BINANCE_CODE_WOULD_IMMEDIATELY_TRIGGER:
            return OrderError.PRICE_VIOLATION, RuntimeError(f'Order would immediately trigger: {response}')

        # Modify in Go maps -2013 to DOES_NOT_EXIST.
        if op == 'modify' and int(code) == BINANCE_CODE_ORDER_DOES_NOT_EXIST:
            return OrderError.DOES_NOT_EXIST, RuntimeError(f'Order does not exist: {response}')

        # Create in Go does not special-case DOES_NOT_EXIST.
        return OrderError.EXCHANGE, RuntimeError(f'Bad response: {response}')
    
    def _update_rate_limits(self) -> None:
        response = requests.get(f'{HTTP_ENDPOINT}/fapi/v1/exchangeInfo').json()

        self.request_weight_minute = None
        self.orders_weight_minute = None
        self.order_weight_10_seconds = None

        for item in response['rateLimits']:
            if item['rateLimitType'] == 'REQUEST_WEIGHT':
                assert item['intervalNum'] == 1, f'Bad intervalNum: {item}'
                self.request_weight_minute = item['limit']

            if item['rateLimitType'] == 'ORDERS':
                if item['interval'] == 'MINUTE' and item['intervalNum'] == 1:
                    self.orders_weight_minute = item['limit']
                
                if item['interval'] == 'SECOND' and item['intervalNum'] == 10:
                    self.order_weight_10_seconds = item['limit']

        assert self.request_weight_minute is not None, f'Bad request weight minute: {response}'
        assert self.orders_weight_minute is not None, f'Bad orders weight minute: {response}'
        assert self.order_weight_10_seconds is not None, f'Bad order weight 10 seconds: {response}'
    
    def _check_status(self) -> None:
        # Check status
        response, code, _can_repeat, err = self._auth_request('/fapi/v1/apiTradingStatus')
        if err is not None:
            raise err
        self._raise_on_bad_code(int(code), response, '_check_status/apiTradingStatus')
        assert len(response['indicators']) == 0, f'Bad API status: {response}'

        # Get hedge or one-way
        response, code, _can_repeat, err = self._auth_request('/fapi/v1/positionSide/dual')
        if err is not None:
            raise err
        self._raise_on_bad_code(int(code), response, '_check_status/positionSide/dual')

        # Change if required
        if response['dualSidePosition'] != self.hedge_mode:
            value = 'true' if self.hedge_mode else 'false'
            self.logger.info(f'Changing hedge mode to {value}')

            try:
                _response, code, _can_repeat, err = self._auth_request('/fapi/v1/positionSide/dual', method='POST', data={'dualSidePosition': value}, make_it_binance_way=True)
                if err is not None:
                    raise err
                self._raise_on_bad_code(int(code), _response, '_check_status/positionSide/dual/post')
                self.logger.info(f'Hedge mode changed to {value}')
            except Exception as e:
                self.logger.error(f'Failed to change hedge mode: {e}')
                raise e

    def _get_listen_key(self) -> str:
        response, code, _can_repeat, err = self._auth_request('/fapi/v1/listenKey', method='POST')
        if err is not None:
            raise err
        self._raise_on_bad_code(int(code), response, '_get_listen_key')
        return response['listenKey']
    
    ### WS methods ###
    def _listen_key_thread(self) -> None:
        last_updated = time.time()

        while True:
            if time.time() - last_updated > 50 * 60:
                self.logger.info(f'Renewing listen key')

                try:
                    self.listen_key = self._get_listen_key()
                    last_updated = time.time()
                    self.logger.info(f'Listen key renewed')

                except Exception as e:
                    self.logger.error(f'Failed to renew listen key: {e}')

            time.sleep(5)

    def _start_ws(self) -> None:
        # Get listen key
        self.listen_key = self._get_listen_key()
        self.logger.info('Got initial listen key')

        # Start listen key thread
        Thread(target=self._listen_key_thread, daemon=True).start()

        def on_message(message: bytes) -> None:
            self.logger.info(f'WS raw message: {message}')
            
            try:
                payload = orjson.loads(message)

                if 'e' in payload and payload['e'] == 'ACCOUNT_UPDATE':
                    if self.on_update is not None:
                        self.on_update()

                if 'e' in payload and payload['e'] == 'ORDER_TRADE_UPDATE':
                    # Message data
                    symbol = payload["o"]["s"]
                    order_id = str(payload['o']['i'])
                    trade_id = str(payload['o']['t'])
                    base_volume = payload['o']['l']
                    price = payload['o']['L']
                    time_ms = payload['T']
                    side = payload['o']['S']

                    if float(base_volume) > 0:
                        if symbol not in self.rules:
                            raise RuntimeError(f'WS fill: rule not found for symbol: {symbol}')

                        rule = self.rules[symbol]

                        base_units = rule.volume_to_units(str(base_volume))
                        price_units = rule.price_to_units(str(price))

                        fill = Fill(
                            symbol=symbol,
                            order_id=order_id,
                            trade_id=trade_id,
                            time_ms=time_ms,
                            arrived_ms=int(time.time() * 1000),
                            price=price_units,
                            base_volume=base_units,
                            from_api=False,
                            side=OrderSide(side),
                            is_filled=(payload['o']['X'] == 'FILLED'),
                        )
                        if self.on_fill is not None:
                            self.on_fill(fill)

                if 'e' in payload and payload['e'] == 'ALGO_UPDATE':
                    if self.on_update is not None:
                        self.on_update()

                    status = payload['o']['X']

                    if status == 'TRIGGERED':
                        symbol = payload["o"]["s"]
                        algo_id = str(payload['o']['aid'])
                        base_volume = payload['o']['q']
                        trigger_price = payload['o']['tp']
                        time_ms = payload['E']
                        side = payload['o']['S']

                        if float(base_volume) > 0:
                            if symbol not in self.rules:
                                raise RuntimeError(f'WS algo fill: rule not found for symbol: {symbol}')

                            rule = self.rules[symbol]

                            base_units = rule.volume_to_units(str(base_volume))
                            price_units = rule.price_to_units(str(trigger_price))

                            fill = Fill(
                                symbol=symbol,
                                order_id=algo_id,
                                trade_id=f'ALGO:{algo_id}:{time_ms}',
                                time_ms=time_ms,
                                arrived_ms=int(time.time() * 1000),
                                price=price_units,
                                base_volume=base_units,
                                from_api=False,
                                side=OrderSide(side),
                                is_filled=True,
                            )
                            if self.on_fill is not None:
                                self.on_fill(fill)

            except Exception as e:
                self.logger.error(f'Failed to process WS message: {e}, message: {message}')

        # Start WS client (handles reconnect loop internally)
        self.ws_client = WsClient(f'{WS_ENDPOINT}/{self.listen_key}', threaded=False, retries=True)
        self.ws_client.set_on_message(on_message)
        self.ws_client.start()
    
    ### Interface methods ###
    def get_rules(self) -> Dict[str, Rule]:
        if self.rules is None:
            self.rules = self._get_rules()

        return self.rules

    def _rules_updater_thread(self) -> None:
        while True:
            # Sleep
            time.sleep(random.randint(60 * 10, 60 * 15))

            # Update rules
            try:
                self.rules = self._get_rules()
            except Exception as e:
                self.logger.error(f'Failed to update rules: {e}')

    # Rules
    def _get_rules(self) -> Dict[str, Rule]:
        # Rules
        rules: Dict[str, Rule] = {}
        
        # Load exchange info
        try:
            exchange_info, code, _can_repeat, err = self._auth_request('/fapi/v1/exchangeInfo')
            if err is not None:
                raise err
            self._raise_on_bad_code(int(code), exchange_info, '_get_rules')
        except Exception as e:
            self.logger.error(f'Failed to load exchange info: {e}')
            raise e

        for item in exchange_info['symbols']:
            # Checks
            if item['status'] != 'TRADING':
                continue

            if item['contractType'] != 'PERPETUAL':
                continue
            
            # Symbol
            symbol = item['symbol']

            if '_' in symbol:
                continue

            # Filter components
            price_step = None
            lot_step = None
            min_base_volume = None
            max_base_volume = None
            min_quote_volume = None
           
            for filter in item['filters']:
                if filter['filterType'] == 'PRICE_FILTER':
                    price_step = filter['tickSize']

                if filter['filterType'] == 'LOT_SIZE':
                    lot_step = filter['stepSize']
                    min_base_volume = filter['minQty']
                    max_base_volume = filter['maxQty']

                if filter['filterType'] == 'MIN_NOTIONAL':
                    min_quote_volume = filter['notional']

            if price_step is None or lot_step is None or min_base_volume is None or max_base_volume is None:
                continue

            # Create rule
            rule = Rule(
                price_step=price_step,
                lot_step=lot_step,
                min_base_volume=min_base_volume,
                max_base_volume=max_base_volume,
                min_quote_volume=min_quote_volume,
            )

            rules[symbol] = rule

        return rules
    
    def get_price(self, symbol: str) -> int:
        try:
            response, code, _can_repeat, err = self._auth_request('/fapi/v1/ticker/price', data={'symbol': symbol})
            if err is not None:
                raise err
            self._raise_on_bad_code(int(code), response, 'get_price')
        except Exception as e:
            self.logger.error(f'Failed to get price: {e}')
            raise e

        if symbol not in self.rules:
            raise RuntimeError(f'get_price: rule not found for symbol: {symbol}')

        rule = self.rules[symbol]

        return rule.price_to_units(str(response['price']))
    
    def set_fill_callback(self, callback: Callable[[Fill], None]) -> None:
        self.on_fill = callback

    def set_update_callback(self, callback: Callable[[], None]) -> None:
        self.on_update = callback

    def create_order(self, position_side: PositionSide, order_side: OrderSide, order_type: OrderType, symbol: str,
                     base_volume: int, price: Optional[int] = None, reduce_only: bool = False) -> Tuple[str, bool, Optional[OrderError], Optional[Exception]]:
        if symbol not in self.rules:
            raise RuntimeError(f'create_order: rule not found for symbol: {symbol}')

        rule = self.rules[symbol]

        if not isinstance(base_volume, int):
            raise RuntimeError(f'create_order: base_volume is not int: {type(base_volume)}')

        if base_volume <= 0:
            raise RuntimeError(f'create_order: base_volume must be > 0, got: {base_volume}')

        if price is not None:
            if not isinstance(price, int):
                raise RuntimeError(f'create_order: price is not int: {type(price)}')

            if price <= 0:
                raise RuntimeError(f'create_order: price must be > 0, got: {price}')

        base_volume_str = rule.volume_from_units(base_volume)
        price_str = None

        if price is not None:
            price_str = rule.price_from_units(price)

        # Side
        if order_side == OrderSide.BUY:
            _side = 'BUY'
        elif order_side == OrderSide.SELL:
            _side = 'SELL'
        else:
            raise RuntimeError(f'Bad order side: {order_side}')

        # Position side
        if position_side == PositionSide.LONG:
            _position_side = 'LONG' if self.hedge_mode else 'BOTH'
        elif position_side == PositionSide.SHORT:
            _position_side = 'SHORT' if self.hedge_mode else 'BOTH'
        elif position_side == PositionSide.BOTH:
            if self.hedge_mode:
                raise RuntimeError(f'Bad position side for hedge mode: {position_side}')

            _position_side = 'BOTH'
        else:
            raise RuntimeError(f'Bad position side: {position_side}')

        # Endpoint and data
        endpoint = '/fapi/v1/order'

        data = {
            'symbol': symbol,
            'side': _side,
            'positionSide': _position_side,
        }
        
        if bool(reduce_only) and not self.hedge_mode:
            data['reduceOnly'] = True

        if order_type == OrderType.MARKET:
            data['type'] = 'MARKET'
            data['quantity'] = base_volume_str
            
        elif order_type == OrderType.LIMIT:
            assert price_str is not None, f'Price is required for LIMIT order'
            data['type'] = 'LIMIT'
            data['price'] = price_str
            data['quantity'] = base_volume_str
            data['timeInForce'] = 'GTX'

        elif order_type == OrderType.STOP_MARKET:
            assert price_str is not None, f'Price is required for STOP_MARKET order'
            endpoint = '/fapi/v1/algoOrder'
            data['algoType'] = 'CONDITIONAL'
            data['type'] = 'STOP_MARKET'
            data['triggerPrice'] = price_str
            data['quantity'] = base_volume_str
            data['timeInForce'] = 'GTC'
            data['workingType'] = 'CONTRACT_PRICE'

        elif order_type == OrderType.STOPLOSS:
            assert price_str is not None, f'Price is required for STOPLOSS order'
            endpoint = '/fapi/v1/algoOrder'
            data['algoType'] = 'CONDITIONAL'
            data['type'] = 'STOP_MARKET'
            data['triggerPrice'] = price_str
            data['timeInForce'] = 'GTC'
            data['closePosition'] = True
            data['workingType'] = 'CONTRACT_PRICE'

        elif order_type == OrderType.TAKE_PROFIT:
            assert price_str is not None, f'Price is required for TAKE_PROFIT order'
            endpoint = '/fapi/v1/algoOrder'
            data['algoType'] = 'CONDITIONAL'
            data['type'] = 'TAKE_PROFIT_MARKET'
            data['triggerPrice'] = price_str
            data['timeInForce'] = 'GTC'
            data['closePosition'] = True
            data['workingType'] = 'CONTRACT_PRICE'

        else:
            raise RuntimeError(f'Bad order type: {order_type}')
        
        # Call
        try:
            response, code, can_repeat, err = self._auth_request(endpoint, method='POST', data=data, make_it_binance_way=True)
            if err is not None:
                return "", bool(can_repeat), OrderError.NETWORK, err

            if int(code) != 0:
                order_error, err2 = self._map_order_op_error('create', int(code), response)
                if err2 is None:
                    raise RuntimeError('create_order: mapped err2 is None')
                return "", bool(can_repeat), order_error, err2
        except Exception as e:
            self.logger.error(f'Failed to create order: {e}, data: {json.dumps(data, indent=4)}')
            return "", True, OrderError.NETWORK, e
        
        if endpoint == '/fapi/v1/algoOrder':
            if 'algoId' not in response:
                return "", False, OrderError.EXCHANGE, RuntimeError(f'Failed to get algoId from response: {response}')

            return str(response['algoId']), True, None, None

        if 'orderId' not in response:
            return "", False, OrderError.EXCHANGE, RuntimeError(f'Failed to get orderId from response: {response}')

        return str(response['orderId']), True, None, None

    def delete_order(self, symbol: str, order_id: str, order_type: OrderType) -> Tuple[bool, Optional[OrderError], Optional[Exception]]:
        endpoint = '/fapi/v1/order'

        try:
            order_id_int = int(order_id)
        except Exception as e:
            return False, OrderError.INPUT_FORMAT, RuntimeError(f'Bad order id: {order_id}, error: {e}')

        if order_type == OrderType.STOP_MARKET or order_type == OrderType.STOPLOSS or order_type == OrderType.TAKE_PROFIT:
            endpoint = '/fapi/v1/algoOrder'
            data = {
                'algoId': order_id_int,
            }
        else:
            data = {
                'symbol': symbol,
                'orderId': order_id_int,
            }

        try:
            response, code, can_repeat, err = self._auth_request(endpoint, method='DELETE', data=data, make_it_binance_way=True)
            if err is not None:
                return bool(can_repeat), OrderError.NETWORK, err

            if int(code) != 0:
                order_error, err2 = self._map_order_op_error('delete', int(code), response)
                if err2 is None:
                    raise RuntimeError('delete_order: mapped err2 is None')
                return bool(can_repeat), order_error, err2
        except Exception as e:
            self.logger.error(f'Failed to delete order: {e}, data: {json.dumps(data, indent=4)}')
            return True, OrderError.NETWORK, e

        return True, None, None

    def modify_order(self, symbol: str, order_id: str, position_side: PositionSide, order_side: OrderSide, order_type: OrderType,
                     base_volume: int, price: Optional[int] = None) -> Tuple[bool, Optional[OrderError], Optional[Exception]]:
        if order_type != OrderType.LIMIT:
            return False, OrderError.INPUT_FORMAT, RuntimeError(f'modify_order: unsupported order_type: {order_type}')

        if not symbol:
            return False, OrderError.INPUT_FORMAT, RuntimeError('modify_order: empty symbol')

        if not order_id:
            return False, OrderError.INPUT_FORMAT, RuntimeError('modify_order: empty order_id')

        if price is None:
            return False, OrderError.INPUT_FORMAT, RuntimeError('modify_order: price is None')

        if not isinstance(price, int):
            return False, OrderError.INPUT_FORMAT, RuntimeError(f'modify_order: price is not int: {type(price)}')

        if int(price) <= 0:
            return False, OrderError.INPUT_FORMAT, RuntimeError(f'modify_order: bad price: {price}')

        if base_volume is None:
            return False, OrderError.INPUT_FORMAT, RuntimeError('modify_order: base_volume is None')

        if not isinstance(base_volume, int):
            return False, OrderError.INPUT_FORMAT, RuntimeError(f'modify_order: base_volume is not int: {type(base_volume)}')

        if int(base_volume) <= 0:
            return False, OrderError.INPUT_FORMAT, RuntimeError(f'modify_order: bad base_volume: {base_volume}')

        if symbol not in self.rules:
            return False, OrderError.INPUT_FORMAT, RuntimeError(f'modify_order: rule not found for symbol: {symbol}')

        rule = self.rules[symbol]
        price_str = rule.price_from_units(int(price))
        base_volume_str = rule.volume_from_units(int(base_volume))

        try:
            order_id_int = int(order_id)
        except Exception as e:
            return False, OrderError.INPUT_FORMAT, RuntimeError(f'modify_order: bad order_id: {order_id}, error: {e}')

        if order_side == OrderSide.BUY:
            side = 'BUY'
        elif order_side == OrderSide.SELL:
            side = 'SELL'
        else:
            return False, OrderError.INPUT_FORMAT, RuntimeError(f'modify_order: bad order_side: {order_side}')

        if position_side != PositionSide.BOTH:
            return False, OrderError.INPUT_FORMAT, RuntimeError(f'modify_order: position_side must be BOTH, got: {position_side}')

        data = {
            'symbol': symbol,
            'orderId': order_id_int,
            'side': side,
            'quantity': base_volume_str,
            'price': price_str,
        }

        try:
            response, code, can_repeat, err = self._auth_request('/fapi/v1/order', method='PUT', data=data, make_it_binance_way=True)
        except Exception as e:
            self.logger.error(f'Failed to modify order: {e}, data: {json.dumps(data, indent=4)}')
            return True, OrderError.NETWORK, e

        if err is not None:
            return bool(can_repeat), OrderError.NETWORK, err

        if int(code) != 0:
            order_error, err2 = self._map_order_op_error('modify', int(code), response)
            if err2 is None:
                raise RuntimeError('modify_order: mapped err2 is None')
            return bool(can_repeat), order_error, err2

        if response is None or not isinstance(response, dict):
            return False, OrderError.EXCHANGE, RuntimeError(f'modify_order: bad response type: {type(response)}')

        if 'orderId' not in response:
            return False, OrderError.EXCHANGE, RuntimeError(f'modify_order: missing orderId in response: {response}')

        if int(response['orderId']) != int(order_id_int):
            return False, OrderError.EXCHANGE, RuntimeError(f'modify_order: wrong orderId in response: {response}')

        if 'status' in response and str(response['status']).upper() == 'CANCELED':
            return False, OrderError.PRICE_VIOLATION, RuntimeError(f'modify_order: order was cancelled during modification: {response}')

        return True, None, None

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        # Data
        data = {}

        if symbol is not None:
            data['symbol'] = symbol

        # Call regular orders
        try:
            response, code, _can_repeat, err = self._auth_request('/fapi/v1/openOrders', data=data)
            if err is not None:
                raise err
            self._raise_on_bad_code(int(code), response, 'get_open_orders/openOrders')
        except Exception as e:
            self.logger.error(f'Failed to get open orders: {e}, data: {json.dumps(data, indent=4)}')
            raise e

        # Orders
        orders = []

        for item in response:
            sym = item['symbol']

            if sym not in self.rules:
                raise RuntimeError(f'get_open_orders: rule not found for symbol: {sym}')

            rule = self.rules[sym]

            if item['side'] == 'BUY':
                order_side = OrderSide.BUY
            elif item['side'] == 'SELL':
                order_side = OrderSide.SELL
            else:
                raise RuntimeError(f'Bad order side: {item["side"]}')

            raw_type = item['type']

            if raw_type == 'LIMIT':
                order_type = OrderType.LIMIT
                order_price = item['price']

            elif raw_type == 'MARKET':
                order_type = OrderType.MARKET
                order_price = item['price']

            elif raw_type == 'STOP':
                order_type = OrderType.STOP_MARKET
                order_price = item['stopPrice']

            elif raw_type == 'STOP_MARKET':
                if 'closePosition' in item and item['closePosition']:
                    order_type = OrderType.STOPLOSS
                else:
                    order_type = OrderType.STOP_MARKET

                order_price = item['stopPrice']

            elif raw_type == 'TAKE_PROFIT' or raw_type == 'TAKE_PROFIT_MARKET':
                order_type = OrderType.TAKE_PROFIT
                order_price = item['stopPrice']

            else:
                raise RuntimeError(f'Bad order type: {raw_type}')

            order = Order(
                symbol=sym,
                order_id=str(item['orderId']),
                order_side=order_side,
                order_type=order_type,
                base_volume=rule.volume_to_units(str(item['origQty'])),
                filled_volume=rule.volume_to_units(str(item['executedQty'])) if float(item['executedQty']) > 0 else 0,
                price=rule.price_to_units(str(order_price)) if float(order_price) > 0 else 0,
            )

            orders.append(order)

        # Call algo orders
        try:
            algo_response, code, _can_repeat, err = self._auth_request('/fapi/v1/openAlgoOrders', data=data)
            if err is not None:
                raise err
            self._raise_on_bad_code(int(code), algo_response, 'get_open_orders/openAlgoOrders')
        except Exception as e:
            self.logger.error(f'Failed to get open algo orders: {e}, data: {json.dumps(data, indent=4)}')
            raise e

        for item in algo_response:
            sym = item['symbol']

            if sym not in self.rules:
                raise RuntimeError(f'get_open_orders: rule not found for symbol: {sym}')

            rule = self.rules[sym]

            if item['side'] == 'BUY':
                order_side = OrderSide.BUY
            elif item['side'] == 'SELL':
                order_side = OrderSide.SELL
            else:
                raise RuntimeError(f'Bad order side: {item["side"]}')

            raw_type = item['orderType']

            if raw_type == 'STOP_MARKET':
                if item['closePosition']:
                    order_type = OrderType.STOPLOSS
                else:
                    order_type = OrderType.STOP_MARKET

            elif raw_type == 'TAKE_PROFIT_MARKET' or raw_type == 'TAKE_PROFIT':
                order_type = OrderType.TAKE_PROFIT

            else:
                raise RuntimeError(f'Bad algo order type: {raw_type}')

            order = Order(
                symbol=sym,
                order_id=str(item['algoId']),
                order_side=order_side,
                order_type=order_type,
                base_volume=rule.volume_to_units(str(item['quantity'])) if float(item['quantity']) > 0 else 0,
                filled_volume=0,
                price=rule.price_to_units(str(item['triggerPrice'])) if float(item['triggerPrice']) > 0 else 0,
            )

            orders.append(order)

        return orders

    def _get_account(self) -> dict:
        try:
            response, code, _can_repeat, err = self._auth_request('/fapi/v3/account')
            if err is not None:
                raise err
            self._raise_on_bad_code(int(code), response, '_get_account')
        except Exception as e:
            self.logger.error(f'Failed to get account: {e}')
            raise e

        return response

    def get_account(self) -> AccountData:
        # Account
        account_response = self._get_account()

        # Balances
        balances = []

        for item in account_response['assets']:
            balance = Balance(
                asset=item['asset'],
                free=item['availableBalance'],
                locked=item['marginBalance'],
            )

            balances.append(balance)

        # Positions
        positions: Dict[str, Position] = {}
        
        for item in account_response['positions']:
            if float(item['positionAmt']) == 0:
                continue

            position = Position(
                symbol=item['symbol'],
                base_volume=item['positionAmt'],
                quote_volume=item['notional'],
                unrealized_pnl=item['unrealizedProfit'],
            )

            positions[position.symbol] = position
        
        # Open orders
        open_orders: List[Order] = self.get_open_orders()

        # Distribute open orders
        for order in open_orders:
            # Create if not exists
            if order.symbol not in positions:
                position = Position(
                    symbol=order.symbol,
                    base_volume='0',
                    quote_volume='0',
                    unrealized_pnl='0',
                )

                positions[position.symbol] = position

            # Add order to position
            positions[order.symbol].open_orders.append(order)

        return AccountData(
            positions=list(positions.values()),
            balances=balances,
        )

    def get_user_trades(self, symbol: str, order_id: Optional[int] = None, from_id: Optional[int] = None) -> List[Fill]:
        if symbol not in self.rules:
            raise RuntimeError(f'get_user_trades: rule not found for symbol: {symbol}')

        rule = self.rules[symbol]

        # Data
        data = {
            'symbol': symbol,
            'limit': 1000,
        }

        if from_id is not None:
            data['fromId'] = from_id

        if order_id is not None:
            data['orderId'] = order_id

        # Call
        try:
            response, code, _can_repeat, err = self._auth_request('/fapi/v1/userTrades', data=data)
            if err is not None:
                raise err
            self._raise_on_bad_code(int(code), response, 'get_user_trades')
        except Exception as e:
            self.logger.error(f'Failed to get user trades: {e}, data: {json.dumps(data, indent=4)}')
            raise e

        # Fills
        fills = []

        for item in response:
            fill = Fill(
                symbol=item['symbol'],
                order_id=str(item['orderId']),
                trade_id=str(item['id']),
                time_ms=item['time'],
                arrived_ms=int(time.time() * 1000),
                price=rule.price_to_units(str(item['price'])),
                base_volume=rule.volume_to_units(str(item['qty'])),
                from_api=True,
                side=OrderSide(item['side']),
                is_filled=False,
            )

            fills.append(fill)

        return fills

    @property
    def user_trades_limit(self) -> int:
        return 1000