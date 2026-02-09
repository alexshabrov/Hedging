"""
CLI/path/cache/results helpers and run configuration utilities.

Provides:
 - get_args(for_mp=False) -> argparse.Namespace:
   parse CLI arguments (single run or multi-process runner).
 - ensure_dir(path) -> str:
   ensure directory exists and return the path.
 - build_*: build_symbol_dir / build_data_dir / build_cache_dir /
   build_steps_cache_dir / build_npy_cache_dir / build_pickle_cache_dir:
   construct and return project subdirectory paths.
 - get_file_path(base_dir, symbol, filename) -> str:
   build full path to a data file.
 - extract_dt_from_filename(filename) -> datetime:
   extract date from '*-trades-YYYY-MM-DD.zip' filename.
 - build_results_pickle_path(...), build_results_workers_dir(...),
   build_worker_results_pickle_path(...):
   paths for mass-test result storage.
 - append_pickle_list(path, items) -> None:
   append to a pickle-stored list (create if missing).
 - load_run_conf(app_dir, conf_name) -> dict:
   load JSON run configuration.
 - validate_run_conf(conf) -> None:
   validate required fields/ranges.
 - get_randomized_run_conf(conf) -> dict:
   sample values from {min, max, step} ranges.
"""
import argparse, os, pickle, json, math, random
from datetime import datetime

# Arguments parser
def get_args(for_mp: bool = False):
    """
    Builds and parses CLI arguments with defaults from environment variables.

       Input:
          - for_mp: if True, parse multiprocessing runner args; otherwise single-run args

       Output:
          - argparse.Namespace with fields:
            - when for_mp == False: symbols, from_date
            - when for_mp == True: symbols, confs, n_workers, n_tests, drop_chases
    """
    parser = argparse.ArgumentParser()
    
    env_symbols = os.environ.get('SYMBOLS')
    env_from_date = os.environ.get('FROM_DATE')
    env_n_workers = os.environ.get('N_WORKERS')
    env_n_tests = os.environ.get('N_TESTS')
    env_confs = os.environ.get('CONFS')
    
    parser.add_argument(
        '--symbols',
        type=str,
        required=not bool(env_symbols),
        default=env_symbols
    )
    if not for_mp:
        parser.add_argument(
            '--from-date',
            type=str,
            required=False,
            default=env_from_date if env_from_date else '2024-01-04'
        )
    else:
        parser.add_argument(
            '--n-workers',
            type=int,
            required=False,
            default=int(env_n_workers) if env_n_workers else 5
        )
        parser.add_argument(
            '--n-tests',
            type=int,
            required=not bool(env_n_tests),
            default=int(env_n_tests) if env_n_tests else None
        )
        parser.add_argument(
            '--confs',
            type=str,
            required=not bool(env_confs),
            default=env_confs if env_confs else None
        )
        parser.add_argument(
            '--drop-chases',
            type=int,
            required=False,
            default=1
        )

    args = parser.parse_args()

    # Normalize lists: comma-separated -> python list[str]
    symbols_raw = args.symbols
    assert isinstance(symbols_raw, str), 'symbols must be a comma-separated string'
    args.symbols = [s.strip() for s in symbols_raw.split(',') if s.strip() != '']
    assert len(args.symbols) > 0, 'symbols list must be non-empty'

    print(f'Symbols: {",".join(args.symbols)}')
    if not for_mp:
        print(f'From date: {args.from_date}')
    else:
        confs_raw = args.confs
        assert isinstance(confs_raw, str), 'confs must be a comma-separated string'
        args.confs = [c.strip() for c in confs_raw.split(',') if c.strip() != '']
        assert len(args.confs) > 0, 'confs list must be non-empty'
        print(f'Workers: {args.n_workers}')
        print(f'Tests: {args.n_tests}')
        print(f'Confs: {",".join(args.confs)}')
        assert int(args.drop_chases) in [0, 1], f'Invalid drop-chases flag: {args.drop_chases}'
        print(f'Drop chases: {int(args.drop_chases)}')

    return args

# Paths helpers
def ensure_dir(path: str) -> str:
    """
    Ensures directory exists.

       Input:
          - path: directory path
       Output:
          - path (same as input)
    """
    os.makedirs(path, exist_ok=True)
    return path

def build_symbol_dir(base_dir: str, symbol: str) -> str:
    """
    Returns path <base_dir>/<symbol> ensuring it exists.
    """
    path = f'{base_dir}/{symbol}'
    return ensure_dir(path)

def build_data_dir(base_dir: str, symbol: str) -> str:
    """
    Returns data path <base_dir>/<symbol>/data ensuring it exists.
    """
    path = f'{build_symbol_dir(base_dir, symbol)}/data'
    return ensure_dir(path)

