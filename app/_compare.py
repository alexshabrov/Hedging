#!/usr/bin/env python3
"""
Compare dashboard metrics (recomputed from Mongo) vs backend/out.txt summary.

The script intentionally mirrors frontend formulas from:
`frontend/modules/services/frontend_service.py`.
"""
from __future__ import annotations

import argparse
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Dict, Iterable, List, Optional, Tuple

from pymongo import MongoClient  # type: ignore[import-not-found]


ACTIVE_POSITIONS_COLLECTION = "backend_positions_active"
ARCHIVE_POSITIONS_COLLECTION = "backend_positions_archive"
HEDGER_RUNS_COLLECTION = "backend_hedger_runs"

SECONDS_PER_YEAR = 365.0 * 24.0 * 60.0 * 60.0


@dataclass
class PositionRow:
    source: str
    run_id: str
    status: str
    total_quote: float
    iterations_finished: int
    avg_iteration_lifetime_sec: float
    pnl_with_hedge_quote: float
    pnl_without_hedge_quote: float
    costs_quote: float
    market_price: Optional[float]


@dataclass
class IterCalc:
    run_id: str
    is_finished: bool
    fees_quote: float
    il_base_delta: float
    il_quote_delta: float
    cex_quote: float
    costs_pnl_quote: float
    gas_paid_quote: float
    swap_cost_quote: float
    pool_hold_seconds: float
    valuation_price: float


@dataclass
class RunAgg:
    sum_fees_quote: float = 0.0
    sum_il_base_delta: float = 0.0
    sum_il_quote_delta: float = 0.0
    sum_cex_quote: float = 0.0
    sum_costs_pnl_quote: float = 0.0
    sum_gas_paid_quote: float = 0.0
    sum_swap_cost_quote: float = 0.0
    sum_pool_hold_seconds: float = 0.0
    last_valuation_price: float = 0.0
    iterations_finished: int = 0


@dataclass
class RecalcPosition:
    source: str
    run_id: str
    status: str
    total_quote: float
    iterations_finished: int
    avg_iteration_lifetime_sec: float
    pnl_with_hedge_quote: float
    pnl_without_hedge_quote: float
    costs_quote: float
    fees_quote: Optional[float]
    il_quote: Optional[float]
    cex_quote: Optional[float]
    hold_seconds: float
    price_used: Optional[float]


@dataclass
class DashboardTotals:
    active_runs: int
    finished_iterations: int
    total_pnl_with_hedge_quote: float
    total_pnl_without_hedge_quote: float
    total_costs_quote: float
    apr_with_hedge_pct: float
    apr_without_hedge_pct: float
    avg_iteration_lifetime_sec: float
    total_invested_quote: float
    total_hold_seconds: float
    component_fees_quote: float
    component_il_quote: float
    component_cex_quote: float
    component_rows_known: int
    component_rows_total: int
    gas_abs_quote: float
    swap_abs_quote: float


def _calc_apr(pnl_quote: float, total_quote: float, hold_sec: float) -> float:
    if float(total_quote) <= 0.0 or float(hold_sec) <= 0.0:
        return 0.0
    return (float(pnl_quote) / float(total_quote)) * (SECONDS_PER_YEAR / float(hold_sec)) * 100.0


def _fmt(value: Optional[float], digits: int = 6) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.{digits}f}"


def _extract_float(label: str, text: str) -> float:
    pattern = rf"^\s*{re.escape(label)}:\s*([-+]?\d+(?:\.\d+)?)"
    m = re.search(pattern, text, flags=re.MULTILINE)
    if m is None:
        raise RuntimeError(f"out.txt parse failed: label not found: {label}")
    return float(m.group(1))


def parse_out_summary(path: str) -> Dict[str, float]:
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    return {
        "current_price": _extract_float("current_price", text),
        "pnl_fees_quote": _extract_float("pnl_fees_quote", text),
        "il_quote": _extract_float("il_quote", text),
        "costs_pnl_quote_signed": _extract_float("costs_pnl_quote (signed)", text),
        "cex_quote": _extract_float("cex_quote", text),
        "pnl_without_hedge_quote": _extract_float("pnl_without_hedge_quote", text),
        "pnl_with_hedge_quote": _extract_float("pnl_with_hedge_quote", text),
        "capital_quote": _extract_float("capital_quote", text),
        "hold_time_seconds": _extract_float("hold_time_seconds", text),
        "apr_without_hedge_pct": _extract_float("apr_without_hedge_pct", text),
        "apr_with_hedge_pct": _extract_float("apr_with_hedge_pct", text),
    }


