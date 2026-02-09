"""
High-level open/close logic via thresholds/targets and chase orchestration.

Provides:
 - ensure_capacity(arr, pos, extra, alloc) -> np.ndarray:
   ensure 2D array row capacity while keeping the same column count.
 - ensure_capacity_1d(arr, pos, extra, alloc) -> np.ndarray:
   ensure 1D array length capacity for writing `extra` items from position `pos`.
 - get_neutral_excursion_pct(all_trades, start_index, stop_index, threshold_price, side) -> float:
   compute maximum price excursion beyond neutral threshold within [start..stop] window,
   returned as percent of the threshold price. side=1 looks below top_threshold,
   side=-1 looks above btm_threshold.
 - append_chase(groups, groups_pos, logic_trace, logic_trace_pos, orders, gtrace, main_pos, side, alloc)
   -> (groups, groups_pos, logic_trace, logic_trace_pos, main_pos):
   append chase orders and trace into aggregating structures.
 - run_logic(all_trades, start_index, volume, max_time_ms, network_delay_ms, distance_steps,
   threshold_as_perc, d_top_threshold, d_btm_threshold, d_top_target, d_btm_target, only_side, entrance_timeout_ms, alloc) ->
   (stopped_at_index, groups, logic_trace, lines, neutral_excursions, err):
   execute the threshold/target logic and collect events.
 - get_logic_trace(all_trades, groups, price_step, volume_step, maker_fee) -> list[dict]:
   build a detailed per-order PnL trace.
 - print_logic_trace(trace) -> None:
   print grouped summaries and a detailed trace.
 - get_logic_stat(...) -> dict:
   aggregated metrics across all chases (success, slippage, time, PnL).
 - plot_logic(...) -> None:
   visualize price, thresholds/targets, orders and trace.
 - random_logic(...) -> None:
   run a demo logic pass with summaries and plots.
"""
import numpy as np
import random, matplotlib.pyplot as plt, os
from datetime import datetime
from numba import njit

from lib.chase import *
from lib.helders import ensure_dir

# Run logic method
@njit
def ensure_capacity(arr: np.ndarray, pos: int, extra: int, alloc: int) -> np.ndarray:
    """
    Ensures that 2D array has enough rows for writing `extra` items from position `pos`.
    Preserves the number of columns and dtype.
    """
    # Always treat as 2D: extend along the first dimension, keep column count
    ncols = arr.shape[1]
    while pos + extra > arr.shape[0]:
        arr = np.concatenate((arr, np.zeros((alloc, ncols), dtype=arr.dtype)))
    return arr

@njit
def ensure_capacity_1d(arr: np.ndarray, pos: int, extra: int, alloc: int) -> np.ndarray:
    """
    Ensures that 1D array has enough length for writing `extra` items from position `pos`.
    Preserves dtype.
    """
    while pos + extra > arr.shape[0]:
        arr = np.concatenate((arr, np.zeros(alloc, dtype=arr.dtype)))
    return arr

@njit
def get_neutral_excursion_pct(all_trades: np.ndarray,
                              start_index: int,
                              stop_index: int,
                              threshold_price: int,
                              side: int) -> float:
    """
    Maximum excursion beyond neutral threshold within [start_index..stop_index], as percent of threshold.
      - side = 1 (long): measure dips below threshold_price (top_threshold)
      - side = -1 (short): measure rises above threshold_price (btm_threshold)
    Preconditions:
      - 0 <= start_index < all_trades.shape[0]
      - 0 <= stop_index < all_trades.shape[0]
      - threshold_price > 0
      - side in {1, -1}
    """
    assert threshold_price > 0
    assert side == 1 or side == -1
    assert start_index >= 0 and start_index < all_trades.shape[0]
    assert stop_index >= 0 and stop_index < all_trades.shape[0]
    
    s = start_index
    e = stop_index
    assert s <= e
    
    max_ex = 0
    
    if side == 1:
        for i in range(s, e + 1):
            p = int(all_trades[i, 1])
            if p < threshold_price:
                d = int(threshold_price - p)
                if d > max_ex:
                    max_ex = d
    else:
        for i in range(s, e + 1):
            p = int(all_trades[i, 1])
            if p > threshold_price:
                d = int(p - threshold_price)
                if d > max_ex:
                    max_ex = d
    return (float(max_ex) / float(threshold_price)) * 100.0

