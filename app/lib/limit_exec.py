"""
Base simulation of limit-order execution and its visualization.

Provides:
 - execute_limit_order(all_trades, index, side, price, volume, max_time_ms, alloc) ->
   (stopped_at_index, volume_left, trace):
   execute a limit order with timeout and record execution trace.
 - plot_limit_order_execution(...) -> None:
   visualize price/volume, the order line and cumulative executed volume.
 - random_test(...) -> None:
   run a demo limit-order execution and plot results.
"""
# Base execution simulation method
import numpy as np, matplotlib.pyplot as plt, random, os
from numba import njit
from datetime import datetime

from lib.helders import ensure_dir

@njit
def execute_limit_order(
        all_trades: np.ndarray,
        index: int,
        side: int,
        price: int,
        volume: int,
        max_time_ms: int,
        alloc: int = 1000
    ) -> tuple[int, int, np.ndarray]:
    """
    Executes a limit order at a given index and price:
    
       Input:
          - all_trades: numpy array of shape (N, 4)
          - index: an index to start
          - side: 1 for buy, -1 for sell
          - price: order price
          - volume: order volume
          - max_time_ms: maximum time to wait for execution if price is out of execution zone
          - alloc: allocation size for trace
       Output:
          - stopped_at_index:
            - > 0: index of the last trade executed
            - 0: no execution
            - < 0: error code
          - volume_left: volume left to execute
          - trace of execution: numpy array of shape (M,), indexes of executions
    """
    # Allocate trace
    trace = np.zeros(alloc, dtype=np.int64)
    pos = 0

    # Volume left
    volume_left = volume

    # Validate input
    if index < 0 or index >= all_trades.shape[0]:
        return -1, volume_left, trace[:pos] # index out of bounds

    if side != 1 and side != -1:
        return -2, volume_left, trace[:pos] # invalid side

    if price <= 0:
        return -3, volume_left, trace[:pos] # invalid price

    if volume <= 0:
        return -4, volume_left, trace[:pos] # invalid volume

    # Post-only: check order price against current price
    current_price = all_trades[index, 1]

    if side == 1 and price >= current_price:
        return -5, volume_left, trace[:pos] # post-only buy order at or above current price

    if side == -1 and price <= current_price:
        return -6, volume_left, trace[:pos] # post-only sell order at or below current price

    # Execute order
    stopped_at_index = index # nothing's done by default
    start_time_ms = all_trades[index, 0]
    
    for i in range(index, all_trades.shape[0]):
        # Split trade
        _, p, v, m = all_trades[i]

        # Ignore if not matching side
        if side == 1: # buy
            if m == 0: # and seller is maker, buyer is taker (not maker)
                continue
        
        if side == -1: # sell
            if m == 1: # and buyer is maker, seller is taker (not maker)
                continue

        # Check if price in execution zone
        in_exec_zone = False

        if side == 1:
            if p <= price:
                in_exec_zone = True

        if side == -1:
            if p >= price:
                in_exec_zone = True

        if not in_exec_zone:
            # Get current time
            current_time_ms = all_trades[i, 0]

            # Check for timeout if price is out of execution zone
            if current_time_ms - start_time_ms > max_time_ms:
                break

            continue # proceed execution otherwise

        # Remove volume
        volume_left = max(0, volume_left - v)

        # Add to trace
        trace[pos] = i
        pos += 1
        
        # Allocate more trace if needed
        if pos == trace.shape[0]:
            trace = np.concatenate((trace, np.zeros(alloc, dtype=np.int64)))

        # Update stopped at index
        stopped_at_index = i

        # Check if stopped
        if volume_left == 0:
            break

    return stopped_at_index, volume_left, trace[:pos]

