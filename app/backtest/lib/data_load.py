"""
Daily Binance trades downloader and simple downloaded-files summary.

Provides:
 - build_download_list(symbol, from_date, base_dir) -> list[str]:
   build a list of missing '*-trades-YYYY-MM-DD.zip' starting from a date.
 - download_files(symbol, download_list, base_dir, download_threads) -> list[str]:
   download files concurrently, validate non-empty, and return a sorted list.
"""
import os, time
from threading import Thread
from queue import Queue
from tqdm import tqdm
from datetime import datetime, timedelta
from lib.helders import *

# Build download list
def build_download_list(symbol: str, from_date: str, base_dir: str) -> list[str]:
    """
    Builds a list of Binance daily trade zip filenames to download starting from a date.

       Input:
          - symbol: market symbol, e.g. 'BTCUSDT'
          - from_date: inclusive start date in 'YYYY-MM-DD'
          - base_dir: base directory to store data under <base_dir>/<symbol>/data

       Output:
          - list of filenames like '{symbol}-trades-YYYY-MM-DD.zip' that do not exist locally
    """
    download_list = []
    already = 0

    dt = datetime.strptime(from_date, '%Y-%m-%d') - timedelta(days=1)
    to_dt = datetime.now() - timedelta(days=2) # history gap in Binance

    while dt < to_dt:
        dt += timedelta(days=1)
        date_str = dt.strftime('%Y-%m-%d')

        # File name and path
        filename = f'{symbol}-trades-{date_str}.zip'
        filepath = get_file_path(base_dir, symbol, filename)

        if os.path.exists(filepath):
            already += 1
            continue

        # Add to list
        download_list.append(filename)

    return download_list

# Download files
def download_files(symbol: str, download_list: list[str], base_dir: str, download_threads: int):
    """
    Downloads Binance daily trade zip files concurrently and returns discovered files summary.

       Input:
          - symbol: market symbol, e.g. 'BTCUSDT'
          - download_list: list of filenames to download (as from build_download_list)
          - base_dir: base directory to store data under <base_dir>/<symbol>/data
          - download_threads: number of concurrent threads to use

       Output:
          - file_list: sorted list of files present under data directory after downloads
            Prints total size in MB and files count.
    """
    def worker(in_queue: Queue, out_queue: Queue):
        while not in_queue.empty():
            # Get filename
            filename = in_queue.get()

            # Url
            url = f'https://data.binance.vision/data/futures/um/daily/trades/{symbol}/{filename}'

            # Download
            filepath = get_file_path(base_dir, symbol, filename)
            os.system(f'wget -q -O {filepath} {url}')

            # Remove if zero size
            if os.path.getsize(filepath) == 0:
                os.remove(filepath) # no history for this day yet

            # Put to out queue
            out_queue.put(filename)

    # Queues
    in_queue = Queue()
    out_queue = Queue()

    # Fill in_queue
    for filename in download_list:
        in_queue.put(filename)

    # Progress bar
    pbar = tqdm(total=len(download_list), desc='Downloading')

    # Spawn threads
    threads = []
    for _ in range(download_threads):
        thread = Thread(target=worker, args=(in_queue, out_queue))
        thread.start()
        threads.append(thread)

    # Wait for all threads to finish
    prev = 0

    while out_queue.qsize() < len(download_list):
        time.sleep(0.1)

        if out_queue.qsize() > prev:
            pbar.update(out_queue.qsize() - prev)
            prev = out_queue.qsize()

    pbar.close()

    # Ensure all threads are finished
    for thread in threads:
        thread.join()

    print('Download complete')

    # List files and get summary size
    size = 0
    file_list = []

    dir_path = build_data_dir(base_dir, symbol)
    if os.path.exists(dir_path):
        for file in os.listdir(dir_path):
            file_path = get_file_path(base_dir, symbol, file)
            s = os.path.getsize(file_path)
            assert s > 0, f'{file} is empty'
            size += s
            file_list.append(file)

    file_list.sort()

    print(f'Total size: {size / 1024 / 1024:.2f} MB, {len(file_list)} files')

    return file_list