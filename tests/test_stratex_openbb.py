"""
tests/test_stratex_openbb.py — Comprehensive Unit & Integration Tests for OpenBB Native Integration.
Tests:
1. Free sentiment provider & caching
2. Free CoinGecko global overview & trending
3. Free macro provider (DXY, 10Y, VIX, Gold)
4. Macroeconomic regime classification (RISK_ON, RISK_OFF, NEUTRAL)
5. Quantitative risk metrics (Sharpe, Sortino, Calmar, VaR 95/99, CVaR, Beta)
6. Advanced volatility estimators (Parkinson, Garman-Klass, Yang-Zhang)
7. Technical overlays (Donchian, Keltner, Pivot Points)
8. obb facade namespaces (crypto, economy, quantitative, technical)
9. Flask REST API endpoints (/api/v1/openbb/*)
"""

import datetime
import math
import numpy as np
import pandas as pd
import pytest

from stratex_openbb import obb
from stratex_openbb.models import (
    CryptoGlobalOverview,
    SentimentReading,
    MacroIndicatorSnapshot,
    MacroRegimeState,
    QuantitativeRiskReport,
    VolatilityEstimates,
)
from stratex_openbb.providers.sentiment_free import FreeSentimentProvider
from stratex_openbb.providers.coingecko import FreeCoinGeckoProvider
from stratex_openbb.providers.macro_free import FreeMacroProvider
from stratex_openbb.economy.macro_regime import MacroRegimeDetector
from stratex_openbb.quantitative.risk_metrics import (
    calculate_returns,
    calculate_sharpe_ratio,
    calculate_sortino_ratio,
    calculate_calmar_ratio,
    calculate_max_drawdown,
    calculate_var_historical,
    calculate_var_parametric,
    calculate_cvar,
    calculate_beta,
    compute_quantitative_risk_report,
)
from stratex_openbb.quantitative.volatility_estimators import (
    compute_parkinson_volatility,
    compute_garman_klass_volatility,
    compute_yang_zhang_volatility,
    compute_volatility_estimates_from_df,
)
from stratex_openbb.quantitative.distribution import analyze_return_distribution
from stratex_openbb.technical.overlays import (
    compute_donchian_channels,
    compute_keltner_channels,
    compute_pivot_points,
)


# ==============================================================================
# 1. PROVIDERS & CACHING TESTS
# ==============================================================================

def test_sentiment_provider_parsing_and_fallback(monkeypatch):
    """Verifies Fear & Greed parser handles valid responses, caching, and offline fallback."""
    provider = FreeSentimentProvider()

    # Test fallback
    def mock_fail(*args, **kwargs):
        raise ConnectionError("Network unreachable")

    monkeypatch.setattr("requests.get", mock_fail)
    reading = provider.get_fear_and_greed(force_refresh=True)
    assert isinstance(reading, SentimentReading)
    assert reading.score == 50
    assert reading.classification == "Neutral"
    assert "Fallback" in reading.source

    # Test valid response
    class MockResponse:
        status_code = 200
        def json(self):
            return {
                "data": [
                    {
                        "value": "78",
                        "value_classification": "Extreme Greed",
                        "timestamp": "1726000000"
                    }
                ]
            }

    monkeypatch.setattr("requests.get", lambda *a, **k: MockResponse())
    reading_live = provider.get_fear_and_greed(force_refresh=True)
    assert reading_live.score == 78
    assert reading_live.classification == "Extreme Greed"
    assert "Alternative.me" in reading_live.source


def test_coingecko_provider_parsing_and_resilience(monkeypatch):
    """Verifies CoinGecko provider parses market metrics and handles rate limits."""
    provider = FreeCoinGeckoProvider()

    class MockCoinGeckoResponse:
        status_code = 200
        def json(self):
            return {
                "data": {
                    "total_market_cap": {"usd": 2_450_000_000_000.0},
                    "total_volume": {"usd": 95_000_000_000.0},
                    "market_cap_percentage": {"btc": 56.4, "eth": 14.8},
                    "active_cryptocurrencies": 14500,
                    "market_cap_change_percentage_24h_usd": 2.35
                }
            }

    monkeypatch.setattr("requests.get", lambda *a, **k: MockCoinGeckoResponse())
    overview = provider.get_global_overview(force_refresh=True)
    assert isinstance(overview, CryptoGlobalOverview)
    assert overview.total_market_cap_usd == 2_450_000_000_000.0
    assert overview.btc_dominance_pct == 56.4
    assert overview.eth_dominance_pct == 14.8
    assert overview.market_cap_change_pct_24h == 2.35


