"""
Swapper factory
Date: 2026-02-10
Version: 1.0
"""
from typing import Union
from dex.lib.logger import get_logger
from dex.models.swapper_models import CowSwapConfig, SwapperType
from dex.swappers.swapper_interface import SwapperInterface
from dex.swappers.cow_swap.cow_swap_class import CowSwapSwapper

# Factory
class SwapperFactory:
    def __init__(self, config: Union[CowSwapConfig]):
        self._config = config
        self._logger = get_logger('swapper_factory')

    def create(self) -> SwapperInterface:
        if not isinstance(self._config, CowSwapConfig):
            raise RuntimeError('config is not CowSwapConfig')

        if self._config.swapper_type == SwapperType.COW_SWAP:
            return CowSwapSwapper(self._config)

        raise RuntimeError(f'Unsupported swapper_type: {self._config.swapper_type}')