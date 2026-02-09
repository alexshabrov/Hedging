import argparse, time
from threading import Thread

from ..exchanges.exchange_factory import get_realtime_class


### CLI ###
def parse_args():
    p = argparse.ArgumentParser()

    p.add_argument('--symbol', required=True)
    p.add_argument('--seconds', type=int, default=60)

    return p.parse_args()


### Main ###
def main():
    args = parse_args()

    RealtimeClass = get_realtime_class('Binance')
    rt = RealtimeClass()

    rt.start()
    rt.wait_for_connect(timeout=60)

    ch = rt.subscribe('123', args.symbol)

    def consumer():
        for it in ch:
            delay = int(time.time() * 1000) - int(it.time_ms)
            print(f'[{args.symbol}] Delay {delay} ms, bookTicker {it.model_dump()}')

    Thread(target=consumer, daemon=True).start()

    for i in range(int(args.seconds)):
        print(f'Before stopping: {int(args.seconds) - i}')
        time.sleep(1)

    rt.stop()
    print('Realtime stopped')

    for i in range(10):
        print(f'Before exiting: {10 - i}')
        time.sleep(1)


if __name__ == '__main__':
    main()