def build_cache_dir(base_dir: str, symbol: str) -> str:
    """
    Returns cache path <base_dir>/<symbol>/cache ensuring it exists.
    """
    path = f'{build_symbol_dir(base_dir, symbol)}/cache'
    return ensure_dir(path)

def build_steps_cache_dir(base_dir: str, symbol: str) -> str:
    """
    Returns steps cache path <base_dir>/<symbol>/cache/steps ensuring it exists.
    """
    path = f'{build_cache_dir(base_dir, symbol)}/steps'
    return ensure_dir(path)

def build_npy_cache_dir(base_dir: str, symbol: str) -> str:
    """
    Returns NPY cache path <base_dir>/<symbol>/cache/npy ensuring it exists.
    """
    path = f'{build_cache_dir(base_dir, symbol)}/npy'
    return ensure_dir(path)

def build_pickle_cache_dir(base_dir: str, symbol: str) -> str:
    """
    Returns pickle cache path <base_dir>/<symbol>/cache/pickle ensuring it exists.
    """
    path = f'{build_cache_dir(base_dir, symbol)}/pickle'
    return ensure_dir(path)

def build_csv_dir(base_dir: str, symbol: str) -> str:
    """
    Returns csv path <base_dir>/<symbol>/csv ensuring it exists.
    """
    path = f'{build_symbol_dir(base_dir, symbol)}/csv'
    return ensure_dir(path)

def build_funding_csv_path(base_dir: str, symbol: str, name: str = 'funding.csv') -> str:
    """
    Returns full path to funding CSV under csv directory.
    """
    folder = build_csv_dir(base_dir, symbol)
    return f'{folder}/{name}'

def get_file_path(base_dir: str, symbol: str, filename: str) -> str:
    """
    Builds full path to file under data directory.

       Input:
          - base_dir: base directory
          - symbol: market symbol
          - filename: file name (e.g., 'BTCUSDT-trades-2024-01-04.zip')
       Output:
          - full path string
    """
    return f'{build_data_dir(base_dir, symbol)}/{filename}'

# Datetime extractor
def extract_dt_from_filename(filename: str) -> datetime:
    """
    Extracts date from '{symbol}-trades-YYYY-MM-DD.zip' filename.

       Input:
          - filename: file name string
       Output:
          - datetime object for the day
    """
    date = filename.split('-trades-')[1].replace('.zip', '')
    dt = datetime.strptime(date, '%Y-%m-%d')
    return dt

# Results helpers
def build_results_pickle_path(base_dir: str, symbol: str, name: str = 'mass_test.pkl') -> str:
    """
    Returns full path to results pickle under cache/pickle.
    """
    folder = build_pickle_cache_dir(base_dir, symbol)
    return f'{folder}/{name}'

def build_results_workers_dir(base_dir: str, symbol: str, conf_name: str) -> str:
    """
    Returns workers results directory path <base_dir>/<symbol>/results/<conf_name> ensuring it exists.
    """
    path = f'{build_symbol_dir(base_dir, symbol)}/results/{conf_name}'
    return ensure_dir(path)

def build_worker_results_pickle_path(base_dir: str, symbol: str, conf_name: str, worker_id: int, name: str = '') -> str:
    """
    Returns per-worker results pickle path.

       Input:
          - base_dir, symbol
          - conf_name: configuration name used to namespace results
          - worker_id: numeric id
          - name: optional file name override; if empty, generated automatically
       Output:
          - full file path string
    """
    folder = build_results_workers_dir(base_dir, symbol, conf_name)
    if name == '':
        filename = f'{symbol}.mass_test.w{int(worker_id)}.pkl'
    else:
        filename = name
    return f'{folder}/{filename}'

def append_pickle_list(path: str, items: list):
    """
    Appends items to a pickle list file, creating it if missing.

       Input:
          - path: file path
          - items: list to append
       Output:
          - None. Writes pickle with extended list.
    """
    ensure_dir(os.path.dirname(path))
    data = []
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, 'rb') as f:
            data = pickle.load(f)
    data.extend(items)
    with open(path, 'wb') as f:
        pickle.dump(data, f)

# Run conf helpers
def load_run_conf(app_dir: str, conf_name: str) -> dict:
    """
    Loads JSON run configuration by name from <app_dir>/run_confs/{conf_name}.json.
    """
    path = f'{app_dir}/run_confs/{conf_name}.json'
    assert os.path.exists(path), f'Config not found: {path}'
    with open(path, 'r') as f:
        return json.load(f)