@njit
def append_chase(groups: np.ndarray,
                 groups_pos: int,
                 logic_trace: np.ndarray,
                 logic_trace_pos: int,
                 orders: np.ndarray,
                 gtrace: np.ndarray,
                 main_pos: int,
                 side: int,
                 alloc: int) -> tuple[np.ndarray, int, np.ndarray, int, int]:
    """
    Appends orders and global trace to groups and logic_trace with main_pos.
    Saves executed volume and side for each order row.
    """
    if orders.shape[0] > 0:
        groups = ensure_capacity(groups, groups_pos, orders.shape[0], alloc)

        for i in range(orders.shape[0]):
            o = orders[i]
            groups[groups_pos + i, 0] = o[0]
            groups[groups_pos + i, 1] = o[1]
            groups[groups_pos + i, 2] = o[2]
            groups[groups_pos + i, 3] = o[3]
            groups[groups_pos + i, 4] = o[4]
            groups[groups_pos + i, 5] = side
            groups[groups_pos + i, 6] = main_pos

        groups_pos += orders.shape[0]

        logic_trace = ensure_capacity(logic_trace, logic_trace_pos, gtrace.shape[0], alloc)

        for i in range(gtrace.shape[0]):
            logic_trace[logic_trace_pos + i, 0] = gtrace[i, 0]
            logic_trace[logic_trace_pos + i, 1] = gtrace[i, 1]
            logic_trace[logic_trace_pos + i, 2] = main_pos

        logic_trace_pos += gtrace.shape[0]

        main_pos += 1

    return groups, groups_pos, logic_trace, logic_trace_pos, main_pos