def _require_dict(v: object, name: str) -> Dict[str, object]:
    if not isinstance(v, dict):
        raise RuntimeError(f"expected dict for {name}, got: {type(v)}")
    return v


def _get_price_from_snapshot(symbol_rule: Dict[str, object], metrics: Dict[str, object]) -> float:
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
    if float(valuation_price) <= 0.0:
        raise RuntimeError(f"bad valuation_price: {valuation_price}")
    return valuation_price


def _open_filled_quote_units(metrics: Dict[str, object]) -> int:
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
    return int(open_filled_quote_units)


def _calc_iteration_components_from_raw(item: Dict[str, object]) -> IterCalc:
    if "run_id" not in item:
        raise RuntimeError("run_id missing")
    if "stats" not in item:
        raise RuntimeError("stats missing")

    stats = _require_dict(item["stats"], "stats")
    calc = _require_dict(stats.get("calc"), "stats.calc")
    uniswap = _require_dict(stats.get("uniswap"), "stats.uniswap")
    live = _require_dict(stats.get("live"), "stats.live")
    snap = _require_dict(live.get("last_snapshot"), "stats.live.last_snapshot")
    symbol_rule = _require_dict(snap.get("symbol_rule"), "stats.live.last_snapshot.symbol_rule")
    metrics = _require_dict(snap.get("metrics"), "stats.live.last_snapshot.metrics")

    valuation_price = _get_price_from_snapshot(symbol_rule, metrics)
    open_filled_quote_units = _open_filled_quote_units(metrics)

    mint = _require_dict(uniswap.get("mint"), "uniswap.mint")
    decrease = _require_dict(uniswap.get("decrease"), "uniswap.decrease")
    collect = _require_dict(uniswap.get("collect"), "uniswap.collect")

    mint_base = 0.0 if mint.get("amount_base") is None else float(mint.get("amount_base"))
    mint_quote = 0.0 if mint.get("amount_quote") is None else float(mint.get("amount_quote"))
    decrease_base = 0.0 if decrease.get("amount_base") is None else float(decrease.get("amount_base"))
    decrease_quote = 0.0 if decrease.get("amount_quote") is None else float(decrease.get("amount_quote"))
    collect_base = 0.0 if collect.get("amount_base") is None else float(collect.get("amount_base"))
    collect_quote = 0.0 if collect.get("amount_quote") is None else float(collect.get("amount_quote"))

    hedge_quote = float(calc.get("hedge_quote"))
    quote_per_cex_unit = float(hedge_quote) / float(open_filled_quote_units)
    if float(quote_per_cex_unit) <= 0.0:
        raise RuntimeError(f"bad quote_per_cex_unit: {quote_per_cex_unit}")
    cex_units = int(metrics.get("realized_pnl_quote_units", 0)) + int(metrics.get("unrealized_pnl_quote_units", 0))
    cex_quote = float(cex_units) * float(quote_per_cex_unit)

    il_base_delta = float(decrease_base) - float(mint_base)
    il_quote_delta = float(decrease_quote) - float(mint_quote)

    fees_quote = (float(collect_quote) - float(decrease_quote)) + (
        (float(collect_base) - float(decrease_base)) * float(valuation_price)
    )

    gas_paid_eth = (
        (0.0 if mint.get("gas_cost_eth") is None else float(mint.get("gas_cost_eth")))
        + (0.0 if decrease.get("gas_cost_eth") is None else float(decrease.get("gas_cost_eth")))
        + (0.0 if collect.get("gas_cost_eth") is None else float(collect.get("gas_cost_eth")))
    )
    gas_paid_quote = float(gas_paid_eth) * float(valuation_price)

    swap_cost_quote = float(item.get("swap_cost_quote", 0.0))
    costs_abs_quote = float(gas_paid_quote) + float(swap_cost_quote)
    costs_pnl_quote = -float(costs_abs_quote)

    mint_tx_timestamp_ms = int(uniswap.get("mint_tx_timestamp_ms", 0))
    decrease_tx_timestamp_ms = int(uniswap.get("decrease_tx_timestamp_ms", 0))
    if int(mint_tx_timestamp_ms) <= 0 or int(decrease_tx_timestamp_ms) <= int(mint_tx_timestamp_ms):
        raise RuntimeError(
            "bad hold timestamps "
            f"mint={mint_tx_timestamp_ms} decrease={decrease_tx_timestamp_ms}"
        )
    pool_hold_seconds = float(int(decrease_tx_timestamp_ms) - int(mint_tx_timestamp_ms)) / 1000.0

    return IterCalc(
        run_id=str(item["run_id"]),
        is_finished=(str(item.get("status", "")) == "finished"),
        fees_quote=float(fees_quote),
        il_base_delta=float(il_base_delta),
        il_quote_delta=float(il_quote_delta),
        cex_quote=float(cex_quote),
        costs_pnl_quote=float(costs_pnl_quote),
        gas_paid_quote=float(gas_paid_quote),
        swap_cost_quote=float(swap_cost_quote),
        pool_hold_seconds=float(pool_hold_seconds),
        valuation_price=float(valuation_price),
    )


