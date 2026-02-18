#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional

from pymongo import MongoClient  # type: ignore[import-not-found]


HEDGER_RUNS_COLLECTION = "backend_hedger_runs"
ACTIVE_POSITIONS_COLLECTION = "backend_positions_active"
ARCHIVE_POSITIONS_COLLECTION = "backend_positions_archive"


@dataclass
class IterationView:
    iteration_no: int
    row_id: str
    status: str
    close_reason: str
    close_trigger_side: str
    base_price: float
    valuation_price: float
    price_move_pct: float
    mint_base: float
    mint_quote: float
    decrease_base: float
    decrease_quote: float
    fees_quote: float
    il_base_delta: float
    il_quote_delta: float
    il_quote: float
    cex_quote: float
    gas_paid_quote: float
    swap_cost_quote: float
    costs_pnl_quote: float
    pnl_fees_il_quote: float
    pnl_fees_il_gas_quote: float
    pnl_fees_il_gas_cex_quote: float
    hold_pnl_vs_start: float
    lp_pnl_vs_start: float
    il_check_lp_minus_hold: float
    start_value_at_base: float
    hold_value_at_close: float
    lp_value_at_close: float
    il_from_row: Optional[float]
    il_diff_vs_row: Optional[float]


def _require_dict(v: object, name: str) -> Dict[str, object]:
    if not isinstance(v, dict):
        raise RuntimeError(f"expected dict for {name}, got {type(v)}")
    return v


def _safe_float(v: object, default: float = 0.0) -> float:
    if v is None:
        return float(default)
    return float(v)


def _calc_valuation_price(stats: Dict[str, object]) -> float:
    live = _require_dict(stats.get("live"), "stats.live")
    snap = _require_dict(live.get("last_snapshot"), "stats.live.last_snapshot")
    symbol_rule = _require_dict(snap.get("symbol_rule"), "stats.live.last_snapshot.symbol_rule")
    metrics = _require_dict(snap.get("metrics"), "stats.live.last_snapshot.metrics")

    price_step_raw = str(symbol_rule.get("price_step", "")).strip()
    if len(price_step_raw) == 0:
        raise RuntimeError("symbol_rule.price_step is empty")
    try:
        price_step = Decimal(price_step_raw)
    except InvalidOperation as exc:
        raise RuntimeError(f"invalid price_step: {price_step_raw}") from exc
    if price_step <= 0:
        raise RuntimeError(f"bad price_step: {price_step_raw}")

    last_mid_price_units = int(metrics.get("last_mid_price_units", 0))
    if int(last_mid_price_units) <= 0:
        raise RuntimeError(f"bad last_mid_price_units: {last_mid_price_units}")

    valuation_price = float(Decimal(int(last_mid_price_units)) * price_step)
    if float(valuation_price) <= 0:
        raise RuntimeError(f"bad valuation_price: {valuation_price}")
    return float(valuation_price)


def _calc_cex_quote(calc: Dict[str, object], stats: Dict[str, object]) -> float:
    live = _require_dict(stats.get("live"), "stats.live")
    snap = _require_dict(live.get("last_snapshot"), "stats.live.last_snapshot")
    metrics = _require_dict(snap.get("metrics"), "stats.live.last_snapshot.metrics")
    chases = metrics.get("chases")
    if not isinstance(chases, list):
        raise RuntimeError("metrics.chases missing or invalid")

    open_filled_quote_units = 0
    for chase in chases:
        if not isinstance(chase, dict):
            continue
        if (
            str(chase.get("kind")) == "open"
            and bool(chase.get("ok"))
            and int(chase.get("filled_quote_units", 0)) > 0
        ):
            open_filled_quote_units = int(chase.get("filled_quote_units", 0))
            break
    if int(open_filled_quote_units) <= 0:
        raise RuntimeError("open_filled_quote_units not found")

    hedge_quote = float(calc.get("hedge_quote"))
    quote_per_cex_unit = float(hedge_quote) / float(open_filled_quote_units)
    if float(quote_per_cex_unit) <= 0:
        raise RuntimeError(f"bad quote_per_cex_unit: {quote_per_cex_unit}")

    cex_units = int(metrics.get("realized_pnl_quote_units", 0)) + int(
        metrics.get("unrealized_pnl_quote_units", 0)
    )
    return float(cex_units) * float(quote_per_cex_unit)