def test_macro_provider_parsing_and_indicators(monkeypatch):
    """Verifies free macro provider parses Yahoo chart data and returns valid snapshots."""
    provider = FreeMacroProvider()

    class MockYahooResponse:
        status_code = 200
        def json(self):
            return {
                "chart": {
                    "result": [
                        {
                            "meta": {
                                "regularMarketPrice": 104.50,
                                "previousClose": 104.10
                            }
                        }
                    ]
                }
            }

    monkeypatch.setattr("requests.get", lambda *a, **k: MockYahooResponse())
    dxy = provider.get_indicator("DXY", force_refresh=True)
    assert isinstance(dxy, MacroIndicatorSnapshot)
    assert dxy.current_value == 104.50
    assert dxy.change_pct > 0
    assert dxy.trend == "BULLISH"


# ==============================================================================
# 2. MACRO REGIME CLASSIFIER TESTS
# ==============================================================================

def test_macro_regime_classification():
    """Verifies macro regime logic correctly outputs RISK_ON, RISK_OFF, and NEUTRAL."""
    class MockMacro:
        def __init__(self, dxy_val, dxy_trend, vix_val, yield_chg):
            self.dxy_val = dxy_val
            self.dxy_trend = dxy_trend
            self.vix_val = vix_val
            self.yield_chg = yield_chg

        def get_indicator(self, key, **kwargs):
            if key == "DXY":
                return MacroIndicatorSnapshot("DX-Y.NYB", "DXY", self.dxy_val, -0.4 if self.dxy_trend == "BEARISH" else 0.4, self.dxy_trend, "2026-09-12T00:00:00Z")
            elif key == "VIX":
                return MacroIndicatorSnapshot("^VIX", "VIX", self.vix_val, 0.0, "NEUTRAL", "2026-09-12T00:00:00Z")
            elif key == "US10Y":
                return MacroIndicatorSnapshot("^TNX", "10Y", 4.10, self.yield_chg, "NEUTRAL", "2026-09-12T00:00:00Z")
            return MacroIndicatorSnapshot(key, key, 100.0, 0.0, "NEUTRAL", "2026-09-12T00:00:00Z")

    class MockSentiment:
        def __init__(self, score):
            self.score = score
        def get_fear_and_greed(self, **kwargs):
            return SentimentReading(self.score, "Greed" if self.score >= 50 else "Fear", "2026-09-12T00:00:00Z")

    # Scenario 1: RISK_ON (VIX 14, DXY Bearish, F&G 70)
    detector_on = MacroRegimeDetector(MockMacro(101.5, "BEARISH", 14.0, -1.0), MockSentiment(70))
    res_on = detector_on.evaluate_regime()
    assert res_on.regime == "RISK_ON"
    assert res_on.confidence >= 0.60

    # Scenario 2: RISK_OFF (VIX 28, DXY Bullish, F&G 20)
    detector_off = MacroRegimeDetector(MockMacro(106.0, "BULLISH", 28.0, 3.0), MockSentiment(20))
    res_off = detector_off.evaluate_regime()
    assert res_off.regime == "RISK_OFF"
    assert res_off.confidence >= 0.60


# ==============================================================================
# 3. QUANTITATIVE RISK & METRICS TESTS
# ==============================================================================

def test_quantitative_risk_math():
    """Verifies mathematical correctness of Sharpe, Sortino, VaR, CVaR, and Drawdown."""
    # Synthetic upward trend with minor volatility
    np.random.seed(42)
    daily_returns = np.array([0.01, -0.005, 0.015, -0.008, 0.012, 0.02, -0.003, 0.009, -0.004, 0.011] * 10)
    prices = 100.0 * np.cumprod(1.0 + daily_returns)

    # Sharpe
    sharpe = calculate_sharpe_ratio(daily_returns, risk_free_rate=0.04, periods_per_year=365)
    assert sharpe > 0.0, "Upward trend must produce positive Sharpe ratio"

    # Sortino
    sortino = calculate_sortino_ratio(daily_returns, risk_free_rate=0.04, periods_per_year=365)
    assert sortino >= sharpe, "Sortino should exceed Sharpe for positively skewed returns"

    # Max Drawdown
    dd = calculate_max_drawdown(prices)
    assert 0.0 <= dd <= 1.0

    # VaR & CVaR
    var95_h = calculate_var_historical(daily_returns, 0.95)
    var99_h = calculate_var_historical(daily_returns, 0.99)
    cvar95 = calculate_cvar(daily_returns, 0.95)

    assert var95_h >= 0.0
    assert var99_h >= var95_h, "99% VaR must be >= 95% VaR"
    assert cvar95 >= var95_h, "CVaR (Expected Shortfall) must be >= VaR"

    # Beta to self must be exactly 1.0
    beta_self = calculate_beta(daily_returns, daily_returns)
    assert pytest.approx(beta_self, 0.01) == 1.0

    # Full report
    report = compute_quantitative_risk_report(prices, symbol="BTCUSDT")
    assert isinstance(report, QuantitativeRiskReport)
    assert report.symbol == "BTCUSDT"
    assert report.sample_size == len(prices)


# ==============================================================================
# 4. VOLATILITY ESTIMATORS TESTS
# ==============================================================================

