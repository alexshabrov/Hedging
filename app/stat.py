import argparse, csv, os
import orjson

from pymongo import MongoClient  # type: ignore[import-not-found]

from live.lib.logger import get_logger
from models.hedger_models import HedgerStats
from modules.hedger_helper import HedgerPnlStats, calc_hedger_pnl_stats


### CLI ###
def parse_args():
    p = argparse.ArgumentParser()
    
    p.add_argument('--mongo-uri', type=str, default='mongodb://hedging_mongo:27017')
    p.add_argument('--mongo-db', type=str, default='hedging')
    p.add_argument('--mongo-collection', type=str, default='hedge_runs')
    
    default_csv = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hedger_pnl_stats.csv')
    p.add_argument('--output-csv', type=str, default=default_csv)
    
    return p.parse_args()


def _doc_to_hedger_stats(doc: dict) -> HedgerStats:
    if doc is None:
        raise RuntimeError('stat.py: doc is None')
    if not isinstance(doc, dict):
        raise RuntimeError(f'stat.py: doc is not dict: {type(doc)}')
    if '_id' not in doc:
        raise RuntimeError('stat.py: _id missing in mongo document')
    
    payload = {}
    for k in doc:
        if k == '_id':
            continue
        payload[str(k)] = doc[k]
    
    raw = orjson.dumps(payload)
    return HedgerStats.from_json(raw)


def _pnl_to_csv_row(run_id: str, pnl: HedgerPnlStats) -> dict:
    if run_id is None or len(str(run_id)) == 0:
        raise RuntimeError('stat.py: run_id is empty')
    if pnl is None:
        raise RuntimeError('stat.py: pnl is None')
    if not isinstance(pnl, HedgerPnlStats):
        raise RuntimeError(f'stat.py: pnl is not HedgerPnlStats: {type(pnl)}')
    
    return {
        'run_id': str(run_id),
        'cex_pnl_quote': float(pnl.cex_pnl_quote),
        'dex_realized_il_quote': float(pnl.dex_realized_il_quote),
        'fees_received_quote': float(pnl.fees_received_quote),
        'gas_paid_eth': float(pnl.gas_paid_eth),
        'gas_paid_quote': float(pnl.gas_paid_quote),
        'pool_hold_seconds': float(pnl.pool_hold_seconds),
        'apr_pct': float(pnl.apr_pct),
        'total_pnl_quote': float(pnl.total_pnl_quote),
    }


### Main ###
def main():
    args = parse_args()
    logger = get_logger('stat')
    
    if not isinstance(args.mongo_uri, str) or len(str(args.mongo_uri)) == 0:
        raise RuntimeError('stat.py: mongo_uri is empty')
    if not isinstance(args.mongo_db, str) or len(str(args.mongo_db)) == 0:
        raise RuntimeError('stat.py: mongo_db is empty')
    if not isinstance(args.mongo_collection, str) or len(str(args.mongo_collection)) == 0:
        raise RuntimeError('stat.py: mongo_collection is empty')
    if not isinstance(args.output_csv, str) or len(str(args.output_csv)) == 0:
        raise RuntimeError('stat.py: output_csv is empty')
    
    client = MongoClient(str(args.mongo_uri), serverSelectionTimeoutMS=5000)
    try:
        _ = client.server_info()
        
        db = client[str(args.mongo_db)]
        col = db[str(args.mongo_collection)]
        docs = list(col.find({}).sort([('_id', 1)]))
        
        rows = []
        skipped = 0
        for doc in docs:
            if doc is None:
                raise RuntimeError('stat.py: mongo document is None')
            if not isinstance(doc, dict):
                raise RuntimeError(f'stat.py: mongo document is not dict: {type(doc)}')
            if '_id' not in doc:
                raise RuntimeError('stat.py: mongo document missing _id')
            
            run_id = str(doc['_id'])
            
            try:
                stats = _doc_to_hedger_stats(doc)
                pnl = calc_hedger_pnl_stats(stats)
                row = _pnl_to_csv_row(str(run_id), pnl)
                rows.append(row)
            except Exception as e:
                skipped += 1
                logger.error(f'stat_skip_doc run_id={run_id} error={e}')
                continue
        
        out_path = os.path.abspath(str(args.output_csv))
        out_dir = os.path.dirname(out_path)
        if len(out_dir) == 0:
            raise RuntimeError(f'stat.py: bad output directory for path: {out_path}')
        os.makedirs(out_dir, exist_ok=True)
        
        fieldnames = [
            'run_id',
            'cex_pnl_quote',
            'dex_realized_il_quote',
            'fees_received_quote',
            'gas_paid_eth',
            'gas_paid_quote',
            'pool_hold_seconds',
            'apr_pct',
            'total_pnl_quote',
        ]
        
        with open(out_path, 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for row in rows:
                w.writerow(row)
        
        logger.info(f'stat_export_done rows={len(rows)} skipped={skipped} output_csv={out_path}')
    finally:
        client.close()


if __name__ == '__main__':
    main()
