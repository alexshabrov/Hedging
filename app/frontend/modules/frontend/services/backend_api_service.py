"""
Backend API client service
Date: 2026-02-13
Version: 2.0
"""
import urllib.error
import urllib.request
from typing import List, Optional

import orjson

from live.lib.logger import get_logger
from models.backend_models import BackendPositionView, BackendStartRunRequest
from modules.frontend.models.frontend_models import (
    FrontendBackendPositionsResponse,
    FrontendBackendStartRunResponse,
    FrontendBackendStopRunResponse,
)


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

    def health(self):
        return self._request_json('GET', '/api/health', None)

    def start_run(self, req: BackendStartRunRequest) -> str:
        if req is None:
            raise RuntimeError('BackendApiService.start_run: req is None')
        if not isinstance(req, BackendStartRunRequest):
            raise RuntimeError(f'BackendApiService.start_run: req is not BackendStartRunRequest: {type(req)}')

        res = self._request_json('POST', '/api/runs/start', req.model_dump())
        parsed = FrontendBackendStartRunResponse.from_dict(res)
        if not bool(parsed.ok):
            raise RuntimeError(f'BackendApiService.start_run: backend error: {parsed.error}')
        if parsed.run_id is None:
            raise RuntimeError('BackendApiService.start_run: parsed.run_id is None')
        return str(parsed.run_id)

    def stop_run(self, run_id: str) -> None:
        if not isinstance(run_id, str) or len(run_id) == 0:
            raise RuntimeError('BackendApiService.stop_run: run_id is empty')

        res = self._request_json('POST', f'/api/runs/{run_id}/stop', None)
        parsed = FrontendBackendStopRunResponse.from_dict(res)
        if not bool(parsed.ok):
            raise RuntimeError(f'BackendApiService.stop_run: backend error: {parsed.error}')

    def list_runtime_positions(self) -> List[BackendPositionView]:
        res = self._request_json('GET', '/api/positions', None)
        parsed = FrontendBackendPositionsResponse.from_dict(res)
        if not bool(parsed.ok):
            raise RuntimeError(f'BackendApiService.list_runtime_positions: backend error: {parsed.error}')
        return parsed.items

    def _request_json(self, method: str, path: str, payload: Optional[dict]):
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
