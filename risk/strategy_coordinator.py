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

from dataclasses import dataclass

from logger import get_logger
from numeric_safety import finite_float

logger = get_logger("strategy_coordinator")


@dataclass
class StrategyProfile:
    name: str
    strategy_type: str  # "trend", "mean_reversion", "momentum", "breakout"
    sharpe_30d: float
    current_allocation_weight: float = 0.20
    is_active: bool = True


def _sharpe(profile: "StrategyProfile | None") -> float:
    """Sharpe used for ranking; unknown/NaN Sharpe ranks at the 0.1 floor."""
    if profile is None:
        return 0.1
    value = finite_float(profile.sharpe_30d)
    return max(0.1, value) if value is not None else 0.1


class StrategyCoordinator:
    """
    Coordinates multi-strategy capital allocation, regime-based boosts, and directional conflict resolution.

    The default profiles' Sharpe ratios are configured priors, not measured
    performance; ``SHARPE_SOURCE`` says so and API consumers surface it.
    """

    SHARPE_SOURCE = "configured_prior"

    def __init__(self, strategies: dict[str, StrategyProfile] | None = None):
        self.strategies = strategies or {
            "strategy_supertrend": StrategyProfile("strategy_supertrend", "trend", 1.85, 0.25),
            "strategy_scalper": StrategyProfile("strategy_scalper", "mean_reversion", 1.45, 0.20),
            "strategy_adx_ema": StrategyProfile("strategy_adx_ema", "trend", 1.30, 0.20),
            "strategy_swing": StrategyProfile("strategy_swing", "momentum", 1.15, 0.15)
        }

    def rebalance_allocations(
        self,
        current_regime: str = "TRENDING",
        cross_correlations: dict[tuple[str, str], float] | None = None
    ) -> dict[str, float]:
        """Calculates optimal strategy weights based on Sharpe, correlations, and regime."""
        total_sharpe = sum(_sharpe(s) for s in self.strategies.values() if s.is_active)
        raw_weights = {}

        # 1. Performance-proportional allocation
        for name, s in self.strategies.items():
            if not s.is_active:
                raw_weights[name] = 0.0
                continue
            raw_weights[name] = _sharpe(s) / total_sharpe

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
                c = finite_float(corr)
                # An unreadable correlation is treated as highly correlated.
                if (c is None or c > 0.80) and s1 in raw_weights and s2 in raw_weights:
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
        signals: dict[str, int]  # { "strategy_supertrend": 1, "strategy_scalper": -1 }
    ) -> tuple[int, str, float]:
        """
        Resolves directional disagreements on the same asset.
        Returns: (resolved_signal, winning_strategy, size_multiplier)
        """
        directions = {}
        for strategy, sig in signals.items():
            value = finite_float(sig)
            directions[strategy] = 0 if value is None else (1 if value > 0 else -1 if value < 0 else 0)
        buys = [s for s, d in directions.items() if d > 0]
        sells = [s for s, d in directions.items() if d < 0]

        if not buys and not sells:
            return 0, "none", 1.0
        if not buys or not sells:
            # No conflict: every directional vote agrees. (The old code returned
            # the *first* entry's signal, so a leading 0/neutral vote discarded
            # an agreeing BUY.)
            voters = buys or sells
            return directions[voters[0]], voters[0], 1.0

        # Conflict detected! Higher Sharpe strategy wins, size halved
        ranked = sorted(buys + sells, key=lambda s: _sharpe(self.strategies.get(s)), reverse=True)
        best = _sharpe(self.strategies.get(ranked[0]))
        tied = [s for s in ranked if _sharpe(self.strategies.get(s)) == best]
        if {directions[s] for s in tied} == {1, -1}:
            logger.warning(f"[STRAT_COORD] ⚔️ Conflict on {symbol} tied at Sharpe {best:.2f} across directions: standing aside")
            return 0, "none", 0.0
        winner = ranked[0]
        resolved_direction = directions[winner]
        logger.warning(
            f"[STRAT_COORD] ⚔️ Conflict on {symbol}: BUYs {buys} vs SELLs {sells}. "
            f"Winner: {winner} (Sharpe {best:.2f}). Direction: {resolved_direction}, Size: 50%"
        )
        return resolved_direction, winner, 0.50
