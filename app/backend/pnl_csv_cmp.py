"""
Compare pnl_dex_cli.csv vs pnl_export_cli.csv by txids.
Generates:
  - pnl_sync.csv
  - pnl_dex_only.csv
  - pnl_db_only.csv
Date: 2026-02-20
Version: 1.1
"""
import csv, math, os, statistics
from typing import Dict, List, Set, Tuple


DEX_FILE_NAME = 'pnl_dex_cli.csv'
DB_FILE_NAME = 'pnl_export_cli.csv'
SYNC_FILE_NAME = 'pnl_sync.csv'
DEX_ONLY_FILE_NAME = 'pnl_dex_only.csv'
DB_ONLY_FILE_NAME = 'pnl_db_only.csv'


def _load_csv(path: str) -> Tuple[List[Dict[str, str]], List[str]]:
    if not os.path.exists(path):
        raise RuntimeError(f'csv file not found: {path}')

    with open(path, 'r', encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise RuntimeError(f'csv has no header: {path}')
        rows = [dict(item) for item in reader]
        headers = [str(h) for h in reader.fieldnames]
        return rows, headers


def _normalize_txid(txid: str) -> str:
    txid_norm = str(txid).strip().lower()
    if txid_norm.startswith('0x'):
        txid_norm = txid_norm[2:]
    return str(txid_norm)


def _extract_txids(row: Dict[str, str]) -> Set[str]:
    out: Set[str] = set()
    for key, value in row.items():
        if not str(key).endswith('_txids'):
            continue
        if value is None:
            continue

        raw = str(value).strip()
        if len(raw) == 0:
            continue

        for token in raw.split('|'):
            token_norm = _normalize_txid(str(token))
            if len(token_norm) == 0:
                continue
            out.add(token_norm)
    return out


def _joined_txids(txids: Set[str]) -> str:
    if len(txids) == 0:
        return ''
    return '|'.join(sorted(txids))


def _build_union_headers(db_headers: List[str], dex_headers: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for name in db_headers:
        if name in seen:
            continue
        out.append(str(name))
        seen.add(str(name))
    for name in dex_headers:
        if name in seen:
            continue
        out.append(str(name))
        seen.add(str(name))
    return out


def _is_tx_field(field_name: str) -> bool:
    field_norm = str(field_name).strip().lower()
    return field_norm.endswith('_txids') or field_norm.endswith('_txid')


def _match_rows(
    dex_rows: List[Dict[str, str]],
    db_rows: List[Dict[str, str]],
) -> Tuple[List[Tuple[int, int, Set[str]]], Set[int], Set[int]]:
    dex_txids = [_extract_txids(row) for row in dex_rows]
    db_txids = [_extract_txids(row) for row in db_rows]

    candidates: List[Tuple[int, int, int, Set[str]]] = []
    for dex_idx, dex_set in enumerate(dex_txids):
        if len(dex_set) == 0:
            continue
        for db_idx, db_set in enumerate(db_txids):
            if len(db_set) == 0:
                continue
            overlap = dex_set.intersection(db_set)
            if len(overlap) == 0:
                continue
            candidates.append((len(overlap), dex_idx, db_idx, overlap))

    # Greedy one-to-one: highest overlap first, deterministic by row order.
    candidates.sort(key=lambda item: (-int(item[0]), int(item[1]), int(item[2])))
    matched: List[Tuple[int, int, Set[str]]] = []
    used_dex: Set[int] = set()
    used_db: Set[int] = set()
    for _score, dex_idx, db_idx, overlap in candidates:
        if dex_idx in used_dex:
            continue
        if db_idx in used_db:
            continue
        matched.append((int(dex_idx), int(db_idx), set(overlap)))
        used_dex.add(int(dex_idx))
        used_db.add(int(db_idx))

    matched.sort(key=lambda item: int(item[0]))
    return matched, used_dex, used_db


def _write_sync_csv(
    path: str,
    matched: List[Tuple[int, int, Set[str]]],
    dex_rows: List[Dict[str, str]],
    db_rows: List[Dict[str, str]],
    union_headers: List[str],
) -> None:
    non_tx_fields = [f for f in union_headers if not _is_tx_field(f)]
    tx_fields = [f for f in union_headers if _is_tx_field(f)]
    numeric_fields: Set[str] = set()
    for field_name in non_tx_fields:
        for dex_idx, db_idx, _overlap in matched:
            dex_raw = '' if field_name not in dex_rows[dex_idx] else str(dex_rows[dex_idx][field_name])
            db_raw = '' if field_name not in db_rows[db_idx] else str(db_rows[db_idx][field_name])
            dex_num = _to_optional_float(dex_raw)
            db_num = _to_optional_float(db_raw)
            if dex_num is not None and db_num is not None:
                numeric_fields.add(str(field_name))
                break

    headers = ['match_no', 'dex_row_no', 'db_row_no']
    for field_name in non_tx_fields:
        headers.append(f'{field_name}_dex')
        headers.append(f'{field_name}_db')
        if field_name in numeric_fields:
            headers.append(f'{field_name}_diff')
    for field_name in tx_fields:
        # Dump tx columns at the end without comparison.
        headers.append(f'{field_name}_dex')
        headers.append(f'{field_name}_db')

    with open(path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()

        for match_no, (dex_idx, db_idx, overlap) in enumerate(matched, start=1):
            dex_row = dex_rows[dex_idx]
            db_row = db_rows[db_idx]
            out_row: Dict[str, str] = {
                'match_no': str(match_no),
                'dex_row_no': str(int(dex_idx) + 2),  # +2 because csv header is line 1
                'db_row_no': str(int(db_idx) + 2),
            }
            for field_name in non_tx_fields:
                dex_raw = '' if field_name not in dex_row else str(dex_row[field_name])
                db_raw = '' if field_name not in db_row else str(db_row[field_name])
                out_row[f'{field_name}_dex'] = str(dex_raw)
                out_row[f'{field_name}_db'] = str(db_raw)

                if field_name in numeric_fields:
                    diff_str = ''
                    dex_num = _to_optional_float(dex_raw)
                    db_num = _to_optional_float(db_raw)
                    if dex_num is not None and db_num is not None:
                        diff_str = f"{_calc_pct_diff(float(dex_num), float(db_num)):.6f}"
                    out_row[f'{field_name}_diff'] = str(diff_str)

            for field_name in tx_fields:
                out_row[f'{field_name}_dex'] = '' if field_name not in dex_row else str(dex_row[field_name])
                out_row[f'{field_name}_db'] = '' if field_name not in db_row else str(db_row[field_name])
            writer.writerow(out_row)


def _write_only_csv(
    path: str,
    headers: List[str],
    rows: List[Dict[str, str]],
    row_indexes: List[int],
) -> None:
    out_headers = ['row_no', 'txids'] + [str(h) for h in headers]
    with open(path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=out_headers)
        writer.writeheader()
        for idx in row_indexes:
            row = rows[idx]
            out: Dict[str, str] = {
                'row_no': str(int(idx) + 2),  # +2 because csv header is line 1
                'txids': _joined_txids(_extract_txids(row)),
            }
            for h in headers:
                out[str(h)] = '' if h not in row else str(row[h])
            writer.writerow(out)


def _to_optional_float(value: str):
    if value is None:
        return None
    raw = str(value).strip()
    if len(raw) == 0:
        return None
    try:
        parsed = float(raw)
    except Exception:
        return None
    if not math.isfinite(parsed):
        return None
    return float(parsed)


def _calc_pct_diff(dex_value: float, db_value: float) -> float:
    eps = 1e-12
    if abs(float(dex_value)) <= eps and abs(float(db_value)) <= eps:
        return 0.0

    denominator = abs(float(db_value))
    if float(denominator) <= eps:
        denominator = max(abs(float(dex_value)), float(eps))
    return (float(dex_value) - float(db_value)) / float(denominator) * 100.0


def _collect_field_stats(
    matched: List[Tuple[int, int, Set[str]]],
    dex_rows: List[Dict[str, str]],
    db_rows: List[Dict[str, str]],
    dex_headers: List[str],
    db_headers: List[str],
) -> List[Dict[str, str]]:
    common_headers = [h for h in db_headers if h in set(dex_headers)]
    out: List[Dict[str, str]] = []

    for field_name in common_headers:
        pct_values: List[float] = []
        for dex_idx, db_idx, _overlap in matched:
            dex_raw = '' if field_name not in dex_rows[dex_idx] else dex_rows[dex_idx][field_name]
            db_raw = '' if field_name not in db_rows[db_idx] else db_rows[db_idx][field_name]
            dex_num = _to_optional_float(dex_raw)
            db_num = _to_optional_float(db_raw)
            if dex_num is None or db_num is None:
                continue
            pct_values.append(float(_calc_pct_diff(float(dex_num), float(db_num))))

        if len(pct_values) == 0:
            continue

        mean_v = statistics.fmean(pct_values)
        std_v = 0.0
        if len(pct_values) > 1:
            std_v = statistics.pstdev(pct_values)
        out.append({
            'field': str(field_name),
            'count': str(len(pct_values)),
            'mean_pct': f'{float(mean_v):.6f}',
            'std_pct': f'{float(std_v):.6f}',
            'min_pct': f'{float(min(pct_values)):.6f}',
            'max_pct': f'{float(max(pct_values)):.6f}',
        })

    out.sort(key=lambda item: str(item['field']))
    return out


def main() -> None:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    dex_path = os.path.join(script_dir, DEX_FILE_NAME)
    db_path = os.path.join(script_dir, DB_FILE_NAME)
    sync_path = os.path.join(script_dir, SYNC_FILE_NAME)
    dex_only_path = os.path.join(script_dir, DEX_ONLY_FILE_NAME)
    db_only_path = os.path.join(script_dir, DB_ONLY_FILE_NAME)

    dex_rows, dex_headers = _load_csv(dex_path)
    db_rows, db_headers = _load_csv(db_path)

    matched, used_dex, used_db = _match_rows(dex_rows=dex_rows, db_rows=db_rows)
    dex_only = [idx for idx in range(len(dex_rows)) if idx not in used_dex]
    db_only = [idx for idx in range(len(db_rows)) if idx not in used_db]

    union_headers = _build_union_headers(db_headers=db_headers, dex_headers=dex_headers)
    _write_sync_csv(
        path=sync_path,
        matched=matched,
        dex_rows=dex_rows,
        db_rows=db_rows,
        union_headers=union_headers,
    )
    _write_only_csv(path=dex_only_path, headers=dex_headers, rows=dex_rows, row_indexes=dex_only)
    _write_only_csv(path=db_only_path, headers=db_headers, rows=db_rows, row_indexes=db_only)
    stats_rows = _collect_field_stats(
        matched=matched,
        dex_rows=dex_rows,
        db_rows=db_rows,
        dex_headers=dex_headers,
        db_headers=db_headers,
    )

    print('=== CSV COMPARE ===')
    print(f'DEX rows: {len(dex_rows)}')
    print(f'DB rows: {len(db_rows)}')
    print(f'Matched rows: {len(matched)}')
    print(f'DEX only rows: {len(dex_only)}')
    print(f'DB only rows: {len(db_only)}')
    print(f'SYNC CSV: {sync_path}')
    print(f'DEX ONLY CSV: {dex_only_path}')
    print(f'DB ONLY CSV: {db_only_path}')
    print('')
    print('=== FIELD DIFF STATS (%: (dex - db) / abs(db) * 100) ===')
    if len(stats_rows) == 0:
        print('No comparable numeric fields in matched rows.')
    else:
        for item in stats_rows:
            print(
                f"{item['field']}: count={item['count']} "
                f"mean={item['mean_pct']}% std={item['std_pct']}% "
                f"min={item['min_pct']}% max={item['max_pct']}%"
            )


if __name__ == '__main__':
    main()
