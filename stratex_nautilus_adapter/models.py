"""stratex_nautilus_adapter/models.py

Domain models, enums, and immutable dataclasses inspired by NautilusTrader:
- High-integrity Order and Position state machine types
- Tick and Multi-type Bar models (Time, Tick, Volume, Value/Dollar)
- Bracket and Contingency order specifications (OCO, OTO)
- Fill reports, Risk results, and execution telemetry
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
    STOP_MARKET = "STOP_MARKET"
    STOP_LIMIT = "STOP_LIMIT"
    TRAILING_STOP = "TRAILING_STOP"


class OrderState(str, Enum):
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    PENDING_CANCEL = "PENDING_CANCEL"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"

    @property
    def is_closed(self) -> bool:
        return self in (
            OrderState.FILLED,
            OrderState.CANCELED,
            OrderState.REJECTED,
            OrderState.EXPIRED,
        )

    @property
    def is_open(self) -> bool:
        return not self.is_closed


class TimeInForce(str, Enum):
    GTC = "GTC"  # Good 'til Canceled
    IOC = "IOC"  # Immediate or Cancel
    FOK = "FOK"  # Fill or Kill
    DAY = "DAY"  # Good for Day


class ContingencyType(str, Enum):
    NONE = "NONE"
    OCO = "OCO"  # One-Cancels-the-Other
    OTO = "OTO"  # One-Triggers-the-Other


class BarType(str, Enum):
    TIME = "TIME"
    TICK = "TICK"
    VOLUME = "VOLUME"
    VALUE = "VALUE"  # Dollar / Notional


class LiquidityRole(str, Enum):
    MAKER = "MAKER"
    TAKER = "TAKER"


@dataclass(frozen=True)
class TradeTick:
    """Represents a granular raw trade tick event with nanosecond precision."""
    symbol: str
    price: float
    size: float
    ts_event: int  # Nanoseconds since Unix epoch
    side: OrderSide = OrderSide.BUY
    trade_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])


@dataclass(frozen=True)
class Bar:
    """Represents an aggregated OHLCV bar across Time, Tick, Volume, or Value dimensions."""
    bar_type: BarType
    symbol: str
    step: float  # e.g., 100 for 100-tick bar, 5.0 for 5-BTC volume bar, 60 for 60s
    open: float
    high: float
    low: float
    close: float
    volume: float
    notional: float
    ticks_count: int
    ts_start: int  # Nanoseconds
    ts_end: int    # Nanoseconds

    @property
    def vwap(self) -> float:
        return self.notional / self.volume if self.volume > 0 else self.close


@dataclass(frozen=True)
class NautilusOrder:
    """Nautilus-style high-integrity order with full lifecycle tracking."""
    client_order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: float | None = None
    stop_price: float | None = None
    time_in_force: TimeInForce = TimeInForce.GTC
    state: OrderState = OrderState.SUBMITTED
    filled_qty: float = 0.0
    remaining_qty: float = 0.0
    avg_fill_price: float = 0.0
    contingency_type: ContingencyType = ContingencyType.NONE
    linked_order_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class BracketOrder:
    """Represents an Entry order linked to Take-Profit (Limit) and Stop-Loss (Stop) orders."""
    bracket_id: str
    symbol: str
    entry_order: NautilusOrder
    take_profit_order: NautilusOrder
    stop_loss_order: NautilusOrder
    is_active: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class FillReport:
    """Represents an execution fill confirmation from matching engine."""
    fill_id: str
    client_order_id: str
    symbol: str
    price: float
    quantity: float
    commission: float
    liquidity_role: LiquidityRole
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class Position:
    """Tracks net position, cost basis, realized and unrealized PnL."""
    symbol: str
    quantity: float = 0.0
    avg_entry_price: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class RiskCheckResult:
    """Outcome of pre-trade risk evaluation."""
    passed: bool
    reason: str = "OK"
    metric: str = "NONE"
    value: float = 0.0
    limit: float = 0.0
