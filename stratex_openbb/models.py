"""
stratex_openbb/models.py — Strongly-typed models for OpenBB market intelligence and quantitative analytics.
"""

from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass(frozen=True)
class CryptoGlobalOverview:
    """Global crypto market aggregates from free public endpoints."""
    total_market_cap_usd: float
    total_volume_24h_usd: float
    btc_dominance_pct: float
    eth_dominance_pct: float
    active_cryptocurrencies: int
    market_cap_change_pct_24h: float
    updated_at: str
    source: str = "CoinGecko (Public Free API)"


@dataclass(frozen=True)
class SentimentReading:
    """Market sentiment reading (e.g., Fear & Greed Index)."""
    score: int                       # 0 to 100
    classification: str              # Extreme Fear, Fear, Neutral, Greed, Extreme Greed
    timestamp: str
    source: str = "Alternative.me (Public Free API)"


@dataclass(frozen=True)
class MacroIndicatorSnapshot:
    """Single macroeconomic indicator snapshot."""
    symbol: str                      # e.g., DX-Y.NYB, ^TNX, ^VIX
    name: str                        # e.g., US Dollar Index, 10-Yr Treasury Yield
    current_value: float
    change_pct: float
    trend: str                       # BULLISH, BEARISH, NEUTRAL
    timestamp: str
    source: str = "Public Market Data"


@dataclass(frozen=True)
class MacroRegimeState:
    """Cross-asset macroeconomic regime determination."""
    regime: str                      # RISK_ON, RISK_OFF, NEUTRAL
    confidence: float                # 0.0 to 1.0
    summary: str
    dxy_value: Optional[float]
    us10y_yield: Optional[float]
    vix_value: Optional[float]
    gold_value: Optional[float]
    fear_greed_score: Optional[int]
    timestamp: str


@dataclass(frozen=True)
class QuantitativeRiskReport:
    """OpenBB-grade quantitative risk and performance metrics."""
    symbol: str
    period_days: int
    annualized_return: float
    annualized_volatility: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    max_drawdown: float
    var_95_historical: float
    var_99_historical: float
    var_95_parametric: float
    var_99_parametric: float
    cvar_95: float                   # Expected Shortfall
    cvar_99: float
    beta_to_benchmark: Optional[float] = None
    skewness: float = 0.0
    kurtosis: float = 0.0
    sample_size: int = 0


@dataclass(frozen=True)
class VolatilityEstimates:
    """Multi-bar advanced quantitative volatility estimators."""
    symbol: str
    timeframe: str
    sample_bars: int
    close_to_close_vol: float
    parkinson_vol: float             # High-Low estimator
    garman_klass_vol: float          # OHLC estimator
    yang_zhang_vol: float            # Unbiased overnight jump + drift estimator
    timestamp: str
