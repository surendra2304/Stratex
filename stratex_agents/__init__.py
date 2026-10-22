"""Stratex bounded agent catalog and order-management primitives."""
from .registry import get_agent_catalog
from .order_intent import TradeIntent
from .order_store import OrderIntentStore, IntentConflictError, IntentInFlightError
from .oms import StratexOrderManager, OMSResult, OrderBlockedError
from .market_data import MarketDataAgent, MarketDataUnavailable
from .risk_agent import DeterministicRiskAgent, RiskDecision
from .portfolio_agent import PortfolioAgent, PortfolioSnapshotError
from .reconciliation_agent import OrderReconciliationAgent
from .portfolio_risk_gate import PortfolioRiskGate
from .portfolio_risk_state import PortfolioRiskStateStore, PortfolioRiskStateError
from .portfolio_risk_runtime import build_portfolio_risk_context
from .quant_agent import QuantitativeStrategyAgent
from .runtime import (
    build_order_manager,
    build_quant_agent,
    build_reconciliation_agent,
    build_venue_aware_authorizer,
)

__all__ = [
    "get_agent_catalog",
    "TradeIntent",
    "OrderIntentStore",
    "IntentConflictError",
    "IntentInFlightError",
    "StratexOrderManager",
    "OMSResult",
    "OrderBlockedError",
    "MarketDataAgent",
    "MarketDataUnavailable",
    "DeterministicRiskAgent",
    "RiskDecision",
    "PortfolioAgent",
    "PortfolioSnapshotError",
    "OrderReconciliationAgent",
    "PortfolioRiskGate",
    "PortfolioRiskStateStore",
    "PortfolioRiskStateError",
    "build_portfolio_risk_context",
    "QuantitativeStrategyAgent",
    "build_order_manager",
    "build_quant_agent",
    "build_reconciliation_agent",
    "build_venue_aware_authorizer",
]
