"""
Data preparation entrypoint for Binance UM trades.

Provides:
 - CLI via get_args(): reads SYMBOL and FROM_DATE (env or --symbol/--from-date).
 - Main flow:
   1) build_download_list: determine which daily archives to download
   2) download_files: parallel download of archives
   3) detect_step_sizes_by_day: compute minimal price/volume steps per day (saves plot)
   4) get_split_map: build step-change segments (split map)
   5) load_and_process: quantize CSVs into int64 arrays per segment
   6) save_example_plot: store preview chart per block
   7) save_to_pickle: persist processed blocks to cache
Outputs go to <base_dir>/<SYMBOL> under data/cache/img; paths configured in params.PARAMS.
Run this script from inside the Docker container.
"""
from lib.helders import *
from lib.steps_splitter import *
from lib.data_load import *
from lib.data_processing import *
from params import PARAMS

if __name__ == '__main__':
    # Get arguments
    args = get_args()
    symbols = args.symbols

    for symbol in symbols:
        # Prepare data
        download_list = build_download_list(symbol, args.from_date, PARAMS['base_dir'])
        print(f'Download list: {len(download_list)} files for {symbol}')

        # Download files
        file_list = download_files(symbol, download_list, PARAMS['base_dir'], PARAMS['download_threads'])

        # Detect step sizes
        min_price_steps, min_volume_steps, files = detect_step_sizes_by_day(file_list, PARAMS['base_dir'], symbol, plot_to_file=f'img/{symbol}/steps.png')
    
        # Get split map
        split_map = get_split_map(min_price_steps, min_volume_steps, files, PARAMS['split_map_min_days'])
        print(f'Split map: {len(split_map)} entries')

        for from_file, price_step, volume_step in split_map:
            print(f' - from file: {from_file}, price step: {price_step}, volume step: {volume_step}')

        # Load, process and plot example per block
        blocks = load_and_process(PARAMS['base_dir'], symbol, files, split_map)

        for i, (block, price_step, volume_step) in enumerate(blocks, 1):
            out_file = f'img/{symbol}/block{i:02d}.png'
            save_example_plot(block, out_file)

        # Save to pickle
        save_to_pickle(PARAMS['base_dir'], symbol, blocks)