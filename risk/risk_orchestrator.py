"""
risk/risk_orchestrator.py — Master Portfolio Risk Authority & Heat Calculator.

Features:
1. Portfolio-Level VaR (Historical simulation with 95% confidence, updated every 5m) and CVaR / Expected Shortfall.
2. Hourly Position Correlation Matrix.
3. Portfolio Heat Metric: Sum of position risks adjusted for correlation.
4. Dynamic Sizing & Protection Invariants:
   - Portfolio Heat > 70% of budget -> New position sizes reduced by 50%.
   - Portfolio Heat > 85% of budget -> Zero new entries permitted.
   - Peak Drawdown > 5.0% -> All position sizes reduced by 30%.
   - Peak Drawdown > 10.0% / 12.0% -> Flatten all positions and halt execution.
5. All decisions appended to risk_orchestration_log.jsonl with full reasoning.

Honesty / fail-closed fixes:
- ``calculate_var_and_cvar`` returned invented "conservative defaults"
  (2.0 / 3.0) with too little data and an invented ``var * 1.25`` CVaR; it now
  returns ``(None, None)`` when VaR cannot be measured and computes CVaR as the
  mean of the tail at or beyond the VaR observation.
- Every logged decision carried hard-coded ``var_95_pct=2.1`` and
  ``cvar_95_pct=2.8``; decisions now log the values actually computed (or null).
- A NaN portfolio heat fell through both ``>=`` checks and was APPROVED at full
  size; NaN/negative requested sizes produced NaN/negative approvals; the
  drawdown controller's ``allow_new_entries=False`` (ACTION level, post-CRITICAL
  recovery) was ignored. All of these now block the entry.
- ``build_measured_snapshot`` computes the dashboard's heat/VaR/drawdown from
  the recorded equity history and active trades instead of the constants the
  endpoint used to return.
"""

import datetime
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from atomic_io import append_jsonl, read_jsonl
from logger import get_logger
from numeric_safety import finite_float, finite_values, positive_float
from risk.circuit_breakers import CircuitBreakerEngine
from risk.drawdown_controller import DrawdownController
from risk.strategy_coordinator import StrategyCoordinator

logger = get_logger("risk_orchestrator")

MIN_RETURNS_FOR_VAR = 10
# Correlation factor used when no correlation matrix is supplied. This is an
# assumption, not a measurement, and is reported as such.
DEFAULT_CORRELATION_ASSUMPTION = 0.85


@dataclass
class RiskOrchestratorDecision:
    decision_id: str
    action: str  # "ALLOW_ENTRY", "REDUCE_50PCT", "BLOCK_ENTRY", "FLATTEN_ALL", "REBALANCE_WEIGHTS"
    portfolio_heat_pct: float | None
    var_95_pct: float | None
    cvar_95_pct: float | None
    drawdown_pct: float
    circuit_breakers_active: bool
    rationale: str
    timestamp: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())


