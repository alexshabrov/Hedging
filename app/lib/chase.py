"""
Chase module: simulates sequences of limit orders ("chase") and visualizes results.

Provides:
 - find_next_index(all_trades, index, network_delay_ms) -> int:
   find the next index accounting for network delay.
 - chase_limit_orders(all_trades, index, side, volume, max_time_ms, network_delay_ms, distance_steps, alloc) ->
   (stopped_at_index, volume_left, orders, global_trace):
   execute a multi-order chase and collect its execution trace.
 - get_w_avg_price(all_trades, orders, global_trace) -> float:
   compute weighted average execution price from the trace.
 - get_chase_stat(...) -> dict:
   build per-chase statistics (time, slippage, leftover, etc.).
 - plot_limit_chase(...) -> None:
   visualize price/volume, orders and chase trace.
 - random_chase(...) -> None:
   run a demo chase from a random start and print/plot stats.
"""
import numpy as np, random, matplotlib.pyplot as plt, os
from datetime import datetime
from numba import njit

from lib.limit_exec import execute_limit_order
from lib.helders import ensure_dir

# Chase simulation method
@njit
def find_next_index(
        all_trades: np.ndarray,
        index: int,
        network_delay_ms: int,
    ) -> int:
    """
    Finds index at least network_delay_ms away from index

       Input:
          - all_trades: numpy array of shape (N, 4)
          - index: an index to start
          - network_delay_ms: network delay in milliseconds for trades delivery
       Output:
          - index: index of the next trade or -1 if no such index found
    """
    next_index = -1
    start_time_ms = all_trades[index, 0]

    for i in range(index + 1, all_trades.shape[0]):
        current_time_ms = all_trades[i, 0]

        if current_time_ms - start_time_ms >= network_delay_ms:
            next_index = i
            break

    return next_index

@njit
def chase_limit_orders(
        all_trades: np.ndarray,
        index: int,
        side: int,
        volume: int,
        max_time_ms: int,
        network_delay_ms: int,
        distance_steps: int,
        alloc: int = 1000,
    ) -> tuple[int, int, np.ndarray, np.ndarray]:
    """
    Chases price using multiple limit orders
    
       Input:
          - all_trades: numpy array of shape (N, 4)
          - index: an index to start
          - side: 1 for buy, -1 for sell
          - volume: order volume
          - max_time_ms: maximum time to wait for execution if price is out of execution zone
          - network_delay_ms: network delay in milliseconds for trades delivery
          - distance_steps: distance in price steps to place next order (from the last known trade price)
          - alloc: allocation size for trace
       Output:
          - stopped_at_index:
            - > 0: index of the last trade executed
            - 0: no execution
            - < 0: error code
          - volume_left: volume left to execute
          - orders: numpy array of shape (n, 4)
            - n orders
            - 0: index of the order creation in trades
            - 1: index of the order deletion in trades or current index if order is still active
            - 2: price of the order
            - 3: order status: -1 - post-only violation, volume_left otherwise
          - combined trace of execution: numpy array of shape (M, 2), indexes of executions and indexes
            of the order in orders
    """
    # Allocate global trace
    global_trace = np.zeros((alloc, 2), dtype=np.int64)
    global_trace_pos = 0

    # Allocate orders: [open_idx, close_idx, price, status_or_left, volume_executed]
    orders = np.zeros((alloc, 5), dtype=np.int64)
    orders_pos = 0
    
    # Global volume left
    volume_left = volume

    # Chase
    while index < all_trades.shape[0]:
        # Current price at the exchange side
        current_price = all_trades[index, 1]

        # ... will be known by user only after network delay
        index = find_next_index(all_trades, index, network_delay_ms)
        if index == -1:
            break

        # So we know the current price, calculating order price
        price = current_price - side * distance_steps

        # Sending order, it takes a network delay too
        index = find_next_index(all_trades, index, network_delay_ms)
        if index == -1:
            break

        # Try to execute order
        order_volume = volume_left
        
        stopped_at_index, volume_left, trace = execute_limit_order(
            all_trades,
            index,
            side,
            price,
            order_volume,
            max_time_ms,
            alloc
        )

        # Check for post-only violation
        post_only_violation = False

        if side == 1 and stopped_at_index == -5:
            post_only_violation = True

        if side == -1 and stopped_at_index == -6:
            post_only_violation = True

        if post_only_violation:
            # Add to orders
            orders[orders_pos, 0] = index
            orders[orders_pos, 1] = index
            orders[orders_pos, 2] = price
            orders[orders_pos, 3] = -1 # post-only violation
            orders[orders_pos, 4] = 0  # volume executed
            orders_pos += 1

            # Allocate more orders if needed
            if orders_pos == orders.shape[0]:
                orders = np.concatenate((orders, np.zeros((alloc, 5), dtype=np.int64)))

            continue

        # Check for another violation
        if stopped_at_index < 0:
            break # runtime error

        # Add to global trace
        while trace.shape[0] + global_trace_pos > global_trace.shape[0]:
            global_trace = np.concatenate((global_trace, np.zeros((alloc, 2), dtype=np.int64)))

        global_trace[global_trace_pos:global_trace_pos + trace.shape[0], 0] = trace
        global_trace[global_trace_pos:global_trace_pos + trace.shape[0], 1] = orders_pos # order index
        global_trace_pos += trace.shape[0]

        # Add to orders
        orders[orders_pos, 0] = index
        orders[orders_pos, 1] = stopped_at_index
        orders[orders_pos, 2] = price
        orders[orders_pos, 3] = volume_left
        orders[orders_pos, 4] = order_volume - volume_left
        orders_pos += 1

        # Allocate more orders if needed
        if orders_pos == orders.shape[0]:
            orders = np.concatenate((orders, np.zeros((alloc, 5), dtype=np.int64)))

        # Update index
        index = stopped_at_index

        # Stop if volume is executed
        if volume_left == 0:
            break

    return index, volume_left, orders[:orders_pos], global_trace[:global_trace_pos]

