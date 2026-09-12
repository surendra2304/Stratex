"""
advisory_gate.py — Safety validation gate for AI-Universe parameter recommendations.

CRITICAL INVARIANTS:
1. AI-Universe is ADVISORY ONLY. Recommendations modify strategy PARAMETERS, never risk LIMITS.
2. Safety gates (RiskGate, ProfitabilityGate, ExecutionPolicy, kill switch) are UNTOUCHABLE.
3. Hardcoded validation bounds cannot be overridden from environment variables.
4. Bounded recommendations require 8 explicit fields and are held in PENDING_AUTHORIZATION
   until separately and explicitly authorized.
"""

import datetime
import os
import uuid
from dataclasses import dataclass, field
from typing import Any

from audit.audit_manager import get_audit_manager
from logger import get_logger

logger = get_logger("advisory_gate")

MANDATORY_BOUNDED_FIELDS = {
    "parameter",
    "current_value",
    "proposed_value",
    "maximum_delta",
    "evidence",
    "confidence",
    "expiry",
    "authorization_required"
}


@dataclass
class BoundedRecommendation:
    parameter: str
    current_value: Any
    proposed_value: Any
    maximum_delta: float
    evidence: str
    confidence: float
    expiry: str
    authorization_required: bool = True
    strategy: str = "global"
    recommendation_id: str = field(default_factory=lambda: f"rec_{uuid.uuid4().hex[:8]}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "parameter": self.parameter,
            "current_value": self.current_value,
            "proposed_value": self.proposed_value,
            "maximum_delta": self.maximum_delta,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "expiry": self.expiry,
            "authorization_required": self.authorization_required,
            "strategy": self.strategy,
            "recommendation_id": self.recommendation_id
        }


