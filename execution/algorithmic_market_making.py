"""
Stratex/execution/algorithmic_market_making.py — Stratex Algorithmic Market Making.

Automated market making: quote generation, spread optimization, inventory
management, adverse selection detection, and hedge execution. Provides
real-time quoting strategies for crypto and equity markets.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from flask import Blueprint, jsonify, request

mk_bp = Blueprint("market_making", __name__, url_prefix="/api/v1/trading/market-making")


@dataclass
class Quote:
    """A market quote."""
    symbol: str
    bid: float = 0.0
    ask: float = 0.0
    bid_size: float = 0.0
    ask_size: float = 0.0
    mid: float = 0.0
    spread: float = 0.0
    timestamp: float = 0.0
    source: str = ""


@dataclass
class MarketMakingConfig:
    """Market making configuration."""
    config_id: str
    symbol: str
    spread_pct: float = 0.1  # target spread percentage
    max_inventory: float = 1000.0
    inventory_skew: float = 0.0
    quote_frequency_ms: int = 100
    adverse_selection_threshold: float = 0.5
    hedge_threshold: float = 0.3
    min_quote_size: float = 1.0
    max_quote_size: float = 100.0
    enabled: bool = True


@dataclass
class InventoryPosition:
    """Current inventory position."""
    symbol: str
    quantity: float = 0.0
    avg_cost: float = 0.0
    market_value: float = 0.0
    pnl: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    last_updated: float = 0.0


@dataclass
class AdverseSelectionEvent:
    """An adverse selection event."""
    event_id: str
    symbol: str
    trigger: str = ""  # smart_order_book, imbalance, news, volatility_spike
    severity: str = "medium"
    description: str = ""
    detected_at: float = 0.0
    quote_suspended: bool = False


@dataclass
class HedgeOrder:
    """A hedge order."""
    hedge_id: str
    symbol: str
    quantity: float = 0.0
    side: str = "buy"  # buy, sell
    hedge_type: str = "offset"  # offset, dynamic, static
    executed: bool = False
    fill_price: float = 0.0
    created_at: float = 0.0
    reason: str = ""


@dataclass
class QuoteStrategy:
    """A quoting strategy."""
    strategy_id: str
    symbol: str
    strategy_name: str = "symmetric"  # symmetric, asymmetric, skewed, aggressive, passive
    parameters: dict = field(default_factory=dict)
    enabled: bool = True
    last_quote: Quote | None = None


@dataclass
class MarketMakingStats:
    """Market making statistics."""
    symbol: str
    total_quotes: int = 0
    total_fills: int = 0
    avg_spread_captured: float = 0.0
    inventory_turnover: float = 0.0
    adverse_selection_count: int = 0
    hedge_count: int = 0
    pnl_total: float = 0.0
    pnl_unrealized: float = 0.0
    sharpe_ratio: float = 0.0


class AlgorithmicMarketMaker:
    """Algorithmic market making engine."""

    def __init__(self):
        self._quotes: list[Quote] = []
        self._configs: dict[str, MarketMakingConfig] = {}
        self._inventory: dict[str, InventoryPosition] = {}
        self._adverse_events: list[AdverseSelectionEvent] = []
        self._hedge_orders: list[HedgeOrder] = []
        self._strategies: dict[str, QuoteStrategy] = {}
        self._stats: dict[str, MarketMakingStats] = {}

    def configure(
        self,
        symbol: str,
        spread_pct: float = 0.1,
        max_inventory: float = 1000.0,
        quote_frequency_ms: int = 100,
    ) -> MarketMakingConfig:
        """Configure market making for a symbol."""
        config = MarketMakingConfig(
            config_id=hashlib.md5(
                f"mk_config:{symbol}:{time.time()}".encode()
            ).hexdigest()[:12],
            symbol=symbol,
            spread_pct=spread_pct,
            max_inventory=max_inventory,
            quote_frequency_ms=quote_frequency_ms,
            enabled=True,
        )
        self._configs[symbol] = config

        # Initialize inventory
        self._inventory[symbol] = InventoryPosition(
            symbol=symbol,
            last_updated=time.time(),
        )

        # Initialize stats
        self._stats[symbol] = MarketMakingStats(symbol=symbol)

        return config

    def generate_quote(
        self,
        symbol: str,
        mid_price: float,
        spread_bps: float = 10.0,
    ) -> Quote:
        """Generate a market making quote."""
        config = self._configs.get(symbol)
        if not config:
            raise Exception(f"Market making not configured for {symbol}")

        spread = mid_price * (spread_bps / 10000)

        # Apply inventory skew
        skew = 0.0
        if symbol in self._inventory:
            inv = self._inventory[symbol]
            skew = config.inventory_skew * (inv.quantity / config.max_inventory)

        bid = mid_price - spread / 2 + skew * mid_price * 0.001
        ask = mid_price + spread / 2 + skew * mid_price * 0.001

        quote = Quote(
            symbol=symbol,
            bid=bid,
            ask=ask,
            bid_size=config.min_quote_size,
            ask_size=config.min_quote_size,
            mid=mid_price,
            spread=spread,
            timestamp=time.time(),
            source="algorithmic",
        )

        self._quotes.append(quote)
        self._stats[symbol].total_quotes += 1

        # Update strategy
        strategy = self._strategies.get(symbol)
        if not strategy:
            strategy = QuoteStrategy(
                strategy_id=hashlib.md5(f"strategy:{symbol}").hexdigest()[:12],
                symbol=symbol,
            )
            self._strategies[symbol] = strategy
        strategy.last_quote = quote

        return quote

    def update_inventory(
        self,
        symbol: str,
        quantity: float,
        price: float,
    ) -> InventoryPosition:
        """Update inventory after a fill."""
        if symbol not in self._inventory:
            self._inventory[symbol] = InventoryPosition(symbol=symbol)

        inv = self._inventory[symbol]
        new_qty = inv.quantity + quantity

        if inv.quantity > 0 and quantity < 0:
            # Reducing position
            realized = abs(quantity) * price - abs(quantity) * inv.avg_cost
            inv.realized_pnl += realized
            new_qty = abs(inv.quantity - abs(quantity))
        elif inv.quantity < 0 and quantity > 0:
            realized = inv.avg_cost * quantity - price * quantity
            inv.realized_pnl += realized
            new_qty = inv.quantity + quantity
        else:
            # Increasing or flipping position
            if abs(quantity) > abs(inv.quantity):
                inv.avg_cost = price

        inv.quantity = new_qty
        inv.market_value = new_qty * price
        inv.unrealized_pnl = (price - inv.avg_cost) * new_qty if new_qty > 0 else (inv.avg_cost - price) * abs(new_qty)
        inv.pnl = inv.realized_pnl + inv.unrealized_pnl
        inv.last_updated = time.time()

        self._inventory[symbol] = inv
        return inv

    def detect_adverse_selection(
        self,
        symbol: str,
        trigger: str,
        description: str = "",
    ) -> AdverseSelectionEvent:
        """Detect adverse selection."""
        event = AdverseSelectionEvent(
            event_id=hashlib.md5(
                f"adverse:{symbol}:{trigger}:{time.time()}".encode()
            ).hexdigest()[:12],
            symbol=symbol,
            trigger=trigger,
            severity="high" if "news" in trigger or "volatility" in trigger else "medium",
            description=description,
            detected_at=time.time(),
            quote_suspended=True,
        )

        self._adverse_events.append(event)

        # Update stats
        if symbol in self._stats:
            self._stats[symbol].adverse_selection_count += 1

        return event

    def execute_hedge(
        self,
        symbol: str,
        quantity: float,
        hedge_type: str = "offset",
    ) -> HedgeOrder:
        """Execute a hedge order."""
        inv = self._inventory.get(symbol)
        if not inv:
            raise Exception(f"No inventory for {symbol}")

        # Determine side: offset means opposite to inventory
        if inv.quantity > 0:
            side = "sell"
        else:
            side = "buy"

        hedge = HedgeOrder(
            hedge_id=hashlib.md5(
                f"hedge:{symbol}:{time.time()}".encode()
            ).hexdigest()[:12],
            symbol=symbol,
            quantity=abs(quantity),
            side=side,
            hedge_type=hedge_type,
            executed=True,
            fill_price=inv.avg_cost * 1.001,
            created_at=time.time(),
            reason=f"Offset inventory of {inv.quantity} {symbol}",
        )

        self._hedge_orders.append(hedge)

        # Update stats
        if symbol in self._stats:
            self._stats[symbol].hedge_count += 1

        return hedge

    def get_stats(self, symbol: str) -> MarketMakingStats:
        """Get market making statistics."""
        stats = self._stats.get(symbol)
        if not stats:
            stats = MarketMakingStats(symbol=symbol)
            self._stats[symbol] = stats

        # Calculate Sharpe ratio
        if stats.total_fills > 0:
            fills = min(stats.total_fills, 50)
            avg_pnl = stats.pnl_total / max(1, fills)
            variance = avg_pnl ** 2 if avg_pnl != 0 else 0.01
            stats.sharpe_ratio = avg_pnl / max(0.01, variance ** 0.5) * 10

        return stats

    def get_report(self) -> dict:
        """Get market making report."""
        return {
            "total_symbols": len(self._configs),
            "total_quotes": len(self._quotes),
            "total_adverse_events": len(self._adverse_events),
            "total_hedge_orders": len(self._hedge_orders),
            "total_strategies": len(self._strategies),
            "recent_quotes": [
                {
                    "symbol": q.symbol,
                    "bid": round(q.bid, 2),
                    "ask": round(q.ask, 2),
                    "spread": round(q.spread, 2),
                }
                for q in self._quotes[-10:]
            ],
            "inventory_summary": [
                {
                    "symbol": inv.symbol,
                    "quantity": inv.quantity,
                    "market_value": inv.market_value,
                    "pnl": inv.pnl,
                }
                for inv in self._inventory.values()
            ],
            "stats_summary": [
                {
                    "symbol": s.symbol,
                    "total_quotes": s.total_quotes,
                    "total_fills": s.total_fills,
                    "pnl_total": s.pnl_total,
                    "adverse_selection_count": s.adverse_selection_count,
                    "sharpe_ratio": s.sharpe_ratio,
                }
                for s in self._stats.values()
            ],
            "recent_adverse_events": [
                {
                    "symbol": e.symbol,
                    "trigger": e.trigger,
                    "severity": e.severity,
                    "quote_suspended": e.quote_suspended,
                }
                for e in self._adverse_events[-5:]
            ],
        }


# Module-level singleton
market_maker = AlgorithmicMarketMaker()


# ── API Routes ──────────────────────────────────────────────────────────────


@mk_bp.route("/config", methods=["POST"])
def configure():
    """Configure market making."""
    data = request.get_json() or {}
    config = market_maker.configure(
        symbol=data["symbol"],
        spread_pct=data.get("spread_pct", 0.1),
        max_inventory=data.get("max_inventory", 1000.0),
        quote_frequency_ms=data.get("quote_frequency_ms", 100),
    )
    return jsonify({"config_id": config.config_id, "symbol": config.symbol, "enabled": config.enabled})


@mk_bp.route("/quote", methods=["POST"])
def generate_quote():
    """Generate a quote."""
    data = request.get_json() or {}
    quote = market_maker.generate_quote(
        symbol=data["symbol"],
        mid_price=data["mid_price"],
        spread_bps=data.get("spread_bps", 10.0),
    )
    return jsonify({
        "symbol": quote.symbol,
        "bid": quote.bid,
        "ask": quote.ask,
        "spread": quote.spread,
        "mid": quote.mid,
    })


@mk_bp.route("/inventory", methods=["POST"])
def update_inventory():
    """Update inventory after fill."""
    data = request.get_json() or {}
    inv = market_maker.update_inventory(
        symbol=data["symbol"],
        quantity=data["quantity"],
        price=data["price"],
    )
    return jsonify({
        "symbol": inv.symbol,
        "quantity": inv.quantity,
        "pnl": inv.pnl,
        "realized_pnl": inv.realized_pnl,
        "unrealized_pnl": inv.unrealized_pnl,
    })


@mk_bp.route("/adverse-selection", methods=["POST"])
def detect_adverse_selection():
    """Detect adverse selection."""
    data = request.get_json() or {}
    event = market_maker.detect_adverse_selection(
        symbol=data["symbol"],
        trigger=data["trigger"],
        description=data.get("description", ""),
    )
    return jsonify({"event_id": event.event_id, "symbol": event.symbol, "severity": event.severity, "quote_suspended": event.quote_suspended})


@mk_bp.route("/hedge", methods=["POST"])
def execute_hedge():
    """Execute a hedge."""
    data = request.get_json() or {}
    hedge = market_maker.execute_hedge(
        symbol=data["symbol"],
        quantity=data["quantity"],
        hedge_type=data.get("hedge_type", "offset"),
    )
    return jsonify({"hedge_id": hedge.hedge_id, "side": hedge.side, "quantity": hedge.quantity, "executed": hedge.executed})


@mk_bp.route("/report", methods=["GET"])
def get_report():
    """Get market making report."""
    return jsonify(market_maker.get_report())
