"""stratex_backtrader_adapter/__init__.py

Package entry point for Stratex Backtrader Integration.
Exports unified `bt` facade, Cerebro orchestrator, Strategy base, Sizers,
Analyzers, Broker, and Domain Models.
"""

from .models import (
    OrderSide,
    OrderType,
    OrderStatus,
    BacktestOrder,
    TradeRecord,
    BacktestResult,
)
from .broker import BacktraderBroker, CommissionScheme
from .sizers import (
    BaseSizer,
    FixedSize,
    PercentSizer,
    VolatilitySizer,
    KellySizer,
)
from .analyzers import (
    BaseAnalyzer,
    SharpeRatio,
    SortinoRatio,
    DrawDown,
    TradeAnalyzer,
    SQN,
    CalmarRatio,
)
from .strategy import (
    Strategy,
    SMACrossStrategy,
    RSIStrategy,
)
from .cerebro import Cerebro
from .client import BacktraderNativeEngine, bt

__all__ = [
    # Unified Facade
    "bt",
    "BacktraderNativeEngine",
    # Core Orchestrator
    "Cerebro",
    # Strategy
    "Strategy",
    "SMACrossStrategy",
    "RSIStrategy",
    # Broker
    "BacktraderBroker",
    "CommissionScheme",
    # Sizers
    "BaseSizer",
    "FixedSize",
    "PercentSizer",
    "VolatilitySizer",
    "KellySizer",
    # Analyzers
    "BaseAnalyzer",
    "SharpeRatio",
    "SortinoRatio",
    "DrawDown",
    "TradeAnalyzer",
    "SQN",
    "CalmarRatio",
    # Models
    "OrderSide",
    "OrderType",
    "OrderStatus",
    "BacktestOrder",
    "TradeRecord",
    "BacktestResult",
]
