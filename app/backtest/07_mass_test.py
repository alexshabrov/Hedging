"""
Mass testing runner for threshold/target chase logic.

Purpose:
 - Spawn multiple worker processes to randomly sample blocks and indices
 - Derive per-test params from a JSON run config
 - Execute run_logic and compute aggregated stats
 - Persist per-worker batches of results to pickle files

Provides:
 - worker(tests_count, symbol, worker_id, out_queue, conf, drop_chases):
   safe wrapper over _worker with error reporting
 - _worker(...):
   main loop for a worker: load data, randomize run config fields, compute indicators,
   convert volume to units, run logic, build stats, optionally drop heavy chase section,
   buffer and append results via append_pickle_list
 - __main__:
   parse CLI (--symbols, --n-workers, --n-tests, --confs, --drop-chases),
   load/validate run configs from app/run_confs/<name>.json,
   start processes per (symbol, conf) combo, display progress (tqdm), handle interrupts, join workers

Inputs:
 - Processed data blocks loaded via load_from_pickle(PARAMS['base_dir'], symbol)
 - JSON run config fields validated by validate_run_conf

Outputs:
 - <base_dir>/<SYMBOL>/results/<SYMBOL>.mass_test.w{worker_id}.pkl files
   containing per-test run params, indicators (t, v), and stats

System requirements for 100 workers:
 - 2TB of RAM
 - 128 cores
 - 300 GB of disk space

Run this script from inside the Docker container.
"""
import random, os, traceback
import multiprocessing as mp
from multiprocessing import Process, Queue
from queue import Empty
from tqdm import tqdm

from lib.helders import *
from lib.data_processing import *
from lib.logic import run_logic, get_logic_stat
from lib.indicators import get_trend_and_volatility_indicator
from lib.data_load import build_download_list, download_files
from lib.steps_splitter import detect_step_sizes_by_day, get_split_map
from params import PARAMS

# Worker
def worker(tests_count: int, symbol: str, conf_name: str, worker_id: int, out_queue: Queue, conf: dict, drop_chases: int):
    try:
        _worker(tests_count, symbol, conf_name, worker_id, out_queue, conf, drop_chases)
    except Exception as e:
        print(f'Worker error: {e}, {traceback.format_exc()}')
        raise e

