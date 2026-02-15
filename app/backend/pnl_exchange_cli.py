"""
Binance futures trades realized PnL report
Date: 2026-02-15
Version: 1.0
"""
import argparse, os, time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceRequestException

from live.lib.logger import get_logger
from live.lib.strict_model import StrictModel


### Models ###
class ExchangeTrade(StrictModel):
    trade_id: int
    order_id: int
    symbol: str
    side: str
    position_side: str
    price: float
    qty: float
    quote_qty: float
    realized_pnl_quote: float
    commission: float
    commission_asset: str
    commission_quote: float
    net_pnl_quote: float
    time_ms: int


class PnlBreakdown(StrictModel):
    realized_quote: float
    commissions_quote: float
    net_quote: float


class VolumeBreakdown(StrictModel):
    buy_qty: float
    sell_qty: float
    turnover_quote: float


class ExchangeReport(StrictModel):
    symbol: str
    quote_asset: str
    window_days: int
    start_ms: int
    end_ms: int
    trades_total: int
    pnl: PnlBreakdown
    volume: VolumeBreakdown
    trades: List[ExchangeTrade]


### CLI ###
def parse_args():
    p = argparse.ArgumentParser()

    p.add_argument('--symbol', required=True)
    p.add_argument('--days', type=int, required=True)
    p.add_argument('--limit', type=int, default=1000)
    p.add_argument('--recv-window', type=int, default=5000)

    p.add_argument('--show-trades', action='store_true')
    p.add_argument('--max-trades-print', type=int, default=200)

    return p.parse_args()


### Helpers ###
def _to_float(v, field_name: str) -> float:
    if isinstance(v, bool):
        raise RuntimeError(f'{field_name} cannot be bool')

    try:
        return float(v)
    except Exception as exc:
        raise RuntimeError(f'failed to parse {field_name} as float: {v}') from exc


def _to_int(v, field_name: str) -> int:
    if isinstance(v, bool):
        raise RuntimeError(f'{field_name} cannot be bool')

    try:
        return int(v)
    except Exception as exc:
        raise RuntimeError(f'failed to parse {field_name} as int: {v}') from exc


def _now_ms() -> int:
    return int(time.time() * 1000)


def _ms_to_iso_utc(ts_ms: int) -> str:
    ts_s = float(int(ts_ms)) / 1000.0
    return datetime.fromtimestamp(ts_s, tz=timezone.utc).isoformat()


def _read_price_from_ticker(raw, symbol: str) -> float:
    if not isinstance(raw, dict):
        raise RuntimeError(f'ticker response is not dict for {symbol}: {type(raw)}')
    if 'price' not in raw:
        raise RuntimeError(f'ticker response has no price for {symbol}')

    return _to_float(raw['price'], f'{symbol}.price')


def _get_futures_quote_asset(client: Client, symbol: str) -> str:
    raw_info = client.futures_exchange_info()
    if not isinstance(raw_info, dict):
        raise RuntimeError(f'futures_exchange_info response is not dict: {type(raw_info)}')
    if 'symbols' not in raw_info:
        raise RuntimeError('futures_exchange_info response has no symbols')

    symbols = raw_info['symbols']
    if not isinstance(symbols, list):
        raise RuntimeError(f'futures_exchange_info symbols is not list: {type(symbols)}')

    symbol_up = str(symbol).upper()
    for raw_symbol in symbols:
        if not isinstance(raw_symbol, dict):
            continue

        if str(raw_symbol.get('symbol', '')).upper() != symbol_up:
            continue

        if 'quoteAsset' not in raw_symbol:
            raise RuntimeError(f'futures symbol has no quoteAsset: {symbol_up}')

        quote_asset = str(raw_symbol['quoteAsset']).upper()
        if len(quote_asset) == 0:
            raise RuntimeError(f'futures symbol quoteAsset is empty: {symbol_up}')
        return quote_asset

    raise RuntimeError(f'symbol is not available on Binance futures: {symbol_up}')


