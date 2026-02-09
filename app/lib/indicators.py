"""
Trend and volatility indicators computed on time-aligned windows.

Provides:
 - _build_time_window_indices(times, index, tf_ms, period) -> (idxs, err):
   build a window of indices of length 'period' with step 'tf_ms', ending at 'index'.
 - _centered_time(n) -> np.ndarray:
   centered time scale [0..n-1] shifted to zero mean.
 - _build_returns_from_prices(prices, eps) -> np.ndarray:
   simple returns r[t] = p[t]/p[t-1] - 1 (in percent).
 - _pearson_corr(a, b, eps) -> float:
   Pearson correlation with zero-division guard.
 - get_trend_indicator(all_trades, index, tf_ms, period) -> (trend, err):
   tanh(corr(time, returns)) over a single window.
 - get_volatility_indicator(all_trades, index, tf_ms, period) -> (vol, err):
   standard deviation of returns over a window.
 - get_trend_and_volatility_indicator(...) -> (trend, vol, err):
   joint computation of trend and volatility on the same window.
"""
import numpy as np
from numba import njit

@njit
def _build_time_window_indices(
    times: np.ndarray,
    index: int,
    tf_ms: int,
    period: int,
) -> tuple[np.ndarray, int]:
    """
    Builds a time-aligned window of exactly period indices ending at times[index].
    Uses searchsorted to step back by tf_ms for each point.

       Input:
          - times: 1D array of timestamps in ms, non-decreasing
          - index: end index of the window (inclusive)
          - tf_ms: timeframe in milliseconds between points
          - period: required number of points
       Output:
          - idxs: array of indices of length period
          - err:
            - 0: no error
            - <0: not enough points found in the past
    """
    # Collect exactly period indices going back in time by tf_ms, ending at times[index]
    out = np.empty(period, dtype=np.int64)
    end_time = times[index]
    upto = times[: index + 1]

    for k in range(period):
        target = end_time - k * tf_ms
        pos = np.searchsorted(upto, target, side='right') - 1
        if pos < 0:
            return out, -1
        out[period - 1 - k] = pos

    return out, 0

@njit
def _centered_time(n: int) -> np.ndarray:
    """
    Builds centered time index [0..n-1] shifted to zero mean.

       Input:
          - n: number of points
       Output:
          - t: array of shape (n,)
    """
    t = np.empty(n, dtype=np.float64)
    for i in range(n):
        t[i] = float(i)
    mean_t = (n - 1) / 2.0
    for i in range(n):
        t[i] = t[i] - mean_t
    return t

@njit
def _build_returns_from_prices(prices: np.ndarray, eps: float) -> np.ndarray:
    """
    Computes simple returns: r[t] = price[t] / price[t-1] - 1
    Uses eps guard for division safety.

       Input:
          - prices: array of shape (N,)
          - eps: epsilon guard for zero division
       Output:
          - returns: array of shape (N-1,)
    """
    n = prices.shape[0]
    out = np.empty(n - 1, dtype=np.float64)
    for i in range(n - 1):
        prev = prices[i]
        if prev < eps:
            prev = eps
        out[i] = 100 * (prices[i + 1] / prev - 1.0) # in percent
    return out

@njit
def _pearson_corr(a: np.ndarray, b: np.ndarray, eps: float) -> float:
    """
    Pearson correlation for two same-length vectors.
    Both arrays are centered inside.
    """
    n = a.shape[0]

    mean_a = 0.0
    mean_b = 0.0
    for i in range(n):
        mean_a += a[i]
        mean_b += b[i]
    mean_a /= n
    mean_b /= n

    cov = 0.0
    ss_a = 0.0
    ss_b = 0.0
    for i in range(n):
        da = a[i] - mean_a
        db = b[i] - mean_b
        cov += da * db
        ss_a += da * da
        ss_b += db * db
    cov /= n

    std_a = np.sqrt(ss_a / n + eps)
    std_b = np.sqrt(ss_b / n + eps)
    return cov / (std_a * std_b + eps)

