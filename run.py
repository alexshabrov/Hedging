import os
import signal
import subprocess
import sys
import threading
import time
from typing import Dict, List


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(BASE_DIR, 'app')


import envcheck
from backend.modules.backend_env import required_env as backend_required_env
from frontend.modules.frontend_env import required_env as frontend_required_env


RUN: List[Dict[str, str]] = [
    {
        'name': 'backend_service',
        'cwd': BASE_DIR,
        'cmd': 'python app/backend/backend_service.py',
    },
    {
        'name': 'frontend_service',
        'cwd': BASE_DIR,
        'cmd': 'python app/frontend/app.py',
    },
]


def apply_default_env() -> None:
    defaults = {
        'FRONT_BACKEND_URL': 'http://127.0.0.1:8080',
        'MONGO_URI': 'mongodb://hedging_mongo:27017',
        'MONGO_DB': 'hedging',
        'MONGO_COLLECTION': 'hedge_runs',
        'TICK_MS': '5',
        'GTX_COOLDOWN_MS': '5',
        'ENTRANCE_TIMEOUT_MS': '60000',
        'COWSWAP_API_TIMEOUT_SEC': '10',
        'COWSWAP_WAIT_TIMEOUT_SEC': '300',
        'COWSWAP_POLL_INTERVAL_SEC': '3',
    }

    for key in defaults:
        if key not in os.environ:
            os.environ[key] = str(defaults[key])


def required_env() -> List[str]:
    names = []
    seen = set()

    for name in backend_required_env():
        if name in seen:
            continue
        seen.add(name)
        names.append(name)

    for name in frontend_required_env():
        if name in seen:
            continue
        seen.add(name)
        names.append(name)

    return names


def check_required_env() -> None:
    apply_default_env()
    envcheck.require(required_env())


class ServiceThread(threading.Thread):
    def __init__(self, spec: Dict[str, str]):
        super().__init__(daemon=True)
        self.spec = spec
        self.proc = None
        self._stop = threading.Event()

    def run(self) -> None:
        name = self.spec['name']
        cwd = self.spec['cwd']
        cmd = self.spec['cmd']

        while not self._stop.is_set():
            print(f'[root-supervisor] starting {name}: {cmd} (cwd={cwd})', flush=True)

            try:
                env = os.environ.copy()
                pp = envcheck.get_default('PYTHONPATH', '')
                paths = [p for p in pp.split(':') if len(p) > 0]
                if APP_DIR not in paths:
                    env['PYTHONPATH'] = APP_DIR if len(pp) == 0 else f'{APP_DIR}:{pp}'
                else:
                    env['PYTHONPATH'] = pp

                self.proc = subprocess.Popen(cmd, cwd=cwd, shell=True, env=env)
                rc = self.proc.wait()
                print(f'[root-supervisor] {name} exited with code {rc}', flush=True)
            except Exception as e:
                print(f'[root-supervisor] failed to start {name}: {e}', file=sys.stderr, flush=True)

            if self._stop.is_set():
                break

            time.sleep(1)

    def stop(self) -> None:
        self._stop.set()
        if self.proc is None:
            return
        if self.proc.poll() is not None:
            return

        try:
            if hasattr(os, 'killpg'):
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            else:
                self.proc.terminate()
        except Exception:
            pass


def main() -> None:
    check_required_env()

    threads = []

    def handle_signal(signum, frame):
        print(f'\n[root-supervisor] received signal {signum}, stopping ...', flush=True)
        for t in threads:
            t.stop()
        time.sleep(1)
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    for spec in RUN:
        t = ServiceThread(spec)
        t.start()
        threads.append(t)

    while True:
        time.sleep(60)


if __name__ == '__main__':
    main()
