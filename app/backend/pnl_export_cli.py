"""
Export LP-block iteration data from Mongo to CSV
Date: 2026-02-19
Version: 1.1
"""
import argparse, csv, os, time
from typing import Dict, List, Optional

from pymongo import MongoClient  # type: ignore[import-not-found]

from live.lib.logger import get_logger
from live.lib.strict_model import StrictModel


### Constants ###
HEDGER_RUNS_COLLECTION = 'backend_hedger_runs'
LP_CSV_HEADERS = [
    'row_id',
    'run_id',
    'iteration_no',
    'status',
    'base_price',
    'price_lower',
    'price_upper',
    'minted_base',
    'minted_quote',
    'closed_base',
    'closed_quote',
    'fees_quote',
    'add_txids',
    'remove_txids',
    'collect_txids',
    'rebalance_txids',
    'rebalance_order_uid',
]


### Models ###
class ExportLpIterationRow(StrictModel):
    row_id: str
    run_id: str
    iteration_no: int
    status: str
    base_price: float
    price_lower: float
    price_upper: float
    minted_base: float
    minted_quote: float
    closed_base: float
    closed_quote: float
    fees_quote: float
    add_txids: str
    remove_txids: str
    collect_txids: str
    rebalance_txids: str
    rebalance_order_uid: str


### CLI ###
def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--run-id', default='')
    p.add_argument('--mongo-uri', type=str, default='mongodb://hedging_mongo:27017')
    p.add_argument('--mongo-db', type=str, default='hedging')
    p.add_argument('--lp-csv-path', type=str, default='')
    return p.parse_args()


### Helpers ###
def _required_dict_field(data: Dict, field_name: str, source: str) -> Dict:
    if field_name not in data:
        raise RuntimeError(f'{source}.{field_name} is missing')
    value = data[field_name]
    if not isinstance(value, dict):
        raise RuntimeError(f'{source}.{field_name} is not dict: {type(value)}')
    return value


def _optional_dict_field(data: Dict, field_name: str) -> Optional[Dict]:
    if field_name not in data:
        return None
    value = data[field_name]
    if value is None:
        return None
    if not isinstance(value, dict):
        raise RuntimeError(f'{field_name} is not dict: {type(value)}')
    return value


def _to_optional_float(value) -> float:
    if value is None:
        return 0.0
    return float(value)


def _default_csv_path() -> str:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    script_name = os.path.splitext(os.path.basename(__file__))[0]
    return os.path.join(str(script_dir), f'{script_name}.csv')


def _collect_ids_from_value(value) -> List[str]:
    out: List[str] = []
    if value is None:
        return out

    if isinstance(value, str):
        value_norm = str(value).strip()
        if len(value_norm) > 0:
            out.append(value_norm)
        return out

    if isinstance(value, (list, tuple)):
        for item in value:
            nested = _collect_ids_from_value(item)
            for nested_item in nested:
                out.append(str(nested_item))
        return out

    value_norm = str(value).strip()
    if len(value_norm) > 0:
        out.append(value_norm)
    return out


def _join_unique_ids(ids: List[str]) -> str:
    uniq: List[str] = []
    seen = set()
    for item in ids:
        item_norm = str(item).strip()
        if len(item_norm) == 0:
            continue
        if item_norm in seen:
            continue
        seen.add(item_norm)
        uniq.append(item_norm)
    return '|'.join(uniq)


def _extract_rebalance_ids(uniswap: Dict) -> str:
    rebalance = _optional_dict_field(uniswap, 'rebalance')
    if rebalance is None:
        return ''

    ids: List[str] = []
    for key in ['tx_hash', 'txid', 'tx_id']:
        if key in rebalance:
            ids.extend(_collect_ids_from_value(rebalance.get(key)))

    order = _optional_dict_field(rebalance, 'order')
    if order is not None:
        for key in ['tx_hash', 'txid', 'tx_id']:
            if key in order:
                ids.extend(_collect_ids_from_value(order.get(key)))

    return _join_unique_ids(ids)


def _extract_rebalance_order_uid(uniswap: Dict) -> str:
    rebalance = _optional_dict_field(uniswap, 'rebalance')
    if rebalance is None:
        return ''

    order = _optional_dict_field(rebalance, 'order')
    if order is None:
        return ''

    return _join_unique_ids(_collect_ids_from_value(order.get('uid')))


def _write_lp_rows_csv(path: str, rows: List[ExportLpIterationRow], logger) -> None:
    if not isinstance(path, str) or len(path.strip()) == 0:
        raise RuntimeError('csv path is empty')

    path_norm = str(path).strip()
    path_dir = os.path.dirname(path_norm)
    if len(path_dir) > 0:
        os.makedirs(path_dir, exist_ok=True)

    with open(path_norm, 'w', encoding='utf-8', newline='') as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=LP_CSV_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                'row_id': str(row.row_id),
                'run_id': str(row.run_id),
                'iteration_no': int(row.iteration_no),
                'status': str(row.status),
                'base_price': f'{float(row.base_price):.6f}',
                'price_lower': f'{float(row.price_lower):.6f}',
                'price_upper': f'{float(row.price_upper):.6f}',
                'minted_base': f'{float(row.minted_base):.6f}',
                'minted_quote': f'{float(row.minted_quote):.6f}',
                'closed_base': f'{float(row.closed_base):.6f}',
                'closed_quote': f'{float(row.closed_quote):.6f}',
                'fees_quote': f'{float(row.fees_quote):.6f}',
                'add_txids': str(row.add_txids),
                'remove_txids': str(row.remove_txids),
                'collect_txids': str(row.collect_txids),
                'rebalance_txids': str(row.rebalance_txids),
                'rebalance_order_uid': str(row.rebalance_order_uid),
            })

    logger.info(f'lp_rows_csv_written path={path_norm} rows={len(rows)}')