@njit
def run_logic(
        all_trades: np.ndarray,
        start_index: int,
        volume: int,
        max_time_ms: int,
        network_delay_ms: int,
        distance_steps: int,
        threshold_as_perc: bool,
        d_top_threshold: int,
        d_btm_threshold: int,
        d_top_target: int,
        d_btm_target: int,
        only_side: int,
        entrance_timeout_ms: int,
        alloc: int = 1000,
    ) -> tuple[int, np.ndarray, np.ndarray, tuple[int, int, int, int], np.ndarray, int]:
    """
    Executes threshold-based chase logic starting at start_index.

       Input:
          - all_trades: numpy array of shape (N, 4)
          - start_index: index to start observation from
          - volume: position volume (units)
          - max_time_ms: per-chase max wait time
          - network_delay_ms: network latency (ms)
          - distance_steps: price distance (steps) for next order in chase
          - threshold_as_perc: when True, thresholds are percents of price (like targets)
          - d_top_threshold: upper threshold offset (steps or percents) from start price
          - d_btm_threshold: lower threshold offset (steps or percents) from start price
          - d_top_target: upper target offset (steps) from start price
          - d_btm_target: lower target offset (steps) from start price
          - only_side: 1 (long-only), -1 (short-only), 0 (both)
          - entrance_timeout_ms: when only_side != 0, stop if no entry within this time
          - alloc: allocation size
       Output:
          - stopped_at_index
          - groups: numpy array of shape (G, 7) -> [open_idx, close_idx, price, status_or_left, volume_executed, side, main_pos]
          - logic_trace: numpy array of shape (T, 3) -> [trade_idx, order_pos, main_pos]
          - lines: (top_target, btm_target, top_threshold, btm_threshold)
          - neutral_excursions: numpy array of shape (K,) float64 (chronological per neutral close):
            for each neutral close — maximum excursion beyond threshold in percent;
            contains only neutral closes (target closes are not included)
          - err: 0 if ok, negative codes on errors
    """
    # Base price
    base_price = all_trades[start_index, 1]

    # Thresholds: convert percents to steps if enabled
    if threshold_as_perc:
        tt = int(base_price * d_top_threshold / 100)
        bt = int(base_price * d_btm_threshold / 100)
    else:
        tt = int(d_top_threshold)
        bt = int(d_btm_threshold)

    # Absolute levels
    top_threshold = base_price + tt
    btm_threshold = base_price - bt
    top_target = base_price + d_top_target
    btm_target = base_price - d_btm_target

    # Lines
    lines = (top_target, btm_target, top_threshold, btm_threshold)

    # State
    index = start_index
    position_side = 0  # 0 flat, 1 long, -1 short
    opened_volume = 0
    err = 0

    # Outputs
    groups = np.zeros((alloc, 7), dtype=np.int64)
    groups_pos = 0

    logic_trace = np.zeros((alloc, 3), dtype=np.int64)
    logic_trace_pos = 0

    # Main position counter
    main_pos = 0
    
    # Per-chase neutral excursions (percent)
    neutral_excursions = np.zeros((alloc,), dtype=np.float64)
    exc_pos = 0
    
    # Start time for optional entrance timeout
    start_time_ms = int(all_trades[start_index, 0])

    # Main loop
    while index >= 0 and index < all_trades.shape[0]:
        # Observe price after network delay
        obs_index = find_next_index(all_trades, index, network_delay_ms)
        if obs_index == -1:
            # End of data
            err = -1
            break

        obs_price = all_trades[obs_index, 1]

        # If flat: optional entrance timeout and threshold crossings to open
        if position_side == 0:
            if only_side != 0:
                # Check for entrance timeout first
                current_time_ms = int(all_trades[obs_index, 0])
                
                if current_time_ms - start_time_ms > entrance_timeout_ms:
                    # Stop without error if no entry happened within timeout
                    index = obs_index
                    err = 0
                    break

            if obs_price > top_threshold and (only_side == 0 or only_side == 1):
                # Open long
                stopped_at_index, volume_left, orders, gtrace = chase_limit_orders(
                    all_trades,
                    obs_index,
                    1,
                    volume,
                    max_time_ms,
                    network_delay_ms,
                    distance_steps,
                    alloc,
                )

                groups, groups_pos, logic_trace, logic_trace_pos, main_pos = append_chase(
                    groups, groups_pos, logic_trace, logic_trace_pos, orders, gtrace, main_pos, 1, alloc
                )
                
                # Move index
                index = stopped_at_index

                # Guarantee full filling
                if volume_left > 0:
                    # Open long failed to fill fully
                    err = -2
                    break

                # Set position
                position_side = 1
                opened_volume = volume
                
                continue

            if obs_price < btm_threshold and (only_side == 0 or only_side == -1):
                # Open short
                stopped_at_index, volume_left, orders, gtrace = chase_limit_orders(
                    all_trades,
                    obs_index,
                    -1,
                    volume,
                    max_time_ms,
                    network_delay_ms,
                    distance_steps,
                    alloc,
                )

                groups, groups_pos, logic_trace, logic_trace_pos, main_pos = append_chase(
                    groups, groups_pos, logic_trace, logic_trace_pos, orders, gtrace, main_pos, -1, alloc
                )

                # Move index
                index = stopped_at_index

                # Guarantee full filling
                if volume_left > 0:
                    # Open short failed to fill fully
                    err = -3
                    break
                
                # Set position
                position_side = -1
                opened_volume = volume
                
                continue

            # Neutral, advance
            index = obs_index
            continue

        # If in position: check targets first
        if position_side == 1:
            if obs_price >= top_target:
                # Close long at target
                stopped_at_index, volume_left, orders, gtrace = chase_limit_orders(
                    all_trades,
                    obs_index,
                    -1,
                    opened_volume,
                    max_time_ms,
                    network_delay_ms,
                    distance_steps,
                    alloc,
                )
                groups, groups_pos, logic_trace, logic_trace_pos, main_pos = append_chase(
                    groups, groups_pos, logic_trace, logic_trace_pos, orders, gtrace, main_pos, -1, alloc
                )

                # Move index
                index = stopped_at_index

                # Guarantee full filling
                if volume_left > 0:
                    # Close long target failed to fill fully
                    err = -4

                break

        if position_side == -1:
            if obs_price <= btm_target:
                # Close short at target
                stopped_at_index, volume_left, orders, gtrace = chase_limit_orders(
                    all_trades,
                    obs_index,
                    1,
                    opened_volume,
                    max_time_ms,
                    network_delay_ms,
                    distance_steps,
                    alloc,
                )
                groups, groups_pos, logic_trace, logic_trace_pos, main_pos = append_chase(
                    groups, groups_pos, logic_trace, logic_trace_pos, orders, gtrace, main_pos, 1, alloc
                )

                # Move index
                index = stopped_at_index

                # Guarantee full filling
                if volume_left > 0:
                    # Close short target failed to fill fully
                    err = -5
                    
                break

        # Neutral close
        if position_side == 1 and obs_price <= top_threshold:
            # Close long neutral
            stopped_at_index, volume_left, orders, gtrace = chase_limit_orders(
                all_trades,
                obs_index,
                -1,
                opened_volume,
                max_time_ms,
                network_delay_ms,
                distance_steps,
                alloc,
            )
            groups, groups_pos, logic_trace, logic_trace_pos, main_pos = append_chase(
                groups, groups_pos, logic_trace, logic_trace_pos, orders, gtrace, main_pos, -1, alloc
            )
            
            # Per-chase metric (neutral close -> measure)
            exc = get_neutral_excursion_pct(
                all_trades,
                obs_index,
                stopped_at_index,
                top_threshold,
                1,
            ) if stopped_at_index > 0 and stopped_at_index < all_trades.shape[0] else 0.0

            neutral_excursions = ensure_capacity_1d(neutral_excursions, exc_pos, 1, alloc)
            neutral_excursions[exc_pos] = exc
            exc_pos += 1

            # Move index
            index = stopped_at_index

            # Guarantee full filling
            if volume_left > 0:
                # Close long neutral failed to fill fully
                err = -6
                break

            # Flat after neutral close: exit immediately in single-side mode
            if only_side != 0:
                break

            # Flat after neutral close, continue
            position_side = 0
            continue

        if position_side == -1 and obs_price >= btm_threshold:
            # Close short neutral
            stopped_at_index, volume_left, orders, gtrace = chase_limit_orders(
                all_trades,
                obs_index,
                1,
                opened_volume,
                max_time_ms,
                network_delay_ms,
                distance_steps,
                alloc,
            )
            groups, groups_pos, logic_trace, logic_trace_pos, main_pos = append_chase(
                groups, groups_pos, logic_trace, logic_trace_pos, orders, gtrace, main_pos, 1, alloc
            )
            
            # Per-chase metric (neutral close -> measure)
            exc = get_neutral_excursion_pct(
                all_trades,
                obs_index,
                stopped_at_index,
                btm_threshold,
                -1,
            ) if stopped_at_index > 0 and stopped_at_index < all_trades.shape[0] else 0.0

            neutral_excursions = ensure_capacity_1d(neutral_excursions, exc_pos, 1, alloc)
            neutral_excursions[exc_pos] = exc
            exc_pos += 1

            # Move index
            index = stopped_at_index

            # Guarantee full filling
            if volume_left > 0:
                # Close short neutral failed to fill fully
                err = -7
                break

            # Flat after neutral close: exit immediately in single-side mode
            if only_side != 0:
                break

            # Flat after neutral close, continue
            position_side = 0
            continue

        # Otherwise keep observing
        index = obs_index

    return index, groups[:groups_pos], logic_trace[:logic_trace_pos], lines, neutral_excursions[:exc_pos], err