class RiskOrchestrator:
    """
    Master risk authority supervising portfolio heat, VaR, correlations, and multi-strategy allocations.
    """

    def __init__(
        self,
        log_file: str = "risk_orchestration_log.jsonl",
        initial_equity: float = 5000.0,
        max_heat_budget_pct: float = 100.0
    ):
        budget = positive_float(max_heat_budget_pct)
        if budget is None:
            raise ValueError(f"max_heat_budget_pct must be finite and > 0, got {max_heat_budget_pct!r}")
        self.log_file = log_file
        self.max_heat_budget = budget
        self.circuit_breakers = CircuitBreakerEngine()
        self.drawdown_ctrl = DrawdownController(initial_equity=initial_equity)
        self.strategy_coordinator = StrategyCoordinator()
        self.decisions_log: list[RiskOrchestratorDecision] = []

    @staticmethod
    def calculate_var_and_cvar(
        portfolio_returns: list[float],
        confidence_level: float = 0.95
    ) -> tuple[float | None, float | None]:
        """Historical VaR and CVaR (expected shortfall) of percentage returns.

        Returns ``(None, None)`` when fewer than ``MIN_RETURNS_FOR_VAR`` finite
        returns are available. Non-finite returns are ignored.
        """
        confidence = finite_float(confidence_level)
        if confidence is None or not 0.5 <= confidence < 1.0:
            raise ValueError(f"confidence_level must be within [0.5, 1.0), got {confidence_level!r}")
        clean = finite_values(portfolio_returns or [])
        if len(clean) < MIN_RETURNS_FOR_VAR:
            return None, None

        sorted_returns = np.sort(np.asarray(clean, dtype=float))
        cutoff_idx = int((1.0 - confidence) * len(sorted_returns))
        var_return = float(sorted_returns[cutoff_idx])
        tail = sorted_returns[: cutoff_idx + 1]
        cvar_return = float(np.mean(tail))
        var = max(0.0, -var_return)
        cvar = max(var, -cvar_return)
        return round(var, 2), round(cvar, 2)

    @staticmethod
    def calculate_portfolio_heat(
        open_positions: list[dict[str, Any]],
        correlation_matrix: np.ndarray | None = None
    ) -> float:
        """
        Calculates correlation-adjusted portfolio heat.
        Heat = sum(pos_risk_pct) * sqrt(avg_correlation).

        Positions without a valid ``risk_pct`` are counted at the 1.5% default
        risk; a negative or non-finite risk is invalid and raises (it used to
        *reduce* the heat or poison it with NaN).
        """
        if not open_positions:
            return 0.0

        individual_risks = []
        for position in open_positions:
            raw = position.get("risk_pct", 1.5) if isinstance(position, dict) else None
            risk = finite_float(raw)
            if risk is None or risk < 0:
                raise ValueError(f"position risk_pct must be a finite number >= 0, got {raw!r}")
            individual_risks.append(risk)
        total_nominal_risk = sum(individual_risks)

        # Average correlation factor
        corr_factor = DEFAULT_CORRELATION_ASSUMPTION
        if correlation_matrix is not None and correlation_matrix.size > 0:
            measured = finite_float(np.mean(correlation_matrix))
            # An unreadable matrix is treated as perfectly correlated (worst case).
            corr_factor = 1.0 if measured is None else measured

        heat = total_nominal_risk * np.sqrt(max(0.20, min(1.0, corr_factor))) * 10.0
        return round(min(100.0, float(heat)), 1)

    def evaluate_new_entry_risk(
        self,
        symbol: str,
        strategy: str,
        requested_size: float,
        current_equity: float,
        portfolio_heat_pct: float,
        portfolio_returns: list[float] | None = None
    ) -> tuple[bool, float, str]:
        """
        Evaluates dynamic sizing and gate authority for new trade entries.
        Returns: (allow_entry, final_size, reason)
        """
        # 1. Update Drawdown (a non-finite equity reading is a CRITICAL breach)
        dd_status = self.drawdown_ctrl.update_equity(current_equity)
        var_95, cvar_95 = self.calculate_var_and_cvar(portfolio_returns or [])
        heat = finite_float(portfolio_heat_pct)

        def decide(action: str, rationale: str, cb_active: bool = False) -> None:
            self._log_decision(action, heat, dd_status.drawdown_pct, cb_active, rationale,
                               var_95=var_95, cvar_95=cvar_95)

        size = positive_float(requested_size)
        if size is None:
            decide("BLOCK_ENTRY", f"Invalid requested size {requested_size!r}")
            return False, 0.0, "BLOCKED_INVALID_REQUESTED_SIZE"

        # 2. Check Circuit Breakers
        cb_status = self.circuit_breakers.get_status_summary()
        if cb_status["any_circuit_breaker_active"]:
            decide("BLOCK_ENTRY", "Systemic circuit breaker active", cb_active=True)
            return False, 0.0, "BLOCKED_BY_CIRCUIT_BREAKER"

        # 3. Check Critical Drawdown (> 10% / 12%) and any level that halts entries
        if dd_status.level.startswith("CRITICAL"):
            decide("FLATTEN_ALL", "Critical drawdown reached")
            return False, 0.0, "FLATTEN_AND_HALT_CRITICAL_DRAWDOWN"
        if not dd_status.allow_new_entries:
            decide("BLOCK_ENTRY", f"Drawdown level {dd_status.level} halts new entries")
            return False, 0.0, f"BLOCKED_DRAWDOWN_{dd_status.level}"

        # 4. Check Portfolio Heat (unknown heat is treated as over budget)
        if heat is None or heat < 0:
            decide("BLOCK_ENTRY", f"Portfolio heat unknown ({portfolio_heat_pct!r})")
            return False, 0.0, "BLOCKED_PORTFOLIO_HEAT_UNKNOWN"
        heat_of_budget = heat / self.max_heat_budget * 100.0
        if heat_of_budget >= 85.0:
            decide("BLOCK_ENTRY", f"Portfolio heat {heat}% >= 85% of budget")
            return False, 0.0, "BLOCKED_PORTFOLIO_HEAT_EXCEEDED_85PCT"
        elif heat_of_budget >= 70.0:
            final_size = size * 0.50 * dd_status.position_size_multiplier
            decide("REDUCE_50PCT", f"Portfolio heat {heat}% >= 70% of budget")
            return True, round(final_size, 6), "APPROVED_REDUCED_50PCT_DUE_TO_HEAT"

        final_size = size * dd_status.position_size_multiplier
        decide("ALLOW_ENTRY", "Nominal risk parameters")
        return True, round(final_size, 6), "APPROVED_NOMINAL"

    def _log_decision(
        self,
        action: str,
        heat: float | None,
        dd: float,
        cb_active: bool,
        rationale: str,
        *,
        var_95: float | None = None,
        cvar_95: float | None = None,
    ) -> None:
        dec = RiskOrchestratorDecision(
            decision_id=f"RISK_DEC_{int(time.time()*1000)}",
            action=action,
            portfolio_heat_pct=heat,
            var_95_pct=var_95,
            cvar_95_pct=cvar_95,
            drawdown_pct=dd,
            circuit_breakers_active=cb_active,
            rationale=rationale
        )
        self.decisions_log.append(dec)
        try:
            append_jsonl(self.log_file, asdict(dec))
        except (OSError, ValueError) as exc:
            # The in-memory log still has the decision; the audit trail gap is
            # surfaced instead of silently swallowed.
            logger.error(f"[RISK_ORCH] Failed to append decision to {self.log_file}: {exc}")