def load_position_rows(db) -> List[PositionRow]:
    rows: List[PositionRow] = []
    for source in (ACTIVE_POSITIONS_COLLECTION, ARCHIVE_POSITIONS_COLLECTION):
        for doc in db[source].find({}, {"_id": 0}):
            if not isinstance(doc, dict):
                raise RuntimeError(f"position doc is not dict in {source}")
            position = _require_dict(doc.get("position"), f"{source}.position")

            run_id = str(position.get("run_id", doc.get("run_id", ""))).strip()
            if len(run_id) == 0:
                raise RuntimeError(f"{source}: empty run_id")

            status = str(position.get("status", doc.get("status", ""))).strip()
            if len(status) == 0:
                raise RuntimeError(f"{source}: empty status for run_id={run_id}")

            market_price_raw = position.get("market_price")
            market_price = None if market_price_raw is None else float(market_price_raw)

            rows.append(
                PositionRow(
                    source=source,
                    run_id=run_id,
                    status=status,
                    total_quote=float(position.get("total_quote", 0.0)),
                    iterations_finished=int(position.get("iterations_finished", 0)),
                    avg_iteration_lifetime_sec=float(position.get("avg_iteration_lifetime_sec", 0.0)),
                    pnl_with_hedge_quote=float(position.get("pnl_with_hedge_quote", 0.0)),
                    pnl_without_hedge_quote=float(position.get("pnl_without_hedge_quote", 0.0)),
                    costs_quote=float(position.get("costs_quote", 0.0)),
                    market_price=market_price,
                )
            )
    return rows


def build_run_aggs(iteration_docs: Iterable[Dict[str, object]]) -> Dict[str, RunAgg]:
    by_run: Dict[str, RunAgg] = {}
    for item in iteration_docs:
        calc = _calc_iteration_components_from_raw(item)
        agg = by_run.get(calc.run_id)
        if agg is None:
            agg = RunAgg()
            by_run[calc.run_id] = agg

        agg.sum_fees_quote += float(calc.fees_quote)
        agg.sum_il_base_delta += float(calc.il_base_delta)
        agg.sum_il_quote_delta += float(calc.il_quote_delta)
        agg.sum_cex_quote += float(calc.cex_quote)
        agg.sum_costs_pnl_quote += float(calc.costs_pnl_quote)
        agg.sum_gas_paid_quote += float(calc.gas_paid_quote)
        agg.sum_swap_cost_quote += float(calc.swap_cost_quote)
        agg.sum_pool_hold_seconds += float(calc.pool_hold_seconds)
        agg.last_valuation_price = float(calc.valuation_price)
        if bool(calc.is_finished):
            agg.iterations_finished += 1
    return by_run


