"""Empirical advisory-impact calculations with explicit evidence boundaries."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AdvisoryImpactAttribution:
    """One impact estimate calculated from caller-supplied before/after results.

    Win-rate values are intentionally unknown: two aggregate PnL values cannot
    reconstruct either period's win rate. The established alpha field name is
    retained for compatibility, but its unit follows the supplied pre/post PnL
    values and does not imply a causal attribution claim.
    """

    decision_id: str
    parameter_name: str
    strategy: str
    pre_change_win_rate: float | None
    post_change_win_rate: float | None
    estimated_alpha_pct: float
    status: str = "UNVERIFIED"
    evidence_status: str = "CALLER_SUPPLIED_PRE_POST_PNL"


class AdvisoryImpactAnalyzer:
    """Calculate only impact values supported by explicit measured inputs."""

    def evaluate_decision_impact(
        self,
        decision_id: str,
        parameter_name: str,
        strategy: str,
        pre_trades_pnl: float,
        post_trades_pnl: float,
    ) -> AdvisoryImpactAttribution:
        for name, value in (("pre_trades_pnl", pre_trades_pnl), ("post_trades_pnl", post_trades_pnl)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(f"{name} must be a finite number")
        if not all(isinstance(value, str) and value.strip() for value in (decision_id, parameter_name, strategy)):
            raise ValueError("decision_id, parameter_name, and strategy must be non-empty strings")

        difference = float(post_trades_pnl) - float(pre_trades_pnl)
        status = (
            "POSITIVE_CONTRIBUTION"
            if difference > 0
            else "NEGATIVE_CONTRIBUTION"
            if difference < 0
            else "NO_CHANGE"
        )
        return AdvisoryImpactAttribution(
            decision_id=decision_id,
            parameter_name=parameter_name,
            strategy=strategy,
            pre_change_win_rate=None,
            post_change_win_rate=None,
            estimated_alpha_pct=round(difference, 8),
            status=status,
            evidence_status="CALLER_SUPPLIED_PRE_POST_PNL",
        )

    def get_monthly_advisory_attribution_summary(self) -> dict[str, Any]:
        """Return unavailable metrics until decision outcomes have durable evidence.

        There is no stored dataset that links an applied advisory decision to a
        sufficiently sized, matched pre/post trade cohort. Counts, alpha, and
        efficacy are therefore unknown—not zero and not seeded example values.
        """
        return {
            "evidence_status": "UNAVAILABLE",
            "reason": "NO_MATCHED_ADVISORY_OUTCOME_LEDGER",
            "total_decisions_applied": None,
            "net_alpha_contribution_pct": None,
            "category_breakdown": {},
            "overall_efficacy_score": None,
        }
