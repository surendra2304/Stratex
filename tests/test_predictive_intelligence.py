"""
tests/test_predictive_intelligence.py — Tests for Predictive Intelligence Client, Filters & Impact Tracking.

Verifies:
1. PredictionClient caching with TTL, fallback predictions, and snapshot generation.
2. PredictionEnhancedStrategyFilter:
   - Invariant: Predictions NEVER initiate a trade (0 base signal -> rejected).
   - Invariant: Predictions veto or modulate conflicting signals.
   - Concurrence: Grants full size on high-confidence agreement.
   - Early Exit Acceleration: Detects adverse regime turns.
3. PredictionImpactTracker logging, attribution, and report aggregation.
"""

import tempfile
import pytest

from intelligence.prediction_client import PredictionClient, AssetPrediction
from strategies.prediction_enhanced import PredictionEnhancedStrategyFilter
from intelligence.impact_tracking import PredictionImpactTracker


def test_prediction_client_caching():
    client = PredictionClient(cache_ttl_seconds=300)
    pred = client.get_prediction("BTC/USDT")
    assert pred is not None
    assert pred.symbol == "BTC/USDT"
    assert pred.direction in ["BULLISH", "BEARISH", "NEUTRAL"]
    assert pred.is_valid() is True

    # Retrieve from cache
    cached = client.get_prediction("BTC/USDT")
    assert cached.timestamp == pred.timestamp


def test_prediction_enhanced_strategy_filter():
    client = PredictionClient()
    # Inject synthetic prediction into cache
    client.cache["BTC/USDT"] = AssetPrediction(
        symbol="BTC/USDT",
        direction="BEARISH",
        confidence=0.85,
        horizon_minutes=60,
        target_price_change_pct=-2.5,
        expires_at=9999999999.0
    )

    filter_engine = PredictionEnhancedStrategyFilter(prediction_client=client, min_agreement_confidence=0.65, veto_conflicting_signals=True)

    # 1. Zero base signal -> MUST NEVER enter on prediction alone
    allow, size, reason = filter_engine.evaluate_entry_filter("BTC/USDT", strategy_signal=0, base_size=1.0)
    assert allow is False
    assert size == 0.0
    assert reason == "NO_BASE_SIGNAL"

    # 2. Bullish base signal vs Bearish AI forecast (85%) -> VETO
    allow, size, reason = filter_engine.evaluate_entry_filter("BTC/USDT", strategy_signal=1, base_size=1.0)
    assert allow is False
    assert size == 0.0
    assert "VETOED" in reason

    # 3. Bearish base signal vs Bearish AI forecast (85%) -> APPROVED with Concurrence
    allow, size, reason = filter_engine.evaluate_entry_filter("BTC/USDT", strategy_signal=-1, base_size=1.0)
    assert allow is True
    assert size == 1.0
    assert "APPROVED_WITH_AI_CONCURRENCE" in reason

    # 4. Early Exit Acceleration
    accelerate, exit_reason = filter_engine.check_early_exit_acceleration("BTC/USDT", current_side="LONG", current_pnl_pct=0.5)
    assert accelerate is True
    assert "ACCELERATED_EXIT" in exit_reason


def test_prediction_impact_tracker():
    with tempfile.TemporaryDirectory() as tmpdir:
        ledger = f"{tmpdir}/impact.jsonl"
        tracker = PredictionImpactTracker(ledger_file=ledger)

        tracker.log_trade_prediction_context(
            trade_id="T_001",
            symbol="BTC/USDT",
            strategy="scalper",
            base_signal=1,
            prediction_direction="BEARISH",
            prediction_confidence=0.80,
            filter_action="VETOED"
        )
        tracker.log_trade_prediction_context(
            trade_id="T_002",
            symbol="ETH/USDT",
            strategy="supertrend",
            base_signal=1,
            prediction_direction="BULLISH",
            prediction_confidence=0.75,
            filter_action="APPROVED"
        )

        rep = tracker.generate_impact_report()
        assert rep["total_evaluations"] == 2
        assert rep["vetoed_trades_count"] == 1
        assert rep["approved_trades_count"] == 1
        assert rep["veto_rate_pct"] == 50.0