def get_logic_trace(all_trades: np.ndarray,
                    groups: np.ndarray,
                    price_step: float,
                    volume_step: float,
                    maker_fee: float = 0.0) -> list[dict]:
    """
    Builds detailed PnL actions list from orders saved in `groups`.
      - One record per executed order row (volume_executed > 0)
      - Uses order close index for time
      - Records deltas and cumulative base/quote with fee
      
      Output item fields:
        - time_ms: unix ms (int)
        - main_pos: chase id (int)
        - group_row: absolute row index in groups (int)
        - order_pos: index of order inside its chase (int)
        - side: 1 long, -1 short (int)
        - base_delta: change in base (units, volume steps) (int)
        - quote_delta: change in quote (real units) (float)
        - fee: fee deducted from quote (real units) (float)
        - base: resulting base after step (units, volume steps) (int)
        - quote: resulting quote after step (real units) (float)
    """
    actions = []
    
    if groups.shape[0] == 0:
        return actions
    
    # Accumulators
    base_acc = 0
    quote_acc = 0.0
    
    # Build per-order events with deltas only
    mps = np.unique(groups[:, 6])
    
    for mp in mps:
        mp = int(mp)
        mask = groups[:, 6] == mp
        row_idx = np.where(mask)[0]
        mp_rows = groups[mask]
        
        for local_pos in range(mp_rows.shape[0]):
            r = mp_rows[local_pos]
            
            vol_exec = int(r[4])
            if vol_exec <= 0:
                continue
            
            side = int(r[5])
            price_int = int(r[2])
            close_idx = int(r[1])
            time_ms = int(all_trades[close_idx, 0])
            
            price_real = float(price_int) * float(price_step)
            volume_real = float(vol_exec) * float(volume_step)
            q_real = volume_real * price_real
            fee_real = q_real * float(maker_fee)
            
            base_delta = side * vol_exec
            quote_delta = -side * q_real
            
            # Accumulate balances inline
            base_acc += int(base_delta)
            quote_acc += float(quote_delta)
            quote_acc -= float(fee_real)
            
            actions.append({
                'time_ms': time_ms,
                'main_pos': mp,
                'group_row': int(row_idx[local_pos]),
                'order_pos': int(local_pos),
                'side': side,
                'price_int': price_int,
                'price_real': price_real,
                'volume_int': vol_exec,
                'volume_real': volume_real,
                'quote_real': q_real,
                'fee_real': fee_real,
                'base_delta': int(base_delta),
                'quote_delta': float(quote_delta),
                'base': int(base_acc),
                'quote': float(quote_acc),
            })
    
    # After processing all orders across all actions, base must be flat
    assert base_acc == 0, "Position base is not flat after processing all orders"
    
    return actions

