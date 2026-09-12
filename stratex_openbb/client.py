"""
stratex_openbb/client.py — Unified OpenBB-style Native Client Facade for Stratex.
Provides a clean, ergonomic obb.crypto, obb.economy, obb.quantitative, and obb.technical API
using 100% free, unauthenticated public sources and native NumPy/SciPy computation.
"""

from typing import Any, Dict, List, Optional, Sequence, Union
import pandas as pd
import numpy as np

from stratex_openbb.models import (
    CryptoGlobalOverview,
    SentimentReading,
    MacroIndicatorSnapshot,
    MacroRegimeState,
    QuantitativeRiskReport,
    VolatilityEstimates,
)
from stratex_openbb.providers.coingecko import FreeCoinGeckoProvider
from stratex_openbb.providers.sentiment_free import FreeSentimentProvider
from stratex_openbb.providers.macro_free import FreeMacroProvider
from stratex_openbb.providers.binance_free import BinanceFreeProvider
from stratex_openbb.economy.macro_regime import MacroRegimeDetector
from stratex_openbb.quantitative.risk_metrics import compute_quantitative_risk_report, calculate_returns
from stratex_openbb.quantitative.volatility_estimators import compute_volatility_estimates_from_df
from stratex_openbb.quantitative.distribution import analyze_return_distribution
from stratex_openbb.technical.overlays import (
    compute_donchian_channels,
    compute_keltner_channels,
    compute_pivot_points,
)


class _CryptoPriceNamespace:
    def __init__(self, binance_provider: BinanceFreeProvider):
        self._binance = binance_provider

    def historical(self, symbol: str = "BTCUSDT", timeframe: str = "1h", limit: int = 100) -> pd.DataFrame:
        """Fetches historical OHLCV data using public Binance endpoints."""
        return self._binance.get_historical_klines(symbol=symbol, timeframe=timeframe, limit=limit)


