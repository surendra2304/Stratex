"""Normalized, immutable trade intent shared by Stratex strategies and venue adapters."""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class TradeIntent:
    """A new-entry intent. Every non-reduce-only entry must carry protective levels."""

    venue: str
    symbol: str
    side: str
    order_type: str
    quantity: float
    environment: str
    stop_loss: float
    take_profit: float
    strategy: str
    client_order_id: str = field(default_factory=lambda: f"stx-{uuid.uuid4().hex[:28]}")
    limit_price: float | None = None
    reduce_only: bool = False
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        venue = self.venue.strip().lower()
        side = self.side.strip().upper()
        order_type = self.order_type.strip().upper()
        environment = self.environment.strip().lower()
        if venue not in {"binance", "mt5"}:
            raise ValueError(f"Unsupported execution venue: {self.venue!r}")
        if not self.symbol or not self.symbol.strip():
            raise ValueError("symbol is required")
        if side not in {"BUY", "SELL", "LONG", "SHORT"}:
            raise ValueError("side must be BUY/SELL/LONG/SHORT")
        if order_type not in {"MARKET", "LIMIT", "STOP", "STOP_LIMIT"}:
            raise ValueError(f"Unsupported order type: {self.order_type!r}")
        if environment not in {"binance_demo", "binance_testnet", "mt5_demo"}:
            raise ValueError("Only Binance Demo/Testnet and MT5 Demo environments are allowed")
        if venue == "binance" and environment not in {"binance_demo", "binance_testnet"}:
            raise ValueError("Binance venue requires a Binance Demo or Testnet environment")
        if venue == "mt5" and environment != "mt5_demo":
            raise ValueError("MT5 venue is restricted to the demo environment")
        if not math.isfinite(float(self.quantity)) or float(self.quantity) <= 0:
            raise ValueError("quantity must be finite and positive")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,36}", self.client_order_id):
            raise ValueError("client_order_id must contain 1-36 alphanumeric or . _ : - characters")
        if not self.strategy.strip():
            raise ValueError("strategy is required")
        if not self.reduce_only:
            for label, value in (("stop_loss", self.stop_loss), ("take_profit", self.take_profit)):
                if not math.isfinite(float(value)) or float(value) <= 0:
                    raise ValueError(f"{label} must be finite and positive for entry orders")
        if self.limit_price is not None and (not math.isfinite(float(self.limit_price)) or float(self.limit_price) <= 0):
            raise ValueError("limit_price must be finite and positive")
        object.__setattr__(self, "venue", venue)
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "order_type", order_type)
        object.__setattr__(self, "environment", environment)
        object.__setattr__(self, "symbol", self.symbol.strip())
        object.__setattr__(self, "strategy", self.strategy.strip())

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe copy used by the durable intent ledger."""
        return asdict(self)

    def payload_hash(self) -> str:
        """Hash every field except the generated timestamp, which isn't order identity."""
        payload = self.to_dict()
        payload.pop("created_at", None)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
