"""Portfolio-wide deterministic risk gate for Stratex demo execution.

The gate consumes a caller-supplied, normalized portfolio snapshot. It never
fetches market data, sends orders, invents FX rates, or trusts LLM confidence.
Missing, stale, degraded, or non-comparable exposure data means deny.
"""
from __future__ import annotations

import math
import os
import time
from typing import Any


class PortfolioRiskGate:
    """Fail-closed daily-loss, drawdown, position-count and exposure controls."""

    def __init__(
        self,
        *,
        max_daily_loss_pct: float | None = None,
        max_drawdown_pct: float | None = None,
        max_gross_exposure_pct: float | None = None,
        max_open_positions: int | None = None,
        max_snapshot_age_seconds: float | None = None,
        clock=time.time,
    ):
        self.max_daily_loss_pct = self._bounded(
            max_daily_loss_pct if max_daily_loss_pct is not None else os.getenv("MAX_DAILY_LOSS_PCT", "0.02"),
            "MAX_DAILY_LOSS_PCT", 0.0001, 0.10,
        )
        self.max_drawdown_pct = self._bounded(
            max_drawdown_pct if max_drawdown_pct is not None else os.getenv("MAX_TESTNET_DRAWDOWN_PCT", "0.05"),
            "MAX_TESTNET_DRAWDOWN_PCT", 0.0001, 0.20,
        )
        self.max_gross_exposure_pct = self._bounded(
            max_gross_exposure_pct if max_gross_exposure_pct is not None else os.getenv("MAX_TESTNET_EXPOSURE", "0.05"),
            "MAX_TESTNET_EXPOSURE", 0.0001, 1.0,
        )
        self.max_open_positions = int(
            max_open_positions if max_open_positions is not None else os.getenv("MAX_OPEN_POSITIONS", "5")
        )
        self.max_snapshot_age_seconds = float(
            max_snapshot_age_seconds if max_snapshot_age_seconds is not None
            else os.getenv("STRATEX_MAX_PORTFOLIO_SNAPSHOT_AGE_SECONDS", "30")
        )
        self.clock = clock
        if self.max_open_positions < 1:
            raise ValueError("MAX_OPEN_POSITIONS must be at least 1")
        if not math.isfinite(self.max_snapshot_age_seconds) or not 1 <= self.max_snapshot_age_seconds <= 3600:
            raise ValueError("STRATEX_MAX_PORTFOLIO_SNAPSHOT_AGE_SECONDS must be in [1, 3600]")

    @staticmethod
    def _bounded(value: Any, name: str, minimum: float, maximum: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be numeric") from exc
        if not math.isfinite(number) or not minimum <= number <= maximum:
            raise ValueError(f"{name} must be in [{minimum}, {maximum}]")
        return number

    @staticmethod
    def _number(snapshot: dict[str, Any], key: str) -> float:
        try:
            value = float(snapshot[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"missing or invalid {key}") from exc
        if not math.isfinite(value):
            raise ValueError(f"{key} must be finite")
        return value

    def evaluate(self, intent, context: dict[str, Any]) -> tuple[bool, str, str]:
        """Evaluate normalized snapshot data before venue-specific risk checks."""
        snapshot = context.get("portfolio") if isinstance(context, dict) else None
        if not isinstance(snapshot, dict):
            return False, "PORTFOLIO_SNAPSHOT_REQUIRED", "No normalized portfolio snapshot was supplied."
        if snapshot.get("status") != "healthy":
            return False, "PORTFOLIO_DEGRADED", "All enabled venue balance and position reads must be healthy."
        if snapshot.get("currency_conversion_complete") is not True:
            return False, "PORTFOLIO_CURRENCY_CONVERSION_INCOMPLETE", "Portfolio equity must be in one explicit reporting currency."
        try:
            captured_at = self._number(snapshot, "captured_at")
            age = self.clock() - captured_at
            if age < -2:
                return False, "PORTFOLIO_CLOCK_SKEW", "Portfolio snapshot timestamp is in the future."
            if age > self.max_snapshot_age_seconds:
                return False, "PORTFOLIO_SNAPSHOT_STALE", f"Snapshot age {age:.1f}s exceeds {self.max_snapshot_age_seconds:.1f}s."
            equity = self._number(snapshot, "reporting_equity")
            day_start = self._number(snapshot, "day_start_equity")
            high_water = self._number(snapshot, "high_water_equity")
            realized_pnl = self._number(snapshot, "realized_pnl_today")
            unrealized_pnl = self._number(snapshot, "unrealized_pnl")
            candidate_notional = self._number(context, "candidate_notional")
        except ValueError as exc:
            return False, "PORTFOLIO_RISK_CONTEXT_INVALID", str(exc)

        currency = str(snapshot.get("reporting_currency", "")).strip().upper()
        if not currency:
            return False, "PORTFOLIO_REPORTING_CURRENCY_MISSING", "Reporting currency is required."
        if min(equity, day_start, high_water) <= 0:
            return False, "PORTFOLIO_EQUITY_INVALID", "Equity baselines must be positive."
        if candidate_notional < 0:
            return False, "CANDIDATE_NOTIONAL_INVALID", "Candidate notional cannot be negative."

        # Both realized and unrealized PnL are included; callers must supply the
        # ledger-derived realized PnL and portfolio snapshot's unrealized PnL.
        daily_loss = max(0.0, -(realized_pnl + unrealized_pnl))
        daily_loss_pct = daily_loss / day_start
        drawdown_pct = max(0.0, (high_water - equity) / high_water)
        if daily_loss_pct >= self.max_daily_loss_pct:
            return False, "DAILY_LOSS_LIMIT_REACHED", (
                f"daily_loss_pct={daily_loss_pct:.6f}; limit={self.max_daily_loss_pct:.6f}"
            )
        if drawdown_pct >= self.max_drawdown_pct:
            return False, "MAX_DRAWDOWN_REACHED", (
                f"drawdown_pct={drawdown_pct:.6f}; limit={self.max_drawdown_pct:.6f}"
            )

        positions = snapshot.get("positions")
        if not isinstance(positions, list):
            return False, "PORTFOLIO_POSITIONS_INVALID", "Positions must be a list with verified reporting-currency notional."
        gross_exposure = 0.0
        for position in positions:
            if not isinstance(position, dict) or position.get("notional_available") is not True:
                return False, "POSITION_NOTIONAL_UNAVAILABLE", "Every open position needs verified reporting-currency notional."
            if str(position.get("notional_currency", currency)).upper() != currency:
                return False, "POSITION_CURRENCY_MISMATCH", "Position notional currency differs from portfolio reporting currency."
            try:
                notional = float(position["notional"])
            except (KeyError, TypeError, ValueError):
                return False, "POSITION_NOTIONAL_INVALID", "Position notional is missing or invalid."
            if not math.isfinite(notional) or notional < 0:
                return False, "POSITION_NOTIONAL_INVALID", "Position notional must be finite and non-negative."
            gross_exposure += notional

        if len(positions) >= self.max_open_positions:
            return False, "MAX_OPEN_POSITIONS_REACHED", (
                f"open_positions={len(positions)}; limit={self.max_open_positions}"
            )
        projected_exposure_pct = (gross_exposure + candidate_notional) / equity
        if projected_exposure_pct >= self.max_gross_exposure_pct:
            return False, "MAX_GROSS_EXPOSURE_REACHED", (
                f"projected_exposure_pct={projected_exposure_pct:.6f}; "
                f"limit={self.max_gross_exposure_pct:.6f}"
            )

        return True, "PORTFOLIO_RISK_OK", (
            f"equity={equity:.2f} {currency}; daily_loss_pct={daily_loss_pct:.6f}; "
            f"drawdown_pct={drawdown_pct:.6f}; projected_exposure_pct={projected_exposure_pct:.6f}; "
            f"open_positions={len(positions)}"
        )

    def health(self) -> dict[str, Any]:
        return {
            "agent_id": "portfolio_risk_gate",
            "status": "ready",
            "max_daily_loss_pct": self.max_daily_loss_pct,
            "max_drawdown_pct": self.max_drawdown_pct,
            "max_gross_exposure_pct": self.max_gross_exposure_pct,
            "max_open_positions": self.max_open_positions,
            "max_snapshot_age_seconds": self.max_snapshot_age_seconds,
            "execution_authority": False,
            "can_deny_execution": True,
        }