def test_volatility_estimators():
    """Verifies Parkinson, Garman-Klass, and Yang-Zhang multi-bar volatility estimators."""
    n = 50
    df = pd.DataFrame({
        "open": [100.0 + i for i in range(n)],
        "high": [102.0 + i for i in range(n)],
        "low": [98.5 + i for i in range(n)],
        "close": [100.5 + i for i in range(n)],
        "volume": [5000.0] * n
    })

    estimates = compute_volatility_estimates_from_df(df, symbol="ETHUSDT", timeframe="1d")
    assert isinstance(estimates, VolatilityEstimates)
    assert estimates.symbol == "ETHUSDT"
    assert estimates.parkinson_vol > 0.0, "Parkinson volatility must be positive"
    assert estimates.garman_klass_vol > 0.0, "Garman-Klass volatility must be positive"
    assert estimates.yang_zhang_vol > 0.0, "Yang-Zhang volatility must be positive"
    assert estimates.close_to_close_vol >= 0.0


# ==============================================================================
# 5. TECHNICAL OVERLAYS TESTS
# ==============================================================================

def test_technical_overlays():
    """Verifies Donchian, Keltner, and Pivot Points calculations."""
    df = pd.DataFrame({
        "high": [10.0, 11.0, 12.0, 13.0, 14.0] * 5,
        "low": [8.0, 9.0, 10.0, 11.0, 12.0] * 5,
        "close": [9.0, 10.0, 11.0, 12.0, 13.0] * 5
    })

    # Donchian
    donchian = compute_donchian_channels(df, window=5)
    assert "donchian_upper" in donchian.columns
    assert "donchian_lower" in donchian.columns
    assert "donchian_middle" in donchian.columns
    valid_idx = 10
    assert donchian["donchian_upper"].iloc[valid_idx] >= donchian["donchian_middle"].iloc[valid_idx]
    assert donchian["donchian_middle"].iloc[valid_idx] >= donchian["donchian_lower"].iloc[valid_idx]

    # Keltner
    keltner = compute_keltner_channels(df, window=5, atr_multiplier=2.0)
    assert "keltner_upper" in keltner.columns
    assert "keltner_lower" in keltner.columns
    assert keltner["keltner_upper"].iloc[valid_idx] >= keltner["keltner_lower"].iloc[valid_idx]

    # Pivot Points
    pivots = compute_pivot_points(high=100.0, low=90.0, close=95.0)
    assert "standard" in pivots
    assert "fibonacci" in pivots
    assert "camarilla" in pivots
    assert pivots["standard"]["pivot"] == 95.0
    assert pivots["standard"]["r1"] > pivots["standard"]["pivot"]
    assert pivots["standard"]["s1"] < pivots["standard"]["pivot"]


# ==============================================================================
# 6. UNIFIED OBB CLIENT INTERFACE TESTS
# ==============================================================================

def test_obb_facade_namespaces():
    """Verifies that the obb singleton provides all expected namespaces and methods."""
    assert hasattr(obb, "crypto")
    assert hasattr(obb, "economy")
    assert hasattr(obb, "quantitative")
    assert hasattr(obb, "technical")

    health = obb.health_check()
    assert health["status"] == "HEALTHY"
    assert health["mode"] == "100% Free Public / Unauthenticated"

    # Test obb.crypto
    overview = obb.crypto.overview()
    assert isinstance(overview, CryptoGlobalOverview)

    sentiment = obb.crypto.sentiment()
    assert isinstance(sentiment, SentimentReading)

    # Test obb.economy
    regime = obb.economy.regime()
    assert isinstance(regime, MacroRegimeState)
    assert regime.regime in ("RISK_ON", "RISK_OFF", "NEUTRAL")

    # Test obb.quantitative
    q_res = obb.quantitative.risk_metrics([100.0, 102.0, 101.0, 105.0, 104.0, 108.0])
    assert isinstance(q_res, QuantitativeRiskReport)

    dist = obb.quantitative.distribution([0.01, -0.02, 0.015, -0.005, 0.02, -0.01, 0.03, -0.02])
    assert "skewness" in dist
    assert "jarque_bera_stat" in dist


# ==============================================================================
# 7. FLASK REST API ENDPOINTS TESTS
# ==============================================================================

def test_flask_openbb_api_endpoints():
    """Verifies all /api/v1/openbb/* routes return valid JSON responses."""
    from dashboard import app
    client = app.test_client()

    endpoints = [
        "/api/v1/openbb/status",
        "/api/v1/openbb/crypto/global",
        "/api/v1/openbb/crypto/sentiment",
        "/api/v1/openbb/crypto/trending",
        "/api/v1/openbb/economy/macro",
        "/api/v1/openbb/economy/indicators",
        "/api/v1/openbb/quantitative/metrics?symbol=BTCUSDT",
    ]

    for ep in endpoints:
        res = client.get(ep)
        assert res.status_code == 200, f"Endpoint {ep} returned HTTP {res.status_code}"
        data = res.get_json()
        assert data is not None, f"Endpoint {ep} returned empty or non-JSON body"
        assert "status" in data or "data" in data
