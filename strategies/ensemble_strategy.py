"""
strategies/ensemble_strategy.py — Multi-Strategy Ensemble Consensus Engine.

Combines signals from multiple individual strategies into a single
high-confidence trade signal using:
1. Weighted voting (by win-rate prior and RR ratio)
2. Minimum agreement threshold (default: 2 of 3 strategies)
3. Regime-aware confidence adjustment
4. Position sizing based on consensus strength
"""
from __future__ import annotations

import math
from collections import namedtuple
from typing import Any

import pandas as pd

from logger import get_logger

logger = get_logger("strategies.ensemble")


class SignalResult(namedtuple("SignalResult", ["side", "sl", "tp", "strategy_type", "win_rate_prior", "rr_ratio"])):
    @property
    def confidence(self):
        return self.win_rate_prior


_STRATEGY_TYPE = "ENSEMBLE"
_OOS_WIN_RATE_PRIOR = 0.72  # Ensemble typically outperforms individual strategies
_RR_RATIO = 2.0


def get_signal(df, strategy_registry=None, regime="TRENDING"):
    """
    Ensemble consensus signal from multiple strategies.

    Args:
        df: Market data DataFrame with OHLCV + features
        strategy_registry: UnifiedStrategyRegistry instance (optional, auto-discovers if None)
        regime: Current market regime ("TRENDING", "RANGING", "BREAKOUT")

    Returns:
        SignalResult with consensus side, SL/TP, and confidence
    """
    if df is None or len(df) < 50:
        return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    # Auto-discover strategies if registry not provided
    if strategy_registry is None:
        try:
            from strategies.strategy_registry import strategy_registry as reg
            strategy_registry = reg
        except Exception:
            return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    # Get best strategies for the current regime
    candidates = strategy_registry.best_strategies_for_regime(regime)
    if len(candidates) < 2:
        return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    # Take top 3-5 strategies for ensemble. Never include ensemble members that
    # dispatch back into the registry, or the call recurses until Python's stack
    # limit is hit (ensemble -> registry -> ensemble -> ...).
    recursive = {"ensemble_strategy", "ml_ensemble_meta"}
    ensemble_members = [s for s in candidates if s.name not in recursive][:5]
    votes = []
    weighted_sl = []
    weighted_tp = []
    total_weight = 0.0

    last = df.iloc[-1]
    close = float(last["close"])
    atr = float(last.get("atr", last.get("atr_14", close * 0.01)))

    for spec in ensemble_members:
        ok, reason = strategy_registry.executable(spec.name, "5m", "EURUSD")
        if not ok:
            continue

        result = strategy_registry.execute_strategy(spec.name, df, "5m", "EURUSD")
        if result.get("status") != "SIGNAL":
            continue

        side = result["side"]
        sl = float(result["stop_loss"])
        tp = float(result["take_profit"])
        win_prior = float(result.get("win_rate_prior") or 0.5)
        rr = float(result.get("rr_ratio") or 1.5)

        # Weight by win-rate prior and RR ratio
        weight = win_prior * (1.0 + rr * 0.1)
        votes.append({
            "strategy": spec.name,
            "side": side,
            "weight": weight,
            "sl": sl,
            "tp": tp,
            "win_prior": win_prior,
            "rr": rr,
        })
        weighted_sl.append(sl * weight)
        weighted_tp.append(tp * weight)
        total_weight += weight

    if not votes or total_weight <= 0:
        return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    # Count bullish vs bearish votes
    bullish = sum(1 for v in votes if v["side"] in ("BUY", "LONG"))
    bearish = sum(1 for v in votes if v["side"] in ("SELL", "SHORT"))
    bullish_weight = sum(v["weight"] for v in votes if v["side"] in ("BUY", "LONG"))
    bearish_weight = sum(v["weight"] for v in votes if v["side"] in ("SELL", "SHORT"))

    # Minimum agreement: at least 2 strategies agree, or strong weight consensus
    if bullish >= 2 and bullish_weight > bearish_weight:
        consensus_side = "BUY"
        consensus_weight = bullish_weight
    elif bearish >= 2 and bearish_weight > bullish_weight:
        consensus_side = "SELL"
        consensus_weight = bearish_weight
    else:
        return SignalResult(None, None, None, _STRATEGY_TYPE, 0.0, 0.0)

    # Weighted average SL/TP
    avg_sl = sum(weighted_sl) / total_weight if total_weight > 0 else close - atr * 1.5
    avg_tp = sum(weighted_tp) / total_weight if total_weight > 0 else close + atr * 2.0

    # Confidence based on consensus strength and agreement ratio
    agreement_ratio = max(bullish, bearish) / len(votes)
    # Base confidence from agreement, minimum 0.3 for weak signals
    confidence = max(0.3, agreement_ratio * 0.4 + (consensus_weight / total_weight) * 0.3)

    # Regime adjustment - only boost for strong regime alignment
    if regime.upper() in ("TRENDING", "BULL_TREND") and consensus_side == "BUY":
        confidence = min(0.80, confidence + 0.10)
    elif regime.upper() in ("BEAR_TREND",) and consensus_side == "SELL":
        confidence = min(0.80, confidence + 0.10)
    elif regime.upper() in ("RANGING", "CHOP"):
        confidence = max(0.3, confidence - 0.10)

    logger.info(
        f"Ensemble signal: {consensus_side} on {len(votes)} strategies, "
        f"confidence={confidence:.2f}, agreement={agreement_ratio:.2f}"
    )

    # Calculate dynamic R:R from actual SL/TP
    dynamic_rr = abs(avg_tp - close) / max(abs(close - avg_sl), 1e-9) if avg_sl and avg_tp else 1.5
    dynamic_rr = min(max(dynamic_rr, 1.0), 3.0)

    return SignalResult(
        consensus_side,
        avg_sl,
        avg_tp,
        _STRATEGY_TYPE,
        confidence,
        dynamic_rr,
    )
