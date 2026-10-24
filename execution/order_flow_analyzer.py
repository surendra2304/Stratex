"""
Stratex/execution/order_flow_analyzer.py — Stratex Order Flow Analyzer.

Order flow analysis, footprint charts, imbalance detection, trade clustering,
and flow prediction.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class OrderFlowRecord:
    """A single order flow record."""
    record_id: str
    symbol: str
    price: float
    size: float
    side: str  # buy, sell
    order_type: str  # market, limit, stop
    timestamp: float = field(default_factory=time.time)


@dataclass
class FootprintLevel:
    """A footprint chart level."""
    price: float
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    imbalance: float = 0.0  # -1.0 to 1.0
    delta: float = 0.0


@dataclass
class FlowAnalysis:
    """Result of order flow analysis."""
    analysis_id: str
    symbol: str
    footprint: list[FootprintLevel] = field(default_factory=list)
    total_volume: float = 0.0
    net_delta: float = 0.0
    imbalance_ratio: float = 0.0
    clusters: list[dict] = field(default_factory=list)
    prediction: str = ""


class OrderFlowAnalyzer:
    """Engine for order flow analysis and prediction."""

    def __init__(self) -> None:
        self._records: list[OrderFlowRecord] = []
        self._analyses: list[FlowAnalysis] = []

    def record_order(self, symbol: str, price: float, size: float, side: str,
                     order_type: str = "market") -> str:
        """Record an order."""
        record_id = f"of_{uuid.uuid4().hex[:12]}"
        record = OrderFlowRecord(
            record_id=record_id, symbol=symbol, price=price,
            size=size, side=side, order_type=order_type,
        )
        self._records.append(record)
        return record_id

    def analyze_flow(self, symbol: str) -> FlowAnalysis:
        """Analyze order flow for a symbol."""
        symbol_records = [r for r in self._records if r.symbol == symbol]
        analysis_id = f"ofa_{uuid.uuid4().hex[:12]}"
        analysis = FlowAnalysis(analysis_id=analysis_id, symbol=symbol)

        if not symbol_records:
            return analysis

        # Build footprint by price level
        price_levels: dict[float, list[OrderFlowRecord]] = {}
        for r in symbol_records:
            price_levels.setdefault(round(r.price, 2), []).append(r)

        total_buy = sum(r.size for r in symbol_records if r.side == "buy")
        total_sell = sum(r.size for r in symbol_records if r.side == "sell")
        analysis.total_volume = total_buy + total_sell
        analysis.net_delta = total_buy - total_sell
        analysis.imbalance_ratio = (total_buy - total_sell) / max(1, analysis.total_volume)

        for price, records in sorted(price_levels.items()):
            buy_vol = sum(r.size for r in records if r.side == "buy")
            sell_vol = sum(r.size for r in records if r.side == "sell")
            level = FootprintLevel(
                price=price,
                buy_volume=buy_vol,
                sell_volume=sell_vol,
                imbalance=(buy_vol - sell_vol) / max(1, buy_vol + sell_vol),
                delta=buy_vol - sell_vol,
            )
            analysis.footprint.append(level)

        # Detect clusters
        large_levels = [f for f in analysis.footprint if abs(f.delta) > analysis.total_volume * 0.1]
        for fl in large_levels:
            analysis.clusters.append({
                "price": fl.price,
                "delta": fl.delta,
                "imbalance": fl.imbalance,
            })

        # Generate prediction
        if analysis.imbalance_ratio > 0.3:
            analysis.prediction = "Bullish order flow — expect upward pressure"
        elif analysis.imbalance_ratio < -0.3:
            analysis.prediction = "Bearish order flow — expect downward pressure"
        else:
            analysis.prediction = "Neutral order flow — range-bound expected"

        self._analyses.append(analysis)
        return analysis

    def get_analyses(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Get flow analyses."""
        analyses = list(self._analyses)
        if symbol:
            analyses = [a for a in analyses if a.symbol == symbol]
        return [
            {
                "analysis_id": a.analysis_id,
                "symbol": a.symbol,
                "total_volume": a.total_volume,
                "net_delta": a.net_delta,
                "imbalance_ratio": a.imbalance_ratio,
                "prediction": a.prediction,
                "cluster_count": len(a.clusters),
            }
            for a in analyses
        ]

    def get_report(self) -> dict[str, Any]:
        """Get engine report."""
        return {
            "total_records": len(self._records),
            "total_analyses": len(self._analyses),
            "engine_status": "operational",
        }


engine = OrderFlowAnalyzer()


@router.post("/trading/orderflow/record")
async def record_order(symbol: str, price: float, size: float, side: str, order_type: str = "market"):
    """Record an order for flow analysis."""
    record_id = engine.record_order(symbol, price, size, side, order_type)
    return {"record_id": record_id, "status": "recorded"}


@router.post("/trading/orderflow/analyze")
async def analyze_flow(symbol: str):
    """Analyze order flow for a symbol."""
    analysis = engine.analyze_flow(symbol)
    return {
        "analysis_id": analysis.analysis_id,
        "total_volume": analysis.total_volume,
        "net_delta": analysis.net_delta,
        "imbalance_ratio": analysis.imbalance_ratio,
        "prediction": analysis.prediction,
        "clusters": analysis.clusters[:5],
    }


@router.get("/trading/orderflow/analyses")
async def list_analyses(symbol: str | None = None):
    """List flow analyses."""
    return {"analyses": engine.get_analyses(symbol)}


@router.get("/trading/orderflow/report")
async def orderflow_report():
    """Get order flow report."""
    return engine.get_report()