def _get_futures_symbol_price(client: Client, symbol: str, logger) -> Optional[float]:
    try:
        raw_futures = client.futures_symbol_ticker(symbol=symbol)
        return float(_read_price_from_ticker(raw_futures, symbol))
    except BinanceAPIException as exc:
        logger.debug(f'futures ticker unavailable for {symbol}: {exc}')
        return None
    except BinanceRequestException:
        raise

    return None


def _commission_to_quote(
    client: Client,
    logger,
    commission_amount: float,
    commission_asset: str,
    quote_asset: str,
    fx_cache: Dict[str, float],
) -> float:
    amount = abs(float(commission_amount))
    asset = str(commission_asset).upper()
    quote = str(quote_asset).upper()

    if amount == 0.0:
        return 0.0
    if asset == quote:
        return float(amount)

    cache_key = f'{asset}->{quote}'
    if cache_key in fx_cache:
        return float(amount) * float(fx_cache[cache_key])

    direct_symbol = f'{asset}{quote}'
    direct_price = _get_futures_symbol_price(client=client, symbol=str(direct_symbol), logger=logger)
    if direct_price is not None:
        fx_cache[cache_key] = float(direct_price)
        return float(amount) * float(direct_price)

    inverse_symbol = f'{quote}{asset}'
    inverse_price = _get_futures_symbol_price(client=client, symbol=str(inverse_symbol), logger=logger)
    if inverse_price is not None:
        if float(inverse_price) <= 0.0:
            raise RuntimeError(f'invalid inverse FX price for {inverse_symbol}: {inverse_price}')

        fx_rate = 1.0 / float(inverse_price)
        fx_cache[cache_key] = float(fx_rate)
        return float(amount) * float(fx_rate)

    raise RuntimeError(f'failed to convert commission asset to quote asset: {asset}->{quote}')


def _required_field(raw: Dict, field_name: str):
    if field_name not in raw:
        raise RuntimeError(f'trade field is missing: {field_name}')
    return raw[field_name]


def _parse_trade(
    raw: Dict,
    quote_asset: str,
    client: Client,
    logger,
    fx_cache: Dict[str, float],
) -> ExchangeTrade:
    trade_id = _to_int(_required_field(raw, 'id'), 'id')
    order_id = _to_int(_required_field(raw, 'orderId'), 'orderId')
    symbol = str(_required_field(raw, 'symbol')).upper()
    side = str(_required_field(raw, 'side')).upper()
    position_side = str(raw.get('positionSide', 'BOTH')).upper()
    price = _to_float(_required_field(raw, 'price'), 'price')
    qty = _to_float(_required_field(raw, 'qty'), 'qty')

    if 'quoteQty' in raw:
        quote_qty = _to_float(raw['quoteQty'], 'quoteQty')
    else:
        quote_qty = float(price) * float(qty)

    realized_pnl_quote = _to_float(_required_field(raw, 'realizedPnl'), 'realizedPnl')
    commission = abs(_to_float(_required_field(raw, 'commission'), 'commission'))
    commission_asset = str(_required_field(raw, 'commissionAsset')).upper()
    time_ms = _to_int(_required_field(raw, 'time'), 'time')

    commission_quote = _commission_to_quote(
        client=client,
        logger=logger,
        commission_amount=float(commission),
        commission_asset=str(commission_asset),
        quote_asset=str(quote_asset),
        fx_cache=fx_cache,
    )
    net_pnl_quote = float(realized_pnl_quote) - float(commission_quote)

    return ExchangeTrade(
        trade_id=int(trade_id),
        order_id=int(order_id),
        symbol=str(symbol),
        side=str(side),
        position_side=str(position_side),
        price=float(price),
        qty=float(qty),
        quote_qty=float(quote_qty),
        realized_pnl_quote=float(realized_pnl_quote),
        commission=float(commission),
        commission_asset=str(commission_asset),
        commission_quote=float(commission_quote),
        net_pnl_quote=float(net_pnl_quote),
        time_ms=int(time_ms),
    )