def print_logic_trace(trace: list[dict]):
    """
    Prints grouped PnL summary by top-level group (main_pos) and current totals,
    then prints the detailed per-order trace lines with time deltas.
      Sections:
        1) Per-group summary: orders count, base/quote deltas and resulting balances
        2) Current totals
        3) Detailed trace: one record per line with +Xms since previous record,
           blank line between groups
    """
    if len(trace) == 0:
        print('Trace is empty')
        return
    
    # Per-group summary
    print("Groups:")
    
    # Collect unique groups in order of first appearance
    seen = set()
    groups_order = []
    for r in trace:
        mp = int(r['main_pos'])
        if mp not in seen:
            seen.add(mp)
            groups_order.append(mp)
    
    for mp in groups_order:
        rs = [r for r in trace if int(r['main_pos']) == mp]
        orders_count = len(rs)
        side_group = int(rs[0]['side']) if orders_count > 0 else 0
        
        base_delta = 0
        quote_delta = 0.0
        fee_sum = 0.0
        
        for r in rs:
            base_delta += int(r['base_delta'])
            quote_delta += float(r['quote_delta'])
            fee_sum += float(r['fee_real'])
        
        base_res = int(rs[-1]['base'])
        quote_res = float(rs[-1]['quote'])
        
        print(f"  group {mp}: side={side_group} orders={orders_count} "
              f"| base+= {base_delta} -> {base_res} "
              f"| quote+= {quote_delta:.6f} fee= {fee_sum:.6f} -> {quote_res:.6f}")
    
    # Current totals
    last = trace[-1]
    base_now = int(last['base'])
    quote_now = float(last['quote'])
    
    print("Current:")
    print(f"  base={base_now} quote={quote_now:.6f}")
    
    # Detailed trace
    print()
    print("Trace:")
    
    prev_time = int(trace[0]['time_ms'])
    last_mp = None
    
    for i in range(len(trace)):
        r = trace[i]
        
        mp = int(r['main_pos'])
        if last_mp is not None and mp != last_mp:
            print()
        last_mp = mp
        
        time_ms = int(r['time_ms'])
        delta_ms = 0 if i == 0 else int(time_ms - prev_time)
        prev_time = time_ms
        
        g = int(r['group_row'])
        o = int(r['order_pos'])
        side = int(r['side'])
        price_real = float(r['price_real'])
        
        base_delta = int(r['base_delta'])
        base_res = int(r['base'])
        
        quote_delta = float(r['quote_delta'])
        fee_real = float(r['fee_real'])
        quote_res = float(r['quote'])
        
        prefix = f"+{delta_ms}ms"
        pnl_tag = " [PnL]" if base_res == 0 else ""
        
        print(f"{prefix:>8} | mp={mp} gp={g} op={o} side={side} price={price_real:.6f} "
              f"base+= {base_delta} -> base={base_res} "
              f"| quote+= {quote_delta:.6f} fee= {fee_real:.6f} -> quote={quote_res:.6f}{pnl_tag}")

