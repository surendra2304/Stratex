"""stratex_freqtrade_adapter/strategy/__init__.py"""

from .interface import IStrategy
from .adapter import FreqtradeStrategyAdapter, SignalResult
from .registry import StrategyRegistry, registry
from . import builtin

__all__ = [
    "IStrategy",
    "FreqtradeStrategyAdapter",
    "SignalResult",
    "StrategyRegistry",
    "registry",
    "builtin",
]