def recalc_positions(rows: List[PositionRow], by_run: Dict[str, RunAgg]) -> List[RecalcPosition]:
    out: List[RecalcPosition] = []
    for row in rows:
        agg = by_run.get(row.run_id)
        if agg is None:
            out.append(
                RecalcPosition(
                    source=row.source,
                    run_id=row.run_id,
                    status=row.status,
                    total_quote=float(row.total_quote),
                    iterations_finished=int(row.iterations_finished),
                    avg_iteration_lifetime_sec=float(row.avg_iteration_lifetime_sec),
                    pnl_with_hedge_quote=float(row.pnl_with_hedge_quote),
                    pnl_without_hedge_quote=float(row.pnl_without_hedge_quote),
                    costs_quote=float(row.costs_quote),
                    fees_quote=None,
                    il_quote=None,
                    cex_quote=None,
                    hold_seconds=float(row.avg_iteration_lifetime_sec) * float(row.iterations_finished),
                    price_used=row.market_price,
                )
            )
            continue

        current_price = float(agg.last_valuation_price)
        if row.market_price is not None:
            current_price = float(row.market_price)
        if float(current_price) <= 0.0:
            raise RuntimeError(f"current_price <= 0 for run_id={row.run_id}: {current_price}")

        il_quote = float(agg.sum_il_base_delta) * float(current_price) + float(agg.sum_il_quote_delta)
        pnl_fees_quote = float(agg.sum_fees_quote)
        pnl_fees_il_quote = float(pnl_fees_quote) + float(il_quote)
        pnl_fees_il_gas_quote = float(pnl_fees_il_quote) + float(agg.sum_costs_pnl_quote)
        pnl_fees_il_gas_cex_quote = float(pnl_fees_il_gas_quote) + float(agg.sum_cex_quote)

        iterations_finished = int(agg.iterations_finished)
        hold_seconds = float(agg.sum_pool_hold_seconds)
        avg_iteration_lifetime_sec = 0.0
        if int(iterations_finished) > 0:
            avg_iteration_lifetime_sec = float(hold_seconds) / float(iterations_finished)

        out.append(
            RecalcPosition(
                source=row.source,
                run_id=row.run_id,
                status=row.status,
                total_quote=float(row.total_quote),
                iterations_finished=int(iterations_finished),
                avg_iteration_lifetime_sec=float(avg_iteration_lifetime_sec),
                pnl_with_hedge_quote=float(pnl_fees_il_gas_cex_quote),
                pnl_without_hedge_quote=float(pnl_fees_il_gas_quote),
                costs_quote=float(agg.sum_costs_pnl_quote),
                fees_quote=float(pnl_fees_quote),
                il_quote=float(il_quote),
                cex_quote=float(agg.sum_cex_quote),
                hold_seconds=float(hold_seconds),
                price_used=float(current_price),
            )
        )

    return out