def get_logic_stat(all_trades: np.ndarray,
                   groups: np.ndarray,
                   logic_trace: np.ndarray,
                   lines: tuple[int, int, int, int],
                   start_index: int,
                   stopped_at_index: int,
                   err: int,
                   test_volume_units: int,
                   price_step: float,
                   volume_step: float,
                   test_volume_usd: float,
                   maker_fee: float = 0.0) -> dict:
    """
    Aggregates statistics for an already executed high-level logic run.
      - Per-chase stats are derived via get_chase_stat
      - Returns aggregated metrics across all chases
    """

    chases = []
    base = 0.0
    quote = 0.0

    if groups.shape[0] > 0:
        # Unique chase ids
        main_positions = np.unique(groups[:, 6])
        for mp in main_positions:
            mp = int(mp)

            # Orders and trace for this chase
            mp_mask = groups[:, 6] == mp
            mp_orders = groups[mp_mask][:, 0:5]

            tr_mask = logic_trace[:, 2] == mp
            mp_trace = logic_trace[tr_mask][:, 0:2]

            if mp_orders.shape[0] == 0:
                continue

            # Start/stop indexes
            mp_start_index = int(mp_orders[0, 0])
            mp_stopped_at = int(np.max(mp_orders[:, 1]))

            # Use saved side from groups
            mp_side = int(groups[mp_mask][0, 5])

            # Build chase stat using helper from lib.chase
            stat = get_chase_stat(
                all_trades=all_trades,
                index=mp_start_index,
                side=mp_side,
                test_volume_usd=test_volume_usd,
                test_volume_units=test_volume_units,
                price_step=price_step,
                volume_step=volume_step,
                stopped_at_index=mp_stopped_at,
                orders=mp_orders,
                global_trace=mp_trace,
                volume_left=0,
            )

            # Calculate PnL
            mp_rows = groups[mp_mask]
            
            for i in range(mp_rows.shape[0]):
                # Get int data
                s_int = int(mp_rows[i, 5])
                vol_int = int(mp_rows[i, 4])
                if vol_int == 0:
                    continue

                # Convert to float
                s = float(s_int)              # side
                vol = float(vol_int)          # volume_executed (in volume steps)
                price = float(mp_rows[i, 2])   # order price (in price steps)
                
                # Base (units)
                base += s * vol
                
                # Convert to real quote using steps
                q_real = (vol * float(volume_step)) * (price * float(price_step))
                quote -= s * q_real
                quote -= q_real * float(maker_fee)

            # Add meta
            stat['main_pos'] = mp
            chases.append(stat)

    # After processing all orders across all chases, base must be flat
    assert base == 0, "Position base is not flat after processing all orders"

    # Aggregates
    n = len(chases)
    # Full execution time from provided start_index to stopped_at_index
    s_idx = int(start_index)
    e_idx = int(stopped_at_index)
    full_execution_time = (float(all_trades[e_idx, 0]) - float(all_trades[s_idx, 0])) / 1000.0
    
    agg = {
        'n_chases': n,
        'success_rate': float(np.mean([1.0 if c['volume_left'] == 0 else 0.0 for c in chases])) if n > 0 else 0.0,
        'avg_slippage': float(np.mean([c['slippage'] for c in chases])) if n > 0 else 0.0,
        'avg_execution_time': float(np.mean([c['execution_time'] for c in chases])) if n > 0 else 0.0,
        'avg_po_violations_pct': float(np.mean([c['po_violations_pct'] for c in chases])) if n > 0 else 0.0,
        'total_trades_executed': int(np.sum([c['trades_executed'] for c in chases])) if n > 0 else 0,
        'pnl': float(quote),
        'full_execution_time': float(full_execution_time),
    }

    return {
        'stopped_at_index': int(stopped_at_index),
        'err': int(err),
        'lines': (int(lines[0]), int(lines[1]), int(lines[2]), int(lines[3])),
        'chases': chases,
        'agg': agg,
    }

