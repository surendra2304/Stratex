"""
Stratex/execution/market_microstructure_analyzer.py — Stratex Market Microstructure Analyzer.

Order book analysis, liquidity detection, market impact modeling,
and microstructure-based trading signals.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class OrderBookLevel:
    """A level in an order book."""
    price: float
    size: float
    order_count: int = 0


@dataclass
class OrderBookSnapshot:
    """A snapshot of an order book."""
    symbol: str
    bids: list[OrderBookLevel] = field(default_factory=list)
    asks: list[OrderBookLevel] = field(default_factory=list)
    spread: float = 0.0
    mid_price: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class MicrostructureSignal:
    """A microstructure-based trading signal."""
    signal_id: str
    symbol: str
    signal: str = "neutral"  # bullish, bearish, neutral
    confidence: float = 0.5
    reason: str = ""
    suggested_size_pct: float = 0.0


class MarketMicrostructureAnalyzer:
    """Engine for market microstructure analysis."""

    def __init__(self) -> None:
        self._order_books: dict[str, OrderBookSnapshot] = {}
        self._signals: list[MicrostructureSignal] = []

    def record_order_book(self, symbol: str, bids: list[tuple[float, float]],
                          asks: list[tuple[float, float]]) -> OrderBookSnapshot:
        """Record an order book snapshot."""
        snapshot = OrderBookSnapshot(symbol=symbol)
        snapshot.bids = [OrderBookLevel(price=p, size=s) for p, s in bids]
        snapshot.asks = [OrderBookLevel(price=p, size=s) for p, s in asks]

        if snapshot.bids and snapshot.asks:
            best_bid = max(snapshot.bids, key=lambda b: b.price)
            best_ask = min(snapshot.asks, key=lambda a: a.price)
            snapshot.spread = best_ask.price - best_bid.price
            snapshot.mid_price = (best_bid.price + best_ask.price) / 2

        self._order_books[symbol] = snapshot
        return snapshot

    def analyze(self, symbol: str) -> MicrostructureSignal:
        """Analyze order book microstructure for trading signals."""
        book = self._order_books.get(symbol)
        if not book:
            raise HTTPException(status_code=404, detail="Order book not found")

        signal = MicrostructureSignal(signal_id=f"sig_{uuid.uuid4().hex[:12]}", symbol=symbol)

        if book.bids and book.asks:
            total_bid_size = sum(b.size for b in book.bids)
            total_ask_size = sum(a.size for a in book.asks)

            if total_bid_size > total_ask_size * 1.5:
                signal.signal = "bullish"
                signal.confidence = min(0.9, total_bid_size / max(1, total_ask_size) * 0.5)
                signal.reason = "Bid-heavy order book indicates buying pressure"
                signal.suggested_size_pct = 2.0
            elif total_ask_size > total_bid_size * 1.5:
                signal.signal = "bearish"
                signal.confidence = min(0.9, total_ask_size / max(1, total_bid_size) * 0.5)
                signal.reason = "Ask-heavy order book indicates selling pressure"
                signal.suggested_size_pct = 2.0
            else:
                signal.signal = "neutral"
                signal.confidence = 0.5
                signal.reason = "Balanced order book"

            if book.spread > 0:
                liquidity_score = 1.0 / max(0.01, book.spread)
                if liquidity_score > 100:
                    signal.confidence *= 1.1
                    signal.reason += " High liquidity"

        self._signals.append(signal)
        return signal

    def get_order_book(self, symbol: str) -> dict[str, Any]:
        """Get an order book snapshot."""
        book = self._order_books.get(symbol)
        if not book:
            raise HTTPException(status_code=404, detail="Order book not found")
        return {
            "symbol": book.symbol,
            "spread": book.spread,
            "mid_price": book.mid_price,
            "bid_levels": len(book.bids),
            "ask_levels": len(book.asks),
        }

    def get_report(self) -> dict[str, Any]:
        """Get engine report."""
        return {
            "total_order_books": len(self._order_books),
            "total_signals": len(self._signals),
            "bullish_signals": sum(1 for s in self._signals if s.signal == "bullish"),
            "bearish_signals": sum(1 for s in self._signals if s.signal == "bearish"),
            "engine_status": "operational",
        }


engine = MarketMicrostructureAnalyzer()


@router.post("/trading/microstructure/orderbook")
async def record_order_book(symbol: str, bids: list[tuple[float, float]], asks: list[tuple[float, float]]):
    """Record an order book snapshot."""
    snapshot = engine.record_order_book(symbol, bids, asks)
    return {"symbol": symbol, "spread": snapshot.spread, "mid_price": snapshot.mid_price}


@router.post("/trading/microstructure/analyze")
async def analyze(symbol: str):
    """Analyze order book microstructure."""
    signal = engine.analyze(symbol)
    return {
        "symbol": signal.symbol,
        "signal": signal.signal,
        "confidence": signal.confidence,
        "suggested_size_pct": signal.suggested_size_pct,
    }


@router.get("/trading/microstructure/orderbook/{symbol}")
async def get_order_book(symbol: str):
    """Get an order book snapshot."""
    return engine.get_order_book(symbol)


@router.get("/trading/microstructure/report")
async def microstructure_report():
    """Get microstructure analyzer report."""
    return engine.get_report()
