"""Trade protections for Stratex.

Inspired by Freqtrade's protection concepts; intentionally independent.
Protections are conservative pre-entry filters and never bypass Stratex RiskGate.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

@dataclass
class ProtectionDecision:
    allowed: bool
    reason: str
    cooldown_until: datetime | None = None

class ProtectionManager:
    def __init__(
        self,
        cooldown_minutes: int = 30,
        stoploss_guard_lookback: int = 6,
        stoploss_guard_max_losses: int = 3,
        low_profit_lookback: int = 20,
        low_profit_min_trades: int = 8,
        low_profit_threshold: float = 0.0,
        max_drawdown_pct: float = 0.05,
    ):
        self.cooldown_minutes = cooldown_minutes
        self.stoploss_guard_lookback = stoploss_guard_lookback
        self.stoploss_guard_max_losses = stoploss_guard_max_losses
        self.low_profit_lookback = low_profit_lookback
        self.low_profit_min_trades = low_profit_min_trades
        self.low_profit_threshold = low_profit_threshold
        self.max_drawdown_pct = max_drawdown_pct
        self._cooldowns: dict[str, datetime] = {}

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    def on_trade_closed(self, trade: dict) -> None:
        reason = str(trade.get("reason", "")).upper()
        symbol = str(trade.get("symbol", ""))
        if reason == "SL_HIT" and symbol:
            self._cooldowns[symbol] = self._utcnow() + timedelta(minutes=self.cooldown_minutes)

    def evaluate(
        self,
        symbol: str,
        history: Iterable[dict],
        equity: float,
        peak_equity: float,
    ) -> ProtectionDecision:
        now = self._utcnow()
        until = self._cooldowns.get(symbol)
        if until and now < until:
            return ProtectionDecision(False, "COOLDOWN", until)

        trades = list(history)
        recent = [t for t in trades if t.get("symbol") == symbol][-self.stoploss_guard_lookback:]
        sl_losses = sum(
            1 for t in recent
            if str(t.get("reason", "")).upper() == "SL_HIT" and float(t.get("net_pnl", 0.0)) < 0
        )
        if sl_losses >= self.stoploss_guard_max_losses:
            return ProtectionDecision(False, "STOPLOSS_GUARD")

        recent_profit = [t for t in trades if t.get("symbol") == symbol][-self.low_profit_lookback:]
        if len(recent_profit) >= self.low_profit_min_trades:
            pnl_sum = sum(float(t.get("net_pnl", 0.0)) for t in recent_profit)
            if pnl_sum <= self.low_profit_threshold:
                return ProtectionDecision(False, "LOW_PROFIT_PAIR")

        if peak_equity > 0:
            dd = (peak_equity - equity) / peak_equity
            if dd >= self.max_drawdown_pct:
                return ProtectionDecision(False, "MAX_DRAWDOWN")

        return ProtectionDecision(True, "PROTECTION_OK")

    def get_status(self) -> dict:
        now = self._utcnow()
        active_cooldowns = {}
        for sym, until in list(self._cooldowns.items()):
            if now < until:
                rem_sec = (until - now).total_seconds()
                active_cooldowns[sym] = {
                    "cooldown_until": until.isoformat(),
                    "remaining_seconds": round(rem_sec, 1),
                }
        return {
            "active_cooldowns": active_cooldowns,
            "cooldown_count": len(active_cooldowns),
            "cooldown_minutes": self.cooldown_minutes,
            "stoploss_guard_lookback": self.stoploss_guard_lookback,
            "stoploss_guard_max_losses": self.stoploss_guard_max_losses,
            "low_profit_lookback": self.low_profit_lookback,
            "low_profit_threshold": self.low_profit_threshold,
            "max_drawdown_pct": self.max_drawdown_pct,
        }

    def clear_cooldowns(self) -> None:
        self._cooldowns.clear()