def build_dashboard_totals(recalc_rows: List[RecalcPosition], by_run: Dict[str, RunAgg]) -> DashboardTotals:
    active_runs = 0
    finished_iterations = 0
    total_invested_quote = 0.0
    total_pnl_with_hedge_quote = 0.0
    total_pnl_without_hedge_quote = 0.0
    total_costs_quote = 0.0
    hold_seconds_sum = 0.0
    hold_seconds_n = 0
    total_hold_seconds = 0.0

    component_fees_quote = 0.0
    component_il_quote = 0.0
    component_cex_quote = 0.0
    component_rows_known = 0
    component_rows_total = 0

    for row in recalc_rows:
        if row.status in ("initialized", "running", "stopping"):
            active_runs += 1

        finished_iterations += int(row.iterations_finished)
        total_invested_quote += float(row.total_quote)
        total_pnl_with_hedge_quote += float(row.pnl_with_hedge_quote)
        total_pnl_without_hedge_quote += float(row.pnl_without_hedge_quote)
        total_costs_quote += float(row.costs_quote)

        if float(row.avg_iteration_lifetime_sec) > 0.0:
            hold_seconds_sum += float(row.avg_iteration_lifetime_sec)
            hold_seconds_n += 1
        total_hold_seconds += float(row.avg_iteration_lifetime_sec) * float(row.iterations_finished)

        component_rows_total += 1
        if row.fees_quote is not None and row.il_quote is not None and row.cex_quote is not None:
            component_rows_known += 1
            component_fees_quote += float(row.fees_quote)
            component_il_quote += float(row.il_quote)
            component_cex_quote += float(row.cex_quote)

    apr_with_hedge_pct = 0.0
    apr_without_hedge_pct = 0.0
    if float(total_invested_quote) > 0.0 and float(total_hold_seconds) > 0.0:
        apr_with_hedge_pct = _calc_apr(
            float(total_pnl_with_hedge_quote),
            float(total_invested_quote),
            float(total_hold_seconds),
        )
        apr_without_hedge_pct = _calc_apr(
            float(total_pnl_without_hedge_quote),
            float(total_invested_quote),
            float(total_hold_seconds),
        )

    avg_iteration_lifetime_sec = 0.0
    if int(hold_seconds_n) > 0:
        avg_iteration_lifetime_sec = float(hold_seconds_sum) / float(hold_seconds_n)

    gas_abs_quote = 0.0
    swap_abs_quote = 0.0
    for agg in by_run.values():
        gas_abs_quote += float(agg.sum_gas_paid_quote)
        swap_abs_quote += float(agg.sum_swap_cost_quote)

    return DashboardTotals(
        active_runs=int(active_runs),
        finished_iterations=int(finished_iterations),
        total_pnl_with_hedge_quote=float(total_pnl_with_hedge_quote),
        total_pnl_without_hedge_quote=float(total_pnl_without_hedge_quote),
        total_costs_quote=float(total_costs_quote),
        apr_with_hedge_pct=float(apr_with_hedge_pct),
        apr_without_hedge_pct=float(apr_without_hedge_pct),
        avg_iteration_lifetime_sec=float(avg_iteration_lifetime_sec),
        total_invested_quote=float(total_invested_quote),
        total_hold_seconds=float(total_hold_seconds),
        component_fees_quote=float(component_fees_quote),
        component_il_quote=float(component_il_quote),
        component_cex_quote=float(component_cex_quote),
        component_rows_known=int(component_rows_known),
        component_rows_total=int(component_rows_total),
        gas_abs_quote=float(gas_abs_quote),
        swap_abs_quote=float(swap_abs_quote),
    )


