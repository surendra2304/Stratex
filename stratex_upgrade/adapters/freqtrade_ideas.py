"""Freqtrade-inspired protections expressed as Stratex-native primitives."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ProtectionConfig:
    stoploss_guard_trades: int = 4
    stoploss_guard_minutes: int = 60
    max_drawdown: Decimal = Decimal("0.05")
    cooldown_minutes: int = 5


class ProtectionEvaluator:
    def __init__(self, config: ProtectionConfig | None = None):
        self.config = config or ProtectionConfig()

    def cooldown_until(self, last_loss_ns: int | None) -> float | None:
        if last_loss_ns is None:
            return None
        return (last_loss_ns / 1_000_000_000) + self.config.cooldown_minutes * 60

    def blocked(self, *, now: float, last_loss_ns: int | None, drawdown: Decimal, consecutive_losses: int) -> tuple[bool, str]:
        if drawdown >= self.config.max_drawdown:
            return True, "MAX_DRAWDOWN"
        if consecutive_losses >= self.config.stoploss_guard_trades:
            return True, "STOPLOSS_GUARD"
        until = self.cooldown_until(last_loss_ns)
        if until is not None and now < until:
            return True, "COOLDOWN"
        return False, "OK"
