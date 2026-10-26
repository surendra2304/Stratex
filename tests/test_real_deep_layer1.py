"""Tests for OpenBB Research Terminal implementation."""

from __future__ import annotations

import sys
from pathlib import Path

# Add Stratex to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.agents.real_openbb import (
    OpenBBResearchEngine,
    OpenBBScreener,
    SecurityProfile,
    FundamentalMetrics,
    TechnicalSignal,
    NewsItem,
    CalendarEvent,
    PortfolioAnalytics,
)


def test_security_profile():
    """Test SecurityProfile creation and properties."""
    profile = SecurityProfile(
        symbol="AAPL",
        name="Apple Inc.",
        asset_type="stock",
        exchange="NASDAQ",
        sector="Technology",
        industry="Consumer Electronics",
        market_cap=3_000_000_000_000,
        current_price=150.0,
        fifty_two_week_low=100.0,
        fifty_two_week_high=200.0,
    )

    assert profile.symbol == "AAPL"
    assert profile.is_large_cap
    assert profile.is_mega_cap
    assert 0 < profile.price_position < 1

    result = profile.to_dict()
    assert "AAPL" in result["symbol"]
    assert result["is_large_cap"] is True


def test_fundamental_metrics():
    """Test FundamentalMetrics analysis."""
    metrics = FundamentalMetrics(
        symbol="AAPL",
        pe_ratio=25.0,
        pb_ratio=4.0,
        fcf_yield=0.06,
        dividend_yield=0.01,
        roe=0.25,
        roa=0.10,
        debt_to_equity=1.5,
        current_ratio=1.8,
        quick_ratio=1.5,
        revenue=100_000_000_000,
        net_income=20_000_000_000,
        free_cash_flow=8_000_000_000,
        beta=1.2,
    )

    assert metrics.pe_ratio == 25.0
    assert metrics.is_undervalued is True
    assert metrics.is_growth_stock is True
    assert 0 < metrics.financial_health_score < 100

    result = metrics.to_dict()
    assert "AAPL" in result["symbol"]
    assert "financial_health_score" in result


def test_technical_signal():
    """Test TechnicalSignal analysis."""
    signal = TechnicalSignal(
        symbol="AAPL",
        rsi_14=25.0,
        macd_histogram=0.5,
        bb_upper=160.0,
        bb_middle=150.0,
        bb_lower=140.0,
        current_price=135.0,
        sma_20=148.0,
        sma_50=145.0,
        sma_200=140.0,
    )

    assert signal.rsi_signal == "oversold"
    assert signal.macd_signal_type == "bullish"
    assert signal.bollinger_signal == "oversold"
    assert signal.composite_signal in ("BUY", "SELL", "HOLD")

    result = signal.to_dict()
    assert "AAPL" in result["symbol"]
    assert "composite_signal" in result


def test_news_item():
    """Test NewsItem sentiment."""
    news = NewsItem(
        title="Apple announces new product",
        source="Reuters",
        sentiment=0.8,
        relevance=0.9,
        symbols=["AAPL"],
    )

    assert news.sentiment_label == "positive"
    result = news.to_dict()
    assert "AAPL" in result["symbols"]


def test_portfolio_analytics():
    """Test PortfolioAnalytics calculations."""
    analytics = PortfolioAnalytics(
        holdings={"AAPL": 100, "GOOGL": 50},
        prices={"AAPL": 150.0, "GOOGL": 2800.0},
    )

    weights = analytics.compute_weights()
    assert "AAPL" in weights
    assert "GOOGL" in weights
    assert abs(sum(weights.values()) - 1.0) < 0.01

    assert analytics.total_value == 100 * 150 + 50 * 2800

    result = analytics.to_dict()
    assert "weights" in result
    assert "concentration_hhi" in result


def test_screener():
    """Test OpenBBScreener filtering."""
    profiles = [
        SecurityProfile(symbol="AAPL", market_cap=3e12, sector="Technology"),
        SecurityProfile(symbol="MSFT", market_cap=2e12, sector="Technology"),
        SecurityProfile(symbol="JPM", market_cap=400e9, sector="Finance"),
    ]

    metrics = [
        FundamentalMetrics(symbol="AAPL", pe_ratio=25.0, fcf_yield=0.06),
        FundamentalMetrics(symbol="MSFT", pe_ratio=30.0, fcf_yield=0.05),
    ]

    # Screen by market cap
    results = OpenBBScreener.screen(profiles, metrics, {"market_cap_min": 2e12})
    assert len(results) == 2
    assert all(r["market_cap"] >= 2e12 for r in results)

    # Screen by sector
    results = OpenBBScreener.screen(profiles, metrics, {"sector": "Finance"})
    assert len(results) == 1
    assert results[0]["symbol"] == "JPM"


def test_research_engine():
    """Test OpenBBResearchEngine full research."""
    engine = OpenBBResearchEngine()

    engine.add_profile(SecurityProfile(
        symbol="AAPL", name="Apple Inc.", sector="Technology",
        market_cap=3e12, current_price=150.0,
    ))
    engine.add_metrics(FundamentalMetrics(symbol="AAPL", pe_ratio=25.0, fcf_yield=0.06))
    engine.add_signal(TechnicalSignal(symbol="AAPL", rsi_14=50.0, current_price=150.0))
    engine.add_news(NewsItem(title="Test", sentiment=0.5, symbols=["AAPL"]))

    result = engine.research("AAPL")
    assert "profile" in result
    assert "fundamentals" in result
    assert "technical" in result
    assert "news" in result


def test_portfolio_sharpe_sortino():
    """Test Sharpe and Sortino ratios."""
    analytics = PortfolioAnalytics()
    returns = [0.01, -0.02, 0.03, 0.01, -0.01, 0.02, 0.01, -0.01, 0.03, 0.02]

    sharpe = analytics.sharpe_ratio(returns)
    sortino = analytics.sortino_ratio(returns)

    assert isinstance(sharpe, float)
    assert isinstance(sortino, float)


def test_portfolio_beta():
    """Test beta calculation."""
    analytics = PortfolioAnalytics()
    returns = [0.01, 0.02, -0.01, 0.03, 0.01]
    market = [0.005, 0.01, -0.005, 0.015, 0.005]

    beta = analytics.beta(returns, market)
    assert isinstance(beta, float)
    assert beta > 0