def plot_logic(all_trades,
               start_index,
               stopped_at_index,
               groups,
               logic_trace,
               lines,
               to_file=None,
               s=50):
    """
    Plots high-level logic context: price series, thresholds/targets, orders and trace points.

       Input:
          - all_trades: array of shape (N, 4) with columns [time, price, volume, is_buyer_maker]
          - start_index: start index of logic run
          - stopped_at_index: final index reached by logic
          - groups: orders array of shape (G, 7):
            [open_idx, close_idx, price, status_or_left, volume_executed, side, main_pos]
          - logic_trace: trace array of shape (T, 3): [trade_idx, order_pos, main_pos]
          - lines: tuple (top_target, btm_target, top_threshold, btm_threshold)
          - to_file: optional path to save the plot
          - s: half-window size around [start_index..stopped_at_index]

       Output:
          - None. Saves or shows a matplotlib plot.
    """
    # Get bounds
    f = max(0, start_index - s)
    t = min(all_trades.shape[0], stopped_at_index + s)

    # Market data (price only)
    dates = [datetime.fromtimestamp(d/1000) for d in all_trades[f:t, 0]]
    prices = all_trades[f:t, 1]
   
    # Trace
    trace = logic_trace[:, 0] if logic_trace.shape[0] > 0 else np.zeros(0, dtype=np.int64)

    trade_dates = [datetime.fromtimestamp(all_trades[i, 0] / 1000) for i in trace]
    trade_prices = all_trades[trace, 1] if trace.shape[0] > 0 else np.zeros(0, dtype=np.int64)
   
    # Plot (single axis)
    plt.clf()
    _, ax = plt.subplots(1, 1, figsize=(20, 8))

    ax.plot(dates, prices)
    ax.set_title('Price')

    # Draw lines: (top_target, btm_target, top_threshold, btm_threshold)
    top_target, btm_target, top_threshold, btm_threshold = lines
    ax.axhline(y=top_target, color='gray', linestyle='--')
    ax.axhline(y=btm_target, color='gray', linestyle='--')
    ax.axhline(y=top_threshold, color='black', linestyle=':')
    ax.axhline(y=btm_threshold, color='black', linestyle=':')

    # Group (chase) separators
    if groups.shape[0] > 0:
        mps = np.unique(groups[:, 6])
        for mp in mps:
            mp = int(mp)
            mp_mask = groups[:, 6] == mp
            mp_rows = groups[mp_mask]
            if mp_rows.shape[0] == 0:
                continue
            open_idx = int(mp_rows[0, 0])
            open_dt = datetime.fromtimestamp(all_trades[open_idx, 0] / 1000)
            side = int(mp_rows[0, 5])
            vcolor = 'green' if side == 1 else 'red'
            ax.axvline(open_dt, color=vcolor, linestyle='--', linewidth=1)

    # Scatter violated orders
    if groups.shape[0] > 0:
        v_orders = groups[groups[:, 3] == -1]
        if v_orders.shape[0] > 0:
            v_dates = [datetime.fromtimestamp(d/1000) for d in all_trades[v_orders[:, 0], 0]]
            v_prices = v_orders[:, 2]
            ax.scatter(v_dates, v_prices, c='black', marker='x', s=100)

    # Plot non-violated orders with side-based colors
    n_orders = groups[groups[:, 3] != -1] if groups.shape[0] > 0 else np.zeros((0, 7), dtype=np.int64)
    for i in range(n_orders.shape[0]):
        o = n_orders[i]
        dt_open = datetime.fromtimestamp(all_trades[o[0], 0] / 1000)
        dt_close = datetime.fromtimestamp(all_trades[o[1], 0] / 1000)
        price = o[2]
        side = o[5]
        order_color = 'green' if side == 1 else 'red'
        ax.plot([dt_open, dt_close], [price, price], c=order_color)

    # Scatter trace per chase with side-based color if available
    if logic_trace.shape[0] > 0 and groups.shape[0] > 0:
        mps = np.unique(logic_trace[:, 2])
        for mp in mps:
            mp = int(mp)
            tr_mask = logic_trace[:, 2] == mp
            tr_idx = logic_trace[tr_mask][:, 0]
            if tr_idx.shape[0] == 0:
                continue
            
            # derive side from any group with this mp
            gmask = groups[:, 6] == mp
            if np.any(gmask):
                side = int(groups[gmask][0, 5])
                order_color = 'green' if side == 1 else 'red'
            else:
                order_color = 'blue'
            td = [datetime.fromtimestamp(all_trades[i, 0] / 1000) for i in tr_idx]
            tp = all_trades[tr_idx, 1]
            ax.scatter(td, tp, c=order_color, s=10)
    else:
        # Scatter trace without side info
        ax.scatter(trade_dates, trade_prices, c='blue', s=10)

    if to_file:
        ensure_dir(os.path.dirname(to_file))
        plt.savefig(to_file)
    else:
        plt.show()

    plt.close()

