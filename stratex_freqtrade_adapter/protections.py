"""stratex_freqtrade_adapter/protections.py

Trade protections for Stratex.
Inspired by Freqtrade's protection concepts; intentionally independent.
Protections are conservative pre-entry filters and never bypass Stratex RiskGate.
100% backwards-compatible with existing Stratex test suites.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, Optional, Tuple


@dataclass
class ProtectionDecision:
    allowed: bool
    reason: str
    cooldown_until: datetime | None = None


class ProtectionManager:
    """Evaluates pair-level and bot-level protections before new trade execution."""

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
        self._cooldowns: Dict[str, datetime] = {}
        self._lock_reasons: Dict[str, str] = {}
        self._global_lock_until: Optional[datetime] = None
        self._global_lock_reason: Optional[str] = None

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    def lock_pair(self, symbol: str, until: datetime, reason: str = "MANUAL_LOCK") -> None:
        """Explicitly locks a specific trading pair until a timestamp."""
        self._cooldowns[symbol] = until
        self._lock_reasons[symbol] = reason

    def lock_all(self, until: datetime, reason: str = "GLOBAL_LOCK") -> None:
        """Locks all trading pairs globally until a timestamp."""
        self._global_lock_until = until
        self._global_lock_reason = reason

    def is_pair_locked(self, symbol: str) -> Tuple[bool, Optional[str]]:
        """Checks if a pair is currently locked (either by pair lock or global lock)."""
        now = self._utcnow()
        if self._global_lock_until and now < self._global_lock_until:
            return True, f"GLOBAL_LOCK_{self._global_lock_reason}"

        until = self._cooldowns.get(symbol)
        if until and now < until:
            reason = self._lock_reasons.get(symbol, "COOLDOWN")
            return True, reason
        return False, None

    def on_trade_closed(self, trade: dict) -> None:
        """Callback invoked whenever a trade is closed."""
        reason = str(trade.get("reason", "")).upper()
        symbol = str(trade.get("symbol", trade.get("pair", "")))
        if reason == "SL_HIT" and symbol:
            until = self._utcnow() + timedelta(minutes=self.cooldown_minutes)
            self._cooldowns[symbol] = until
            self._lock_reasons[symbol] = "COOLDOWN"

    def evaluate(
        self,
        symbol: str,
        history: Iterable[dict],
        equity: float,
        peak_equity: float,
    ) -> ProtectionDecision:
        now = self._utcnow()

        # Global lock check
        if self._global_lock_until and now < self._global_lock_until:
            return ProtectionDecision(False, self._global_lock_reason or "GLOBAL_LOCK", self._global_lock_until)

        # Pair cooldown / lock check
        until = self._cooldowns.get(symbol)
        if until and now < until:
            reason = self._lock_reasons.get(symbol, "COOLDOWN")
            return ProtectionDecision(False, reason, until)

        trades = list(history)

        # Stoploss guard
        recent = [t for t in trades if t.get("symbol") == symbol][-self.stoploss_guard_lookback:]
        sl_losses = sum(
            1 for t in recent
            if str(t.get("reason", "")).upper() == "SL_HIT" and float(t.get("net_pnl", 0.0)) < 0
        )
        if sl_losses >= self.stoploss_guard_max_losses:
            return ProtectionDecision(False, "STOPLOSS_GUARD")

        # Low profit pair guard
        recent_profit = [t for t in trades if t.get("symbol") == symbol][-self.low_profit_lookback:]
        if len(recent_profit) >= self.low_profit_min_trades:
            pnl_sum = sum(float(t.get("net_pnl", 0.0)) for t in recent_profit)
            if pnl_sum <= self.low_profit_threshold:
                return ProtectionDecision(False, "LOW_PROFIT_PAIR")

        # Max drawdown guard
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
                    "reason": self._lock_reasons.get(sym, "COOLDOWN"),
                }

        global_lock = None
        if self._global_lock_until and now < self._global_lock_until:
            global_lock = {
                "locked_until": self._global_lock_until.isoformat(),
                "remaining_seconds": round((self._global_lock_until - now).total_seconds(), 1),
                "reason": self._global_lock_reason,
            }

        return {
            "active_cooldowns": active_cooldowns,
            "cooldown_count": len(active_cooldowns),
            "global_lock": global_lock,
            "cooldown_minutes": self.cooldown_minutes,
            "stoploss_guard_lookback": self.stoploss_guard_lookback,
            "stoploss_guard_max_losses": self.stoploss_guard_max_losses,
            "low_profit_lookback": self.low_profit_lookback,
            "low_profit_threshold": self.low_profit_threshold,
            "max_drawdown_pct": self.max_drawdown_pct,
        }

    def clear_cooldowns(self) -> None:
        self._cooldowns.clear()
        self._lock_reasons.clear()
        self._global_lock_until = None
        self._global_lock_reason = None