@njit
def get_w_avg_price(all_trades, orders, global_trace):
    """
    Computes weighted average price of executed orders
    Uses volumes from trades and prices from orders (not trades)

       Input:
          - all_trades: numpy array of shape (N, 4)
          - orders: numpy array of shape (n, 4)
          - global_trace: numpy array of shape (M, 2)

       Output:
          - w_avg_price: weighted average price
    """
    w_avg_price = 0
    w_sum = 0

    for i in range(global_trace.shape[0]):
        # Split trace step
        idx, order_pos = global_trace[i]

        # Get order
        order = orders[order_pos]

        # Get order price
        price = float(order[2])

        # Get trade volume
        volume = float(all_trades[idx, 2])

        # Update weighted average price
        w_avg_price += price * volume
        w_sum += volume

    return w_avg_price / w_sum if w_sum > 0 else 0

def get_chase_stat(all_trades,
                   index: int,
                   side: int,
                   test_volume_usd: float,
                   test_volume_units: int,
                   price_step: float,
                   volume_step: float,
                   stopped_at_index: int,
                   orders: np.ndarray,
                   global_trace: np.ndarray,
                   volume_left: int) -> dict:
    """
    Computes and returns statistics for a single chase.

       Input:
          - all_trades: numpy array of shape (N, 4) with columns [time, price, volume, is_buyer_maker]
          - index: starting index of the chase
          - side: 1 for long (buy then sell), -1 for short (sell then buy)
          - test_volume_usd: notional size in quote currency (for info only)
          - test_volume_units: volume in base units (quantized by volume_step)
          - price_step: real price step (float) to convert integer price to real
          - volume_step: real volume step (float) to convert integer volume to real
          - stopped_at_index: final index reached by the chase (max close index of its orders)
          - orders: numpy array of shape (G, 5) with rows:
            [open_idx, close_idx, price_int, status_or_left, volume_executed]
            - status_or_left == -1 denotes post-only violation (no execution)
          - global_trace: numpy array of shape (T, 2)
            - column 0: trade index
            - column 1: order row index in orders
          - volume_left: remaining unfilled volume at the end (units in volume steps)

       Output:
          - dict with fields:
            - index: start index
            - side: side of the chase (1 or -1)
            - intended_price: integer price at start
            - price_real: real price at start (intended_price * price_step)
            - base_volume: approx base units for test_volume_usd at intended price
            - test_volume_units: requested volume in integer units
            - stopped_at_index: last index reached by the chase
            - trades_executed: number of trades processed (stopped_at_index - index)
            - trace_size: number of trade events in global_trace
            - volume_left: leftover volume (units)
            - volume_left_pct: leftover volume in % of requested
            - po_violations_pct: percent of orders with post-only violation
            - execution_time: seconds between start and stop times
            - exec_price: weighted average execution price from orders and trade volumes
            - slippage: signed slippage (%) vs intended_price, adjusted by side
    """
    # Intended price and real price
    intended_price = float(all_trades[index, 1])
    price_real = intended_price * float(price_step)

    # Base volume in base asset units (for info)
    base_volume = float(test_volume_usd) / price_real if price_real > 0 else 0.0

    # Post-only violations percent
    n_orders = int(orders.shape[0])
    po_violations = 0.0
    if n_orders > 0:
        po_violations = 100.0 * float(np.sum(orders[:, 3] == -1)) / float(n_orders)

    # Execution time
    current_time = float(all_trades[index, 0])
    stopped_time = float(all_trades[stopped_at_index, 0])
    execution_time = (stopped_time - current_time) / 1000.0

    # Weighted average execution price and slippage
    exec_price = float(get_w_avg_price(all_trades, orders, global_trace))
    slippage = 0.0
    if intended_price > 0:
        slippage = -100.0 * float(side) * (exec_price / intended_price - 1.0)

    # Derived measures
    trades_executed = int(stopped_at_index - index)
    trace_size = int(global_trace.shape[0])
    volume_left_pct = (float(volume_left) / float(test_volume_units) * 100.0) if test_volume_units > 0 else 0.0

    return {
        'index': int(index),
        'side': int(side),
        'intended_price': float(intended_price),
        'price_real': float(price_real),
        'base_volume': float(base_volume),
        'test_volume_units': int(test_volume_units),
        'stopped_at_index': int(stopped_at_index),
        'trades_executed': trades_executed,
        'trace_size': trace_size,
        'volume_left': int(volume_left),
        'volume_left_pct': float(volume_left_pct),
        'po_violations_pct': float(po_violations),
        'execution_time': float(execution_time),
        'exec_price': float(exec_price),
        'slippage': float(slippage),
    }

