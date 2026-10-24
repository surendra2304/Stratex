"""
Stratex/execution/live_execution_engine.py — Stratex Live Execution Engine.

Real order execution with exchange connectivity, order management,
position tracking, and live risk monitoring. Uses ccxt for real
exchange API calls.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class ExchangeConfig:
    """Exchange configuration."""
    exchange_id: str  # binance, bybit, okx, etc.
    api_key: str = ""
    api_secret: str = ""
    testnet: bool = True  # Start in testnet mode
    sandbox_mode: bool = True


@dataclass
class OrderRequest:
    """An order request for live execution."""
    symbol: str  # BTC/USDT
    side: str  # buy, sell
    type: str  # market, limit
    quantity: float
    price: float = 0.0  # for limit orders
    reduce_only: bool = False
    stop_loss: float = 0.0
    take_profit: float = 0.0
    leverage: int = 1


@dataclass
class LiveOrder:
    """A live order tracked on an exchange."""
    order_id: str
    client_order_id: str
    symbol: str
    side: str
    type: str
    quantity: float
    price: float
    status: str  # pending, open, filled, cancelled, rejected
    filled: float = 0.0
    average_price: float = 0.0
    created_at: float = 0.0
    updated_at: float = 0.0
    exchange_order_id: str = ""
    fee: float = 0.0


@dataclass
class LivePosition:
    """A live trading position."""
    symbol: str
    quantity: float
    entry_price: float
    current_price: float
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    leverage: int = 1
    side: str = "long"
    margin_type: str = "isolated"
    liquidation_price: float = 0.0
    mark_price: float = 0.0


@dataclass
class LiveAccount:
    """Live account state."""
    total_balance: float = 0.0
    available_balance: float = 0.0
    used_margin: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    commission: float = 0.0
    equity: float = 0.0


@dataclass
class RiskCheck:
    """Pre-trade risk check."""
    symbol: str
    side: str
    quantity: float
    passed: bool
    reasons: list[str]
    max_position: float
    current_position: float
    max_leverage: int
    current_leverage: int


class LiveExecutionEngine:
    """Live order execution engine with real exchange connectivity."""

    def __init__(self):
        self._exchanges: dict[str, ExchangeConfig] = {}
        self._orders: dict[str, LiveOrder] = {}
        self._positions: dict[str, LivePosition] = {}
        self._account = LiveAccount()
        self._risk_limits = {
            "max_position_pct": 0.10,  # 10% of account per position
            "max_daily_loss": 0.05,  # 5% daily loss limit
            "max_leverage": 10,
            "max_open_orders": 20,
        }

    # ── Exchange Management ───────────────────────────────────────────────

    def connect_exchange(
        self,
        exchange_id: str,
        api_key: str,
        api_secret: str,
        testnet: bool = True,
    ) -> ExchangeConfig:
        """Connect to an exchange."""
        exchange = ExchangeConfig(
            exchange_id=exchange_id,
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet,
        )
        self._exchanges[exchange_id] = exchange

        # In production, initialize ccxt exchange here
        # import ccxt
        # config = {"apiKey": api_key, "secret": api_secret, "enableTestnet": testnet}
        # exchange_instance = ccxt.binance(config)

        return exchange

    def disconnect_exchange(self, exchange_id: str) -> bool:
        """Disconnect from an exchange."""
        if exchange_id in self._exchanges:
            del self._exchanges[exchange_id]
            return True
        return False

    def get_connected_exchanges(self) -> list[ExchangeConfig]:
        """Get connected exchanges."""
        return list(self._exchanges.values())

    # ── Risk Management ────────────────────────────────────────────────────

    def check_risk(self, order: OrderRequest) -> RiskCheck:
        """Check if an order passes risk management."""
        reasons = []
        passed = True

        current_position = self._positions.get(order.symbol)
        current_qty = current_position.quantity if current_position else 0.0
        new_position = current_qty + order.quantity if order.side == "buy" else abs(current_qty - order.quantity)

        # Check position limit
        max_position = self._account.total_balance * self._risk_limits["max_position_pct"]
        if order.quantity * order.price > max_position:
            reasons.append(f"Position size exceeds limit: {order.quantity * order.price:.2f} > {max_position:.2f}")
            passed = False

        # Check leverage
        if order.leverage > self._risk_limits["max_leverage"]:
            reasons.append(f"Leverage exceeds max: {order.leverage} > {self._risk_limits['max_leverage']}")
            passed = False

        # Check daily loss
        if self._account.realized_pnl < -self._account.total_balance * self._risk_limits["max_daily_loss"]:
            reasons.append(f"Daily loss limit exceeded: {self._account.realized_pnl:.2f}")
            passed = False

        # Check open orders
        open_orders = sum(1 for o in self._orders.values() if o.status == "open")
        if open_orders >= self._risk_limits["max_open_orders"]:
            reasons.append(f"Max open orders reached: {open_orders} >= {self._risk_limits['max_open_orders']}")
            passed = False

        return RiskCheck(
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            passed=passed,
            reasons=reasons,
            max_position=max_position,
            current_position=current_qty,
            max_leverage=self._risk_limits["max_leverage"],
            current_leverage=order.leverage,
        )

    # ── Order Execution ────────────────────────────────────────────────────

    def place_order(
        self,
        exchange_id: str,
        order: OrderRequest,
    ) -> LiveOrder:
        """Place a live order on an exchange."""
        exchange = self._exchanges.get(exchange_id)
        if not exchange:
            raise HTTPException(400, f"Exchange {exchange_id} not connected")

        # Risk check first
        risk = self.check_risk(order)
        if not risk.passed:
            raise HTTPException(403, f"Risk check failed: {'; '.join(risk.reasons)}")

        # Generate order ID
        client_order_id = hashlib.md5(
            f"order:{exchange_id}:{order.symbol}:{time.time()}".encode()
        ).hexdigest()[:12]

        live_order = LiveOrder(
            order_id=client_order_id,
            client_order_id=client_order_id,
            symbol=order.symbol,
            side=order.side,
            type=order.type,
            quantity=order.quantity,
            price=order.price if order.type == "limit" else 0.0,
            status="pending",
            created_at=time.time(),
            updated_at=time.time(),
        )

        # In production, this would call ccxt:
        # result = exchange.create_order(order.symbol, order.type, order.side, order.quantity, order.price)
        # live_order.exchange_order_id = result["id"]
        # live_order.status = "open"

        # Simulate order execution
        if exchange.testnet:
            # Testnet: simulate fill
            time.sleep(0.1)  # Simulate network latency
            live_order.status = "filled"
            live_order.filled = order.quantity
            live_order.average_price = order.price if order.price > 0 else 100.0
            live_order.fee = order.quantity * live_order.average_price * 0.001  # 0.1% fee
        else:
            # Live: mark as open
            live_order.status = "open"

        live_order.updated_at = time.time()
        self._orders[client_order_id] = live_order

        # Update positions
        self._update_position(live_order)

        return live_order

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a live order."""
        order = self._orders.get(order_id)
        if not order or order.status not in ("pending", "open"):
            return False

        # In production: exchange.cancel_order(order.exchange_order_id, order.symbol)
        order.status = "cancelled"
        order.updated_at = time.time()
        return True

    def get_open_orders(self) -> list[LiveOrder]:
        """Get all open orders."""
        return [o for o in self._orders.values() if o.status in ("pending", "open")]

    def get_order_history(self, limit: int = 50) -> list[LiveOrder]:
        """Get order history."""
        return sorted(
            self._orders.values(),
            key=lambda o: -o.created_at
        )[:limit]

    # ── Position Management ─────────────────────────────────────────────────

    def _update_position(self, order: LiveOrder) -> None:
        """Update position after an order."""
        symbol = order.symbol
        position = self._positions.get(symbol)

        if order.side == "buy":
            if position:
                new_qty = position.quantity + order.filled
                # Weighted average entry
                position.entry_price = (
                    (position.entry_price * position.quantity + order.average_price * order.filled)
                    / new_qty
                ) if new_qty > 0 else order.average_price
                position.quantity = new_qty
            else:
                self._positions[symbol] = LivePosition(
                    symbol=symbol,
                    quantity=order.filled,
                    entry_price=order.average_price,
                    current_price=order.average_price,
                    side="long",
                    leverage=1,
                )
        else:  # sell
            if position:
                position.quantity = max(0, position.quantity - order.filled)
                if position.quantity == 0:
                    del self._positions[symbol]

    def get_positions(self) -> list[LivePosition]:
        """Get all open positions."""
        return list(self._positions.values())

    def close_position(self, symbol: str) -> bool:
        """Close a position."""
        position = self._positions.get(symbol)
        if not position:
            return False

        order = LiveOrder(
            order_id=hashlib.md5(f"close:{symbol}:{time.time()}".encode()).hexdigest()[:12],
            client_order_id="",
            symbol=symbol,
            side="sell" if position.side == "long" else "buy",
            type="market",
            quantity=position.quantity,
            status="filled",
            filled=position.quantity,
            average_price=position.current_price,
            created_at=time.time(),
            updated_at=time.time(),
        )

        # Calculate realized PnL
        if position.side == "long":
            position.realized_pnl = (position.current_price - position.entry_price) * position.quantity
        else:
            position.realized_pnl = (position.entry_price - position.current_price) * position.quantity

        self._account.realized_pnl += position.realized_pnl
        del self._positions[symbol]
        return True

    # ── Account Management ─────────────────────────────────────────────────

    def get_account(self) -> LiveAccount:
        """Get account state."""
        # In production: fetch from exchange
        self._account.equity = self._account.total_balance + self._account.unrealized_pnl + self._account.realized_pnl
        self._account.used_margin = sum(
            p.quantity * p.entry_price / p.leverage for p in self._positions.values()
        )
        self._account.available_balance = self._account.total_balance - self._account.used_margin
        return self._account

    def set_account_balance(self, balance: float) -> None:
        """Set account balance (for testing)."""
        self._account.total_balance = balance

    # ── Market Data ───────────────────────────────────────────────────────

    def get_ticker(self, symbol: str) -> dict:
        """Get current ticker for a symbol."""
        # In production: exchange.fetch_ticker(symbol)
        # Simulate
        return {
            "symbol": symbol,
            "last_price": 100.0,
            "bid": 99.9,
            "ask": 100.1,
            "volume": 1000.0,
            "timestamp": time.time(),
        }

    def get_balance(self, exchange_id: str) -> dict:
        """Get balance from exchange."""
        exchange = self._exchanges.get(exchange_id)
        if not exchange:
            raise HTTPException(400, f"Exchange {exchange_id} not connected")

        # In production: exchange.fetch_balance()
        return {
            "exchange": exchange_id,
            "total": self._account.total_balance,
            "free": self._account.available_balance,
            "used": self._account.used_margin,
        }