def _fetch_futures_account_trades(
    client: Client,
    logger,
    symbol: str,
    start_ms: int,
    end_ms: int,
    limit: int,
    recv_window: int,
) -> List[Dict]:
    if int(start_ms) <= 0:
        raise RuntimeError(f'start_ms must be > 0: {start_ms}')
    if int(end_ms) <= int(start_ms):
        raise RuntimeError(f'end_ms must be > start_ms: start={start_ms} end={end_ms}')
    if int(limit) <= 0 or int(limit) > 1000:
        raise RuntimeError(f'limit must be in [1..1000]: {limit}')

    out: List[Dict] = []
    seen_trade_ids = set()
    cursor_ms = int(start_ms)
    n_pages = 0

    while int(cursor_ms) <= int(end_ms):
        n_pages += 1
        if int(n_pages) > 10000:
            raise RuntimeError('too many pages while fetching trades, aborting')

        raw_page = client.futures_account_trades(
            symbol=str(symbol),
            startTime=int(cursor_ms),
            endTime=int(end_ms),
            limit=int(limit),
            recvWindow=int(recv_window),
        )

        if not isinstance(raw_page, list):
            raise RuntimeError(f'futures_account_trades response is not list: {type(raw_page)}')
        if len(raw_page) == 0:
            break

        page = sorted(raw_page, key=lambda item: (int(item['time']), int(item['id'])))
        new_items = 0
        for item in page:
            if not isinstance(item, dict):
                raise RuntimeError(f'trade item is not dict: {type(item)}')
            if 'id' not in item:
                raise RuntimeError('trade item has no id')

            trade_id = int(item['id'])
            if int(trade_id) in seen_trade_ids:
                continue

            seen_trade_ids.add(int(trade_id))
            out.append(item)
            new_items += 1

        page_last_ms = _to_int(_required_field(page[-1], 'time'), 'time')
        logger.info(
            f'page={n_pages} cursor_ms={cursor_ms} fetched={len(page)} new={new_items} last_ms={page_last_ms} total={len(out)}'
        )

        if len(page) < int(limit):
            break

        next_cursor = int(page_last_ms) + 1
        if int(next_cursor) <= int(cursor_ms):
            raise RuntimeError(f'pagination did not advance: cursor={cursor_ms} next_cursor={next_cursor}')

        cursor_ms = int(next_cursor)

    out_sorted = sorted(out, key=lambda item: (int(item['time']), int(item['id'])))
    return out_sorted


def _build_report(client: Client, args, logger) -> ExchangeReport:
    symbol = str(args.symbol).upper()
    days = int(args.days)
    limit = int(args.limit)
    recv_window = int(args.recv_window)

    if len(symbol) == 0:
        raise RuntimeError('symbol is empty')
    if int(days) <= 0:
        raise RuntimeError(f'days must be > 0: {days}')
    if int(limit) <= 0 or int(limit) > 1000:
        raise RuntimeError(f'limit must be in [1..1000]: {limit}')
    if int(recv_window) <= 0:
        raise RuntimeError(f'recv_window must be > 0: {recv_window}')

    quote_asset = _get_futures_quote_asset(client=client, symbol=str(symbol))
    end_ms = _now_ms()
    start_ms = int(end_ms) - int(days) * 24 * 60 * 60 * 1000

    raw_trades = _fetch_futures_account_trades(
        client=client,
        logger=logger,
        symbol=str(symbol),
        start_ms=int(start_ms),
        end_ms=int(end_ms),
        limit=int(limit),
        recv_window=int(recv_window),
    )

    fx_cache: Dict[str, float] = {}
    trades: List[ExchangeTrade] = []
    for raw_trade in raw_trades:
        trade = _parse_trade(
            raw=raw_trade,
            quote_asset=str(quote_asset),
            client=client,
            logger=logger,
            fx_cache=fx_cache,
        )
        trades.append(trade)

    realized_quote = 0.0
    commissions_quote = 0.0
    net_quote = 0.0
    buy_qty = 0.0
    sell_qty = 0.0
    turnover_quote = 0.0

    for trade in trades:
        realized_quote += float(trade.realized_pnl_quote)
        commissions_quote += float(trade.commission_quote)
        net_quote += float(trade.net_pnl_quote)
        turnover_quote += abs(float(trade.quote_qty))

        if str(trade.side).upper() == 'BUY':
            buy_qty += float(trade.qty)
        elif str(trade.side).upper() == 'SELL':
            sell_qty += float(trade.qty)

    return ExchangeReport(
        symbol=str(symbol),
        quote_asset=str(quote_asset),
        window_days=int(days),
        start_ms=int(start_ms),
        end_ms=int(end_ms),
        trades_total=len(trades),
        pnl=PnlBreakdown(
            realized_quote=float(realized_quote),
            commissions_quote=float(commissions_quote),
            net_quote=float(net_quote),
        ),
        volume=VolumeBreakdown(
            buy_qty=float(buy_qty),
            sell_qty=float(sell_qty),
            turnover_quote=float(turnover_quote),
        ),
        trades=trades,
    )