def _build_iteration_view(item: Dict[str, object]) -> IterationView:
    stats = _require_dict(item.get("stats"), "stats")
    calc = _require_dict(stats.get("calc"), "stats.calc")
    uniswap = _require_dict(stats.get("uniswap"), "stats.uniswap")
    mint = _require_dict(uniswap.get("mint"), "uniswap.mint")
    decrease = _require_dict(uniswap.get("decrease"), "uniswap.decrease")
    collect = _require_dict(uniswap.get("collect"), "uniswap.collect")

    base_price = float(calc.get("base_price"))
    valuation_price = _calc_valuation_price(stats)
    price_move_pct = 0.0
    if float(base_price) > 0:
        price_move_pct = (float(valuation_price) / float(base_price) - 1.0) * 100.0

    mint_base = _safe_float(mint.get("amount_base"))
    mint_quote = _safe_float(mint.get("amount_quote"))
    decrease_base = _safe_float(decrease.get("amount_base"))
    decrease_quote = _safe_float(decrease.get("amount_quote"))
    collect_base = _safe_float(collect.get("amount_base"))
    collect_quote = _safe_float(collect.get("amount_quote"))

    il_base_delta = float(decrease_base) - float(mint_base)
    il_quote_delta = float(decrease_quote) - float(mint_quote)
    il_quote = float(il_base_delta) * float(valuation_price) + float(il_quote_delta)

    fees_quote = (float(collect_quote) - float(decrease_quote)) + (
        (float(collect_base) - float(decrease_base)) * float(valuation_price)
    )

    gas_paid_eth = (
        _safe_float(mint.get("gas_cost_eth"))
        + _safe_float(decrease.get("gas_cost_eth"))
        + _safe_float(collect.get("gas_cost_eth"))
    )
    gas_paid_quote = float(gas_paid_eth) * float(valuation_price)

    swap_cost_quote = _safe_float(item.get("swap_cost_quote"))
    costs_abs_quote = float(gas_paid_quote) + float(swap_cost_quote)
    costs_pnl_quote = -float(costs_abs_quote)

    cex_quote = _calc_cex_quote(calc, stats)

    pnl_fees_il_quote = float(fees_quote) + float(il_quote)
    pnl_fees_il_gas_quote = float(pnl_fees_il_quote) + float(costs_pnl_quote)
    pnl_fees_il_gas_cex_quote = float(pnl_fees_il_gas_quote) + float(cex_quote)

    # Notebook-like decomposition:
    # start value (at start price), hold value (initial amounts at close), LP close value.
    start_value_at_base = float(mint_base) * float(base_price) + float(mint_quote)
    hold_value_at_close = float(mint_base) * float(valuation_price) + float(mint_quote)
    lp_value_at_close = float(decrease_base) * float(valuation_price) + float(decrease_quote)
    hold_pnl_vs_start = float(hold_value_at_close) - float(start_value_at_base)
    lp_pnl_vs_start = float(lp_value_at_close) - float(start_value_at_base)
    il_check_lp_minus_hold = float(lp_value_at_close) - float(hold_value_at_close)

    il_from_row: Optional[float] = None
    il_diff_vs_row: Optional[float] = None
    pnl_obj = item.get("pnl")
    if isinstance(pnl_obj, dict) and pnl_obj.get("dex_realized_il_quote") is not None:
        il_from_row = float(pnl_obj.get("dex_realized_il_quote"))
        il_diff_vs_row = float(il_quote) - float(il_from_row)

    return IterationView(
        iteration_no=int(item.get("iteration_no", 0)),
        row_id=str(item.get("id", "")),
        status=str(item.get("status", "")),
        close_reason=str(item.get("close_reason", "")),
        close_trigger_side=str(item.get("close_trigger_side", "")),
        base_price=float(base_price),
        valuation_price=float(valuation_price),
        price_move_pct=float(price_move_pct),
        mint_base=float(mint_base),
        mint_quote=float(mint_quote),
        decrease_base=float(decrease_base),
        decrease_quote=float(decrease_quote),
        fees_quote=float(fees_quote),
        il_base_delta=float(il_base_delta),
        il_quote_delta=float(il_quote_delta),
        il_quote=float(il_quote),
        cex_quote=float(cex_quote),
        gas_paid_quote=float(gas_paid_quote),
        swap_cost_quote=float(swap_cost_quote),
        costs_pnl_quote=float(costs_pnl_quote),
        pnl_fees_il_quote=float(pnl_fees_il_quote),
        pnl_fees_il_gas_quote=float(pnl_fees_il_gas_quote),
        pnl_fees_il_gas_cex_quote=float(pnl_fees_il_gas_cex_quote),
        hold_pnl_vs_start=float(hold_pnl_vs_start),
        lp_pnl_vs_start=float(lp_pnl_vs_start),
        il_check_lp_minus_hold=float(il_check_lp_minus_hold),
        start_value_at_base=float(start_value_at_base),
        hold_value_at_close=float(hold_value_at_close),
        lp_value_at_close=float(lp_value_at_close),
        il_from_row=il_from_row,
        il_diff_vs_row=il_diff_vs_row,
    )


def _fmt(v: Optional[float], digits: int = 6) -> str:
    if v is None:
        return "n/a"
    return f"{float(v):.{digits}f}"


