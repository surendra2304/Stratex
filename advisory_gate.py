"""
advisory_gate.py — Safety validation gate for AI-Universe parameter recommendations.

CRITICAL INVARIANTS:
1. AI-Universe is ADVISORY ONLY. Recommendations modify strategy PARAMETERS, never risk LIMITS.
2. Safety gates (RiskGate, ProfitabilityGate, ExecutionPolicy, kill switch) are UNTOUCHABLE.
3. Hardcoded validation bounds cannot be overridden from environment variables.
"""

import datetime
from dataclasses import dataclass, field
from typing import Any

from logger import get_logger

logger = get_logger("advisory_gate")


@dataclass
class AdvisoryResult:
    verdict: str  # "APPLY" | "REJECT" | "SHADOW_LOG_ONLY"
    decision_id: str
    rationale: str
    applied_changes: list[dict[str, Any]] = field(default_factory=list)
    rejected_changes: list[dict[str, Any]] = field(default_factory=list)
    bounds_checked: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.datetime.utcnow().isoformat() + "Z")

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "decision_id": self.decision_id,
            "rationale": self.rationale,
            "applied_changes": self.applied_changes,
            "rejected_changes": self.rejected_changes,
            "bounds_checked": self.bounds_checked,
            "timestamp": self.timestamp
        }


