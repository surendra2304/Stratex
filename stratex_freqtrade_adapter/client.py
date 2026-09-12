"""stratex_freqtrade_adapter/client.py

Unified FreqtradeNativeEngine facade for Stratex.
Provides clean namespaced access:
- ft.strategies
- ft.pairlists
- ft.protections
- ft.roi
- ft.data
- ft.optimizer
- ft.walkforward
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import pandas as pd

from .strategy.interface import IStrategy
from .strategy.adapter import FreqtradeStrategyAdapter, SignalResult
from .strategy.registry import StrategyRegistry, registry
from .pairlist.manager import PairListManager
from .pairlist.volume_pairlist import VolumePairList
from .pairlist.static_pairlist import StaticPairList
from .pairlist.filters import PriceFilter, SpreadFilter, VolatilityFilter
from .protections import ProtectionManager, ProtectionDecision
from .roi.roi_engine import ROIEngine
from .roi.trailing_engine import TrailingStopEngine
from .data.downloader import FreqtradeDataDownloader
from .optimizer import StrategyOptimizer, OptimizationConfig
from .walkforward import WalkForwardValidator, Window


class StrategyNamespace:
    """Namespace for Freqtrade strategies."""

    def __init__(self, reg: StrategyRegistry):
        self._registry = reg

    def get(self, name: str) -> Optional[type[IStrategy]]:
        return self._registry.get(name)

    def list(self) -> List[Dict[str, Any]]:
        return self._registry.list_strategies()

    def create(self, name: str, config: Optional[dict] = None) -> Optional[IStrategy]:
        return self._registry.create(name, config)

    def wrap(self, strategy: IStrategy, name: Optional[str] = None) -> FreqtradeStrategyAdapter:
        """Wraps any IStrategy into a Stratex-compliant strategy."""
        return FreqtradeStrategyAdapter(strategy, name=name)


class PairlistNamespace:
    """Namespace for dynamic PairList generation and filtering."""

    def __init__(self):
        self.manager = PairListManager()
        self.volume = VolumePairList()
        self.static = StaticPairList()

    def generate(self) -> List[str]:
        return self.manager.generate_pairlist()

    def evaluate_volume(self, number_assets: int = 20) -> List[str]:
        v = VolumePairList(number_assets=number_assets)
        return v.filter_pairlist([])


class ROINamespace:
    """Namespace for minimal_roi tables and trailing stop state machines."""

    @staticmethod
    def create_roi_engine(minimal_roi: Dict[str, float]) -> ROIEngine:
        return ROIEngine(minimal_roi)

    @staticmethod
    def create_trailing_engine(
        trailing_stop: bool = True,
        trailing_stop_positive: float = 0.015,
        trailing_stop_positive_offset: float = 0.025,
        trailing_only_offset_is_reached: bool = True,
    ) -> TrailingStopEngine:
        return TrailingStopEngine(
            trailing_stop=trailing_stop,
            trailing_stop_positive=trailing_stop_positive,
            trailing_stop_positive_offset=trailing_stop_positive_offset,
            trailing_only_offset_is_reached=trailing_only_offset_is_reached,
        )


class FreqtradeNativeEngine:
    """Unified Facade matching Freqtrade capabilities into Stratex."""

    def __init__(self):
        self.strategies = StrategyNamespace(registry)
        self.pairlists = PairlistNamespace()
        self.protections = ProtectionManager()
        self.roi = ROINamespace()
        self.data = FreqtradeDataDownloader()

    def status(self) -> Dict[str, Any]:
        return {
            "status": "HEALTHY",
            "registered_strategies": len(self.strategies.list()),
            "protections": self.protections.get_status(),
            "pairlist": self.pairlists.manager.get_status(),
            "mode": "100% Free Public Sources (Binance unauthenticated)",
        }


# Global singleton facade
ft = FreqtradeNativeEngine()
