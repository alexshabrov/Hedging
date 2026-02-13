"""
Backend API client service
Date: 2026-02-13
Version: 1.0
"""
import urllib.error
import urllib.request
from typing import Dict, List

import orjson

from live.lib.logger import get_logger


class BackendApiService:
    def __init__(self, backend_base_url: str, timeout_sec: int = 10):
        if not isinstance(backend_base_url, str) or len(backend_base_url) == 0:
            raise RuntimeError('BackendApiService: backend_base_url is empty')
        if not isinstance(timeout_sec, int):
            raise RuntimeError(f'BackendApiService: timeout_sec is not int: {type(timeout_sec)}')
        if int(timeout_sec) <= 0:
            raise RuntimeError(f'BackendApiService: timeout_sec must be > 0, got: {timeout_sec}')

        self._backend_base_url = str(backend_base_url).rstrip('/')
        self._timeout_sec = int(timeout_sec)
        self._logger = get_logger('frontend_backend_api')

    def health(self) -> dict:
        return self._request_json('GET', '/api/health', None)

    def start_run(self, payload: dict) -> str:
        if payload is None:
            raise RuntimeError('BackendApiService.start_run: payload is None')
        if not isinstance(payload, dict):
            raise RuntimeError(f'BackendApiService.start_run: payload is not dict: {type(payload)}')

        res = self._request_json('POST', '/api/runs/start', payload)

        if 'ok' not in res:
            raise RuntimeError('BackendApiService.start_run: ok is missing in response')
        if bool(res['ok']) is not True:
            if 'error' not in res:
                raise RuntimeError('BackendApiService.start_run: backend returned not ok without error')
            raise RuntimeError(f"BackendApiService.start_run: backend error: {res['error']}")
        if 'run_id' not in res:
            raise RuntimeError('BackendApiService.start_run: run_id is missing in response')

        return str(res['run_id'])

    def stop_run(self, run_id: str) -> None:
        if not isinstance(run_id, str) or len(run_id) == 0:
            raise RuntimeError('BackendApiService.stop_run: run_id is empty')

        res = self._request_json('POST', f'/api/runs/{run_id}/stop', None)

        if 'ok' not in res:
            raise RuntimeError('BackendApiService.stop_run: ok is missing in response')
        if bool(res['ok']) is not True:
            if 'error' not in res:
                raise RuntimeError('BackendApiService.stop_run: backend returned not ok without error')
            raise RuntimeError(f"BackendApiService.stop_run: backend error: {res['error']}")

    def list_runtime_positions(self) -> List[Dict]:
        res = self._request_json('GET', '/api/positions', None)

        if 'ok' not in res:
            raise RuntimeError('BackendApiService.list_runtime_positions: ok is missing in response')
        if bool(res['ok']) is not True:
            if 'error' not in res:
                raise RuntimeError('BackendApiService.list_runtime_positions: backend returned not ok without error')
            raise RuntimeError(f"BackendApiService.list_runtime_positions: backend error: {res['error']}")
        if 'items' not in res:
            raise RuntimeError('BackendApiService.list_runtime_positions: items is missing in response')
        if not isinstance(res['items'], list):
            raise RuntimeError(f"BackendApiService.list_runtime_positions: items is not list: {type(res['items'])}")

        out = []
        for row in res['items']:
            if not isinstance(row, dict):
                raise RuntimeError(f'BackendApiService.list_runtime_positions: row is not dict: {type(row)}')
            out.append(dict(row))
        return out

    def _request_json(self, method: str, path: str, payload: dict):
        if not isinstance(method, str) or len(method) == 0:
            raise RuntimeError('BackendApiService._request_json: method is empty')
        if not isinstance(path, str) or len(path) == 0:
            raise RuntimeError('BackendApiService._request_json: path is empty')

        url = f'{self._backend_base_url}{path}'
        data = None
        headers = {
            'Accept': 'application/json',
        }

        if payload is not None:
            if not isinstance(payload, dict):
                raise RuntimeError(f'BackendApiService._request_json: payload is not dict: {type(payload)}')
            data = orjson.dumps(payload)
            headers['Content-Type'] = 'application/json'

        req = urllib.request.Request(
            url=url,
            method=str(method),
            data=data,
            headers=headers,
        )

        try:
            with urllib.request.urlopen(req, timeout=self._timeout_sec) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            if e.fp is not None:
                err_body = e.fp.read()
                try:
                    err_payload = orjson.loads(err_body)
                    if isinstance(err_payload, dict):
                        if 'error' in err_payload:
                            raise RuntimeError(f'BackendApiService HTTP error {e.code}: {err_payload["error"]}')
                        raise RuntimeError(f'BackendApiService HTTP error {e.code}: {err_payload}')
                except Exception:
                    pass
            raise RuntimeError(f'BackendApiService HTTP error {e.code}: {e.reason}')
        except Exception as e:
            raise RuntimeError(f'BackendApiService request failed: {e}')

        payload_out = orjson.loads(body)
        if not isinstance(payload_out, dict):
            raise RuntimeError(f'BackendApiService: response payload is not dict: {type(payload_out)}')

        self._logger.info(f'backend_request_ok method={method} path={path}')
        return payload_out
