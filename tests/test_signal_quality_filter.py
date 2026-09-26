"""
tests/test_signal_quality_filter.py
Unit tests verifying the 80%+ win rate Signal Quality filter rules:
1. ADX regime gate (ADX < 25 rejected as MARKET_CHOP)
2. Full EMA trend alignment (EMA20 > EMA50 > EMA200 for BUY, EMA20 < EMA50 < EMA200 for SELL)
3. Volume multiplier filter (0.8x 20-bar average)
4. Risk-reward geometry check
"""

import pandas as pd
import pytest
from testnet_engine.signal_quality import evaluate_signal_quality


def create_mock_df(adx=30.0, ema20=105.0, ema50=100.0, ema200=95.0, close=106.0, open_p=104.0, volume=1000.0, atr=1.0, rsi=55.0):
    rows = []
    for i in range(25):
        rows.append({
            "open": 100.0,
            "high": 102.0,
            "low": 98.0,
            "close": 101.0,
            "volume": 1000.0,
            "ema_20": 101.0,
            "ema_50": 100.0,
            "ema_200": 95.0,
            "atr": 1.0,
            "rsi": 50.0,
            "adx": 30.0,
        })
    # Last candle with parameters
    rows[-1] = {
        "open": open_p,
        "high": max(open_p, close) + 0.5,
        "low": min(open_p, close) - 0.5,
        "close": close,
        "volume": volume,
        "ema_20": ema20,
        "ema_50": ema50,
        "ema_200": ema200,
        "atr": atr,
        "rsi": rsi,
        "adx": adx,
    }
    return pd.DataFrame(rows)


def test_signal_quality_adx_chop_rejection():
    """Verify ADX < 25 is rejected as MARKET_CHOP."""
    df = create_mock_df(adx=20.0) # Choppy market
    ok, reason, detail = evaluate_signal_quality(df, "BUY", 106.0, 105.0, 107.0, "supertrend")
    assert ok is False
    assert reason == "QUALITY_MARKET_CHOP_ADX_LOW"


def test_signal_quality_trend_alignment():
    """Verify trend alignment gate enforces EMA20 > EMA50 > EMA200."""
    # Counter-trend: EMA20 < EMA50 while trying to BUY
    df = create_mock_df(adx=30.0, ema20=98.0, ema50=100.0, ema200=95.0)
    ok, reason, detail = evaluate_signal_quality(df, "BUY", 106.0, 105.0, 107.0, "supertrend")
    assert ok is False
    assert reason == "QUALITY_COUNTER_TREND"


def test_signal_quality_volume_filter():
    """Verify low volume (< 0.8x avg) is rejected."""
    # Volume 500 when avg is 1000 -> ratio 0.5 < 0.8
    df = create_mock_df(adx=30.0, volume=500.0)
    ok, reason, detail = evaluate_signal_quality(df, "BUY", 106.0, 105.0, 107.0, "supertrend")
    assert ok is False
    assert reason == "QUALITY_LOW_VOLUME"


def test_signal_quality_valid_signal():
    """Verify perfectly aligned high-probability signal passes."""
    # Volume 1200, ADX 30, EMA20=105 > EMA50=100 > EMA200=95, Close=106 > Open=104, SL=105, TP=107
    df = create_mock_df(adx=30.0, ema20=105.0, ema50=100.0, ema200=95.0, close=106.0, open_p=104.0, volume=1200.0)
    ok, reason, detail = evaluate_signal_quality(df, "BUY", 106.0, 105.0, 107.0, "supertrend")
    assert ok is True
    assert reason == "QUALITY_OK"


@pytest.mark.parametrize(
    ("side", "close", "open_p", "ema21", "ema50", "ema200", "sl", "tp"),
    [
        ("BUY", 106.0, 104.0, 105.0, 100.0, 95.0, 105.0, 107.0),
        ("SELL", 94.0, 96.0, 95.0, 100.0, 105.0, 95.0, 92.0),
    ],
)
def test_signal_quality_uses_main_feature_column_names(
    side, close, open_p, ema21, ema50, ema200, sl, tp
):
    """Main features.py emits ema_21/rsi_14, not ema_20/rsi."""
    rows = []
    for _ in range(25):
        rows.append({
            "open": open_p,
            "high": max(open_p, close) + 1.0,
            "low": min(open_p, close) - 1.0,
            "close": close,
            "volume": 1000.0,
            "ema_21": ema21,
            "ema_50": ema50,
            "ema_200": ema200,
            "atr_14": 1.0,
            "rsi_14": 55.0,
            # ADX is absent in the shared feature pipeline; that filter is
            # intentionally fail-open when no real ADX measurement exists.
        })
    ok, reason, detail = evaluate_signal_quality(
        pd.DataFrame(rows), side, close, sl, tp, "feature_column_regression"
    )
    assert ok is True
    assert reason == "QUALITY_OK"
    assert detail["ema_20"] == ema21
    assert detail["rsi"] == 55.0


def test_signal_quality_rejects_real_countertrend_using_feature_columns():
    df = create_mock_df(adx=30.0, ema20=98.0, ema50=100.0, ema200=105.0)
    # Simulate the actual features.py schema, which doesn't emit ema_20.
    df = df.drop(columns=["ema_20"])
    df["ema_21"] = 98.0
    ok, reason, _ = evaluate_signal_quality(
        df, "BUY", 106.0, 105.0, 107.0, "feature_column_regression"
    )
    assert ok is False
    assert reason == "QUALITY_COUNTER_TREND"
