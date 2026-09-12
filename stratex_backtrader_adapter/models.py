"""stratex_backtrader_adapter/models.py

Domain models, enums, and dataclasses inspired by Backtrader:
- Order lifecycle models (Side, Type, Status, BacktestOrder)
- Trade record structures with execution metadata and PnL
- Backtest summary result structures containing quantitative metrics
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import uuid


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


class OrderStatus(str, Enum):
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    COMPLETED = "COMPLETED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"

    @property
    def is_closed(self) -> bool:
        return self in (
            OrderStatus.COMPLETED,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
        )


@dataclass
class BacktestOrder:
    """Represents a simulated order within Backtrader broker."""
    order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    size: float
    price: float | None = None
    status: OrderStatus = OrderStatus.SUBMITTED
    created_idx: int = 0
    executed_idx: int | None = None
    executed_price: float = 0.0
    commission: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class TradeRecord:
    """Represents a closed completed round-trip trade."""
    trade_id: str
    symbol: str
    side: OrderSide
    size: float
    entry_price: float
    exit_price: float
    entry_idx: int
    exit_idx: int
    pnl: float
    pnl_pct: float
    commission: float
    duration_bars: int

    @property
    def is_win(self) -> bool:
        return self.pnl > 0.0

    @property
    def is_loss(self) -> bool:
        return self.pnl < 0.0


@dataclass
class BacktestResult:
    """Aggregated quantitative performance summary of a Cerebro execution."""
    strategy_name: str
    starting_cash: float
    ending_cash: float
    total_pnl: float
    total_return_pct: float
    trades: list[TradeRecord]
    analyzers: dict[str, any] = field(default_factory=dict)
    equity_curve: list[float] = field(default_factory=list)