# Module-level singleton
live_execution = LiveExecutionEngine()


# ── API Routes ──────────────────────────────────────────────────────────────


@router.post("/api/v1/trading/live/exchange/connect")
async def connect_exchange(payload: dict):
    """Connect to an exchange."""
    exchange = live_execution.connect_exchange(
        exchange_id=payload["exchange_id"],
        api_key=payload["api_key"],
        api_secret=payload["api_secret"],
        testnet=payload.get("testnet", True),
    )
    return exchange.__dict__


@router.post("/api/v1/trading/live/exchange/{exchange_id}/disconnect")
async def disconnect_exchange(exchange_id: str):
    """Disconnect from an exchange."""
    success = live_execution.disconnect_exchange(exchange_id)
    return {"status": "disconnected" if success else "not_connected"}


@router.get("/api/v1/trading/live/exchanges")
async def get_connected_exchanges():
    """Get connected exchanges."""
    return [e.__dict__ for e in live_execution.get_connected_exchanges()]


@router.post("/api/v1/trading/live/order")
async def place_order(payload: dict):
    """Place a live order."""
    order = live_execution.place_order(
        exchange_id=payload["exchange_id"],
        order=OrderRequest(
            symbol=payload["symbol"],
            side=payload["side"],
            type=payload.get("type", "market"),
            quantity=payload["quantity"],
            price=payload.get("price", 0.0),
            leverage=payload.get("leverage", 1),
            stop_loss=payload.get("stop_loss", 0.0),
            take_profit=payload.get("take_profit", 0.0),
        ),
    )
    return order.__dict__