class AdvisoryGate:
    """
    Validates and bounds any strategy parameter change suggested by AI-Universe.
    """

    # --- HARD-CODED BOUNDS (IMMUTABLE SAFETY CONSTANTS) ---
    MAX_PARAM_CHANGE_PCT: float = 20.0  # Max ±20% deviation from current value
    POSITION_SIZE_MIN_MULT: float = 0.5  # Min 0.5x of current position sizing
    POSITION_SIZE_MAX_MULT: float = 1.5  # Max 1.5x of current position sizing
    MAX_CHANGES_PER_DECISION: int = 2   # Maximum 2 parameter changes per consultation
    COOLDOWN_HOURS: float = 4.0          # Minimum 4 hours between applied parameter modifications

    FORBIDDEN_PARAMS: set[str] = {
        "max_daily_loss",
        "max_daily_loss_pct",
        "max_drawdown",
        "max_testnet_drawdown_pct",
        "live_trading_enabled",
        "api_key",
        "secret_key",
        "risk_limits",
        "risk_limit",
        "trading_mode",
        "paper_safe_mode",
        "testnet_enabled"
    }

    # Identifying parameter families
    POSITION_SIZE_KEYWORDS: set[str] = {
        "trade_qty",
        "position_size",
        "max_position_size",
        "qty",
        "sizing"
    }

    LEVERAGE_KEYWORDS: set[str] = {
        "leverage",
        "futures_leverage",
        "max_leverage"
    }

    def validate(
        self,
        decision: dict[str, Any],
        current_params: dict[str, Any],
        last_applied_time: datetime.datetime | None = None,
        shadow_mode: bool = True
    ) -> AdvisoryResult:
        """
        Validates an AIUniverseDecision against strict quantitative safety bounds.
        """
        decision_id = str(decision.get("decision_id", "UNKNOWN_DECISION"))
        parameter_changes = decision.get("parameter_changes", [])

        bounds_checked = {
            "max_param_change_pct": self.MAX_PARAM_CHANGE_PCT,
            "position_size_min_mult": self.POSITION_SIZE_MIN_MULT,
            "position_size_max_mult": self.POSITION_SIZE_MAX_MULT,
            "max_changes_per_decision": self.MAX_CHANGES_PER_DECISION,
            "cooldown_hours": self.COOLDOWN_HOURS,
            "forbidden_params": list(self.FORBIDDEN_PARAMS)
        }

        # Check 1: AI status must be APPROVED or RECOMMENDED
        ai_status = str(decision.get("status", "")).upper()
        if ai_status not in ["APPROVED", "RECOMMENDED", "SUCCESS", "VALIDATED"]:
            rationale = f"AI decision status '{ai_status}' is not approved for execution."
            return AdvisoryResult(
                verdict="REJECT",
                decision_id=decision_id,
                rationale=rationale,
                rejected_changes=[{"change": c, "reason": "AI status not approved"} for c in parameter_changes],
                bounds_checked=bounds_checked
            )

        # Check 2: Max changes per decision
        if len(parameter_changes) > self.MAX_CHANGES_PER_DECISION:
            rationale = f"Decision requested {len(parameter_changes)} changes, exceeding maximum limit of {self.MAX_CHANGES_PER_DECISION}."
            return AdvisoryResult(
                verdict="REJECT",
                decision_id=decision_id,
                rationale=rationale,
                rejected_changes=[{"change": c, "reason": "Exceeded max changes per decision limit"} for c in parameter_changes],
                bounds_checked=bounds_checked
            )

        # Check 3: Cooldown enforcement (only applies if we actually have changes to apply and not shadow mode)
        now = datetime.datetime.utcnow()
        if last_applied_time is not None and not shadow_mode:
            elapsed_hours = (now - last_applied_time).total_seconds() / 3600.0
            if elapsed_hours < self.COOLDOWN_HOURS:
                rationale = f"Cooldown in effect: {elapsed_hours:.2f}h elapsed since last applied change, required {self.COOLDOWN_HOURS}h."
                return AdvisoryResult(
                    verdict="REJECT",
                    decision_id=decision_id,
                    rationale=rationale,
                    rejected_changes=[{"change": c, "reason": f"Cooldown active ({elapsed_hours:.1f}h < {self.COOLDOWN_HOURS}h)"} for c in parameter_changes],
                    bounds_checked=bounds_checked
                )

        applied_changes = []
        rejected_changes = []

        # Validate each individual parameter change
        for item in parameter_changes:
            if not isinstance(item, dict):
                rejected_changes.append({"change": item, "reason": "Malformed parameter change item: must be dict"})
                continue

            param_name = str(item.get("parameter", item.get("name", ""))).strip().lower()
            strategy_name = str(item.get("strategy", "global")).strip().lower()
            target_value = item.get("new_value", item.get("value", None))
            current_value = item.get("current_value", current_params.get(param_name, current_params.get(f"{strategy_name}.{param_name}")))

            # Check: Forbidden parameter list
            if param_name in self.FORBIDDEN_PARAMS or any(f in param_name for f in self.FORBIDDEN_PARAMS):
                reason = f"Parameter '{param_name}' is in FORBIDDEN_PARAMS safety list (risk limits & credentials are immutable)."
                rejected_changes.append({"change": item, "reason": reason})
                continue

            if target_value is None:
                rejected_changes.append({"change": item, "reason": "Missing target new_value"})
                continue

            # Case A: Numeric Parameter Validation
            if isinstance(target_value, (int, float)) and current_value is not None and isinstance(current_value, (int, float)):
                c_val = float(current_value)
                t_val = float(target_value)

                # 1. Leverage Rule: leverage may only decrease or stay the same
                if any(k in param_name for k in self.LEVERAGE_KEYWORDS):
                    if t_val > c_val:
                        reason = f"Leverage increase rejected ({c_val}x -> {t_val}x). Leverage may only decrease or stay same."
                        rejected_changes.append({"change": item, "reason": reason})
                        continue

                # 2. Position Size Sizing Rule: 0.5x to 1.5x of current
                elif any(k in param_name for k in self.POSITION_SIZE_KEYWORDS):
                    if c_val > 0:
                        ratio = t_val / c_val
                        if ratio < self.POSITION_SIZE_MIN_MULT or ratio > self.POSITION_SIZE_MAX_MULT:
                            reason = f"Position size multiplier {ratio:.2f}x outside allowed bounds [{self.POSITION_SIZE_MIN_MULT}x, {self.POSITION_SIZE_MAX_MULT}x]."
                            rejected_changes.append({"change": item, "reason": reason})
                            continue

                # 3. Standard Strategy Parameter: ±20% deviation limit
                else:
                    if c_val != 0:
                        pct_change = abs((t_val - c_val) / c_val) * 100.0
                        if round(pct_change, 4) > self.MAX_PARAM_CHANGE_PCT:
                            reason = f"Parameter change {pct_change:.2f}% exceeds maximum allowed ±{self.MAX_PARAM_CHANGE_PCT}%."
                            rejected_changes.append({"change": item, "reason": reason})
                            continue

            # Change is valid
            applied_changes.append({
                "strategy": strategy_name,
                "parameter": param_name,
                "current_value": current_value,
                "new_value": target_value,
                "reason": item.get("reason", "Approved by AdvisoryGate")
            })

        # Determine overall verdict
        if not parameter_changes:
            verdict = "REJECT"
            rationale = "No parameter changes proposed in decision."
        elif not applied_changes:
            verdict = "REJECT"
            rationale = f"All {len(rejected_changes)} proposed changes failed AdvisoryGate safety bounds."
        elif shadow_mode:
            verdict = "SHADOW_LOG_ONLY"
            rationale = f"{len(applied_changes)} change(s) validated successfully, held in shadow mode without live execution."
        else:
            verdict = "APPLY"
            rationale = f"{len(applied_changes)} change(s) passed all safety bounds and scheduled for overlay application."

        return AdvisoryResult(
            verdict=verdict,
            decision_id=decision_id,
            rationale=rationale,
            applied_changes=applied_changes,
            rejected_changes=rejected_changes,
            bounds_checked=bounds_checked
        )
