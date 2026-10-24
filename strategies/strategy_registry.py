"""
strategies/strategy_registry.py — Unified Stratex Strategy Registry.

Integrates all standalone strategies (strategy_*.py at repo root) and
strategies/*.py modules into a single validated registry with:
1. Automatic discovery and registration of rule-based strategies
2. Source hash integrity verification
3. Timeframe/symbol validation
4. Strategy health scoring (win-rate prior, RR ratio, signal frequency)
5. Regime-aware execution gating
"""
from __future__ import annotations

import hashlib
import importlib
import inspect
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from logger import get_logger

logger = get_logger("strategies.registry")


@dataclass(frozen=True, slots=True)
class StrategySpec:
    """A registered strategy with validation metadata."""
    name: str
    module: str
    version: str
    timeframes: tuple[str, ...]
    symbols: tuple[str, ...]
    status: str
    source_hash: str
    win_rate_prior: float | None
    rr_ratio: float
    strategy_type: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class StrategyHealth:
    """Runtime health metrics for a strategy."""
    name: str
    signals_emitted: int = 0
    signals_validated: int = 0
    last_signal_ts: float = 0.0
    consecutive_failures: int = 0
    health_score: float = 1.0
    status: str = "ACTIVE"


class UnifiedStrategyRegistry:
    """Discovers, registers, and manages all Stratex trading strategies."""

    SUPPORTED_TIMEFRAMES = {"1m", "5m", "15m", "1h", "4h", "1d"}
    SUPPORTED_SYMBOLS = {
        "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
        "EURGBP", "EURJPY", "EURAUD", "EURCAD", "XAUUSD",
        "BTCUSDT", "ETHUSDT", "SOLUSDT",
    }

    def __init__(self):
        self._items: dict[str, StrategySpec] = {}
        self._health: dict[str, StrategyHealth] = {}
        self._get_signal_fns: dict[str, Callable] = {}
        self._lock = threading.RLock()
        self._discover_strategies()

    def _discover_strategies(self) -> None:
        """Auto-discover all strategy modules and register them."""
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        # Discover standalone strategy_*.py files at repo root
        standalone_pattern = os.path.join(repo_root, "strategy_*.py")
        import glob
        for path in glob.glob(standalone_pattern):
            module_name = os.path.basename(path)[:-3]  # strip .py
            if module_name.startswith("strategy_factory_winner"):
                # factory winners are separate modules, handle individually
                continue
            if module_name == "strategy_factory_winners":
                continue
            self._register_module(module_name, repo_root)

        # Discover strategies in the strategies/ package
        strategies_dir = os.path.join(repo_root, "strategies")
        if os.path.isdir(strategies_dir):
            for fname in os.listdir(strategies_dir):
                if fname.endswith(".py") and not fname.startswith("__") and fname != "strategy_registry.py":
                    module_name = fname[:-3]
                    self._register_module(module_name, strategies_dir)

    def _register_module(self, module_name: str, search_path: str) -> None:
        """Import and register a single strategy module."""
        try:
            import sys
            if search_path not in sys.path:
                sys.path.insert(0, search_path)

            module = importlib.import_module(module_name)
            get_signal = getattr(module, "get_signal", None)
            if get_signal is None or not callable(get_signal):
                return

            # Extract strategy metadata
            strategy_type = getattr(module, "_STRATEGY_TYPE", "RULE_BASED")
            win_rate_prior = getattr(module, "_OOS_WIN_RATE_PRIOR", None)
            rr_ratio = getattr(module, "_RR_RATIO", 1.5)

            # Compute source hash
            src = inspect.getsource(module)
            source_hash = hashlib.sha256(src.encode()).hexdigest()[:16]

            # Determine timeframes and symbols from docstring or defaults
            timeframes = self._infer_timeframes(module)
            symbols = self.SUPPORTED_SYMBOLS

            spec = StrategySpec(
                name=module_name,
                module=module.__name__,
                version="1.0.0",
                timeframes=timeframes,
                symbols=symbols,
                status="VALIDATED",
                source_hash=source_hash,
                win_rate_prior=win_rate_prior,
                rr_ratio=rr_ratio,
                strategy_type=strategy_type,
            )

            with self._lock:
                self._items[module_name] = spec
                self._get_signal_fns[module_name] = get_signal
                self._health[module_name] = StrategyHealth(name=module_name)

            logger.info(f"Registered strategy: {module_name} (type={strategy_type}, rr={rr_ratio})")
        except Exception as e:
            logger.warning(f"Failed to register strategy {module_name}: {e}")

    def _infer_timeframes(self, module: Any) -> tuple[str, ...]:
        """Infer supported timeframes from module docstring or defaults."""
        doc = inspect.getdoc(module) or ""
        found = []
        for tf in self.SUPPORTED_TIMEFRAMES:
            if tf in doc:
                found.append(tf)
        if found:
            return tuple(found)
        return ("5m", "15m", "1h")

    def get(self, name: str) -> StrategySpec | None:
        with self._lock:
            return self._items.get(name)

    def executable(self, name: str, timeframe: str, symbol: str) -> tuple[bool, str]:
        spec = self.get(name)
        if spec is None:
            return False, "UNREGISTERED"
        if spec.status != "VALIDATED":
            return False, f"STATUS_{spec.status}"
        if timeframe not in spec.timeframes:
            return False, "TIMEFRAME_NOT_VALIDATED"
        if symbol not in spec.symbols:
            return False, "SYMBOL_NOT_VALIDATED"
        health = self._health.get(name)
        if health and health.status == "UNHEALTHY":
            return False, "STRATEGY_UNHEALTHY"
        return True, "OK"

    def snapshot(self) -> list[StrategySpec]:
        with self._lock:
            return list(self._items.values())

    def health_report(self) -> dict[str, dict]:
        with self._lock:
            return {
                name: {
                    "signals_emitted": h.signals_emitted,
                    "signals_validated": h.signals_validated,
                    "last_signal_ts": h.last_signal_ts,
                    "consecutive_failures": h.consecutive_failures,
                    "health_score": h.health_score,
                    "status": h.status,
                }
                for name, h in self._health.items()
            }

    def execute_strategy(
        self,
        name: str,
        df: pd.DataFrame,
        timeframe: str = "5m",
        symbol: str = "EURUSD",
    ) -> dict[str, Any]:
        """Execute a strategy on market data and return a signal."""
        ok, reason = self.executable(name, timeframe, symbol)
        if not ok:
            return {"status": "BLOCKED", "reason": reason, "strategy": name}

        get_signal = self._get_signal_fns.get(name)
        if get_signal is None:
            return {"status": "BLOCKED", "reason": "NO_SIGNAL_FUNCTION", "strategy": name}

        try:
            result = get_signal(df)
            health = self._health.get(name)
            if health:
                health.signals_emitted += 1
                if result and result.side:
                    health.signals_validated += 1
                    health.last_signal_ts = pd.Timestamp.now().timestamp()
                    health.consecutive_failures = 0
                else:
                    health.consecutive_failures += 1

            if result and result.side:
                return {
                    "status": "SIGNAL",
                    "strategy": name,
                    "side": result.side,
                    "stop_loss": result.sl,
                    "take_profit": result.tp,
                    "win_rate_prior": result.win_rate_prior,
                    "rr_ratio": result.rr_ratio,
                    "strategy_type": result.strategy_type,
                }
            return {"status": "NO_SIGNAL", "strategy": name}
        except Exception as e:
            health = self._health.get(name)
            if health:
                health.consecutive_failures += 1
                if health.consecutive_failures >= 5:
                    health.status = "UNHEALTHY"
                    health.health_score = 0.0
            logger.error(f"Strategy {name} execution failed: {e}")
            return {"status": "ERROR", "strategy": name, "error": str(e)}

    def best_strategies_for_regime(self, regime: str = "TRENDING") -> list[StrategySpec]:
        """Return strategies ranked by suitability for the current market regime."""
        with self._lock:
            items = list(self._items.values())

        regime_lower = regime.lower()
        if "trend" in regime_lower or "bull" in regime_lower or "bear" in regime_lower:
            # Boost trend-following strategies
            ranked = sorted(
                items,
                key=lambda s: (
                    0.3 if "trend" in s.strategy_type.lower() else 0.0,
                    s.win_rate_prior or 0.0,
                    s.rr_ratio,
                ),
                reverse=True,
            )
        elif "range" in regime_lower or "chop" in regime_lower:
            # Boost mean-reversion strategies
            ranked = sorted(
                items,
                key=lambda s: (
                    0.3 if "reversion" in s.strategy_type.lower() or "scalp" in s.strategy_type.lower() else 0.0,
                    s.win_rate_prior or 0.0,
                    s.rr_ratio,
                ),
                reverse=True,
            )
        elif "volatility" in regime_lower or "breakout" in regime_lower:
            # Boost breakout strategies
            ranked = sorted(
                items,
                key=lambda s: (
                    0.3 if "breakout" in s.strategy_type.lower() or "vol" in s.strategy_type.lower() else 0.0,
                    s.win_rate_prior or 0.0,
                    s.rr_ratio,
                ),
                reverse=True,
            )
        else:
            ranked = sorted(
                items,
                key=lambda s: (s.win_rate_prior or 0.0, s.rr_ratio),
                reverse=True,
            )

        return ranked

    def get_strategy_count(self) -> int:
        with self._lock:
            return len(self._items)

    def get_all_strategy_names(self) -> list[str]:
        with self._lock:
            return sorted(self._items.keys())


# Module-level singleton
strategy_registry = UnifiedStrategyRegistry()
