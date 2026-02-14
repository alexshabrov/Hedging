from live.lib.strict_model import StrictModel
from live.logic.models import HedgeChaseKind
from backend.models.hedger_models import HedgerStats


class HedgerPnlStats(StrictModel):
    cex_pnl_quote: float
    dex_realized_il_quote: float
    fees_received_quote: float
    gas_paid_eth: float
    gas_paid_quote: float
    pool_hold_seconds: float
    apr_pct: float
    total_pnl_quote: float

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'cex_pnl_quote': float(self.cex_pnl_quote),
            'dex_realized_il_quote': float(self.dex_realized_il_quote),
            'fees_received_quote': float(self.fees_received_quote),
            'gas_paid_eth': float(self.gas_paid_eth),
            'gas_paid_quote': float(self.gas_paid_quote),
            'pool_hold_seconds': float(self.pool_hold_seconds),
            'apr_pct': float(self.apr_pct),
            'total_pnl_quote': float(self.total_pnl_quote),
        }


def _to_float(v, name):
    if v is None:
        raise RuntimeError(f'Hedger helper: {name} is None')
    return float(v)


def _to_optional_float(v) -> float:
    if v is None:
        return 0.0
    return float(v)


def _get_open_filled_quote_units(stats: HedgerStats) -> int:
    snap = stats.live.last_snapshot
    if snap is None:
        raise RuntimeError('Hedger helper: stats.live.last_snapshot is None')

    chases = snap.metrics.chases
    if chases is None:
        raise RuntimeError('Hedger helper: stats.live.last_snapshot.metrics.chases is None')

    for chase in chases:
        if chase is None:
            raise RuntimeError('Hedger helper: chase is None')

        if chase.kind == HedgeChaseKind.OPEN and bool(chase.ok) and int(chase.filled_quote_units) > 0:
            return int(chase.filled_quote_units)

    raise RuntimeError('Hedger helper: open chase with filled_quote_units > 0 not found')


def calc_hedger_pnl_stats(stats: HedgerStats) -> HedgerPnlStats:
    if stats is None:
        raise RuntimeError('Hedger helper: stats is None')
    if not isinstance(stats, HedgerStats):
        raise RuntimeError(f'Hedger helper: stats is not HedgerStats: {type(stats)}')

    mint = stats.uniswap.mint
    if mint is None:
        raise RuntimeError('Hedger helper: stats.uniswap.mint is None')

    decrease = stats.uniswap.decrease
    if decrease is None:
        raise RuntimeError('Hedger helper: stats.uniswap.decrease is None')

    collect = stats.uniswap.collect
    if collect is None:
        raise RuntimeError('Hedger helper: stats.uniswap.collect is None')

    snap = stats.live.last_snapshot
    if snap is None:
        raise RuntimeError('Hedger helper: stats.live.last_snapshot is None')

    base_price = _to_float(stats.calc.base_price, 'stats.calc.base_price')

    open_filled_quote_units = _get_open_filled_quote_units(stats)
    if int(open_filled_quote_units) <= 0:
        raise RuntimeError(f'Hedger helper: bad open_filled_quote_units: {open_filled_quote_units}')

    quote_per_cex_unit = float(stats.calc.hedge_quote) / float(open_filled_quote_units)
    if float(quote_per_cex_unit) <= 0:
        raise RuntimeError(f'Hedger helper: bad quote_per_cex_unit: {quote_per_cex_unit}')

    cex_pnl_quote_units = int(snap.metrics.realized_pnl_quote_units) + int(snap.metrics.unrealized_pnl_quote_units)
    cex_pnl_quote = float(cex_pnl_quote_units) * float(quote_per_cex_unit)

    mint_quote = _to_optional_float(mint.amount_quote)
    mint_base = _to_optional_float(mint.amount_base)
    decrease_quote = _to_optional_float(decrease.amount_quote)
    decrease_base = _to_optional_float(decrease.amount_base)
    collect_quote = _to_optional_float(collect.amount_quote)
    collect_base = _to_optional_float(collect.amount_base)

    dex_realized_il_quote = (float(decrease_quote) + float(decrease_base) * float(base_price)) - (float(mint_quote) + float(mint_base) * float(base_price))
    # collect_fees returns full owed tokens (including principal moved into owed after decrease).
    # Net fees are the part above decrease amounts.
    fees_received_quote = (
        (float(collect_quote) - float(decrease_quote))
        + (float(collect_base) - float(decrease_base)) * float(base_price)
    )

    gas_paid_eth = (
        _to_optional_float(mint.gas_cost_eth)
        + _to_optional_float(decrease.gas_cost_eth)
        + _to_optional_float(collect.gas_cost_eth)
    )
    gas_paid_quote = float(gas_paid_eth) * float(base_price)
    
    mint_tx_timestamp_ms = int(stats.uniswap.mint_tx_timestamp_ms)
    decrease_tx_timestamp_ms = int(stats.uniswap.decrease_tx_timestamp_ms)
    if int(mint_tx_timestamp_ms) <= 0:
        raise RuntimeError(f'Hedger helper: bad mint_tx_timestamp_ms: {mint_tx_timestamp_ms}')
    if int(decrease_tx_timestamp_ms) <= 0:
        raise RuntimeError(f'Hedger helper: bad decrease_tx_timestamp_ms: {decrease_tx_timestamp_ms}')
    if int(decrease_tx_timestamp_ms) <= int(mint_tx_timestamp_ms):
        raise RuntimeError(
            f'Hedger helper: bad tx hold time: mint_tx_timestamp_ms={mint_tx_timestamp_ms} decrease_tx_timestamp_ms={decrease_tx_timestamp_ms}'
        )
    
    pool_hold_seconds = float(int(decrease_tx_timestamp_ms) - int(mint_tx_timestamp_ms)) / 1000.0
    if float(pool_hold_seconds) <= 0:
        raise RuntimeError(f'Hedger helper: bad pool_hold_seconds: {pool_hold_seconds}')
    
    pool_total_quote = float(stats.calc.total_quote)
    if float(pool_total_quote) <= 0:
        raise RuntimeError(f'Hedger helper: bad total_quote: {pool_total_quote}')
    
    seconds_per_year = 365.0 * 24.0 * 60.0 * 60.0
    apr_pct = (float(fees_received_quote) / float(pool_total_quote)) * (float(seconds_per_year) / float(pool_hold_seconds)) * 100.0

    total_pnl_quote = float(cex_pnl_quote) + float(dex_realized_il_quote) + float(fees_received_quote) - float(gas_paid_quote)

    return HedgerPnlStats(
        cex_pnl_quote=float(cex_pnl_quote),
        dex_realized_il_quote=float(dex_realized_il_quote),
        fees_received_quote=float(fees_received_quote),
        gas_paid_eth=float(gas_paid_eth),
        gas_paid_quote=float(gas_paid_quote),
        pool_hold_seconds=float(pool_hold_seconds),
        apr_pct=float(apr_pct),
        total_pnl_quote=float(total_pnl_quote),
    )
