"""
Funding rates downloader for Binance UM.

Provides:
 - CLI via get_args(): reads SYMBOL (FROM_DATE is unused here)
 - Main flow:
   1) build output directory <base_dir>/<SYMBOL>/csv
   2) download full funding history from Binance API backwards from current time
   3) write CSV with columns: timestamp_ms, funding_rate
Outputs are saved under the symbol's csv directory. Run inside the Docker container.
"""
from lib.helders import *
from params import PARAMS

import requests, csv, os, time
from datetime import datetime


BINANCE_FAPI_BASE = 'https://fapi.binance.com'


def fetch_all_funding_backward(symbol: str) -> list:
    """
    Fetches full history of funding rates for the symbol by paging backwards from current time.
    
       Input:
          - symbol: market symbol (e.g., 'BTCUSDT')
       Output:
          - list of funding entries as returned by Binance API
    """
    url = f'{BINANCE_FAPI_BASE}/fapi/v1/fundingRate'
    all_items = []
    cursor_end = int(time.time() * 1000)
    page = 0
   
    while True:
        page += 1
        params = {
            'symbol': symbol,
            'startTime': cursor_end - (1000//8) * 3600 * 24 * 1000,
            'endTime': cursor_end,
            'limit': 1000,
        }
        resp = requests.get(url, params=params, timeout=30)
        assert resp.status_code == 200, f'HTTP {resp.status_code} on fundingRate page {page}: {resp.text}'
        
        items = resp.json()
        assert isinstance(items, list), 'fundingRate: invalid response type'
        
        if len(items) == 0:
            break
        
        all_items.extend(items)

        print(f'Got {len(items)} items from {datetime.fromtimestamp(items[0]["fundingTime"] / 1000)} to {datetime.fromtimestamp(items[-1]["fundingTime"] / 1000)}')
      
        first_ts = int(items[0]['fundingTime'])
        cursor_end = first_ts - 1

    # Sort by timestamp
    all_items.sort(key=lambda x: x['fundingTime'])

    return all_items


def write_funding_csv(path: str, items: list):
    """
    Writes CSV with columns: timestamp_ms, funding_rate.

       Input:
          - path: output file path
          - items: funding entries from Binance API
       Output:
          - None. Writes a CSV file.
    """
    ensure_dir(os.path.dirname(path))
    
    with open(path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['timestamp_ms', 'funding_rate'])
        for it in items:
            w.writerow([it['fundingTime'], it['fundingRate']])


if __name__ == '__main__':
    args = get_args()
    base_dir = PARAMS['base_dir']
    symbols = args.symbols

    for symbol in symbols:
        print(f'Downloading funding history for: {symbol}')
        csv_path = build_funding_csv_path(base_dir, symbol)
        print(f'Output CSV: {csv_path}')

        items = fetch_all_funding_backward(symbol)
        print(f'Total records: {len(items)}')

        write_funding_csv(csv_path, items)
        print('Done.')