def print_report(
    out_summary: Dict[str, float],
    totals: DashboardTotals,
    recalc_rows: List[RecalcPosition],
) -> None:
    dashboard_without = float(totals.total_pnl_without_hedge_quote)
    dashboard_with = float(totals.total_pnl_with_hedge_quote)
    out_without = float(out_summary["pnl_without_hedge_quote"])
    out_with = float(out_summary["pnl_with_hedge_quote"])

    delta_without = dashboard_without - out_without
    delta_with = dashboard_with - out_with

    # Component deltas (valid only when all rows have known component decomposition).
    full_components = (totals.component_rows_known == totals.component_rows_total)
    delta_fees = None
    delta_il = None
    delta_costs = None
    delta_cex = None
    if full_components:
        delta_fees = float(totals.component_fees_quote) - float(out_summary["pnl_fees_quote"])
        delta_il = float(totals.component_il_quote) - float(out_summary["il_quote"])
        delta_costs = float(totals.total_costs_quote) - float(out_summary["costs_pnl_quote_signed"])
        delta_cex = float(totals.component_cex_quote) - float(out_summary["cex_quote"])

    print("=== INPUTS ===")
    print(f"out.current_price                {_fmt(out_summary['current_price'])}")
    print(f"mongo.total_runs_rows            {len(recalc_rows)}")
    print("")

    print("=== DASHBOARD (MONGO RE-CALC, FRONTEND FORMULAS) ===")
    print(f"active_runs                      {totals.active_runs}")
    print(f"finished_iterations              {totals.finished_iterations}")
    print(f"total_pnl_with_hedge_quote       {_fmt(totals.total_pnl_with_hedge_quote)}")
    print(f"total_pnl_without_hedge_quote    {_fmt(totals.total_pnl_without_hedge_quote)}")
    print(f"total_costs_quote (signed)       {_fmt(totals.total_costs_quote)}")
    print(f"apr_with_hedge_pct               {_fmt(totals.apr_with_hedge_pct, 4)}")
    print(f"apr_without_hedge_pct            {_fmt(totals.apr_without_hedge_pct, 4)}")
    print(f"avg_iteration_lifetime_sec       {_fmt(totals.avg_iteration_lifetime_sec, 2)}")
    print(f"total_invested_quote             {_fmt(totals.total_invested_quote)}")
    print(f"total_hold_seconds               {_fmt(totals.total_hold_seconds)}")
    print("")

    print("=== OUT.TXT SUMMARY ===")
    print(f"pnl_with_hedge_quote             {_fmt(out_summary['pnl_with_hedge_quote'])}")
    print(f"pnl_without_hedge_quote          {_fmt(out_summary['pnl_without_hedge_quote'])}")
    print(f"costs_pnl_quote (signed)         {_fmt(out_summary['costs_pnl_quote_signed'])}")
    print(f"cex_quote                        {_fmt(out_summary['cex_quote'])}")
    print(f"capital_quote                    {_fmt(out_summary['capital_quote'])}")
    print(f"hold_time_seconds                {_fmt(out_summary['hold_time_seconds'])}")
    print(f"apr_with_hedge_pct               {_fmt(out_summary['apr_with_hedge_pct'], 4)}")
    print(f"apr_without_hedge_pct            {_fmt(out_summary['apr_without_hedge_pct'], 4)}")
    print("")

    print("=== DELTA (DASHBOARD - OUT) ===")
    print(f"delta_pnl_with_hedge_quote       {_fmt(delta_with)}")
    print(f"delta_pnl_without_hedge_quote    {_fmt(delta_without)}")
    print(f"delta_costs_pnl_quote            {_fmt(totals.total_costs_quote - out_summary['costs_pnl_quote_signed'])}")
    print(f"delta_apr_with_hedge_pct         {_fmt(totals.apr_with_hedge_pct - out_summary['apr_with_hedge_pct'], 4)}")
    print(f"delta_apr_without_hedge_pct      {_fmt(totals.apr_without_hedge_pct - out_summary['apr_without_hedge_pct'], 4)}")
    print("")

    print("=== COMPONENT DECOMPOSITION ===")
    if not full_components:
        print(
            "warning: component decomposition is partial "
            f"(known_rows={totals.component_rows_known}/{totals.component_rows_total})"
        )
    else:
        print(f"dashboard.fees_quote             {_fmt(totals.component_fees_quote)}")
        print(f"dashboard.il_quote               {_fmt(totals.component_il_quote)}")
        print(f"dashboard.costs_pnl_quote        {_fmt(totals.total_costs_quote)}")
        print(f"dashboard.cex_quote              {_fmt(totals.component_cex_quote)}")
        print(f"dashboard.without = fees+il+cost {_fmt(totals.component_fees_quote + totals.component_il_quote + totals.total_costs_quote)}")
        print(
            "dashboard.with = without+cex     "
            f"{_fmt(totals.component_fees_quote + totals.component_il_quote + totals.total_costs_quote + totals.component_cex_quote)}"
        )
        print("")
        print(f"delta_fees_quote                 {_fmt(delta_fees)}")
        print(f"delta_il_quote                   {_fmt(delta_il)}")
        print(f"delta_costs_pnl_quote            {_fmt(delta_costs)}")
        print(f"delta_cex_quote                  {_fmt(delta_cex)}")
        print(
            "check_delta_without              "
            f"{_fmt((delta_fees or 0.0) + (delta_il or 0.0) + (delta_costs or 0.0))}"
        )
        print(
            "check_delta_with                 "
            f"{_fmt((delta_fees or 0.0) + (delta_il or 0.0) + (delta_costs or 0.0) + (delta_cex or 0.0))}"
        )
    print("")

    print("=== COSTS BREAKDOWN (FROM ITERATIONS) ===")
    print(f"gas_abs_quote_sum                {_fmt(totals.gas_abs_quote)}")
    print(f"swap_cost_abs_quote_sum          {_fmt(totals.swap_abs_quote)}")
    print(f"costs_abs_total                  {_fmt(totals.gas_abs_quote + totals.swap_abs_quote)}")
    print("")

    print("=== APR BASE COMPARISON ===")
    print(f"dashboard APR capital base       {_fmt(totals.total_invested_quote)} (sum total_quote across rows)")
    print(f"out APR capital base             {_fmt(out_summary['capital_quote'])} (wallet start balances)")
    print(f"dashboard hold seconds           {_fmt(totals.total_hold_seconds)} (sum per-iteration hold)")
    print(f"out hold seconds                 {_fmt(out_summary['hold_time_seconds'])} (first tx -> last tx)")
    print("")

    # Reprice APR to show denominator impact.
    apr_without_out_pnl_on_dash_base = _calc_apr(
        pnl_quote=out_summary["pnl_without_hedge_quote"],
        total_quote=totals.total_invested_quote,
        hold_sec=totals.total_hold_seconds,
    )
    apr_with_out_pnl_on_dash_base = _calc_apr(
        pnl_quote=out_summary["pnl_with_hedge_quote"],
        total_quote=totals.total_invested_quote,
        hold_sec=totals.total_hold_seconds,
    )
    print("=== APR WHAT-IF ===")
    print(f"apr_without using out pnl + dashboard base   {_fmt(apr_without_out_pnl_on_dash_base, 4)}")
    print(f"apr_with using out pnl + dashboard base      {_fmt(apr_with_out_pnl_on_dash_base, 4)}")
    print("")

    print("=== PER-RUN RE-CALC SNAPSHOT ===")
    print("run_id                             source                     status      iters  avg_hold_s   pnl_with     pnl_without  costs       price")
    for row in sorted(recalc_rows, key=lambda x: x.run_id):
        run_short = row.run_id[:32].ljust(32)
        src_short = row.source[:24].ljust(24)
        status_short = row.status[:10].ljust(10)
        print(
            f"{run_short}  {src_short}  {status_short}  "
            f"{str(row.iterations_finished).rjust(5)}  "
            f"{_fmt(row.avg_iteration_lifetime_sec, 2).rjust(10)}  "
            f"{_fmt(row.pnl_with_hedge_quote).rjust(10)}  "
            f"{_fmt(row.pnl_without_hedge_quote).rjust(10)}  "
            f"{_fmt(row.costs_quote).rjust(10)}  "
            f"{_fmt(row.price_used, 2).rjust(8)}"
        )

    print("")
    print("=== NOTES ===")
    print("- Dashboard includes CEX PnL from hedger snapshots; out.txt uses cex_quote=0 by design.")
    print("- Dashboard costs include gas + swap_cost_quote; out.txt chain currently uses gas only.")
    print("- Dashboard APR base uses sum(total_quote) and sum(pool_hold_seconds).")
    print("- out.txt APR base uses wallet start capital and wall-clock tx span.")
    print("- Live dashboard may differ slightly from this script for active runs because UI can use runtime market price not stored in Mongo.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare dashboard metrics vs out.txt summary.")
    parser.add_argument("--mongo-uri", default="mongodb://172.18.0.3:27017", help="Mongo URI")
    parser.add_argument("--mongo-db", default="hedging", help="Mongo database name")
    parser.add_argument(
        "--out-file",
        default="/home/ubuntu/Hedging/app/backend/out.txt",
        help="Path to out.txt produced by history CLI",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    client = MongoClient(str(args.mongo_uri), serverSelectionTimeoutMS=5000)
    _ = client.server_info()
    db = client[str(args.mongo_db)]

    rows = load_position_rows(db)
    iter_docs = list(db[HEDGER_RUNS_COLLECTION].find({}, {"_id": 0}).sort([("run_id", 1), ("iteration_no", 1)]))
    by_run = build_run_aggs(iter_docs)
    recalc_rows = recalc_positions(rows, by_run)
    totals = build_dashboard_totals(recalc_rows, by_run)
    out_summary = parse_out_summary(str(args.out_file))

    print_report(
        out_summary=out_summary,
        totals=totals,
        recalc_rows=recalc_rows,
    )


if __name__ == "__main__":
    main()