@dataclass
class AdvisoryResult:
    verdict: str  # "APPLY" | "REJECT" | "SHADOW_LOG_ONLY" | "PENDING_AUTHORIZATION"
    decision_id: str
    rationale: str
    applied_changes: list[dict[str, Any]] = field(default_factory=list)
    rejected_changes: list[dict[str, Any]] = field(default_factory=list)
    bounds_checked: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())

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
    Validates and bounds any strategy parameter change suggested by AI-Universe or Inference.
    """

    # --- HARD-CODED BOUNDS (IMMUTABLE SAFETY CONSTANTS) ---
    MAX_PARAM_CHANGE_PCT: float = 20.0  # Max ±20% deviation from current value
    POSITION_SIZE_MIN_MULT: float = 0.5  # Min 0.5x of current position sizing
    POSITION_SIZE_MAX_MULT: float = 1.5  # Max 1.5x of current position sizing
    MAX_CHANGES_PER_DECISION: int = 2   # Maximum 2 parameter changes per consultation
    COOLDOWN_HOURS: float = 4.0          # Minimum 4 hours between applied parameter modifications
    MAX_TELEMETRY_STALENESS_SECONDS: float = 60.0  # Telemetry older than 60s is rejected
    MIN_REQUIRED_TRADES: int = 10        # Insufficient data if fewer than 10 trades
    MAX_DRAWDOWN_CUTOFF_PCT: float = 15.0 # 15% hard cutoff for AI advisory

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
        shadow_mode: bool = True,
        telemetry_freshness_sec: float | None = None,
        trade_count: int | None = None,
        current_drawdown_pct: float | None = None,
        inconsistent_state: bool = False,
        require_separate_auth: bool = False
    ) -> AdvisoryResult:
        """
        Validates an AIUniverseDecision against strict quantitative safety bounds,
        telemetry freshness, sufficient data, drawdown thresholds, and consistency checks.
        """
        decision_id = str(decision.get("decision_id", "UNKNOWN_DECISION"))
        parameter_changes = decision.get("parameter_changes", [])

        bounds_checked = {
            "max_param_change_pct": self.MAX_PARAM_CHANGE_PCT,
            "position_size_min_mult": self.POSITION_SIZE_MIN_MULT,
            "position_size_max_mult": self.POSITION_SIZE_MAX_MULT,
            "max_changes_per_decision": self.MAX_CHANGES_PER_DECISION,
            "cooldown_hours": self.COOLDOWN_HOURS,
            "forbidden_params": list(self.FORBIDDEN_PARAMS),
            "max_telemetry_staleness_sec": self.MAX_TELEMETRY_STALENESS_SECONDS,
            "min_required_trades": self.MIN_REQUIRED_TRADES,
            "max_drawdown_cutoff_pct": self.MAX_DRAWDOWN_CUTOFF_PCT
        }

        # Check: Inconsistent State (e.g. panic active or position reconciliation mismatch)
        if inconsistent_state:
            rationale = "System state is inconsistent or emergency halt is active. All advisory recommendations rejected."
            get_audit_manager().record_event(
                event_type="RECOMMENDATION_REJECTED",
                actor="advisory_gate",
                details={"decision_id": decision_id, "reason": "INCONSISTENT_STATE"},
                rationale=rationale,
                status="REJECTED"
            )
            return AdvisoryResult(
                verdict="REJECT",
                decision_id=decision_id,
                rationale=rationale,
                rejected_changes=[{"change": c, "reason": "System state inconsistent"} for c in parameter_changes],
                bounds_checked=bounds_checked
            )

        # Check: Telemetry Freshness
        if telemetry_freshness_sec is not None and telemetry_freshness_sec > self.MAX_TELEMETRY_STALENESS_SECONDS:
            rationale = f"Telemetry is stale ({telemetry_freshness_sec:.1f}s > {self.MAX_TELEMETRY_STALENESS_SECONDS}s). Advisory rejected."
            get_audit_manager().record_event(
                event_type="RECOMMENDATION_REJECTED",
                actor="advisory_gate",
                details={"decision_id": decision_id, "telemetry_freshness_sec": telemetry_freshness_sec},
                rationale=rationale,
                status="REJECTED"
            )
            return AdvisoryResult(
                verdict="REJECT",
                decision_id=decision_id,
                rationale=rationale,
                rejected_changes=[{"change": c, "reason": f"Stale telemetry ({telemetry_freshness_sec:.1f}s)"} for c in parameter_changes],
                bounds_checked=bounds_checked
            )

        # Check: Insufficient Data
        if trade_count is not None and trade_count < self.MIN_REQUIRED_TRADES:
            rationale = f"Insufficient data: only {trade_count} trades recorded (minimum required: {self.MIN_REQUIRED_TRADES}). Advisory rejected."
            get_audit_manager().record_event(
                event_type="RECOMMENDATION_REJECTED",
                actor="advisory_gate",
                details={"decision_id": decision_id, "trade_count": trade_count},
                rationale=rationale,
                status="REJECTED"
            )
            return AdvisoryResult(
                verdict="REJECT",
                decision_id=decision_id,
                rationale=rationale,
                rejected_changes=[{"change": c, "reason": "Insufficient trade data"} for c in parameter_changes],
                bounds_checked=bounds_checked
            )

        # Check: Excessive Drawdown
        if current_drawdown_pct is not None and current_drawdown_pct > self.MAX_DRAWDOWN_CUTOFF_PCT:
            rationale = f"Excessive drawdown: current drawdown {current_drawdown_pct:.1f}% exceeds cutoff {self.MAX_DRAWDOWN_CUTOFF_PCT:.1f}%. Advisory rejected."
            get_audit_manager().record_event(
                event_type="RECOMMENDATION_REJECTED",
                actor="advisory_gate",
                details={"decision_id": decision_id, "drawdown_pct": current_drawdown_pct},
                rationale=rationale,
                status="REJECTED"
            )
            return AdvisoryResult(
                verdict="REJECT",
                decision_id=decision_id,
                rationale=rationale,
                rejected_changes=[{"change": c, "reason": "Excessive drawdown breach"} for c in parameter_changes],
                bounds_checked=bounds_checked
            )

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
        now = datetime.datetime.now(datetime.timezone.utc)
        if last_applied_time is not None and not shadow_mode:
            last_dt = last_applied_time if last_applied_time.tzinfo is not None else last_applied_time.replace(tzinfo=datetime.timezone.utc)
            elapsed_hours = (now - last_dt).total_seconds() / 3600.0
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
            target_value = item.get("proposed_value", item.get("new_value", item.get("value", None)))
            current_value = item.get("current_value", current_params.get(param_name, current_params.get(f"{strategy_name}.{param_name}")))

            # Check: Forbidden parameter list
            if param_name in self.FORBIDDEN_PARAMS or any(f in param_name for f in self.FORBIDDEN_PARAMS):
                reason = f"Parameter '{param_name}' is in FORBIDDEN_PARAMS safety list (risk limits & credentials are immutable)."
                rejected_changes.append({"change": item, "reason": reason})
                continue

            if target_value is None:
                rejected_changes.append({"change": item, "reason": "Missing target proposed_value or new_value"})
                continue

            # Check bounded recommendation attributes if present
            max_delta = item.get("maximum_delta")
            if max_delta is not None:
                try:
                    delta_limit = float(max_delta)
                    if isinstance(target_value, (int, float)) and current_value is not None:
                        actual_delta = abs(float(target_value) - float(current_value))
                        if actual_delta > (delta_limit + 1e-9):
                            reason = f"Delta {actual_delta:.4f} exceeds maximum_delta limit of {delta_limit:.4f}."
                            rejected_changes.append({"change": item, "reason": reason})
                            continue
                except (ValueError, TypeError):
                    rejected_changes.append({"change": item, "reason": "Invalid maximum_delta specification"})
                    continue

            # Check expiry if provided
            expiry_str = item.get("expiry")
            if expiry_str:
                try:
                    exp_dt = datetime.datetime.fromisoformat(str(expiry_str).replace("Z", "+00:00"))
                    if exp_dt.tzinfo is None:
                        exp_dt = exp_dt.replace(tzinfo=datetime.timezone.utc)
                    if exp_dt < now:
                        reason = f"Recommendation expired at {expiry_str}."
                        rejected_changes.append({"change": item, "reason": reason})
                        continue
                except Exception as e:
                    rejected_changes.append({"change": item, "reason": f"Malformed expiry timestamp: {e}"})
                    continue

            # Check confidence if provided
            conf = item.get("confidence")
            if conf is not None:
                try:
                    conf_val = float(conf)
                    if conf_val < 0.0 or conf_val > 1.0:
                        rejected_changes.append({"change": item, "reason": f"Invalid confidence {conf_val}: must be in [0.0, 1.0]"})
                        continue
                    if conf_val < 0.40:
                        rejected_changes.append({"change": item, "reason": f"Confidence {conf_val:.2f} below minimum threshold 0.40"})
                        continue
                except (ValueError, TypeError):
                    rejected_changes.append({"change": item, "reason": "Invalid confidence score"})
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
                "proposed_value": target_value,
                "maximum_delta": max_delta,
                "evidence": item.get("evidence", ""),
                "confidence": item.get("confidence", decision.get("confidence", 0.8)),
                "expiry": item.get("expiry", (now + datetime.timedelta(hours=4)).isoformat()),
                "authorization_required": item.get("authorization_required", True),
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
        elif require_separate_auth:
            verdict = "PENDING_AUTHORIZATION"
            rationale = f"{len(applied_changes)} bounded recommendation(s) validated; held pending explicit authorization."
        else:
            verdict = "APPLY"
            rationale = f"{len(applied_changes)} change(s) passed all safety bounds and scheduled for overlay application."

        # Audit event
        audit_type = "RECOMMENDATION_REJECTED" if verdict == "REJECT" else "RECOMMENDATION_GENERATED"
        get_audit_manager().record_event(
            event_type=audit_type,
            actor="advisory_gate",
            details={
                "decision_id": decision_id,
                "verdict": verdict,
                "applied_changes_count": len(applied_changes),
                "rejected_changes_count": len(rejected_changes)
            },
            rationale=rationale,
            status=verdict
        )

        return AdvisoryResult(
            verdict=verdict,
            decision_id=decision_id,
            rationale=rationale,
            applied_changes=applied_changes,
            rejected_changes=rejected_changes,
            bounds_checked=bounds_checked
        )

    def validate_bounded_recommendation(
        self,
        recommendation: dict[str, Any] | BoundedRecommendation,
        current_params: dict[str, Any] | None = None
    ) -> tuple[bool, str, dict[str, Any]]:
        """
        Validates a single bounded recommendation against the mandatory 8 fields and bounds.
        Returns (is_valid, rejection_reason, normalized_dict).
        """
        raw = recommendation.to_dict() if isinstance(recommendation, BoundedRecommendation) else dict(recommendation)

        # 1. Check all 8 mandatory fields
        missing = MANDATORY_BOUNDED_FIELDS - set(raw.keys())
        if missing:
            return False, f"Missing mandatory bounded recommendation fields: {sorted(list(missing))}", raw

        # 2. Check non-empty evidence
        if not str(raw.get("evidence", "")).strip():
            return False, "Evidence field must be a non-empty explanation or telemetry reference.", raw

        # 3. Check expiry
        now = datetime.datetime.now(datetime.timezone.utc)
        try:
            exp_dt = datetime.datetime.fromisoformat(str(raw["expiry"]).replace("Z", "+00:00"))
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=datetime.timezone.utc)
            if exp_dt < now:
                return False, f"Recommendation has already expired at {raw['expiry']}.", raw
        except Exception as e:
            return False, f"Malformed expiry timestamp: {e}", raw

        # 4. Check confidence
        try:
            conf = float(raw["confidence"])
            if conf < 0.0 or conf > 1.0:
                return False, f"Confidence {conf} out of bounds [0.0, 1.0].", raw
        except (ValueError, TypeError):
            return False, "Invalid confidence value.", raw

        # 5. Check delta bounds
        curr = raw["current_value"]
        prop = raw["proposed_value"]
        max_delta = raw["maximum_delta"]
        try:
            c_val = float(curr)
            p_val = float(prop)
            m_delta = float(max_delta)
            actual_delta = abs(p_val - c_val)

            if actual_delta > (m_delta + 1e-9):
                return False, f"Proposed delta {actual_delta:.4f} exceeds maximum_delta {m_delta:.4f}.", raw

            if c_val != 0:
                pct = (actual_delta / abs(c_val)) * 100.0
                if round(pct, 4) > self.MAX_PARAM_CHANGE_PCT:
                    return False, f"Proposed change {pct:.2f}% exceeds max allowed ±{self.MAX_PARAM_CHANGE_PCT}%.", raw
        except (ValueError, TypeError):
            pass

        # 6. Check authorization_required is True
        if raw.get("authorization_required") is not True:
            return False, "authorization_required must be strictly True.", raw

        # 7. Check forbidden params
        param = str(raw["parameter"]).strip().lower()
        if param in self.FORBIDDEN_PARAMS or any(f in param for f in self.FORBIDDEN_PARAMS):
            return False, f"Parameter '{param}' is in forbidden safety list.", raw

        return True, "Valid bounded recommendation.", raw
