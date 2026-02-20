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
class IterationIlBreakdown:
    iteration_id: str
    iteration_no: int
    status: str
    close_reason: str
    opened_leg: str
    raw_close_trigger_side: str
    derived_close_trigger_side: str
    base_price: float
    price_lower: float
    price_upper: float
    valuation_price: float
    total_quote: float
    mint_base: float
    mint_quote: float
    decrease_base: float
    decrease_quote: float
    mint_value_at_base: float
    step_exit_value_at_valuation: float
    il_front_real_mint_formula: float
    il_old_total_quote_formula: float
    il_boundary_from_raw_side: Optional[float]
    il_boundary_from_derived_side: Optional[float]
    il_backend_saved: Optional[float]
    il_legacy_mint_delta_formula: float
    diff_front_real_mint_vs_boundary_derived: Optional[float]
    diff_front_real_mint_vs_backend_saved: Optional[float]


def _require_dict(value: object, name: str) -> Dict[str, object]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{name} is not dict: {type(value)}")
    return value


def _require_key(data: Dict[str, object], key: str, path: str) -> object:
    if key not in data:
        raise RuntimeError(f"{path}.{key} is missing")
    return data[key]


def _safe_optional_float(value: object) -> float:
    if value is None:
        return 0.0
    return float(value)


def _calc_valuation_price(stats: Dict[str, object]) -> float:
    live = _require_dict(_require_key(stats, "live", "stats"), "stats.live")
    snap = _require_dict(_require_key(live, "last_snapshot", "stats.live"), "stats.live.last_snapshot")
    symbol_rule = _require_dict(
        _require_key(snap, "symbol_rule", "stats.live.last_snapshot"),
        "stats.live.last_snapshot.symbol_rule",
    )
    metrics = _require_dict(
        _require_key(snap, "metrics", "stats.live.last_snapshot"),
        "stats.live.last_snapshot.metrics",
    )

    price_step_raw = str(_require_key(symbol_rule, "price_step", "stats.live.last_snapshot.symbol_rule")).strip()
    if len(price_step_raw) == 0:
        raise RuntimeError("symbol_rule.price_step is empty")
    try:
        price_step = Decimal(price_step_raw)
    except InvalidOperation as exc:
        raise RuntimeError(f"invalid price_step: {price_step_raw}") from exc
    if price_step <= 0:
        raise RuntimeError(f"bad price_step: {price_step_raw}")

    last_mid_price_units = int(_require_key(metrics, "last_mid_price_units", "stats.live.last_snapshot.metrics"))
    if int(last_mid_price_units) <= 0:
        raise RuntimeError(f"bad last_mid_price_units: {last_mid_price_units}")

    valuation_price = float(Decimal(int(last_mid_price_units)) * price_step)
    if float(valuation_price) <= 0.0:
        raise RuntimeError(f"bad valuation_price: {valuation_price}")
    return float(valuation_price)


def _derive_close_trigger_side_from_raw(stats: Dict[str, object]) -> Optional[str]:
    live = _require_dict(_require_key(stats, "live", "stats"), "stats.live")
    snap = _require_dict(_require_key(live, "last_snapshot", "stats.live"), "stats.live.last_snapshot")

    close_reason_raw = _require_key(snap, "close_reason", "stats.live.last_snapshot")
    if close_reason_raw is None:
        return None
    close_reason = str(close_reason_raw)

    if close_reason == "mock_upper":
        return "upper"
    if close_reason == "mock_lower":
        return "lower"

    if close_reason == "target":
        opened_leg_raw = _require_key(snap, "opened_leg", "stats.live.last_snapshot")
        if opened_leg_raw is None:
            raise RuntimeError("opened_leg is None for close_reason=target")
        opened_leg = str(opened_leg_raw)
        if opened_leg == "long":
            return "upper"
        if opened_leg == "short":
            return "lower"
        raise RuntimeError(f"unsupported opened_leg for target close_reason: {opened_leg}")

    if close_reason == "neutral":
        return None
    if close_reason == "forced":
        return None
    if close_reason == "manual_stop":
        return None
    if close_reason == "mock_closed":
        return None
    if close_reason == "mock_failed":
        return None

    raise RuntimeError(f"unsupported close_reason: {close_reason}")


