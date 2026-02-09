"""
Detect minimal price/volume steps by day and build a split map.

Provides:
 - get_step(number: str) -> float:
   infer minimal decimal step from a numeric string.
 - detect_step_sizes_by_day(file_list, base_dir, symbol, plot_to_file=None) ->
   (min_price_steps, min_volume_steps, files):
   compute minimal steps per-day (with cache) and optionally plot evolution.
 - get_split_map(min_price_steps, min_volume_steps, files, split_map_min_days)
   -> list[(from_file, price_step, volume_step)]:
   build a split map marking segment starts when steps change with minimal distance.
"""
import pandas as pd, matplotlib.pyplot as plt, os, json
from tqdm import tqdm
from typing import Optional

from lib.helders import *

# Get step from string method
def get_step(number: str) -> float:
    """
    Infers minimal decimal step from a numeric string.
      - '1' -> 1.0
      - '0.0500' -> 0.01

       Input:
          - number: numeric string as from CSV (price or qty)
       Output:
          - minimal step as float
    """
    number = number.split('.')
    if len(number) == 1:
        return 1.0
    else:
        s = number[1]
        s = s.rstrip('0')
        return 10 ** -len(s)

# Detect step sizes by day (using cache)
def detect_step_sizes_by_day(
        file_list: list[str],
        base_dir: str,
        symbol: str,
        plot_to_file: Optional[str] = None,
    ):
    """
    Detects minimal price and volume steps per day using CSV strings and caches results.

       Input:
          - file_list: list of filenames for days to process
          - base_dir: base directory
          - symbol: market symbol
          - plot_to_file: optional path to save visualization of steps evolution

       Output:
          - (min_price_steps, min_volume_steps, files)
            lists aligned with input files order (skipping missing cached days is not performed)
    """

    min_price_steps = []
    min_volume_steps = []
    days = []
    files = []

    data_dir = build_data_dir(base_dir, symbol)
    cache_folder = build_steps_cache_dir(base_dir, symbol)

    pbar = tqdm(total=len(file_list), desc='Detecting step sizes')

    for file in file_list:
        pbar.update(1)
        day = extract_dt_from_filename(file)

        # Cache file
        cache_file = f'{cache_folder}/{file.replace(".zip", ".json")}'
       
        # Check if have already in cache
        if os.path.exists(cache_file):
            data = json.load(open(cache_file, 'r'))

            min_price_steps.append(data['min_price_step'])
            min_volume_steps.append(data['min_volume_step'])
            days.append(day)
            files.append(file)
            continue

        # Load dataframe in string mode
        df = pd.read_csv(f'{data_dir}/{file}', compression='zip', dtype=str)

        # Get prices and volumes as string lists
        prices = df['price'].tolist()
        volumes = df['qty'].tolist()

        # Max step
        min_price_step = min([get_step(p) for p in prices])
        min_volume_step = min([get_step(v) for v in volumes])
    
        min_price_steps.append(min_price_step)
        min_volume_steps.append(min_volume_step)
        days.append(day)
        files.append(file)

        # Save to cache
        with open(cache_file, 'w') as f:
            json.dump({
                'min_price_step': min_price_step,
                'min_volume_step': min_volume_step,
            }, f)

    pbar.close()

    # Plot
    if plot_to_file is not None:
        ensure_dir(os.path.dirname(plot_to_file))
        plt.clf()
        _, ax = plt.subplots(2, 1, figsize=(20, 10))

        ax[0].plot(days, min_price_steps)
        ax[0].set_title('Min price step')

        ax[1].plot(days, min_volume_steps)
        ax[1].set_title('Min volume step')

        plt.savefig(plot_to_file)
        plt.close()

    return min_price_steps, min_volume_steps, files

# Build split map: (from_file, price_step, volume_step) starting at each change
def get_split_map(
        min_price_steps: list[float],
        min_volume_steps: list[float],
        files: list[str],
        split_map_min_days: int,
    ) -> list[tuple[str, float, float]]:
    """
    Builds split map marking start of each new (price_step, volume_step) combination.
    Enforces a minimal number of days between consecutive splits.

       Input:
          - min_price_steps: per-day minimal price steps
          - min_volume_steps: per-day minimal volume steps
          - files: per-day filenames (same order as step lists)
          - split_map_min_days: minimal distance between splits; shorter changes are ignored

       Output:
          - list of tuples (from_file, price_step, volume_step)
    """
    
    split_map: list[tuple[str, float, float]] = []
    
    if len(files) == 0:
        return split_map
    
    split_map.append((files[0], min_price_steps[0], min_volume_steps[0]))
    prev_split = None
    
    for i in range(1, len(files)):
        if min_price_steps[i] != min_price_steps[i - 1] or min_volume_steps[i] != min_volume_steps[i - 1]:
            if prev_split is not None:
                if i - prev_split < split_map_min_days:
                    continue

            prev_split = i
            split_map.append((files[i], min_price_steps[i], min_volume_steps[i]))
    
    return split_map