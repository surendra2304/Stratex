from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

class EventType(str, Enum):
    MARKET_DATA = "market_data"
    SIGNAL = "signal"
    INTENT = "intent"
    ORDER_UPDATE = "order_update"
    FILL = "fill"
    POSITION_UPDATE = "position_update"
    HEARTBEAT = "heartbeat"

@dataclass(frozen=True)
class MarketEvent:
    ts_ns: int
    symbol: str
    payload: Mapping[str, Any]
    event_type: EventType = EventType.MARKET_DATA

@dataclass(frozen=True)
class OrderIntent:
    intent_id: str
    strategy_version: str
    symbol: str
    side: str
    quantity: float
    order_type: str
    limit_price: float | None = None
    reduce_only: bool = False

@dataclass
class RuntimeState:
    running: bool = False
    last_event_ns: int | None = None
    processed_events: int = 0
    fault: str | None = None
