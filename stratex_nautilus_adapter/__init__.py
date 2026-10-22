"""stratex_nautilus_adapter/__init__.py

Package entry point for Stratex NautilusTrader Integration.
Exports the unified `nt` facade, domain models, event bus, order state machine,
bar aggregators, pre-trade risk engine, and execution simulator.
"""

from .models import (
    OrderSide,
    OrderType,
    OrderState,
    TimeInForce,
    ContingencyType,
    BarType,
    LiquidityRole,
    TradeTick,
    Bar,
    NautilusOrder,
    BracketOrder,
    FillReport,
    Position,
    RiskCheckResult,
)
from .event_bus import NautilusEventBus, Event, default_event_bus
from .orders import (
    NautilusOrderManager,
    InvalidStateTransitionError,
    OrderNotFoundError,
    default_order_manager,
)
from .bar_aggregator import (
    BarAggregator,
    TickBarAggregator,
    VolumeBarAggregator,
    ValueBarAggregator,
    TimeBarAggregator,
    BarAggregatorEngine,
)
from .risk import NautilusRiskEngine, RiskConfig, default_risk_engine
from .simulator import (
    NautilusExecutionSimulator,
    SimulatorConfig,
    SlippageModel,
)
from .client import NautilusNativeEngine, nt

__all__ = [
    # Unified Facade
    "nt",
    "NautilusNativeEngine",
    # Domain Models & Enums
    "OrderSide",
    "OrderType",
    "OrderState",
    "TimeInForce",
    "ContingencyType",
    "BarType",
    "LiquidityRole",
    "TradeTick",
    "Bar",
    "NautilusOrder",
    "BracketOrder",
    "FillReport",
    "Position",
    "RiskCheckResult",
    # Event Bus
    "NautilusEventBus",
    "Event",
    "default_event_bus",
    # Orders & State Machine
    "NautilusOrderManager",
    "InvalidStateTransitionError",
    "OrderNotFoundError",
    "default_order_manager",
    # Bar Aggregation
    "BarAggregator",
    "TickBarAggregator",
    "VolumeBarAggregator",
    "ValueBarAggregator",
    "TimeBarAggregator",
    "BarAggregatorEngine",
    # Risk
    "NautilusRiskEngine",
    "RiskConfig",
    "default_risk_engine",
    # Simulation
    "NautilusExecutionSimulator",
    "SimulatorConfig",
    "SlippageModel",
]