def validate_run_conf(conf: dict):
    """
    Validates required fields and ranges in a run configuration.

       Input:
          - conf: configuration dict (parsed JSON)
       Output:
          - None. Raises assertions on invalid configuration.
    """
    # Required scalar keys
    for k in [
        'max_time_ms',
        'network_delay_ms',
        'distance_steps',
        'threshold_as_perc',
        'd_top_threshold',
        'd_btm_threshold',
        'tf_ms',
        'period',
        'only_side',
        'entrance_timeout_ms',
    ]:
        assert k in conf, f'Missing key in config: {k}'
    
    # Types and ranges for new scalar keys
    assert isinstance(conf['threshold_as_perc'], bool), 'threshold_as_perc must be boolean'
    # only_side can be int or list[int] with values in {-1,0,1}
    osv = conf['only_side']
    if isinstance(osv, list):
        assert len(osv) > 0, 'only_side list must be non-empty'
        for i, v in enumerate(osv):
            assert int(v) in [-1, 0, 1], f'invalid only_side[{i}] value: {v}'
    else:
        assert int(osv) in [-1, 0, 1], f'only_side must be -1, 0 or 1, got: {osv}'
    assert int(conf['entrance_timeout_ms']) >= 0, f'entrance_timeout_ms must be >= 0, got: {conf["entrance_timeout_ms"]}'
    
    # Optional boolean flags
    if 'targets_sync' in conf:
        assert isinstance(conf['targets_sync'], bool), 'targets_sync must be boolean'
    
    # Required ranged keys with {min, max, step} or lists
    for rk in [
        'd_top_target_perc',
        'd_btm_target_perc',
    ]:
        assert rk in conf, f'Missing ranged key in config: {rk}'
        node = conf[rk]
        if isinstance(node, dict):
            for sub in ['min', 'max', 'step']:
                assert sub in node, f'Missing {sub} in config.{rk}'
            assert float(node['step']) > 0, f'Invalid step in config.{rk}'
            assert float(node['min']) <= float(node['max']), f'min > max in config.{rk}'
        else:
            assert isinstance(node, list), f'Config field {rk} must be an object {{min,max,step}} or a list of values'
            assert len(node) > 0, f'{rk} list must be non-empty'
            for i, v in enumerate(node):
                # Allow fractional percentages (> 0)
                assert float(v) > 0, f'Invalid value at {rk}[{i}]: {v}'
    
    # test_volume_usd supports dict or list
    assert 'test_volume_usd' in conf, 'Missing ranged key in config: test_volume_usd'
    tv = conf['test_volume_usd']
    if isinstance(tv, dict):
        for sub in ['min', 'max', 'step']:
            assert sub in tv, f'Missing {sub} in config.test_volume_usd'
        assert float(tv['step']) > 0, 'Invalid step in config.test_volume_usd'
        assert float(tv['min']) > 0, 'min must be > 0 in config.test_volume_usd'
        assert float(tv['min']) <= float(tv['max']), 'min > max in config.test_volume_usd'
    else:
        assert isinstance(tv, list), 'test_volume_usd must be an object {min,max,step} or a list of values'
        assert len(tv) > 0, 'test_volume_usd list must be non-empty'
        for i, v in enumerate(tv):
            assert float(v) > 0, f'Invalid value at test_volume_usd[{i}]: {v}'

def get_randomized_run_conf(conf: dict) -> dict:
    """
    Samples randomized scalar values from ranged fields in a config.

       Input:
          - conf: configuration dict with fields:
            - 'test_volume_usd' as object {min, max, step} or list of values
            - 'd_top_target_perc', 'd_btm_target_perc' as objects {min, max, step}
       Output:
          - dict with sampled fields: test_volume_usd, d_top_target_perc, d_btm_target_perc
    """
    def pick(r):
        rmin = float(r['min'])
        rmax = float(r['max'])
        rstep = float(r['step'])
        n = int(math.floor((rmax - rmin) / rstep))
        k = random.randint(0, max(0, n))
        return rmin + k * rstep
    
    # test_volume_usd two modes
    tv = conf['test_volume_usd']
    if isinstance(tv, dict):
        test_volume_usd = float(pick(tv))
    else:
        idx = random.randint(0, len(tv) - 1)
        test_volume_usd = float(tv[idx])
    
    # only_side two modes
    osv = conf['only_side']
    if isinstance(osv, list):
        only_side = int(random.choice(osv))
    else:
        only_side = int(osv)
    
    def pick_node(node):
        if isinstance(node, dict):
            return float(pick(node))
        return float(random.choice(node))
    
    # targets_sync: if true, pick one value and apply to both
    sync = bool(conf.get('targets_sync', False))
    if sync:
        val = pick_node(conf['d_top_target_perc'])
        d_top_target_perc = float(val)
        d_btm_target_perc = float(val)
    else:
        d_top_target_perc = float(pick_node(conf['d_top_target_perc']))
        d_btm_target_perc = float(pick_node(conf['d_btm_target_perc']))
    
    return {
        'test_volume_usd': float(test_volume_usd),
        'only_side': int(only_side),
        'd_top_target_perc': float(d_top_target_perc),
        'd_btm_target_perc': float(d_btm_target_perc),
    }