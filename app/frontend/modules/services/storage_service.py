"""
Mongo storage service
Date: 2026-02-13
Version: 1.0
"""
from typing import List, Optional

from pymongo import MongoClient  # type: ignore[import-not-found]

from live.lib.logger import get_logger
from frontend.modules.frontend.models.frontend_models import (
    FrontendActivePositionDoc,
    FrontendArchivePositionDoc,
    FrontendIterationDoc,
)


### Collections ###
ACTIVE_POSITIONS_COLLECTION = 'backend_positions_active'
ARCHIVE_POSITIONS_COLLECTION = 'backend_positions_archive'
HEDGER_RUNS_COLLECTION = 'backend_hedger_runs'


class StorageService:
    def __init__(self, mongo_uri: str, mongo_db: str):
        if not isinstance(mongo_uri, str) or len(mongo_uri) == 0:
            raise RuntimeError('StorageService: mongo_uri is empty')
        if not isinstance(mongo_db, str) or len(mongo_db) == 0:
            raise RuntimeError('StorageService: mongo_db is empty')

        self._mongo_client = MongoClient(str(mongo_uri), serverSelectionTimeoutMS=5000)
        self._db = self._mongo_client[str(mongo_db)]
        self._logger = get_logger('frontend_storage')

        _ = self._mongo_client.server_info()
        self._logger.info('storage_ready')

    def list_active_positions(self) -> List[FrontendActivePositionDoc]:
        col = self._db[str(ACTIVE_POSITIONS_COLLECTION)]
        cursor = col.find({}, {'_id': 0}).sort('started_at_ms', -1)

        out = []
        for item in cursor:
            if not isinstance(item, dict):
                raise RuntimeError(f'StorageService.list_active_positions: item is not dict: {type(item)}')
            out.append(FrontendActivePositionDoc.from_dict(item))
        return out

    def list_archive_positions(self) -> List[FrontendArchivePositionDoc]:
        col = self._db[str(ARCHIVE_POSITIONS_COLLECTION)]
        cursor = col.find({}, {'_id': 0}).sort('finished_at_ms', -1)

        out = []
        for item in cursor:
            if not isinstance(item, dict):
                raise RuntimeError(f'StorageService.list_archive_positions: item is not dict: {type(item)}')
            out.append(FrontendArchivePositionDoc.from_dict(item))
        return out

    def find_position(self, run_id: str) -> Optional[FrontendActivePositionDoc]:
        if not isinstance(run_id, str) or len(run_id) == 0:
            raise RuntimeError('StorageService.find_position: run_id is empty')

        col = self._db[str(ACTIVE_POSITIONS_COLLECTION)]
        item = col.find_one({'run_id': str(run_id)}, {'_id': 0})
        if item is None:
            return None
        if not isinstance(item, dict):
            raise RuntimeError(f'StorageService.find_position: item is not dict: {type(item)}')
        return FrontendActivePositionDoc.from_dict(item)

    def find_position_archive(self, run_id: str) -> Optional[FrontendArchivePositionDoc]:
        if not isinstance(run_id, str) or len(run_id) == 0:
            raise RuntimeError('StorageService.find_position_archive: run_id is empty')

        col = self._db[str(ARCHIVE_POSITIONS_COLLECTION)]
        item = col.find_one({'run_id': str(run_id)}, {'_id': 0})
        if item is None:
            return None
        if not isinstance(item, dict):
            raise RuntimeError(f'StorageService.find_position_archive: item is not dict: {type(item)}')
        return FrontendArchivePositionDoc.from_dict(item)

    def list_iterations_by_run(self, run_id: str) -> List[FrontendIterationDoc]:
        if not isinstance(run_id, str) or len(run_id) == 0:
            raise RuntimeError('StorageService.list_iterations_by_run: run_id is empty')

        col = self._db[str(HEDGER_RUNS_COLLECTION)]
        cursor = col.find({'run_id': str(run_id)}, {'_id': 0}).sort('iteration_no', -1)

        out = []
        for item in cursor:
            if not isinstance(item, dict):
                raise RuntimeError(f'StorageService.list_iterations_by_run: item is not dict: {type(item)}')
            out.append(FrontendIterationDoc.from_dict(item))
        return out

    def find_iteration(self, iteration_id: str) -> FrontendIterationDoc:
        if not isinstance(iteration_id, str) or len(iteration_id) == 0:
            raise RuntimeError('StorageService.find_iteration: iteration_id is empty')

        col = self._db[str(HEDGER_RUNS_COLLECTION)]
        item = col.find_one({'id': str(iteration_id)}, {'_id': 0})
        if item is None:
            raise RuntimeError(f'StorageService.find_iteration: iteration not found: {iteration_id}')
        if not isinstance(item, dict):
            raise RuntimeError(f'StorageService.find_iteration: item is not dict: {type(item)}')
        return FrontendIterationDoc.from_dict(item)