def _find_run_position(db, run_id: str) -> Optional[Dict[str, object]]:
    for collection in (ACTIVE_POSITIONS_COLLECTION, ARCHIVE_POSITIONS_COLLECTION):
        doc = db[collection].find_one({"run_id": str(run_id)}, {"_id": 0})
        if isinstance(doc, dict):
            return doc
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Explain negative IL for upper triggers from Mongo using frontend math."
    )
    parser.add_argument(
        "--run-id",
        default="70a691194196486eabd454b13f3b4661",
        help="Run ID to inspect",
    )
    parser.add_argument(
        "--mongo-uri",
        default=os.getenv("MONGO_URI", "mongodb://172.18.0.3:27017"),
        help="Mongo URI",
    )
    parser.add_argument(
        "--mongo-db",
        default=os.getenv("MONGO_DB", "hedging"),
        help="Mongo DB name",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    client = MongoClient(str(args.mongo_uri), serverSelectionTimeoutMS=5000)
    _ = client.server_info()
    db = client[str(args.mongo_db)]

    run_id = str(args.run_id)
    docs = list(
        db[HEDGER_RUNS_COLLECTION]
        .find({"run_id": run_id}, {"_id": 0})
        .sort([("iteration_no", -1)])
    )
    if len(docs) == 0:
        raise RuntimeError(f"no iterations found in {HEDGER_RUNS_COLLECTION} for run_id={run_id}")

    pos_doc = _find_run_position(db, run_id)
    print("=== RUN ===")
    print(f"run_id={run_id}")
    print(f"mongo_uri={args.mongo_uri}")
    print(f"mongo_db={args.mongo_db}")
    print(f"iterations_loaded={len(docs)}")
    if isinstance(pos_doc, dict):
        position = pos_doc.get("position")
        if isinstance(position, dict):
            print(
                "position: "
                f"status={position.get('status')} "
                f"market_price={_fmt(_safe_float(position.get('market_price'), 0.0), 6)} "
                f"total_quote={_fmt(_safe_float(position.get('total_quote'), 0.0), 6)}"
            )
    print("")

    views: List[IterationView] = []
    for item in docs:
        if not isinstance(item, dict):
            continue
        views.append(_build_iteration_view(item))

    upper_rows = [v for v in views if v.close_trigger_side == "upper"]
    upper_negative_il = [v for v in upper_rows if float(v.il_quote) < 0.0]

    print("=== QUICK CHECK ===")
    print(f"upper_iterations={len(upper_rows)}")
    print(f"upper_with_negative_il={len(upper_negative_il)}")
    print("")

    print("=== PER ITERATION (frontend math + notebook-style decomposition) ===")
    for v in views:
        lp_vs_start_pct = 0.0
        hold_vs_start_pct = 0.0
        if float(v.start_value_at_base) > 0:
            lp_vs_start_pct = (float(v.lp_pnl_vs_start) / float(v.start_value_at_base)) * 100.0
            hold_vs_start_pct = (float(v.hold_pnl_vs_start) / float(v.start_value_at_base)) * 100.0

        print(
            f"[iter={v.iteration_no}] side={v.close_trigger_side} reason={v.close_reason} status={v.status} "
            f"base={_fmt(v.base_price, 6)} close={_fmt(v.valuation_price, 6)} move={_fmt(v.price_move_pct, 4)}%"
        )
        print(
            "  frontend: "
            f"fees={_fmt(v.fees_quote, 6)} "
            f"il={_fmt(v.il_quote, 6)} "
            f"fees+il={_fmt(v.pnl_fees_il_quote, 6)} "
            f"costs={_fmt(v.costs_pnl_quote, 6)} "
            f"cex={_fmt(v.cex_quote, 6)} "
            f"total={_fmt(v.pnl_fees_il_gas_cex_quote, 6)}"
        )
        print(
            "  deltas:   "
            f"il_base_delta={_fmt(v.il_base_delta, 6)} "
            f"il_quote_delta={_fmt(v.il_quote_delta, 6)} "
            f"gas_paid_quote={_fmt(v.gas_paid_quote, 6)} "
            f"swap_cost_quote={_fmt(v.swap_cost_quote, 6)}"
        )
        print(
            "  notebook-like: "
            f"hold_vs_start={_fmt(v.hold_pnl_vs_start, 6)} ({_fmt(hold_vs_start_pct, 4)}%) "
            f"lp_vs_start={_fmt(v.lp_pnl_vs_start, 6)} ({_fmt(lp_vs_start_pct, 4)}%) "
            f"il_check(lp-hold)={_fmt(v.il_check_lp_minus_hold, 6)}"
        )
        if v.il_from_row is not None:
            print(
                "  row_check: "
                f"row.dex_realized_il_quote={_fmt(v.il_from_row, 6)} "
                f"diff_calc-row={_fmt(v.il_diff_vs_row, 10)}"
            )
        print("")

    if len(upper_negative_il) > 0:
        print("=== WHY NEGATIVE IL ON UPPER (from data) ===")
        for v in upper_negative_il:
            print(
                f"iter={v.iteration_no}: "
                f"il_base_delta={_fmt(v.il_base_delta, 6)} * close_price={_fmt(v.valuation_price, 6)} "
                f"+ il_quote_delta={_fmt(v.il_quote_delta, 6)} => il={_fmt(v.il_quote, 6)}"
            )
        print("")

    print("=== CONCLUSION ===")
    print("close_trigger_side is determined only by boundary hit, not by IL sign.")
    print("frontend IL is dex_realized_il_quote = (decrease_base-mint_base)*valuation_price + (decrease_quote-mint_quote).")
    print("notebook LP-vs-start can be positive while IL (LP-vs-Hold-at-close) is negative.")


if __name__ == "__main__":
    main()
