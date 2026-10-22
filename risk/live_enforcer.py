"""
risk/live_enforcer.py — Live capital risk enforcer (graduated capital levels).

``LiveRiskEnforcer`` polices kill switch, halts, daily loss, drawdown,
trading window, position count/size, correlation and volatility before any
live entry. Live trading itself remains permanently disabled in this
repository; this gate is still exercised by the authorization/status
endpoints and must be correct.

Fail-closed fixes (previously all fail-open):
- NaN daily PnL / equity / volatility / notional evaluated as "within
  tolerance" because ``NaN <= -limit`` and ``NaN > threshold`` are False; a NaN
  drawdown became 0% via ``max(0.0, nan)``.
- A zero/negative peak equity returned "Peak equity zero" → allowed.
- ``validate_new_entry`` accepted ``today_realized_loss`` but never checked it,
  so the documented daily-loss invariant was not enforced at entry time.
- A non-positive or NaN order notional passed the size cap.
- Trading-window hours were not validated (``(22, 6)`` silently blocked
  every hour) and used naive ``utcnow()``.

This file used to be a byte-for-byte copy of ``risk/circuit_breakers.py``;
``CircuitBreakerEngine`` now lives only there.
"""

import datetime
import time
from dataclasses import dataclass
from typing import Any

from logger import get_logger
from numeric_safety import finite_float, non_negative_float, positive_float

logger = get_logger("live_enforcer")


@dataclass
class LiveEnforcerStatus:
    is_halted: bool = False
    halt_reason: str = ""
    halt_until: float | None = None
    requires_reauthorization: bool = False
    daily_realized_loss: float = 0.0
    current_drawdown_pct: float = 0.0
    active_positions_count: int = 0
    volatility_24h_pct: float = 0.0
    kill_switch_active: bool = False


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _validate_window(window: Any) -> tuple[int, int] | None:
    if window is None:
        return None
    try:
        start_hr, end_hr = window
    except (TypeError, ValueError) as exc:
        raise ValueError(f"trading_window_hours must be a (start, end) pair, got {window!r}") from exc
    if not all(isinstance(h, int) and not isinstance(h, bool) for h in (start_hr, end_hr)):
        raise ValueError(f"trading_window_hours must contain integers, got {window!r}")
    if not 0 <= start_hr < end_hr <= 24:
        raise ValueError(
            f"trading_window_hours must satisfy 0 <= start < end <= 24 (overnight windows are not supported), got {window!r}"
        )
    return start_hr, end_hr


