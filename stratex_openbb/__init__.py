"""
stratex_openbb — Native OpenBB Platform Integration for Stratex.
Delivers OpenBB-grade investment research, macroeconomic regime detection,
and quantitative risk analytics using 100% free, unauthenticated public sources.

Usage:
    from stratex_openbb import obb

    # Global Crypto Intelligence
    overview = obb.crypto.overview()
    sentiment = obb.crypto.sentiment()

    # Macroeconomic Regime
    macro = obb.economy.regime()
    dxy = obb.economy.indicator("DXY")

    # Quantitative Risk Analytics
    risk = obb.quantitative.risk_metrics(prices, symbol="BTCUSDT")
    vols = obb.quantitative.volatility_estimators(df, symbol="BTCUSDT")

    # Technical Overlays
    donchian = obb.technical.donchian(df, window=20)
"""

from .client import OpenBBNativeEngine
from .models import (
    CryptoGlobalOverview,
    SentimentReading,
    MacroIndicatorSnapshot,
    MacroRegimeState,
    QuantitativeRiskReport,
    VolatilityEstimates,
)

# Global singleton client instance mimicking the official `obb` interface
obb = OpenBBNativeEngine()

__all__ = [
    "obb",
    "OpenBBNativeEngine",
    "CryptoGlobalOverview",
    "SentimentReading",
    "MacroIndicatorSnapshot",
    "MacroRegimeState",
    "QuantitativeRiskReport",
    "VolatilityEstimates",
]