def _calc_boundary_il(decrease_base: float, decrease_quote: float, total_quote: float, side: Optional[str], price_lower: float, price_upper: float) -> Optional[float]:
    if side is None:
        return None
    if side == "upper":
        return float(decrease_base) * float(price_upper) + float(decrease_quote) - float(total_quote)
    if side == "lower":
        return float(decrease_base) * float(price_lower) + float(decrease_quote) - float(total_quote)
    raise RuntimeError(f"bad side: {side}")


def _fmt(value: Optional[float], digits: int = 8) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.{digits}f}"


def _find_run_position(db, run_id: str) -> Optional[Dict[str, object]]:
    for collection in (ACTIVE_POSITIONS_COLLECTION, ARCHIVE_POSITIONS_COLLECTION):
        row = db[collection].find_one({"run_id": str(run_id)}, {"_id": 0})
        if isinstance(row, dict):
            return row
    return None


def _build_iteration_breakdown(doc: Dict[str, object]) -> IterationIlBreakdown:
    stats = _require_dict(_require_key(doc, "stats", "iteration"), "iteration.stats")
    calc = _require_dict(_require_key(stats, "calc", "iteration.stats"), "iteration.stats.calc")
    uniswap = _require_dict(_require_key(stats, "uniswap", "iteration.stats"), "iteration.stats.uniswap")
    live = _require_dict(_require_key(stats, "live", "iteration.stats"), "iteration.stats.live")
    snap = _require_dict(_require_key(live, "last_snapshot", "iteration.stats.live"), "iteration.stats.live.last_snapshot")

    mint = _require_dict(_require_key(uniswap, "mint", "iteration.stats.uniswap"), "iteration.stats.uniswap.mint")
    decrease = _require_dict(_require_key(uniswap, "decrease", "iteration.stats.uniswap"), "iteration.stats.uniswap.decrease")

    valuation_price = _calc_valuation_price(stats)

    base_price = float(_require_key(calc, "base_price", "iteration.stats.calc"))
    price_lower = float(_require_key(calc, "price_lower", "iteration.stats.calc"))
    price_upper = float(_require_key(calc, "price_upper", "iteration.stats.calc"))
    total_quote = float(_require_key(calc, "total_quote", "iteration.stats.calc"))
    if float(total_quote) <= 0.0:
        raise RuntimeError(f"bad total_quote: {total_quote}")

    mint_base = _safe_optional_float(_require_key(mint, "amount_base", "iteration.stats.uniswap.mint"))
    mint_quote = _safe_optional_float(_require_key(mint, "amount_quote", "iteration.stats.uniswap.mint"))
    decrease_base = _safe_optional_float(_require_key(decrease, "amount_base", "iteration.stats.uniswap.decrease"))
    decrease_quote = _safe_optional_float(_require_key(decrease, "amount_quote", "iteration.stats.uniswap.decrease"))

    mint_value_at_base = float(mint_base) * float(base_price) + float(mint_quote)
    step_exit_value = float(decrease_base) * float(valuation_price) + float(decrease_quote)
    il_front_real_mint_formula = float(step_exit_value) - float(mint_value_at_base)
    il_old_total_quote_formula = float(step_exit_value) - float(total_quote)

    raw_side = _require_key(doc, "close_trigger_side", "iteration")
    raw_side_str = "" if raw_side is None else str(raw_side)
    derived_side = _derive_close_trigger_side_from_raw(stats)

    il_boundary_from_raw_side = _calc_boundary_il(
        decrease_base=float(decrease_base),
        decrease_quote=float(decrease_quote),
        total_quote=float(total_quote),
        side=None if len(raw_side_str) == 0 else str(raw_side_str),
        price_lower=float(price_lower),
        price_upper=float(price_upper),
    )
    il_boundary_from_derived_side = _calc_boundary_il(
        decrease_base=float(decrease_base),
        decrease_quote=float(decrease_quote),
        total_quote=float(total_quote),
        side=derived_side,
        price_lower=float(price_lower),
        price_upper=float(price_upper),
    )

    il_backend_saved = None
    if "pnl" in doc and doc["pnl"] is not None:
        pnl = _require_dict(doc["pnl"], "iteration.pnl")
        dex_il_raw = _require_key(pnl, "dex_realized_il_quote", "iteration.pnl")
        if dex_il_raw is not None:
            il_backend_saved = float(dex_il_raw)

    il_legacy_mint_delta_formula = (
        (float(decrease_base) - float(mint_base)) * float(valuation_price)
        + (float(decrease_quote) - float(mint_quote))
    )

    diff_front_real_mint_vs_boundary_derived = None
    if il_boundary_from_derived_side is not None:
        diff_front_real_mint_vs_boundary_derived = float(il_front_real_mint_formula) - float(il_boundary_from_derived_side)

    diff_front_real_mint_vs_backend_saved = None
    if il_backend_saved is not None:
        diff_front_real_mint_vs_backend_saved = float(il_front_real_mint_formula) - float(il_backend_saved)

    close_reason_raw = _require_key(snap, "close_reason", "iteration.stats.live.last_snapshot")
    opened_leg_raw = _require_key(snap, "opened_leg", "iteration.stats.live.last_snapshot")

    return IterationIlBreakdown(
        iteration_id=str(_require_key(doc, "id", "iteration")),
        iteration_no=int(_require_key(doc, "iteration_no", "iteration")),
        status=str(_require_key(doc, "status", "iteration")),
        close_reason="" if close_reason_raw is None else str(close_reason_raw),
        opened_leg="" if opened_leg_raw is None else str(opened_leg_raw),
        raw_close_trigger_side=str(raw_side_str),
        derived_close_trigger_side="" if derived_side is None else str(derived_side),
        base_price=float(base_price),
        price_lower=float(price_lower),
        price_upper=float(price_upper),
        valuation_price=float(valuation_price),
        total_quote=float(total_quote),
        mint_base=float(mint_base),
        mint_quote=float(mint_quote),
        decrease_base=float(decrease_base),
        decrease_quote=float(decrease_quote),
        mint_value_at_base=float(mint_value_at_base),
        step_exit_value_at_valuation=float(step_exit_value),
        il_front_real_mint_formula=float(il_front_real_mint_formula),
        il_old_total_quote_formula=float(il_old_total_quote_formula),
        il_boundary_from_raw_side=il_boundary_from_raw_side,
        il_boundary_from_derived_side=il_boundary_from_derived_side,
        il_backend_saved=il_backend_saved,
        il_legacy_mint_delta_formula=float(il_legacy_mint_delta_formula),
        diff_front_real_mint_vs_boundary_derived=diff_front_real_mint_vs_boundary_derived,
        diff_front_real_mint_vs_backend_saved=diff_front_real_mint_vs_backend_saved,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reproduce IL mismatch from raw Mongo docs for one run/iteration.")
    parser.add_argument("--run-id", default="70a691194196486eabd454b13f3b4661", help="Run ID")
    parser.add_argument("--iteration-id", default="e52dc9e2b15e4a61b289a35bf0be7cd1", help="Iteration ID")
    parser.add_argument(
        "--mongo-uri",
        default=os.getenv("MONGO_URI", "mongodb://172.18.0.3:27017"),
        help="Mongo URI",
    )
    parser.add_argument(
        "--mongo-db",
        default=os.getenv("MONGO_DB", "hedging"),
        help="Mongo database",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    client = MongoClient(str(args.mongo_uri), serverSelectionTimeoutMS=5000)
    _ = client.server_info()
    db = client[str(args.mongo_db)]

    run_id = str(args.run_id)
    iteration_id = str(args.iteration_id)

    docs = list(
        db[HEDGER_RUNS_COLLECTION]
        .find({"run_id": run_id}, {"_id": 0})
        .sort([("iteration_no", 1)])
    )
    if len(docs) == 0:
        raise RuntimeError(f"no iteration docs for run_id={run_id}")

    target_doc = db[HEDGER_RUNS_COLLECTION].find_one({"id": iteration_id}, {"_id": 0})
    if target_doc is None:
        raise RuntimeError(f"iteration not found: {iteration_id}")
    if not isinstance(target_doc, dict):
        raise RuntimeError(f"iteration doc is not dict: {type(target_doc)}")
    if str(_require_key(target_doc, "run_id", "iteration")) != run_id:
        raise RuntimeError(
            f"iteration run mismatch: requested run_id={run_id}, iteration.run_id={target_doc['run_id']}"
        )

    position_doc = _find_run_position(db, run_id)

    print("=== TARGET ===")
    print(f"run_id={run_id}")
    print(f"iteration_id={iteration_id}")
    print(f"mongo_uri={args.mongo_uri}")
    print(f"mongo_db={args.mongo_db}")
    print(f"run_iterations={len(docs)}")
    if isinstance(position_doc, dict):
        position = _require_dict(_require_key(position_doc, "position", "position_doc"), "position_doc.position")
        print(
            "position: "
            f"status={_require_key(position, 'status', 'position_doc.position')} "
            f"market_price={_fmt(None if position['market_price'] is None else float(position['market_price']), 6)} "
            f"total_quote={_fmt(float(position['total_quote']), 6)}"
        )
    print("")

    target = _build_iteration_breakdown(target_doc)
    print("=== TARGET ITERATION BREAKDOWN ===")
    print(
        f"iter_no={target.iteration_no} status={target.status} "
        f"close_reason={target.close_reason or 'n/a'} opened_leg={target.opened_leg or 'n/a'} "
        f"raw_side={target.raw_close_trigger_side or 'n/a'} derived_side={target.derived_close_trigger_side or 'n/a'}"
    )
    print(
        f"prices: base={_fmt(target.base_price, 8)} lower={_fmt(target.price_lower, 8)} "
        f"upper={_fmt(target.price_upper, 8)} valuation={_fmt(target.valuation_price, 8)}"
    )
    print(
        f"amounts: mint_base={_fmt(target.mint_base, 8)} mint_quote={_fmt(target.mint_quote, 8)} "
        f"decrease_base={_fmt(target.decrease_base, 8)} decrease_quote={_fmt(target.decrease_quote, 8)} "
        f"total_quote={_fmt(target.total_quote, 8)}"
    )
    print(
        f"value check: mint_value_at_base={_fmt(target.mint_value_at_base, 8)} "
        f"step_exit_value={_fmt(target.step_exit_value_at_valuation, 8)}"
    )
    print("")
    print("realized_il candidates:")
    print(f"  frontend_current(step_exit@valuation-mint@base): {_fmt(target.il_front_real_mint_formula, 8)}")
    print(f"  old_total_quote_formula(step_exit-total_quote):  {_fmt(target.il_old_total_quote_formula, 8)}")
    print(f"  boundary_from_raw_side:                      {_fmt(target.il_boundary_from_raw_side, 8)}")
    print(f"  boundary_from_derived_side:                  {_fmt(target.il_boundary_from_derived_side, 8)}")
    print(f"  backend_saved_row.pnl.dex_realized_il_quote: {_fmt(target.il_backend_saved, 8)}")
    print(f"  legacy_mint_delta_formula:                   {_fmt(target.il_legacy_mint_delta_formula, 8)}")
    print("")
    print(f"delta(front_real_mint - boundary_derived): {_fmt(target.diff_front_real_mint_vs_boundary_derived, 8)}")
    print(f"delta(front_real_mint - backend_saved):    {_fmt(target.diff_front_real_mint_vs_backend_saved, 8)}")
    print("")

    run_breakdowns: List[IterationIlBreakdown] = []
    for doc in docs:
        if not isinstance(doc, dict):
            raise RuntimeError(f"run iteration item is not dict: {type(doc)}")
        run_breakdowns.append(_build_iteration_breakdown(doc))

    sum_front_real_mint = 0.0
    sum_old_total_quote = 0.0
    sum_boundary_derived = 0.0
    sum_backend_saved = 0.0
    boundary_derived_count = 0
    backend_saved_count = 0

    for row in run_breakdowns:
        sum_front_real_mint += float(row.il_front_real_mint_formula)
        sum_old_total_quote += float(row.il_old_total_quote_formula)
        if row.il_boundary_from_derived_side is not None:
            sum_boundary_derived += float(row.il_boundary_from_derived_side)
            boundary_derived_count += 1
        if row.il_backend_saved is not None:
            sum_backend_saved += float(row.il_backend_saved)
            backend_saved_count += 1

    print("=== RUN IL TOTALS ===")
    print(f"sum_frontend_current_real_mint: {_fmt(sum_front_real_mint, 8)}")
    print(f"sum_old_total_quote_formula:    {_fmt(sum_old_total_quote, 8)}")
    print(f"sum_boundary_derived_side:   {_fmt(sum_boundary_derived, 8)}  (count={boundary_derived_count}/{len(run_breakdowns)})")
    print(f"sum_backend_saved_row_il:    {_fmt(sum_backend_saved, 8)}  (count={backend_saved_count}/{len(run_breakdowns)})")
    print("")
    print("=== NOTES ===")
    print("- Notebook target formula: step_exit_value - start_value_target.")
    print("- Current frontend mapping here: step_exit_value = decrease_base * valuation_price + decrease_quote, start_value_target = mint_base * base_price + mint_quote.")
    print("- If valuation_price is not equal to boundary price, frontend_current and boundary formulas diverge.")


if __name__ == "__main__":
    main()