@njit
def get_trend_indicator(
    all_trades: np.ndarray,
    index: int,
    tf_ms: int,
    period: int,
) -> tuple[float, int]:
    """
    Trend indicator based on Pearson correlation between centered time and log-price.
    Builds a time window by searchsorted aligned to tf_ms. Requires exactly period points.

       Input:
          - all_trades: array of shape (N, 4) with columns [time, price, volume, is_buyer_maker]
          - index: end index (inclusive) to compute indicator at
          - tf_ms: timeframe in milliseconds
          - period: number of points required
       Output:
          - trend: value in (-1, 1) via tanh(correlation)
          - err:
            - 0: ok
            - <0: not enough data points for the requested window
    """
    times = all_trades[:, 0]
    prices = all_trades[:, 1]

    idxs, err = _build_time_window_indices(times, index, tf_ms, period)
    if err != 0:
        return 0.0, err

    if period < 2:
        return 0.0, -1

    # Price window
    px = np.empty(period, dtype=np.float64)
    for i in range(period):
        px[i] = float(prices[idxs[i]])

    # Returns and centered time
    eps = 1e-12
    rets = _build_returns_from_prices(px, eps)
    t = _centered_time(rets.shape[0])

    # Correlation and squash
    corr = _pearson_corr(t, rets, eps)
    return np.tanh(corr), 0

@njit
def get_volatility_indicator(
    all_trades: np.ndarray,
    index: int,
    tf_ms: int,
    period: int,
) -> tuple[float, int]:
    """
    Volatility indicator: standard deviation of returns over a single window.
    Builds time-aligned window by searchsorted, then std of r[t] = p[t]/p[t-1] - 1.

       Input:
          - all_trades: array of shape (N, 4) with columns [time, price, volume, is_buyer_maker]
          - index: end index (inclusive) to compute indicator at
          - tf_ms: timeframe in milliseconds
          - period: number of price points in the window (>= 2)
       Output:
          - value: std(returns) over the window
          - err:
            - 0: ok
            - <0: not enough data points for the requested window
    """
    times = all_trades[:, 0]
    prices = all_trades[:, 1]

    idxs, err = _build_time_window_indices(times, index, tf_ms, period)
    if err != 0:
        return 0.0, err

    if period < 2:
        return 0.0, -1

    # Price window
    px = np.empty(period, dtype=np.float64)
    for i in range(period):
        px[i] = float(prices[idxs[i]])

    # Returns and std
    eps = 1e-12
    rets = _build_returns_from_prices(px, eps)
    return np.std(rets), 0

@njit
def get_trend_and_volatility_indicator(
    all_trades: np.ndarray,
    index: int,
    tf_ms: int,
    period: int,
) -> tuple[float, float, int]:
    """
    Combined indicator: trend (tanh(corr(time, returns))) and volatility (std of returns).
    Computes both on the same time-aligned window to avoid duplicate work.

       Input:
          - all_trades: array of shape (N, 4) with columns [time, price, volume, is_buyer_maker]
          - index: end index (inclusive) to compute indicator at
          - tf_ms: timeframe in milliseconds
          - period: number of price points in the window (>= 2)
       Output:
          - trend: value in (-1, 1) via tanh(correlation with centered time)
          - vol: std(returns) over the window
          - err:
            - 0: ok
            - <0: not enough data points for the requested window
    """
    times = all_trades[:, 0]
    prices = all_trades[:, 1]

    idxs, err = _build_time_window_indices(times, index, tf_ms, period)
    if err != 0:
        return 0.0, 0.0, err

    if period < 2:
        return 0.0, 0.0, -1

    # Price window
    px = np.empty(period, dtype=np.float64)
    for i in range(period):
        px[i] = float(prices[idxs[i]])

    # Returns, centered time
    eps = 1e-12
    rets = _build_returns_from_prices(px, eps)
    t = _centered_time(rets.shape[0])

    # Trend and vol
    corr = _pearson_corr(t, rets, eps)
    trend = np.tanh(corr)
    vol = np.std(rets)

    return trend, vol, 0