### Main ###
def main() -> None:
    args = parse_args()
    logger = get_logger('exchange_cli')

    if 'BINANCE_KEY' not in os.environ:
        raise RuntimeError('BINANCE_KEY not found in environment variables')
    if 'BINANCE_SECRET' not in os.environ:
        raise RuntimeError('BINANCE_SECRET not found in environment variables')

    binance_key = os.environ['BINANCE_KEY']
    binance_secret = os.environ['BINANCE_SECRET']

    client = Client(api_key=str(binance_key), api_secret=str(binance_secret))

    try:
        client.futures_ping()
    except Exception as exc:
        raise RuntimeError(f'binance futures ping failed: {exc}') from exc

    try:
        report = _build_report(client=client, args=args, logger=logger)
    except (BinanceAPIException, BinanceRequestException) as exc:
        raise RuntimeError(f'binance request failed: {exc}') from exc

    print('=== BINANCE TRADES REPORT ===')
    print(f'Symbol: {report.symbol}')
    print(f'Quote asset: {report.quote_asset}')
    print(f'Window (days): {report.window_days}')
    print(f'From (UTC): {_ms_to_iso_utc(report.start_ms)}')
    print(f'To   (UTC): {_ms_to_iso_utc(report.end_ms)}')
    print(f'Trades total: {report.trades_total}')
    print('')

    print('=== SUMMARY ===')
    print(f'Realized PnL ({report.quote_asset}): {report.pnl.realized_quote:.8f}')
    print(f'Commissions  ({report.quote_asset}): {report.pnl.commissions_quote:.8f}')
    print(f'Net PnL      ({report.quote_asset}): {report.pnl.net_quote:.8f}')
    print('')
    print(f'BUY qty:  {report.volume.buy_qty:.8f}')
    print(f'SELL qty: {report.volume.sell_qty:.8f}')
    print(f'Turnover ({report.quote_asset}): {report.volume.turnover_quote:.8f}')

    if bool(args.show_trades):
        max_print = int(args.max_trades_print)
        if int(max_print) <= 0:
            raise RuntimeError(f'max_trades_print must be > 0: {max_print}')

        print('')
        print('=== TRADES ===')
        print_total = min(len(report.trades), int(max_print))
        idx = 0
        while idx < int(print_total):
            trade = report.trades[idx]
            print(f'[{idx + 1}] id={trade.trade_id} order_id={trade.order_id} ts={_ms_to_iso_utc(trade.time_ms)}')
            print(f'    side={trade.side} position_side={trade.position_side} price={trade.price:.8f} qty={trade.qty:.8f}')
            print(
                f'    realized={trade.realized_pnl_quote:.8f} '
                f'commission={trade.commission:.8f} {trade.commission_asset} '
                f'commission_quote={trade.commission_quote:.8f} '
                f'net={trade.net_pnl_quote:.8f}'
            )
            idx += 1

        hidden = len(report.trades) - int(print_total)
        if int(hidden) > 0:
            print(f'... skipped {hidden} trades, increase --max-trades-print to see more')


if __name__ == '__main__':
    main()