def plot_limit_chase(all_trades, index, stopped_at_index, orders, global_trace, side, to_file=None, s=50):
    """
    Plots chase context: price, volume, cumulative traded volume, orders and trace.

       Input:
          - all_trades: numpy array of shape (N, 4) with columns [time, price, volume, is_buyer_maker]
          - index: start index of the chase
          - stopped_at_index: final index reached by the chase
          - orders: numpy array of shape (G, 5) -> [open_idx, close_idx, price, status_or_left, volume_executed]
          - global_trace: numpy array of shape (T, 2) -> [trade_idx, order_pos]
          - side: 1 for long, -1 for short (used for coloring)
          - to_file: optional path to save the plot; if None, shows the figure
          - s: half-window size (number of trades) around [index..stopped_at_index] to visualize

       Output:
          - None. Saves or shows a matplotlib plot.
    """
    # Get bounds
    f = max(0, index - s)
    t = min(all_trades.shape[0], stopped_at_index + s)

    # Market data
    dates = [datetime.fromtimestamp(d/1000) for d in all_trades[f:t, 0]]
    prices = all_trades[f:t, 1]
    volumes = all_trades[f:t, 2]
   
    # Trace
    trace = global_trace[:, 0]

    trade_dates = [datetime.fromtimestamp(all_trades[i, 0] / 1000) for i in trace]
    trade_prices = all_trades[trace, 1]
    trace_volume = all_trades[trace, 2]

    # Plot
    order_color = 'green' if side == 1 else 'red'

    plt.clf()
    _, ax = plt.subplots(3, 1, figsize=(20, 16))

    ax[0].plot(dates, prices)
    ax[0].set_title('Price')

    ax[1].plot(dates, volumes)
    ax[1].set_title('Volume')

    ax[2].plot(trade_dates, np.cumsum(trace_volume))
    ax[2].set_title('Trace volume')

    # Scatter violated orders
    v_orders = orders[orders[:, 3] == -1]
    v_dates = [datetime.fromtimestamp(d/1000) for d in all_trades[v_orders[:, 0], 0]]
    v_prices = v_orders[:, 2]

    ax[0].scatter(v_dates, v_prices, c='black', marker='x', s=100)

    # Plot non-violated orders
    n_orders = orders[orders[:, 3] != -1]

    for i in range(n_orders.shape[0]):
        o = n_orders[i]

        dt_open = datetime.fromtimestamp(all_trades[o[0], 0] / 1000)
        dt_close = datetime.fromtimestamp(all_trades[o[1], 0] / 1000)
        price = o[2]

        ax[0].plot([dt_open, dt_close], [price, price], c=order_color)

    # Scatter trace
    ax[0].scatter(trade_dates, trade_prices, c=order_color, s=10)

    if to_file:
        ensure_dir(os.path.dirname(to_file))
        plt.savefig(to_file)
    else:
        plt.show()

    plt.close()

