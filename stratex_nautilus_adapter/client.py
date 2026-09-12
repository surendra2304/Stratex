"""stratex_nautilus_adapter/client.py

Unified Facade for NautilusTrader integration in Stratex:
- Exposes `nt` singleton with sub-engine access (bus, orders, aggregator, risk, simulator)
- Strictly enforces permanent security invariant: LIVE_TRADING_ENABLED = False
"""

from __future__ import annotations

from typing import Any
import pandas as pd

from .models import (
    TradeTick,
    Bar,
    BarType,
    BracketOrder,
    NautilusOrder,
    OrderSide,
    OrderType,
    RiskCheckResult,
    FillReport,
    Position,
)
from .event_bus import NautilusEventBus, default_event_bus
from .orders import NautilusOrderManager, default_order_manager
from .bar_aggregator import BarAggregatorEngine
from .risk import NautilusRiskEngine, default_risk_engine, RiskConfig
from .simulator import NautilusExecutionSimulator, SimulatorConfig


class NautilusNativeEngine:
    """Institutional Event-Driven Trading Engine facade inspired by NautilusTrader."""

    # Permanent Security Invariant
    LIVE_TRADING_ENABLED: bool = False

    def __init__(
        self,
        event_bus: NautilusEventBus | None = None,
        order_manager: NautilusOrderManager | None = None,
        risk_engine: NautilusRiskEngine | None = None,
        simulator: NautilusExecutionSimulator | None = None,
    ) -> None:
        self.bus = event_bus or default_event_bus
        self.orders = order_manager or default_order_manager
        self.aggregator = BarAggregatorEngine
        self.risk = risk_engine or default_risk_engine
        self.simulator = simulator or NautilusExecutionSimulator(
            order_manager=self.orders
        )

    def route_live_order(self, order: NautilusOrder) -> None:
        """Enforces permanent live execution prevention."""
        if not self.LIVE_TRADING_ENABLED:
            raise PermissionError(
                "Live Nautilus order routing is strictly disabled under the Stratex security policy. "
                "LIVE_TRADING_ENABLED = False is permanent."
            )

    def create_bracket(
        self,
        symbol: str,
        side: OrderSide | str,
        quantity: float,
        entry_price: float,
        take_profit_price: float,
        stop_loss_price: float,
        entry_type: OrderType | str = OrderType.LIMIT,
    ) -> BracketOrder:
        """Creates an Entry + Take-Profit (Limit) + Stop-Loss (Stop) bracket order."""
        side_enum = OrderSide(side) if isinstance(side, str) else side
        type_enum = OrderType(entry_type) if isinstance(entry_type, str) else entry_type

        # Pre-trade risk check on entry order
        dummy_order = NautilusOrder(
            client_order_id="pre_check",
            symbol=symbol.upper(),
            side=side_enum,
            order_type=type_enum,
            quantity=quantity,
            price=entry_price,
        )
        risk_result = self.risk.check_order(dummy_order, open_orders_count=len(self.orders.list_orders(is_open=True)))
        if not risk_result.passed:
            raise ValueError(f"Pre-trade risk check failed: {risk_result.reason}")

        bracket = self.orders.create_bracket_order(
            symbol=symbol,
            side=side_enum,
            quantity=quantity,
            entry_price=entry_price,
            take_profit_price=take_profit_price,
            stop_loss_price=stop_loss_price,
            entry_type=type_enum,
        )
        self.bus.publish("orders.bracket.created", bracket)
        return bracket

    def aggregate(
        self,
        ticks: list[TradeTick],
        bar_type: BarType | str = BarType.TICK,
        step: float = 100.0,
    ) -> list[Bar]:
        """Aggregates ticks into completed OHLCV bars."""
        btype = BarType(bar_type) if isinstance(bar_type, str) else bar_type
        bars = self.aggregator.aggregate_ticks(ticks, bar_type=btype, step=step)
        for b in bars:
            self.bus.publish(f"data.bar.{b.symbol.lower()}", b)
        return bars

    def check_risk(
        self,
        symbol: str,
        side: OrderSide | str,
        quantity: float,
        price: float,
    ) -> RiskCheckResult:
        """Runs pre-trade risk evaluation against active risk configuration."""
        side_enum = OrderSide(side) if isinstance(side, str) else side
        dummy_order = NautilusOrder(
            client_order_id="check_risk",
            symbol=symbol.upper(),
            side=side_enum,
            order_type=OrderType.LIMIT,
            quantity=quantity,
            price=price,
        )
        current_pos = self.simulator.get_position(symbol)
        open_count = len(self.orders.list_orders(is_open=True))
        return self.risk.check_order(
            dummy_order,
            current_position=current_pos,
            open_orders_count=open_count,
        )

    def simulate(self, ticks: list[TradeTick]) -> list[FillReport]:
        """Feeds a stream of trade ticks into the matching engine simulator."""
        fills: list[FillReport] = []
        for tick in ticks:
            self.bus.publish(f"data.tick.{tick.symbol.lower()}", tick)
            tick_fills = self.simulator.process_tick(tick)
            for f in tick_fills:
                self.bus.publish("execution.fill", f)
                fills.append(f)
        return fills

    def get_status(self) -> dict[str, Any]:
        """Returns overall Nautilus engine telemetry and status."""
        return {
            "status": "HEALTHY",
            "live_trading_enabled": self.LIVE_TRADING_ENABLED,
            "bus": self.bus.get_stats(),
            "open_orders_count": len(self.orders.list_orders(is_open=True)),
            "total_orders_count": len(self.orders.list_orders()),
            "total_brackets_count": len(self.orders.list_brackets()),
            "total_fills_count": len(self.simulator.list_fills()),
            "risk_config": {
                "max_order_notional_usd": self.risk.config.max_order_notional_usd,
                "max_position_notional_usd": self.risk.config.max_position_notional_usd,
                "max_open_orders": self.risk.config.max_open_orders,
                "max_orders_per_second": self.risk.config.max_orders_per_second,
                "max_drawdown_pct": self.risk.config.max_drawdown_pct,
            },
        }


# Global singleton facade
nt = NautilusNativeEngine()
