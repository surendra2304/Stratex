"""Fail-closed quantitative evidence policy for strategy lifecycle promotion.

This module is deliberately pure: it evaluates metrics already written by the
optimization/validation pipeline and performs no data fetching or trading.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

MIN_OUT_OF_SAMPLE_TRADES = 30
MIN_OUT_OF_SAMPLE_PROFIT_FACTOR = 1.0
MAX_FAILED_WALK_FORWARD_WINDOWS = 0
PROMOTION_ELIGIBLE = "PROMOTION_ELIGIBLE"
RESEARCH_ONLY = "RESEARCH ONLY"


def evaluate_oos_metrics(artifact: Any) -> dict[str, Any]:
    """Return an objective eligibility decision from a saved validation artifact.

    The artifact must contain a finite OOS trade count and profit factor plus
    at least one explicitly passing walk-forward window. Missing, malformed,
    non-finite, or failed evidence always makes the result ineligible.
    """
    reasons: list[str] = []
    if not isinstance(artifact, Mapping):
        return {"eligible": False, "reasons": ["artifact must be a JSON object"], "failed_windows": None}

    oos = artifact.get("optimized_oos")
    if not isinstance(oos, Mapping):
        reasons.append("optimized_oos metrics are missing")
        oos = {}

    raw_trades = oos.get("trade_count", oos.get("total_trades"))
    trades: int | None = None
    if isinstance(raw_trades, bool):
        reasons.append("optimized_oos trade count is invalid")
    else:
        try:
            trade_number = float(raw_trades)
            if not math.isfinite(trade_number) or not trade_number.is_integer() or trade_number < 0:
                raise ValueError
            trades = int(trade_number)
        except (TypeError, ValueError, OverflowError):
            reasons.append("optimized_oos trade count is missing or invalid")
    if trades is not None and trades < MIN_OUT_OF_SAMPLE_TRADES:
        reasons.append(
            f"out-of-sample sample is too small ({trades} < {MIN_OUT_OF_SAMPLE_TRADES} trades)"
        )

    raw_pf = oos.get("profit_factor")
    pf: float | None = None
    if isinstance(raw_pf, bool):
        reasons.append("optimized_oos profit factor is invalid")
    else:
        try:
            pf = float(raw_pf)
            if not math.isfinite(pf) or pf < 0:
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            pf = None
            reasons.append("optimized_oos profit factor is missing or invalid")
    if pf is not None and pf < MIN_OUT_OF_SAMPLE_PROFIT_FACTOR:
        reasons.append(
            "out-of-sample profit factor is below "
            f"{MIN_OUT_OF_SAMPLE_PROFIT_FACTOR:.2f} ({pf:.4g})"
        )

    windows = artifact.get("walk_forward_windows")
    failed_windows: int | None = None
    if not isinstance(windows, list) or not windows:
        reasons.append("walk-forward window evidence is missing")
    else:
        failed_windows = 0
        for index, window in enumerate(windows, start=1):
            if not isinstance(window, Mapping) or str(window.get("status", "")).upper() != "PASS":
                failed_windows += 1
                reasons.append(f"walk-forward window {index} is not marked PASS")
        if failed_windows > MAX_FAILED_WALK_FORWARD_WINDOWS:
            reasons.append(
                "failed walk-forward windows exceed the allowed maximum "
                f"({failed_windows} > {MAX_FAILED_WALK_FORWARD_WINDOWS})"
            )

    return {
        "eligible": not reasons,
        "reasons": reasons,
        "trade_count": trades,
        "profit_factor": pf,
        "failed_windows": failed_windows,
    }