# Random test
def random_chase(test_volume_usd, max_time_ms, network_delay_ms, distance_steps,
                 all_trades, price_step, volume_step, to_file=None):
    """
    Runs a single random chase for an illustrative test and plots the result.

       Input:
          - test_volume_usd: notional size in quote currency
          - max_time_ms: maximum wait time per limit order outside execution zone
          - network_delay_ms: simulated network latency (ms)
          - distance_steps: price distance (steps) for next order
          - all_trades: numpy array of shape (N, 4) with columns [time, price, volume, is_buyer_maker]
          - price_step: real price step
          - volume_step: real volume step
          - to_file: optional path to save plots; if None, shows the figures

       Output:
          - None. Prints stats and shows/saves plots.
    """
    # Select random index
    index = random.randint(0, all_trades.shape[0] - 1)

    # Random side
    side = random.choice([1, -1])
    print(f'Side: {side}')

    # Get price
    intended_price = all_trades[index, 1]

    # Convert volume
    price_real = intended_price * price_step
    print(f'Real price: {price_real} (intended)')

    # Quote volume to base
    base_volume = test_volume_usd / price_real
    print(f'Base volume: {base_volume:.4f}')

    # To units
    test_volume_units = int(base_volume / volume_step)
    print(f'Test volume units: {test_volume_units:.4f} for volume step {volume_step}')

    # Chase
    stopped_at_index, volume_left, orders, global_trace = chase_limit_orders(
        all_trades,
        index,
        side,
        test_volume_units,
        max_time_ms,
        network_delay_ms,
        distance_steps,
    )

    # Stats
    stat = get_chase_stat(
        all_trades=all_trades,
        index=index,
        side=side,
        test_volume_usd=test_volume_usd,
        test_volume_units=test_volume_units,
        price_step=price_step,
        volume_step=volume_step,
        stopped_at_index=stopped_at_index,
        orders=orders,
        global_trace=global_trace,
        volume_left=volume_left,
    )

    print(f"Stopped at index: {stat['stopped_at_index']} ({stat['trades_executed']} trades executed)")
    print(f"Trace shape: ({stat['trace_size']}, 2)")
    print(f"Volume left: {stat['volume_left']} ({stat['volume_left_pct']:.2f}%)")
    print(f"Post-only violations: {stat['po_violations_pct']:.2f}%")

    print(f"Execution time: {stat['execution_time']:.4f} seconds")
    print(f"Slippage: {stat['slippage']:.2f}%")

    # Show
    plot_limit_chase(all_trades, index, stopped_at_index, orders, global_trace, side, to_file=to_file)
        