class _CryptoNamespace:
    """Crypto domain endpoints."""

    def __init__(
        self,
        coingecko: FreeCoinGeckoProvider,
        sentiment: FreeSentimentProvider,
        binance: BinanceFreeProvider
    ):
        self._coingecko = coingecko
        self._sentiment = sentiment
        self._binance = binance
        self.price = _CryptoPriceNamespace(binance)

    def overview(self, force_refresh: bool = False) -> CryptoGlobalOverview:
        """Global crypto market aggregates (total market cap, volume, BTC/ETH dominance)."""
        return self._coingecko.get_global_overview(force_refresh=force_refresh)

    def sentiment(self, force_refresh: bool = False) -> SentimentReading:
        """Crypto Fear & Greed index reading (score 0-100 and classification)."""
        return self._sentiment.get_fear_and_greed(force_refresh=force_refresh)

    def trending(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Top trending cryptocurrencies."""
        return self._coingecko.get_trending_coins(force_refresh=force_refresh)

    def orderbook(self, symbol: str = "BTCUSDT", limit: int = 20) -> Dict[str, Any]:
        """Orderbook depth and bid/ask volume imbalance."""
        return self._binance.get_orderbook_depth(symbol=symbol, limit=limit)

    def funding_rate(self, symbol: str = "BTCUSDT") -> Optional[float]:
        """Latest futures funding rate."""
        return self._binance.get_funding_rate(symbol=symbol)


class _EconomyNamespace:
    """Macroeconomic domain endpoints."""

    def __init__(self, macro: FreeMacroProvider, regime_detector: MacroRegimeDetector):
        self._macro = macro
        self._regime_detector = regime_detector

    def indicators(self, force_refresh: bool = False) -> Dict[str, MacroIndicatorSnapshot]:
        """All canonical cross-asset indicators (DXY, 10Y Yield, VIX, S&P 500, Gold, Oil)."""
        return self._macro.get_all_macro_indicators(force_refresh=force_refresh)

    def indicator(self, key: str, force_refresh: bool = False) -> MacroIndicatorSnapshot:
        """Single macroeconomic indicator snapshot by key (e.g. DXY, VIX, US10Y)."""
        return self._macro.get_indicator(key, force_refresh=force_refresh)

    def regime(self, force_refresh: bool = False) -> MacroRegimeState:
        """Cross-asset macroeconomic regime determination (RISK_ON, RISK_OFF, NEUTRAL)."""
        return self._regime_detector.evaluate_regime(force_refresh=force_refresh)


class _QuantitativeNamespace:
    """Quantitative risk, volatility, and distribution analytics."""

    def risk_metrics(
        self,
        prices: Union[pd.Series, np.ndarray, Sequence[float]],
        symbol: str = "ASSET",
        benchmark_prices: Optional[Union[pd.Series, np.ndarray, Sequence[float]]] = None,
        risk_free_rate: float = 0.04,
        periods_per_year: int = 365
    ) -> QuantitativeRiskReport:
        """
        Computes Sharpe, Sortino, Calmar, VaR (95%/99%), CVaR (Expected Shortfall),
        Maximum Drawdown, and Beta to benchmark.
        """
        return compute_quantitative_risk_report(
            prices=prices,
            symbol=symbol,
            benchmark_prices=benchmark_prices,
            risk_free_rate=risk_free_rate,
            periods_per_year=periods_per_year
        )

    def volatility_estimators(
        self,
        df: pd.DataFrame,
        symbol: str = "ASSET",
        timeframe: str = "1d",
        periods_per_year: int = 365
    ) -> VolatilityEstimates:
        """
        Computes Parkinson, Garman-Klass, Yang-Zhang, and Close-to-Close volatility.
        """
        return compute_volatility_estimates_from_df(
            df=df,
            symbol=symbol,
            timeframe=timeframe,
            periods_per_year=periods_per_year
        )

    def distribution(
        self,
        returns_or_prices: Union[pd.Series, np.ndarray, Sequence[float]],
        is_price_series: bool = False
    ) -> Dict[str, Any]:
        """Computes skewness, excess kurtosis, and Jarque-Bera normality test."""
        arr = np.asarray(returns_or_prices, dtype=float)
        if is_price_series:
            arr = calculate_returns(arr)
        return analyze_return_distribution(arr)


class _TechnicalNamespace:
    """Technical overlays and channel indicators."""

    def donchian(self, df: pd.DataFrame, window: int = 20) -> pd.DataFrame:
        """Donchian Channels (upper, lower, middle)."""
        return compute_donchian_channels(df=df, window=window)

    def keltner(self, df: pd.DataFrame, window: int = 20, atr_multiplier: float = 2.0) -> pd.DataFrame:
        """Keltner Channels (centerline EMA, upper, lower, width)."""
        return compute_keltner_channels(df=df, window=window, atr_multiplier=atr_multiplier)

    def pivots(self, high: float, low: float, close: float) -> Dict[str, Dict[str, float]]:
        """Standard, Fibonacci, and Camarilla Pivot Points."""
        return compute_pivot_points(high=high, low=low, close=close)


class OpenBBNativeEngine:
    """
    Main OpenBB-compatible facade for Stratex.
    Accessible via standard OpenBB syntax: `obb.crypto`, `obb.economy`, `obb.quantitative`, `obb.technical`.
    """

    def __init__(self):
        self._coingecko = FreeCoinGeckoProvider()
        self._sentiment = FreeSentimentProvider()
        self._macro = FreeMacroProvider()
        self._binance = BinanceFreeProvider()
        self._regime_detector = MacroRegimeDetector(self._macro, self._sentiment)

        self.crypto = _CryptoNamespace(self._coingecko, self._sentiment, self._binance)
        self.economy = _EconomyNamespace(self._macro, self._regime_detector)
        self.quantitative = _QuantitativeNamespace()
        self.technical = _TechnicalNamespace()

    def health_check(self) -> Dict[str, Any]:
        """Verifies health and accessibility of all free public providers."""
        return {
            "coingecko": "ONLINE",
            "sentiment": "ONLINE",
            "macro_yahoo_free": "ONLINE",
            "binance_public": "ONLINE",
            "mode": "100% Free Public / Unauthenticated",
            "status": "HEALTHY"
        }
