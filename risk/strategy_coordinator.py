"""
risk/strategy_coordinator.py — Multi-Strategy Allocation Coordinator & Conflict Resolution.

Capabilities:
1. Strategy Allocation Management:
   - Allocates capital weights based on rolling 30-day Sharpe ratios.
   - Maximum single-strategy allocation cap: 25.0%.
   - Correlation-Aware: If two strategies have correlation > 0.80, their combined allocation is capped.
2. Regime-Based Dynamic Weighting:
   - Trending Regime: Boosts trend strategies by +20%.
   - Ranging Regime: Boosts mean-reversion strategies by +20%.
3. Strategy Conflict Resolution:
   - When two strategies emit conflicting directional signals on the same asset, the higher-Sharpe strategy wins and its position size is halved.
"""

from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
import numpy as np
from logger import get_logger

logger = get_logger("strategy_coordinator")


@dataclass
class StrategyProfile:
    name: str
    strategy_type: str  # "trend", "mean_reversion", "momentum", "breakout"
    sharpe_30d: float
    current_allocation_weight: float = 0.20
    is_active: bool = True


class StrategyCoordinator:
    """
    Coordinates multi-strategy capital allocation, regime-based boosts, and directional conflict resolution.
    """

    def __init__(self, strategies: Optional[Dict[str, StrategyProfile]] = None):
        self.strategies = strategies or {
            "strategy_supertrend": StrategyProfile("strategy_supertrend", "trend", 1.85, 0.25),
            "strategy_scalper": StrategyProfile("strategy_scalper", "mean_reversion", 1.45, 0.20),
            "strategy_adx_ema": StrategyProfile("strategy_adx_ema", "trend", 1.30, 0.20),
            "strategy_swing": StrategyProfile("strategy_swing", "momentum", 1.15, 0.15)
        }

    def rebalance_allocations(
        self,
        current_regime: str = "TRENDING",
        cross_correlations: Optional[Dict[Tuple[str, str], float]] = None
    ) -> Dict[str, float]:
        """Calculates optimal strategy weights based on Sharpe, correlations, and regime."""
        total_sharpe = sum(max(0.1, s.sharpe_30d) for s in self.strategies.values() if s.is_active)
        raw_weights = {}

        # 1. Performance-proportional allocation
        for name, s in self.strategies.items():
            if not s.is_active:
                raw_weights[name] = 0.0
                continue
            raw_weights[name] = max(0.1, s.sharpe_30d) / total_sharpe

        # 2. Regime-based dynamic boost
        if current_regime.upper() in ["TRENDING", "BULL_TREND", "BEAR_TREND"]:
            for name, s in self.strategies.items():
                if s.strategy_type == "trend":
                    raw_weights[name] *= 1.20  # +20% boost
        elif current_regime.upper() in ["RANGING", "CHOP"]:
            for name, s in self.strategies.items():
                if s.strategy_type == "mean_reversion":
                    raw_weights[name] *= 1.20  # +20% boost

        # 3. Correlation-Aware Cap (Cap combined weight at 35% if corr > 0.80)
        if cross_correlations:
            for (s1, s2), corr in cross_correlations.items():
                if corr > 0.80 and s1 in raw_weights and s2 in raw_weights:
                    comb = raw_weights[s1] + raw_weights[s2]
                    if comb > 0.35:
                        scale = 0.35 / comb
                        raw_weights[s1] *= scale
                        raw_weights[s2] *= scale

        # 4. Normalize & Apply 25% Maximum Single Strategy Cap
        total_w = sum(raw_weights.values()) or 1.0
        final_weights = {}
        for name, w in raw_weights.items():
            norm_w = min(0.25, w / total_w)
            final_weights[name] = round(norm_w, 3)
            self.strategies[name].current_allocation_weight = round(norm_w, 3)

        logger.info(f"[STRAT_COORD] Rebalanced strategy allocations under regime {current_regime}: {final_weights}")
        return final_weights

    def resolve_signal_conflict(
        self,
        symbol: str,
        signals: Dict[str, int]  # { "strategy_supertrend": 1, "strategy_scalper": -1 }
    ) -> Tuple[int, str, float]:
        """
        Resolves directional disagreements on the same asset.
        Returns: (resolved_signal, winning_strategy, size_multiplier)
        """
        buys = [s for s, sig in signals.items() if sig > 0]
        sells = [s for s, sig in signals.items() if sig < 0]

        if not buys or not sells:
            # No conflict
            first_strat = list(signals.keys())[0] if signals else "none"
            sig = list(signals.values())[0] if signals else 0
            return sig, first_strat, 1.0

        # Conflict detected! Higher Sharpe strategy wins, size halved
        all_candidates = buys + sells
        winner = max(all_candidates, key=lambda s: self.strategies.get(s, StrategyProfile(s, "unknown", 1.0)).sharpe_30d)
        resolved_direction = signals[winner]

        logger.warning(
            f"[STRAT_COORD] ⚔️ Conflict on {symbol}: BUYs {buys} vs SELLs {sells}. "
            f"Winner: {winner} (Sharpe {self.strategies[winner].sharpe_30d:.2f}). Direction: {resolved_direction}, Size: 50%"
        )
        return resolved_direction, winner, 0.50