def random_logic(test_volume_usd,
                 max_time_ms,
                 network_delay_ms,
                 distance_steps,
                 threshold_as_perc,
                 d_top_threshold,
                 d_btm_threshold,
                 d_top_target_perc,
                 d_btm_target_perc,
                 only_side,
                 entrance_timeout_ms,
                 all_trades,
                 price_step,
                 volume_step,
                 maker_fee=0.0,
                 to_file=None):
    """
    Runs a single random high-level logic test including open/close chases and plots results.

       Input:
          - test_volume_usd: notional size in quote currency
          - max_time_ms: per-chase max wait time
          - network_delay_ms: simulated latency (ms)
          - distance_steps: chase price step distance
          - threshold_as_perc: when True, thresholds are percents of price
          - d_top_threshold: upper threshold offset (steps or percents)
          - d_btm_threshold: lower threshold offset (steps or percents)
          - d_top_target_perc: upper target as percent of intended price
          - d_btm_target_perc: lower target as percent of intended price
          - only_side: 1 long-only, -1 short-only, 0 both
          - entrance_timeout_ms: if only_side != 0 and no entry within this time, stop
          - all_trades: array [time, price, volume, is_buyer_maker]
          - price_step: real price step
          - volume_step: real volume step
          - maker_fee: maker fee in fraction (e.g., 0.0002)
          - to_file: optional path to save the plot

       Output:
          - None. Prints stats and shows/saves plots and detailed trace.
    """
    # Select random index
    index = random.randint(0, all_trades.shape[0] - 1)

    print(f'Index: {index}')

    # Get price
    intended_price = all_trades[index, 1]

    # Convert volume
    price_real = intended_price * price_step
    print(f'Real price: {price_real} (intended)')

    # Quote volume to base
    base_volume = test_volume_usd / price_real if price_real > 0 else 0.0
    print(f'Base volume: {base_volume:.4f}')

    # To units
    test_volume_units = int(base_volume / volume_step) if volume_step > 0 else 0
    print(f'Test volume units: {test_volume_units:.4f} for volume step {volume_step}')

    # Calculate targets in units
    d_top_target = int(intended_price * d_top_target_perc / 100)
    d_btm_target = int(intended_price * d_btm_target_perc / 100)
    assert d_top_target > 1 and d_btm_target > 1, f'Targets must be greater than 1, got: {d_top_target}, {d_btm_target}'

    # Run logic
    stopped_at_index, groups, logic_trace, lines, neutral_excursions, err = run_logic(
        all_trades=all_trades,
        start_index=index,
        volume=test_volume_units,
        max_time_ms=max_time_ms,
        network_delay_ms=network_delay_ms,
        distance_steps=distance_steps,
        threshold_as_perc=threshold_as_perc,
        d_top_threshold=d_top_threshold,
        d_btm_threshold=d_btm_threshold,
        d_top_target=d_top_target,
        d_btm_target=d_btm_target,
        only_side=only_side,
        entrance_timeout_ms=entrance_timeout_ms,
    )

    # Stats
    stat = get_logic_stat(
        all_trades=all_trades,
        groups=groups,
        logic_trace=logic_trace,
        lines=lines,
        start_index=index,
        stopped_at_index=stopped_at_index,
        err=err,
        test_volume_units=test_volume_units,
        price_step=price_step,
        volume_step=volume_step,
        test_volume_usd=test_volume_usd,
        maker_fee=maker_fee,
    )

    total_trades_executed = stat['agg']['total_trades_executed']
    avg_po_violations_pct = stat['agg']['avg_po_violations_pct']
    avg_slippage = stat['agg']['avg_slippage']
    full_execution_time = stat['agg']['full_execution_time']

    print(f"Stopped at index: {stat['stopped_at_index']} ({total_trades_executed} trades executed)")
    print(f"Groups shape: ({groups.shape[0]}, 7)")
    print(f"Logic trace shape: ({logic_trace.shape[0]}, 3)")
    print(f"Neutral excursions (per chase): {len(neutral_excursions)}")
    print(f"Average post-only violations: {avg_po_violations_pct:.2f}%")
    print(f"Full execution time: {full_execution_time:.4f} seconds")
    print(f"Avg. slippage: {avg_slippage:.2f}%")
    print(f"PnL: {stat['agg']['pnl']:.6f}")

    # Show
    plot_logic(
        all_trades=all_trades,
        start_index=index,
        stopped_at_index=stopped_at_index,
        groups=groups,
        logic_trace=logic_trace,
        lines=lines,
        to_file=to_file,
        s=50,
    )
    
    # Get logic trace
    trace = get_logic_trace(
        all_trades=all_trades,
        groups=groups,
        price_step=price_step,
        volume_step=volume_step,
        maker_fee=maker_fee,
    )
    
    # Print logic trace
    print_logic_trace(trace)