class LiveRiskEnforcer:
    """
    Autonomous guardian strictly policing live capital allocations.
    """

    def __init__(
        self,
        level: int = 1,
        initial_capital: float = 1000.0,
        trading_window_hours: tuple[int, int] | None = None,
        max_volatility_threshold_pct: float = 8.5
    ):
        from deployment.capital_levels import CapitalLevelSpec, get_level_spec
        capital = non_negative_float(initial_capital)
        if capital is None:
            raise ValueError(f"initial_capital must be a finite number >= 0, got {initial_capital!r}")
        vol_threshold = positive_float(max_volatility_threshold_pct)
        if vol_threshold is None:
            raise ValueError(f"max_volatility_threshold_pct must be finite and > 0, got {max_volatility_threshold_pct!r}")
        self.level = level
        self.spec: CapitalLevelSpec = get_level_spec(level)
        self.initial_capital = capital
        self.trading_window_hours = _validate_window(trading_window_hours)
        self.max_volatility_threshold_pct = vol_threshold
        self.status = LiveEnforcerStatus()
        self.correlated_pairs_map = {
            ("BTC/USDT", "ETH/USDT"): 0.88,
            ("BTC/USDT", "SOL/USDT"): 0.82,
            ("ETH/USDT", "SOL/USDT"): 0.85
        }

    def _halt(self, reason: str, *, until: float | None = None, reauthorize: bool = False) -> None:
        self.status.is_halted = True
        self.status.halt_reason = reason
        if until is not None:
            self.status.halt_until = until
        if reauthorize:
            self.status.requires_reauthorization = True
        logger.critical(f"[LIVE_ENFORCER] 🚨 {reason}")

    def trigger_kill_switch(self, source: str = "OPERATOR_API", rationale: str = "Emergency halt requested") -> dict[str, Any]:
        """Immediately activates kill switch and halts all live operations."""
        self.status.kill_switch_active = True
        self._halt(f"KILL_SWITCH triggered by {source}: {rationale}", reauthorize=True)
        return {
            "action": "FLATTEN_ALL",
            "halted": True,
            "reason": self.status.halt_reason,
            "timestamp": _utc_now().isoformat()
        }

    def evaluate_daily_loss(self, today_realized_pnl: float, current_equity: float) -> tuple[bool, str]:
        """
        Checks if today's net loss exceeds the level max daily loss limit.
        If breached (or the PnL cannot be read), halts trading for 24 hours and flags FLATTEN_ALL.
        """
        pnl = finite_float(today_realized_pnl)
        if pnl is None:
            self._halt(f"Daily PnL is unreadable ({today_realized_pnl!r}); halted for 24h pending reconciliation.",
                       until=time.time() + 86400)
            return False, self.status.halt_reason
        self.status.daily_realized_loss = max(0.0, -pnl)
        max_loss_dollars = self.initial_capital * self.spec.max_daily_loss_pct
        if pnl <= -max_loss_dollars:
            self._halt(
                f"Daily loss limit breached (${abs(pnl):.2f} >= ${max_loss_dollars:.2f} limit: "
                f"{self.spec.max_daily_loss_pct*100}%). Halted for 24h.",
                until=time.time() + 86400,
            )
            return False, self.status.halt_reason
        return True, "Daily loss within tolerance"

    def evaluate_drawdown(self, peak_equity: float, current_equity: float) -> tuple[bool, str]:
        """
        Checks if total drawdown exceeds level threshold.
        If breached — or if the drawdown cannot be computed — flattens all
        positions and requires physical re-authorization.
        """
        peak = positive_float(peak_equity)
        current = finite_float(current_equity)
        if peak is None or current is None:
            self._halt(
                f"Drawdown cannot be verified (peak={peak_equity!r}, current={current_equity!r}). "
                "Requires physical re-authorization.",
                reauthorize=True,
            )
            return False, self.status.halt_reason
        dd_pct = max(0.0, (peak - current) / peak) * 100.0
        self.status.current_drawdown_pct = round(dd_pct, 2)
        max_dd_pct = self.spec.max_drawdown_limit_pct * 100.0
        if dd_pct >= max_dd_pct:
            self._halt(
                f"Drawdown limit breached ({dd_pct:.2f}% >= {max_dd_pct}% limit for Level {self.level}). "
                "Requires physical re-authorization.",
                reauthorize=True,
            )
            return False, self.status.halt_reason
        return True, "Drawdown within tolerance"

    def check_correlation_limit(self, proposed_symbol: str, current_open_symbols: list[str]) -> tuple[bool, str]:
        """
        Ensures no more than 2 highly-correlated (> 0.85) assets are held simultaneously.
        """
        correlated_count = 0
        for sym in current_open_symbols:
            pair = (proposed_symbol, sym) if (proposed_symbol, sym) in self.correlated_pairs_map else (sym, proposed_symbol)
            corr = self.correlated_pairs_map.get(pair, 0.0)
            if corr >= 0.85:
                correlated_count += 1
        if correlated_count >= 2:
            return False, f"Correlation limit reached: {proposed_symbol} is highly correlated with {correlated_count} active positions (Max 2 allowed)."
        return True, "Correlation nominal"

    def check_volatility_circuit_breaker(self, rolling_vol_pct: float) -> tuple[bool, str]:
        """Halts new entry orders if rolling market volatility spikes above threshold (or is unreadable)."""
        vol = finite_float(rolling_vol_pct)
        if vol is None or vol < 0:
            return False, f"Volatility circuit breaker active: 24h volatility is unreadable ({rolling_vol_pct!r})."
        self.status.volatility_24h_pct = vol
        if vol > self.max_volatility_threshold_pct:
            return False, f"Volatility circuit breaker active: Realized 24h vol {vol:.2f}% > {self.max_volatility_threshold_pct}% threshold."
        return True, "Volatility nominal"

    def check_time_window_restrictions(self) -> tuple[bool, str]:
        """Checks if current UTC hour is within permitted trading window."""
        if not self.trading_window_hours:
            return True, "24/7 trading window"
        start_hr, end_hr = self.trading_window_hours
        curr_hr = _utc_now().hour
        if not (start_hr <= curr_hr < end_hr):
            return False, f"Outside permitted trading window ({start_hr}:00 - {end_hr}:00 UTC. Current: {curr_hr}:00 UTC)."
        return True, "Inside permitted trading window"

    def validate_new_entry(
        self,
        symbol: str,
        notional: float,
        current_open_positions: list[dict[str, Any]],
        current_equity: float,
        today_realized_loss: float = 0.0,
        rolling_vol_pct: float = 3.5
    ) -> tuple[bool, str]:
        """
        Comprehensive pre-trade gate validating all live risk invariants.

        ``today_realized_loss`` is today's realized loss magnitude (>= 0).
        """
        # Check active halt or kill switch
        if self.status.kill_switch_active:
            return False, f"BLOCKED: Kill switch is active ({self.status.halt_reason})"

        if self.status.is_halted:
            if self.status.halt_until and time.time() >= self.status.halt_until and not self.status.requires_reauthorization:
                self.status.is_halted = False
                self.status.halt_until = None
                self.status.halt_reason = ""
            else:
                return False, f"BLOCKED: Live engine halted ({self.status.halt_reason})"

        order_notional = positive_float(notional)
        if order_notional is None:
            return False, f"BLOCKED: Order notional {notional!r} must be a finite number > 0."
        equity = positive_float(current_equity)
        if equity is None:
            return False, f"BLOCKED: Current equity {current_equity!r} must be a finite number > 0."
        if not isinstance(current_open_positions, list):
            return False, "BLOCKED: Open positions state is not a list."

        # 0. Daily loss invariant (documented but previously never checked here)
        loss = non_negative_float(today_realized_loss)
        if loss is None:
            return False, f"BLOCKED: today_realized_loss {today_realized_loss!r} must be a finite number >= 0."
        daily_ok, daily_msg = self.evaluate_daily_loss(-loss, equity)
        if not daily_ok:
            return False, f"BLOCKED: {daily_msg}"

        # 1. Trading window check
        win_ok, win_msg = self.check_time_window_restrictions()
        if not win_ok:
            return False, f"BLOCKED: {win_msg}"

        # 2. Max Strategy / Position count check for this level
        max_pos_allowed = max(1, self.spec.max_strategies * 2)
        self.status.active_positions_count = len(current_open_positions)
        if len(current_open_positions) >= max_pos_allowed:
            return False, f"BLOCKED: Max position count ({len(current_open_positions)} >= {max_pos_allowed}) for Level {self.level} reached."

        # 3. Position Size Limit (% of capital)
        max_notional_allowed = equity * self.spec.max_position_size_pct
        if order_notional > (max_notional_allowed * 1.05):  # 5% buffer
            return False, f"BLOCKED: Order notional ${order_notional:.2f} exceeds Level {self.level} max position cap ${max_notional_allowed:.2f} ({self.spec.max_position_size_pct*100}%)."

        # 4. Correlation check
        open_syms = [p.get("symbol", "") for p in current_open_positions if isinstance(p, dict)]
        corr_ok, corr_msg = self.check_correlation_limit(symbol, open_syms)
        if not corr_ok:
            return False, f"BLOCKED: {corr_msg}"

        # 5. Volatility check
        vol_ok, vol_msg = self.check_volatility_circuit_breaker(rolling_vol_pct)
        if not vol_ok:
            return False, f"BLOCKED: {vol_msg}"

        return True, "All live risk gates passed"
