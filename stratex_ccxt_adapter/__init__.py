"""stratex_ccxt_adapter/__init__.py

Package entry point for Stratex CCXT Multi-Exchange Integration.
Exports normalized models, precision helpers, error mappers, single-exchange adapter,
and the multi-exchange CCXTHub.
100% backwards-compatible with existing modules and test suites.
"""

from .models import (
    NormalizedMarket,
    NormalizedOrder,
    NormalizedTicker,
    ArbitrageOpportunity,
    OrderBookDepthAnalysis,
    FundingRateComparison,
)
from .precision import PrecisionHelper
from .errors import CCXTErrorMapper
from .client import CCXTExchangeAdapter
from .arbitrage import ArbitrageScanner
from .funding import FundingRateComparator
from .depth import OrderBookAnalyzer
from .hub import CCXTHub, ccxt_hub

__all__ = [
    # Legacy & Base models
    "NormalizedMarket",
    "NormalizedOrder",
    "NormalizedTicker",
    "PrecisionHelper",
    "CCXTErrorMapper",
    "CCXTExchangeAdapter",
    # Enhanced Multi-Exchange Hub & Features
    "ArbitrageOpportunity",
    "OrderBookDepthAnalysis",
    "FundingRateComparison",
    "ArbitrageScanner",
    "FundingRateComparator",
    "OrderBookAnalyzer",
    "CCXTHub",
    "ccxt_hub",
]
