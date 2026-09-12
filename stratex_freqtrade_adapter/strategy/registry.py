"""stratex_freqtrade_adapter/strategy/registry.py

Registry for Freqtrade strategies, enabling discovery, inspection, and instantiation.
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional, Type
from .interface import IStrategy

# Global module-level dictionary of registered strategies
_STRATEGIES: Dict[str, Type[IStrategy]] = {}


class StrategyRegistry:
    """Registry for Freqtrade strategies."""

    def __init__(self):
        self._strategies = _STRATEGIES

    @classmethod
    def register(cls, name: Optional[str] = None):
        """Decorator to register a strategy class."""
        def decorator(subclass: Type[IStrategy]):
            key = (name or subclass.__name__).lower()
            _STRATEGIES[key] = subclass
            return subclass
        return decorator

    def add(self, name: str, strategy_cls: Type[IStrategy]) -> None:
        _STRATEGIES[name.lower()] = strategy_cls

    def get(self, name: str) -> Optional[Type[IStrategy]]:
        _ensure_builtins()
        return _STRATEGIES.get(name.lower())

    def create(self, name: str, config: Optional[dict] = None) -> Optional[IStrategy]:
        cls = self.get(name)
        if cls:
            return cls(config=config)
        return None

    def list_strategies(self) -> List[Dict[str, Any]]:
        _ensure_builtins()
        result = []
        for name, strat_cls in _STRATEGIES.items():
            result.append({
                "name": name,
                "class_name": strat_cls.__name__,
                "timeframe": getattr(strat_cls, "timeframe", "5m"),
                "stoploss": getattr(strat_cls, "stoploss", -0.05),
                "minimal_roi": getattr(strat_cls, "minimal_roi", {}),
                "can_short": getattr(strat_cls, "can_short", True),
                "trailing_stop": getattr(strat_cls, "trailing_stop", False),
            })
        return result


def _ensure_builtins():
    """Lazily ensures canonical built-in strategies are imported and registered."""
    try:
        from . import builtin  # noqa: F401
    except Exception:
        pass


registry = StrategyRegistry()
_ensure_builtins()
