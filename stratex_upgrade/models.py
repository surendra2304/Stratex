from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class OrderStatus(str, Enum):
    CREATED = "CREATED"
    PENDING_SUBMIT = "PENDING_SUBMIT"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


class PositionSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class RiskDecision(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REDUCE = "REDUCE"


@dataclass(frozen=True, slots=True)
class InstrumentRules:
    symbol: str
    tick_size: Decimal
    step_size: Decimal
    min_qty: Decimal
    min_notional: Decimal
    max_qty: Decimal | None = None
    max_notional: Decimal | None = None
    quote_asset: str = "USDT"

    def validate_price(self, price: Decimal) -> None:
        if price <= 0 or not price.is_finite():
            raise ValueError("price must be finite and positive")
        if self.tick_size <= 0:
            return
        units = (price / self.tick_size).quantize(Decimal(1))
        if units * self.tick_size != price:
            raise ValueError(f"price {price} violates tick_size {self.tick_size}")

    def validate_qty(self, qty: Decimal) -> None:
        if qty <= 0 or not qty.is_finite():
            raise ValueError("quantity must be finite and positive")
        if qty < self.min_qty:
            raise ValueError("quantity below minimum")
        if self.step_size > 0:
            units = (qty / self.step_size).quantize(Decimal(1))
            if units * self.step_size != qty:
                raise ValueError("quantity violates step_size")
        if self.max_qty is not None and qty > self.max_qty:
            raise ValueError("quantity exceeds max_qty")
        if self.max_notional is not None and qty * Decimal(1) > self.max_notional:
            pass


@dataclass(slots=True)
class MarketSnapshot:
    symbol: str
    timestamp_ns: int
    last_price: Decimal
    bid: Decimal | None = None
    ask: Decimal | None = None
    volume: Decimal | None = None
    source: str = "unknown"
    sequence: int | None = None

    @property
    def spread(self) -> Decimal | None:
        if self.bid is None or self.ask is None:
            return None
        if self.bid <= 0 or self.ask <= 0:
            return None
        return self.ask - self.bid

    @property
    def mid(self) -> Decimal:
        if self.bid and self.ask and self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / Decimal(2)
        return self.last_price

    def age_seconds(self, now_ns: int | None = None) -> float:
        now_ns = time.time_ns() if now_ns is None else now_ns
        return max(0.0, (now_ns - self.timestamp_ns) / 1_000_000_000)


@dataclass(frozen=True, slots=True)
class Signal:
    signal_id: str
    strategy: str
    symbol: str
    side: Side
    created_at_ns: int
    timeframe: str
    confidence: float = 0.0
    entry_price: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    rationale: str = ""
    feature_hash: str = ""

    def __post_init__(self) -> None:
        if not self.signal_id:
            raise ValueError("signal_id is required")
        if not self.strategy:
            raise ValueError("strategy is required")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be within [0,1]")

    def canonical_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["side"] = self.side.value
        for k in ("entry_price", "stop_loss", "take_profit"):
            v = payload[k]
            payload[k] = None if v is None else str(v)
        return payload

    def fingerprint(self) -> str:
        raw = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class OrderIntent:
    intent_id: str
    signal_id: str
    symbol: str
    side: Side
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal | None
    stop_price: Decimal | None
    reduce_only: bool
    created_at_ns: int
    strategy: str
    client_order_id: str

    @classmethod
    def create(
        cls,
        signal: Signal,
        quantity: Decimal,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Decimal | None = None,
        stop_price: Decimal | None = None,
        reduce_only: bool = False,
    ) -> OrderIntent:
        if quantity <= 0:
            raise ValueError("order quantity must be positive")
        cid_seed = f"{signal.strategy}|{signal.signal_id}|{signal.symbol}|{signal.side.value}|{quantity}"
        cid = "stx-" + hashlib.sha256(cid_seed.encode()).hexdigest()[:24]
        return cls(
            intent_id=str(uuid.uuid4()),
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            side=signal.side,
            order_type=order_type,
            quantity=quantity,
            limit_price=limit_price,
            stop_price=stop_price,
            reduce_only=reduce_only,
            created_at_ns=time.time_ns(),
            strategy=signal.strategy,
            client_order_id=cid,
        )


@dataclass(frozen=True, slots=True)
class Fill:
    fill_id: str
    order_id: str
    symbol: str
    side: Side
    quantity: Decimal
    price: Decimal
    fee: Decimal
    fee_asset: str
    timestamp_ns: int
    liquidity: str = "unknown"


@dataclass(slots=True)
class OrderRecord:
    order_id: str
    intent_id: str
    client_order_id: str
    symbol: str
    side: Side
    quantity: Decimal
    filled_quantity: Decimal = Decimal(0)
    avg_fill_price: Decimal | None = None
    status: OrderStatus = OrderStatus.CREATED
    provider_status: str | None = None
    reason: str | None = None
    updated_at_ns: int = field(default_factory=time.time_ns)

    def apply_fill(self, fill: Fill) -> None:
        if fill.order_id != self.order_id:
            raise ValueError("fill order_id mismatch")
        if fill.quantity <= 0 or fill.price <= 0:
            raise ValueError("invalid fill")
        new_qty = self.filled_quantity + fill.quantity
        if new_qty > self.quantity:
            raise ValueError("fill exceeds order quantity")
        old_value = self.filled_quantity * (self.avg_fill_price or Decimal(0))
        new_value = old_value + fill.quantity * fill.price
        self.filled_quantity = new_qty
        self.avg_fill_price = new_value / new_qty
        self.status = OrderStatus.FILLED if new_qty == self.quantity else OrderStatus.PARTIALLY_FILLED
        self.updated_at_ns = fill.timestamp_ns


@dataclass(slots=True)
class Position:
    symbol: str
    side: PositionSide
    quantity: Decimal
    avg_entry_price: Decimal
    realized_pnl: Decimal = Decimal(0)
    fees: Decimal = Decimal(0)

    def mark(self, price: Decimal) -> Decimal:
        if self.side == PositionSide.LONG:
            return (price - self.avg_entry_price) * self.quantity
        return (self.avg_entry_price - price) * self.quantity

    def apply_fill(self, side: Side, qty: Decimal, price: Decimal, fee: Decimal) -> Decimal:
        if qty <= 0 or price <= 0:
            raise ValueError("invalid position fill")
        signed = qty if side == Side.BUY else -qty
        position_signed = self.quantity if self.side == PositionSide.LONG else -self.quantity
        realized = Decimal(0)
        if position_signed == 0 or position_signed * signed > 0:
            combined = abs(position_signed) + abs(signed)
            if position_signed == 0:
                self.side = PositionSide.LONG if signed > 0 else PositionSide.SHORT
                self.quantity = abs(signed)
                self.avg_entry_price = price
            else:
                self.avg_entry_price = (
                    self.avg_entry_price * abs(position_signed) + price * abs(signed)
                ) / combined
                self.quantity = combined
        else:
            closing = min(abs(position_signed), abs(signed))
            if self.side == PositionSide.LONG:
                realized = (price - self.avg_entry_price) * closing
            else:
                realized = (self.avg_entry_price - price) * closing
            self.quantity = abs(position_signed) - closing
            if self.quantity == 0:
                self.avg_entry_price = Decimal(0)
                self.side = PositionSide.LONG if signed > 0 else PositionSide.SHORT
            elif abs(signed) > closing:
                remainder = abs(signed) - closing
                self.side = PositionSide.LONG if signed > 0 else PositionSide.SHORT
                self.quantity = remainder
                self.avg_entry_price = price
        self.realized_pnl += realized - fee
        self.fees += fee
        return realized - fee


@dataclass(frozen=True, slots=True)
class RiskCheck:
    decision: RiskDecision
    reason: str
    proposed_qty: Decimal
    max_qty: Decimal
    notional: Decimal
    risk_amount: Decimal


@dataclass(frozen=True, slots=True)
class StrategyOutcome:
    strategy: str
    signal_id: str
    return_pct: float
    pnl: Decimal
    fees: Decimal
    slippage: Decimal
    holding_seconds: float
    regime: str = "UNKNOWN"


def decimal_from(value: Any) -> Decimal:
    try:
        out = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"invalid decimal: {value!r}") from exc
    if not out.is_finite():
        raise ValueError("decimal must be finite")
    return out