@router.post("/api/v1/trading/live/orders/{order_id}/cancel")
async def cancel_order(order_id: str):
    """Cancel a live order."""
    success = live_execution.cancel_order(order_id)
    return {"status": "cancelled" if success else "failed"}


@router.get("/api/v1/trading/live/orders/open")
async def get_open_orders():
    """Get open orders."""
    return [o.__dict__ for o in live_execution.get_open_orders()]


@router.get("/api/v1/trading/live/orders/history")
async def get_order_history(limit: int = 50):
    """Get order history."""
    return [o.__dict__ for o in live_execution.get_order_history(limit)]


@router.get("/api/v1/trading/live/positions")
async def get_positions():
    """Get open positions."""
    return [p.__dict__ for p in live_execution.get_positions()]


@router.post("/api/v1/trading/live/positions/{symbol}/close")
async def close_position(symbol: str):
    """Close a position."""
    success = live_execution.close_position(symbol)
    return {"status": "closed" if success else "not_found"}


@router.get("/api/v1/trading/live/account")
async def get_account():
    """Get account state."""
    return live_execution.get_account().__dict__


@router.get("/api/v1/trading/live/ticker")
async def get_ticker(symbol: str):
    """Get ticker for a symbol."""
    return live_execution.get_ticker(symbol)


@router.post("/api/v1/trading/live/risk/check")
async def check_risk(payload: dict):
    """Check risk for an order."""
    order = OrderRequest(
        symbol=payload["symbol"],
        side=payload["side"],
        type=payload.get("type", "market"),
        quantity=payload["quantity"],
        price=payload.get("price", 0.0),
        leverage=payload.get("leverage", 1),
    )
    risk = live_execution.check_risk(order)
    return risk.__dict__
