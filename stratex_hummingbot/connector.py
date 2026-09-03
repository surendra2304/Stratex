from dataclasses import dataclass
from typing import Protocol, Mapping, Any

@dataclass(frozen=True)
class ConnectorHealth:
    connected: bool
    last_market_data_ms: int | None
    last_order_update_ms: int | None
    error: str | None = None

class ConnectorContract(Protocol):
    def fetch_markets(self) -> Mapping[str, Any]: ...
    def fetch_order_book(self, symbol: str) -> Any: ...
    def place_order(self, request: Mapping[str, Any]) -> Any: ...
    def cancel_order(self, order_id: str) -> Any: ...