def plot_limit_order_execution(all_trades, index, stopped_at_index, trace, side, price, to_file=None, s=50):
    """
    Plots limit order execution context: price, volume and cumulative executed volume.

       Input:
          - all_trades: numpy array of shape (N, 4) with columns [time, price, volume, is_buyer_maker]
          - index: order creation index
          - stopped_at_index: order close (last execution) index
          - trace: numpy array of shape (M,) with executed trade indices
          - side: 1 for buy, -1 for sell
          - price: integer limit price (in steps)
          - to_file: optional path to save the figure
          - s: half-window size around [index..stopped_at_index] for context

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
   
    # Order line
    create_dt = datetime.fromtimestamp(all_trades[index, 0] / 1000)
    delete_dt = datetime.fromtimestamp(all_trades[stopped_at_index, 0] / 1000)

    # Trace
    trade_dates = [datetime.fromtimestamp(all_trades[i, 0] / 1000) for i in trace]
    trade_prices = all_trades[trace, 1]
    trace_volume = all_trades[trace, 2]

    # Plot
    plt.clf()
    _, ax = plt.subplots(3, 1, figsize=(20, 16))

    ax[0].plot(dates, prices)
    ax[0].set_title('Price')

    ax[1].plot(dates, volumes)
    ax[1].set_title('Volume')

    ax[2].plot(trade_dates, np.cumsum(trace_volume))
    ax[2].set_title('Trace volume')

    # Order line
    ax[0].plot([create_dt, delete_dt], [price, price])

    # Scatter trace
    ax[0].scatter(trade_dates, trade_prices, c='green' if side == 1 else 'red', s=10)
    
    if to_file is not None:
        ensure_dir(os.path.dirname(to_file))
        plt.savefig(to_file)
    else:
        plt.show()
        
    plt.close()

# Random test
def random_test(test_volume_usd, max_time_ms, all_trades, price_step, volume_step, to_file=None):
    """
    Runs a single random limit order execution test and plots the result.

       Input:
          - test_volume_usd: order notional in quote currency
          - max_time_ms: maximum wait time outside execution zone
          - all_trades: numpy array of shape (N, 4) with columns [time, price, volume, is_buyer_maker]
          - price_step: real price step
          - volume_step: real volume step
          - to_file: optional path to save the plot

       Output:
          - None. Prints stats and shows/saves plot.
    """
    # Select random index
    index = random.randint(0, all_trades.shape[0] - 1)

    # Random side
    side = random.choice([1, -1])

    # Random price
    current_price = all_trades[index, 1]
    random_shift = random.randint(1, 10) # in price units

    price = current_price - side * random_shift
    print(f'Current price: {current_price}, random shift: {random_shift}, side: {side}, price: {price}')

    # Convert volume
    price_real = price * price_step
    print(f'Real price: {price_real}')

    # Quote volume to base
    base_volume = test_volume_usd / price_real
    print(f'Base volume: {base_volume:.4f}')

    # To units
    test_volume_units = int(base_volume / volume_step)
    print(f'Test volume units: {test_volume_units:.4f} for volume step {volume_step}')

    # Test execution
    stopped_at_index, volume_left, trace = execute_limit_order(all_trades, index, side, price, test_volume_units, max_time_ms)
    print(f'Stopped at index: {stopped_at_index} ({stopped_at_index - index} trades executed)')
    print(f'Trace shape: {trace.shape}')
    print(f'Volume left: {volume_left} ({volume_left / test_volume_units * 100}%)')

    # Measure drift
    stopped_at_price = all_trades[stopped_at_index, 1]

    d = 100 * (float(stopped_at_price) / float(price) - 1)
    drift = max(0, - side * d)
    print(f'Drift: {drift}%')

    # Measure execution time
    current_time = all_trades[index, 0]
    stopped_time = all_trades[stopped_at_index, 0]

    execution_time = (stopped_time - current_time) / 1000
    print(f'Execution time: {execution_time:.4f} seconds')

    # Show
    plot_limit_order_execution(all_trades, index, stopped_at_index, trace, side, price, to_file=to_file)