def _build_export_row(item: Dict) -> ExportLpIterationRow:
    if 'id' not in item:
        raise RuntimeError('iteration.id is missing')
    if 'run_id' not in item:
        raise RuntimeError('iteration.run_id is missing')
    if 'iteration_no' not in item:
        raise RuntimeError('iteration.iteration_no is missing')
    stats = _required_dict_field(item, 'stats', 'iteration')
    calc = _required_dict_field(stats, 'calc', 'iteration.stats')
    uniswap = _required_dict_field(stats, 'uniswap', 'iteration.stats')
    pnl = _optional_dict_field(item, 'pnl')
    status = '' if item.get('status') is None else str(item.get('status'))

    mint_obj = _optional_dict_field(uniswap, 'mint')
    decrease_obj = _optional_dict_field(uniswap, 'decrease')
    collect_obj = _optional_dict_field(uniswap, 'collect')

    mint_base = 0.0
    mint_quote = 0.0
    if mint_obj is not None:
        mint_base = float(_to_optional_float(mint_obj.get('amount_base')))
        mint_quote = float(_to_optional_float(mint_obj.get('amount_quote')))

    decrease_base = 0.0
    decrease_quote = 0.0
    if decrease_obj is not None:
        decrease_base = float(_to_optional_float(decrease_obj.get('amount_base')))
        decrease_quote = float(_to_optional_float(decrease_obj.get('amount_quote')))

    fees_quote = 0.0
    if pnl is not None:
        fees_quote = float(_to_optional_float(pnl.get('fees_received_quote')))

    add_txids = ''
    if mint_obj is not None:
        add_txids = _join_unique_ids(_collect_ids_from_value(mint_obj.get('tx_hash')))

    remove_txids = ''
    if decrease_obj is not None:
        remove_txids = _join_unique_ids(_collect_ids_from_value(decrease_obj.get('tx_hash')))

    collect_txids = ''
    if collect_obj is not None:
        collect_txids = _join_unique_ids(_collect_ids_from_value(collect_obj.get('tx_hash')))

    rebalance_txids = _extract_rebalance_ids(uniswap)
    rebalance_order_uid = _extract_rebalance_order_uid(uniswap)

    return ExportLpIterationRow(
        row_id=str(item['id']),
        run_id=str(item['run_id']),
        iteration_no=int(item['iteration_no']),
        status=str(status),
        base_price=float(calc['base_price']),
        price_lower=float(calc['price_lower']),
        price_upper=float(calc['price_upper']),
        minted_base=float(mint_base),
        minted_quote=float(mint_quote),
        closed_base=float(decrease_base),
        closed_quote=float(decrease_quote),
        fees_quote=float(fees_quote),
        add_txids=str(add_txids),
        remove_txids=str(remove_txids),
        collect_txids=str(collect_txids),
        rebalance_txids=str(rebalance_txids),
        rebalance_order_uid=str(rebalance_order_uid),
    )


### Main ###
def main() -> None:
    args = parse_args()
    logger = get_logger('pnl_export_cli')
    t_start = time.perf_counter()

    if not isinstance(args.mongo_uri, str) or len(args.mongo_uri) == 0:
        raise RuntimeError('mongo_uri is empty')
    if not isinstance(args.mongo_db, str) or len(args.mongo_db) == 0:
        raise RuntimeError('mongo_db is empty')
    run_id_filter = str(args.run_id).strip()

    client = MongoClient(str(args.mongo_uri), serverSelectionTimeoutMS=5000)
    try:
        _ = client.server_info()
        db = client[str(args.mongo_db)]
        col = db[str(HEDGER_RUNS_COLLECTION)]
        query: Dict = {}
        if len(run_id_filter) > 0:
            query['run_id'] = str(run_id_filter)
        cursor = col.find(query, {'_id': 0}).sort([('run_id', 1), ('iteration_no', 1)])

        rows: List[ExportLpIterationRow] = []
        docs_total = 0
        for item in cursor:
            docs_total += 1
            if not isinstance(item, dict):
                raise RuntimeError(f'iteration doc is not dict: {type(item)}')

            row = _build_export_row(item)
            rows.append(row)

        if len(rows) == 0:
            filter_tail = ' (all runs)'
            if len(run_id_filter) > 0:
                filter_tail = f' (run_id={run_id_filter})'
            raise RuntimeError(f'no rows found in {HEDGER_RUNS_COLLECTION}{filter_tail}')

        rows.sort(key=lambda x: (str(x.run_id), int(x.iteration_no)))

        lp_csv_path = str(args.lp_csv_path).strip()
        if len(lp_csv_path) == 0:
            lp_csv_path = _default_csv_path()
        _write_lp_rows_csv(path=str(lp_csv_path), rows=rows, logger=logger)

        print('=== LP EXPORT ===')
        print(f'Run ID filter: {run_id_filter if len(run_id_filter) > 0 else "ALL"}')
        print(f'Documents loaded: {docs_total}')
        print(f'Rows exported: {len(rows)}')
        print(f'CSV: {lp_csv_path}')
        logger.info(
            f'export_done run_id_filter={run_id_filter if len(run_id_filter) > 0 else "ALL"} docs_total={docs_total} rows={len(rows)} '
            f'elapsed_sec={time.perf_counter() - t_start:.2f}'
        )
    finally:
        client.close()


if __name__ == '__main__':
    main()
