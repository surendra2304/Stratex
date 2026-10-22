"""stratex_freqtrade_adapter/__init__.py

Package entry point for Stratex Freqtrade Native Adapter.
Exports unified `ft` facade, Strategy, Pairlist, Protections, and Hyperopt components.
100% backwards-compatible with all existing Stratex modules and test suites.
"""

from .parameters import (
    BaseParameter, IntParameter, RealParameter, CategoricalParameter
)
from .protections import ProtectionManager, ProtectionDecision
from .optimizer import StrategyOptimizer, OptimizationConfig
from .walkforward import WalkForwardValidator, Window
from .stratex_bridge import StratexStrategyBridge
from .strategy_parameterizer import ParameterizedADXEMA, get_parameterized_strategy

from .strategy.interface import IStrategy
from .strategy.adapter import FreqtradeStrategyAdapter, SignalResult
from .strategy.registry import StrategyRegistry, registry
from .roi.roi_engine import ROIEngine
from .roi.trailing_engine import TrailingStopEngine
from .pairlist.volume_pairlist import VolumePairList
from .pairlist.static_pairlist import StaticPairList
from .pairlist.manager import PairListManager
from .pairlist.filters import PriceFilter, SpreadFilter, VolatilityFilter
from .data.downloader import FreqtradeDataDownloader
from .client import ft, FreqtradeNativeEngine

__all__ = [
    # Unified Facade
    "ft",
    "FreqtradeNativeEngine",
    # Strategy Pipeline
    "IStrategy",
    "FreqtradeStrategyAdapter",
    "SignalResult",
    "StrategyRegistry",
    "registry",
    # ROI & Trailing Stop
    "ROIEngine",
    "TrailingStopEngine",
    # Pairlists
    "VolumePairList",
    "StaticPairList",
    "PairListManager",
    "PriceFilter",
    "SpreadFilter",
    "VolatilityFilter",
    # Data
    "FreqtradeDataDownloader",
    # Preserved Parameters & Protections
    "BaseParameter",
    "IntParameter",
    "RealParameter",
    "CategoricalParameter",
    "ProtectionManager",
    "ProtectionDecision",
    "StrategyOptimizer",
    "OptimizationConfig",
    "WalkForwardValidator",
    "Window",
    "StratexStrategyBridge",
    "ParameterizedADXEMA",
    "get_parameterized_strategy",
]