def _equity_value(record: dict[str, Any]) -> float | None:
    for key in ("total_equity", "equity", "balance"):
        if key in record:
            return positive_float(record.get(key))
    return None


def build_measured_snapshot(
    equity_history_path: str,
    active_trades_path: str,
    *,
    max_points: int = 5000,
) -> dict[str, Any]:
    """Measure portfolio risk from recorded state; never invent numbers.

    * VaR/CVaR: historical, from consecutive equity snapshots (percent returns).
    * Drawdown: replay of the equity series through :class:`DrawdownController`.
    * Heat: from active trades' stop distance × quantity relative to the latest
      equity, with the correlation factor reported as an assumption.

    Every metric that cannot be measured is ``None`` with a reason.
    """
    history = read_jsonl(equity_history_path)
    equities = [e for e in (_equity_value(r) for r in history.records[-max_points:]) if e is not None]
    reasons: dict[str, str] = {}
    snapshot: dict[str, Any] = {
        "equity_points": len(equities),
        "equity_history_skipped_lines": history.skipped,
        "var_95_pct": None,
        "cvar_95_pct": None,
        "drawdown_metrics": None,
        "portfolio_heat_pct": None,
        "open_positions_measured": 0,
        "correlation_matrix": None,
    }
    reasons["correlation_matrix"] = "No per-symbol return feed is wired into this endpoint."

    if len(equities) >= 2:
        returns = [(b - a) / a * 100.0 for a, b in zip(equities, equities[1:], strict=False)]
        var_95, cvar_95 = RiskOrchestrator.calculate_var_and_cvar(returns)
        snapshot["var_95_pct"], snapshot["cvar_95_pct"] = var_95, cvar_95
        if var_95 is None:
            reasons["var_95_pct"] = f"Need >= {MIN_RETURNS_FOR_VAR + 1} equity snapshots; have {len(equities)}."
        controller = DrawdownController(initial_equity=equities[0])
        for equity in equities[1:]:
            controller.update_equity(equity)
        status = controller.status
        snapshot["drawdown_metrics"] = {
            "current_drawdown_pct": status.drawdown_pct,
            "peak_equity": status.peak_equity,
            "current_equity": status.current_equity,
            "level": status.level,
            "position_size_multiplier": status.position_size_multiplier,
        }
    else:
        reasons["var_95_pct"] = "Fewer than 2 recorded equity snapshots."
        reasons["drawdown_metrics"] = "Fewer than 2 recorded equity snapshots."

    latest_equity = equities[-1] if equities else None
    try:
        with open(active_trades_path, encoding="utf-8") as fh:
            trades = json.load(fh)
    except FileNotFoundError:
        trades = []
    except (OSError, ValueError) as exc:
        trades = None
        reasons["portfolio_heat_pct"] = f"Active trades file unreadable: {exc}"

    if trades is not None:
        if latest_equity is None:
            reasons["portfolio_heat_pct"] = "No recorded equity to normalise position risk."
        elif not isinstance(trades, list):
            reasons["portfolio_heat_pct"] = "Active trades file is not a list."
        else:
            positions = []
            for trade in trades:
                if not isinstance(trade, dict):
                    continue
                qty = positive_float(trade.get("quantity"))
                entry = positive_float(trade.get("entry_price"))
                stop = positive_float(trade.get("sl_price"))
                if qty is None or entry is None:
                    continue
                if stop is None:
                    # An unprotected position risks its full notional.
                    risk_value = qty * entry
                else:
                    risk_value = qty * abs(entry - stop)
                positions.append({"symbol": trade.get("symbol"), "risk_pct": risk_value / latest_equity * 100.0})
            snapshot["open_positions_measured"] = len(positions)
            snapshot["portfolio_heat_pct"] = RiskOrchestrator.calculate_portfolio_heat(positions)
            snapshot["correlation_assumption"] = DEFAULT_CORRELATION_ASSUMPTION

    snapshot["unmeasured_reasons"] = reasons
    return snapshot