def _worker(tests_count: int, symbol: str, conf_name: str, worker_id: int, out_queue: Queue, conf: dict, drop_chases: int):
    # Load data per worker
    blocks = load_from_pickle(PARAMS['base_dir'], symbol)

    # Per-worker output
    out_path = build_worker_results_pickle_path(PARAMS['base_dir'], symbol, conf_name, worker_id)
    buffer: list[dict] = []
    per_worker_batch = max(1, tests_count // 10)
    progress_batch = 0
    progress_batch_size = 100

    # Static params (from conf)
    max_time_ms = int(conf['max_time_ms'])
    network_delay_ms = int(conf['network_delay_ms'])
    distance_steps = int(conf['distance_steps'])
    threshold_as_perc = bool(conf['threshold_as_perc'])
    d_top_threshold = int(conf['d_top_threshold'])
    d_btm_threshold = int(conf['d_btm_threshold'])
    entrance_timeout_ms = int(conf['entrance_timeout_ms'])

    # Indicators params
    tf_ms = int(conf['tf_ms'])
    period = int(conf['period'])

    for _ in range(tests_count):
        # Count every attempted test towards progress
        progress_batch += 1
        if progress_batch >= progress_batch_size:
            out_queue.put(progress_batch)
            progress_batch = 0

        # Randomize run conf for this test
        rc = get_randomized_run_conf(conf)
        test_volume_usd = float(rc['test_volume_usd'])
        d_top_target_perc = float(rc['d_top_target_perc'])
        d_btm_target_perc = float(rc['d_btm_target_perc'])
        only_side = int(rc['only_side'])

        # Random block and index
        all_trades, price_step, volume_step = random.choice(blocks)
        start_index = random.randint(0, all_trades.shape[0] - 1)

        # Parametrize
        t, v, e = get_trend_and_volatility_indicator(all_trades, start_index, tf_ms, period)
        if e != 0:
            continue

        # Convert USD to units via steps at start index
        intended_price = all_trades[start_index, 1]
        price_real = intended_price * price_step
        if price_real <= 0:
            continue
        
        base_volume = test_volume_usd / price_real
        test_volume_units = int(base_volume / volume_step) if volume_step > 0 else 0
        if test_volume_units <= 0:
            continue

        # Targets in units (by percent of current price)
        d_top_target = int(intended_price * d_top_target_perc / 100)
        d_btm_target = int(intended_price * d_btm_target_perc / 100)
        if d_top_target <= 1 or d_btm_target <= 1:
            continue

        # Run logic
        stopped_at_index, groups, logic_trace, lines, neutral_excursions, err = run_logic(
            all_trades=all_trades,
            start_index=start_index,
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
        if err != 0:
            continue

        # Build stat (with real-money pnl)
        stat = get_logic_stat(
            all_trades=all_trades,
            groups=groups,
            logic_trace=logic_trace,
            lines=lines,
            start_index=start_index,
            stopped_at_index=stopped_at_index,
            err=err,
            test_volume_units=test_volume_units,
            price_step=price_step,
            volume_step=volume_step,
            test_volume_usd=test_volume_usd,
            maker_fee=0.0,
        )

        # Drop heavy chases section if enabled
        if int(drop_chases) == 1:
            del stat['chases']

        # Save locally in per-worker batches
        buffer.append({
            't': float(t),
            'v': float(v),
            'start_index': int(start_index),
            'start_time_ms': int(all_trades[start_index, 0]),
            'run_conf': rc,
            'stat': stat,
            'neutral_excursions': [float(x) for x in neutral_excursions],
        })
        
        if len(buffer) >= per_worker_batch:
            append_pickle_list(out_path, buffer)
            buffer.clear()

    # Flush remaining
    if len(buffer) > 0:
        append_pickle_list(out_path, buffer)
        buffer.clear()
   
    # Flush remaining progress
    if progress_batch > 0:
        out_queue.put(progress_batch)

def ensure_prepared_data_for_symbol(symbol: str):
    """
    Minimal data preparation flow (no tests/plots) to ensure cached pickle exists.
    Triggered only when <base_dir>/<symbol>/cache/pickle/{symbol}.pkl is missing.
    """
    base_dir = PARAMS['base_dir']
    pickle_folder = build_pickle_cache_dir(base_dir, symbol)
    pickle_path = f'{pickle_folder}/{symbol}.pkl'
    
    if os.path.exists(pickle_path):
        return
    
    print(f'No prepared data for {symbol}. Preparing minimally...')
    from_date = os.environ.get('FROM_DATE', '2024-01-04')
    
    # 1) Build download list
    download_list = build_download_list(symbol, from_date, base_dir)
    print(f'Download list: {len(download_list)} files for {symbol}')
    
    # 2) Download (or discover) files
    file_list = download_files(symbol, download_list, base_dir, PARAMS['download_threads'])
    if len(file_list) == 0:
        print(f'No trade files found for {symbol}; skipping preparation.')
        return
    
    # 3) Detect minimal steps by day (skip plotting)
    min_price_steps, min_volume_steps, files = detect_step_sizes_by_day(file_list, base_dir, symbol, plot_to_file=None)
    
    # 4) Build split map
    split_map = get_split_map(min_price_steps, min_volume_steps, files, PARAMS['split_map_min_days'])
    if len(split_map) == 0:
        print(f'Empty split map for {symbol}; skipping preparation.')
        return
    
    # 5) Load/process and save to pickle
    blocks = load_and_process(base_dir, symbol, files, split_map)
    save_to_pickle(base_dir, symbol, blocks)
    print(f'Prepared and cached {len(blocks)} blocks for {symbol}.')

if __name__ == '__main__':
    # Force spawn start method
    mp.set_start_method('spawn', force=True)

    # Args
    args = get_args(for_mp=True)

    symbols = args.symbols
    n_workers = int(args.n_workers)
    n_tests = int(args.n_tests)
    confs = args.confs

    app_dir = os.path.dirname(__file__)
    
    # Ensure data prepared for each symbol (minimal flow) before starting workers
    for s in set(symbols):
        ensure_prepared_data_for_symbol(s)
    
    combos: list[tuple[str, str]] = []
    for s in symbols:
        for c in confs:
            combos.append((s, c))

    # Queue and unified progress
    out_queue: Queue = Queue()
    per_worker = max(1, n_tests // n_workers)

    for symbol, conf_name in combos:
        print(f'Running: symbol={symbol} conf={conf_name}')

        # Load and validate config per combo
        conf = load_run_conf(app_dir, conf_name)
        validate_run_conf(conf)

        # Progress bar per combo
        pbar = tqdm(total=n_tests, desc='Testing')

        workers: list[Process] = []
        for i in range(n_workers):
            p = Process(target=worker, args=(per_worker, symbol, conf_name, int(i), out_queue, conf, int(args.drop_chases)))
            p.start()
            workers.append(p)

        # Drain loop for this combo
        alive = True
        
        try:
            while alive or not out_queue.empty():
                alive = any(p.is_alive() for p in workers)
                drained = 0
                
                try:
                    # Try to get at least one item with timeout
                    item = out_queue.get(timeout=0.2)
                    drained += item
                    # Drain the rest without blocking
                    while True:
                        item = out_queue.get_nowait()
                        drained += item
                
                except Empty:
                    pass
                
                if drained:
                    pbar.update(drained)
        
        except KeyboardInterrupt:
            print('Interrupted, terminating workers...')
            for p in workers:
                if p.is_alive():
                    p.terminate()
            for p in workers:
                p.join(timeout=0.5)
            break
        finally:
            # Join workers for this combo
            for p in workers:
                p.join()
            pbar.close()

    print('Mass test completed')

