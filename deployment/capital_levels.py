"""
deployment/capital_levels.py — Graduated Capital Exposure Hierarchy & Level Specifications.

Defines:
- Level 1 (Pilot): $500 - $1,000 max capital, single strategy, 5% max position, 2% max daily loss, 5% max DD.
- Level 2 (Growth): $2,000 - $5,000 max capital, up to 3 strategies, 8% max position, 3% max daily loss, 8% max DD.
- Level 3 (Established): $10,000 - $25,000 max capital, all strategies, 10% max position, 4% max daily loss, 12% max DD.
- Level 4 (Scale): $50,000+ max capital, custom parameters, manual review.
- Automatic Demotion triggers on consecutive loss days or drawdown breach.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CapitalLevelSpec:
    level: int
    name: str
    min_capital: float
    max_capital: float
    max_strategies: int
    max_position_size_pct: float     # % of total capital
    max_daily_loss_pct: float        # % of total capital
    max_drawdown_limit_pct: float    # % of total capital
    required_clean_days_for_next: int
    allow_custom_params: bool = False


GRADUATED_LEVELS: dict[int, CapitalLevelSpec] = {
    1: CapitalLevelSpec(
        level=1,
        name="LEVEL_1_PILOT",
        min_capital=500.0,
        max_capital=1000.0,
        max_strategies=1,
        max_position_size_pct=0.05,
        max_daily_loss_pct=0.02,
        max_drawdown_limit_pct=0.05,
        required_clean_days_for_next=30
    ),
    2: CapitalLevelSpec(
        level=2,
        name="LEVEL_2_GROWTH",
        min_capital=2000.0,
        max_capital=5000.0,
        max_strategies=3,
        max_position_size_pct=0.08,
        max_daily_loss_pct=0.03,
        max_drawdown_limit_pct=0.08,
        required_clean_days_for_next=30
    ),
    3: CapitalLevelSpec(
        level=3,
        name="LEVEL_3_ESTABLISHED",
        min_capital=10000.0,
        max_capital=25000.0,
        max_strategies=6,
        max_position_size_pct=0.10,
        max_daily_loss_pct=0.04,
        max_drawdown_limit_pct=0.12,
        required_clean_days_for_next=60
    ),
    4: CapitalLevelSpec(
        level=4,
        name="LEVEL_4_SCALE",
        min_capital=50000.0,
        max_capital=1000000.0,
        max_strategies=10,
        max_position_size_pct=0.10,
        max_daily_loss_pct=0.04,
        max_drawdown_limit_pct=0.15,
        required_clean_days_for_next=90,
        allow_custom_params=True
    )
}


def get_level_spec(level: int) -> CapitalLevelSpec:
    """Returns immutable level specification. Defaults to Level 1 if invalid."""
    return GRADUATED_LEVELS.get(level, GRADUATED_LEVELS[1])


def check_demotion_trigger(
    current_level: int,
    current_drawdown_pct: float,
    consecutive_loss_days: int
) -> tuple[bool, int | None, str]:
    """
    Evaluates if account performance requires demoting to a lower capital tier.
    Returns (should_demote, new_level, reason).
    """
    if current_level <= 1:
        # At Level 1, breach means complete halt
        spec = get_level_spec(1)
        if current_drawdown_pct >= spec.max_drawdown_limit_pct * 100.0:
            return True, None, f"Level 1 drawdown limit ({spec.max_drawdown_limit_pct*100}%) breached: {current_drawdown_pct:.2f}%"
        if consecutive_loss_days >= 3:
            return True, None, f"Level 1 consecutive losing days threshold reached ({consecutive_loss_days} days >= 3)"
        return False, 1, "Level 1 metrics nominal"

    spec = get_level_spec(current_level)
    if current_drawdown_pct >= spec.max_drawdown_limit_pct * 100.0:
        new_lvl = current_level - 1
        return True, new_lvl, f"Drawdown ({current_drawdown_pct:.2f}%) breached Level {current_level} limit ({spec.max_drawdown_limit_pct*100}%). Demoting to Level {new_lvl}."

    if consecutive_loss_days >= 3:
        new_lvl = current_level - 1
        return True, new_lvl, f"Consecutive losing days ({consecutive_loss_days}) exceeded 3-day tolerance. Demoting to Level {new_lvl}."

    return False, current_level, "Nominal"
