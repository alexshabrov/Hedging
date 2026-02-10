"""
Swapper interface
Date: 2026-02-10
Version: 1.0
"""
from dex.models.swapper_models import SwapRequest, SwapResult

# Base interface
class SwapperInterface:
    def swap_sync(self, request: SwapRequest) -> SwapResult:
        raise NotImplementedError('swap_sync must be implemented')