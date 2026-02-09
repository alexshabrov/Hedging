"""
Loading and quantizing trades by price/volume steps, caching and simple plotting.

Provides:
 - load_and_process(base_dir, symbol, files, split_map) -> list[(np.ndarray, price_step, volume_step)]:
   load CSV archives, convert to [time, price, volume, is_buyer_maker] (int64),
   apply steps and assemble contiguous segments.
 - save_to_pickle(base_dir, symbol, data) -> None:
   persist prepared segments to pickle cache.
 - load_from_pickle(base_dir, symbol) -> list[(np.ndarray, price_step, volume_step)]:
   load prepared segments from pickle cache.
 - save_example_plot(all_trades, out_file, n) -> None:
   save a downsampled (uniform) price/volume preview plot.
"""
import os, numpy as np, pandas as pd, matplotlib.pyplot as plt, pickle
from tqdm import tqdm
from datetime import datetime
from lib.helders import *
from lib.steps_splitter import *

# Load CSVs, convert price/volume by step, reorder columns, cast to int64.
# Returns list of (np.ndarray, price_step, volume_step) – one per split segment.
def load_and_process(
        base_dir: str,
        symbol: str,
        files: list[str],
        split_map: list[tuple[str, float, float]],
    ) -> list[tuple[np.ndarray, float, float]]:
    """
    Loads CSV trade files, quantizes price/volume by step per split segment, and returns arrays.
    Uses per-file NPY cache keyed by steps to speed up repeated processing.

       Input:
          - base_dir: base directory with data under <base_dir>/<symbol>/data
          - symbol: market symbol, e.g. 'BTCUSDT'
          - files: list of filenames (ordered by day)
          - split_map: list of tuples (from_file, price_step, volume_step) defining step changes
            - from_file must be present in files

       Output:
          - list of tuples (all_trades, price_step, volume_step) per contiguous segment
            where all_trades is np.ndarray of shape (M, 4) with columns [time, price, volume, is_buyer_maker]
            values are int64 quantized by steps
    """

    assert len(files) > 0, 'Files list must not be empty'
    assert len(split_map) > 0, 'Split map must not be empty'

    data_dir = build_data_dir(base_dir, symbol)
    cache_folder = build_npy_cache_dir(base_dir, symbol)

    # Map: start_index -> (price_step, volume_step)
    start_index_to_steps = {}

    file_to_index = {f: i for i, f in enumerate(files)}

    for from_file, price_step, volume_step in split_map:
        assert from_file in file_to_index, 'Split file not found in files list'
        start_index_to_steps[file_to_index[from_file]] = (price_step, volume_step)

    # Sorted split starts
    split_starts = sorted(start_index_to_steps.keys())

    # Build segment index ranges
    segments = []
    for si, start in enumerate(split_starts):
        end = split_starts[si + 1] if si + 1 < len(split_starts) else len(files)
        price_step, volume_step = start_index_to_steps[start]
        segments.append((start, end, price_step, volume_step))

    results = []

    pbar = tqdm(total=len(files), desc='Processing files')

    for start, end, price_step, volume_step in segments:
        arrays = []

        for i in range(start, end):
            file = files[i]

            # NPY cache for this file under current steps
            # Example: SOLUSDC-trades-2024-01-04.p0.01.v1.0.npy
            cache_file = f'{cache_folder}/{file.replace(".zip", f".p{price_step}.v{volume_step}.npy")}'

            if os.path.exists(cache_file):
                df_np = np.load(cache_file)
                arrays.append(df_np)
                pbar.update(1)
                continue

            # Load as float dataframe (original columns order from Binance: id, price, qty, quoteQty, time, isBuyerMaker, ...)
            df = pd.read_csv(f'{data_dir}/{file}', compression='zip')

            # Convert to numpy float64 for vectorized operations
            df_np = df.to_numpy(dtype=np.float64)

            # Reorder to the working layout:
            # [time, price, volume, is_buyer_maker]
            # - time:           df column index 4
            # - price:          df column index 1
            # - volume (qty):   df column index 2
            # - is_buyer_maker: df column index 5
            df_np = df_np[:, [4, 1, 2, 5]]

            # Quantize price/volume by current segment steps
            # Result remains float64 temporarily to keep vectorized rounding
            df_np[:, 1] = np.round(df_np[:, 1] / price_step)
            df_np[:, 2] = np.round(df_np[:, 2] / volume_step)

            # Commit to int64 representation for compactness and further processing
            df_np = df_np.astype(np.int64)

            # Save to cache
            np.save(cache_file, df_np)

            # Accumulate file block for current segment
            arrays.append(df_np)

            pbar.update(1)

        if arrays:
            arr = np.concatenate(arrays)
            results.append((arr, price_step, volume_step))

    pbar.close()

    return results

# Save processed to pickle
def save_to_pickle(base_dir: str, symbol: str, data: list[tuple[np.ndarray, float, float]]):
    """
    Saves processed segments to a pickle file in cache.

       Input:
          - base_dir: base directory
          - symbol: market symbol
          - data: list of (np.ndarray, price_step, volume_step) from load_and_process

       Output:
          - None. Writes pickle to <base_dir>/<symbol>/cache/pickle/{symbol}.pkl
    """
    folder = build_pickle_cache_dir(base_dir, symbol)
    out_file = f'{folder}/{symbol}.pkl'

    with open(out_file, 'wb') as f:
        pickle.dump(data, f)

# Load processed from pickle
def load_from_pickle(base_dir: str, symbol: str) -> list[tuple[np.ndarray, float, float]]:
    """
    Loads processed segments from pickle cache.

       Input:
          - base_dir: base directory
          - symbol: market symbol

       Output:
          - list of (np.ndarray, price_step, volume_step)
            Raises assertion if pickle file does not exist.
    """
    folder = build_pickle_cache_dir(base_dir, symbol)
    in_file = f'{folder}/{symbol}.pkl'

    assert os.path.exists(in_file), f'Pickle file not found: {in_file}'

    with open(in_file, 'rb') as f:
        return pickle.load(f)

# Save example plot for a block: downsample uniformly to ~n points
def save_example_plot(all_trades: np.ndarray, out_file: str, n: int = 10_000):
    """
    Saves a quick-look plot for a trades array: price and volume, downsampled to ~n points.

       Input:
          - all_trades: array of shape (N, 4) with columns [time, price, volume, is_buyer_maker]
          - out_file: path to save the figure
          - n: target points after uniform downsampling (approx)

       Output:
          - None. Writes a matplotlib PNG file.
    """
    ensure_dir(os.path.dirname(out_file))

    step = max(1, all_trades.shape[0] // n)

    dates = all_trades[:, 0][::step]
    prices = all_trades[:, 1][::step]
    volumes = all_trades[:, 2][::step]

    dates = [datetime.fromtimestamp(d / 1000) for d in dates]

    plt.clf()
    _, ax = plt.subplots(2, 1, figsize=(20, 10))

    ax[0].plot(dates, prices)
    ax[0].set_title('Price')

    ax[1].plot(dates, volumes)
    ax[1].set_title('Volume')

    plt.savefig(out_file)
    plt.close()

