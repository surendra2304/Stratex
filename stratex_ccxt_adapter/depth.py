"""stratex_ccxt_adapter/depth.py

Order book depth, wall detection, and volume-weighted micro-price analyzer.
100% free mathematical evaluation on public L2 orderbooks.
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from .models import OrderBookDepthAnalysis


class OrderBookAnalyzer:
    """Analyzes raw L2 order books to produce structured liquidity & imbalance metrics."""

    @staticmethod
    def analyze(
        order_book: Dict[str, Any],
        symbol: str = "BTCUSDT",
        exchange: str = "binance",
        depth_levels: int = 15,
    ) -> OrderBookDepthAnalysis:
        bids = order_book.get("bids") or []
        asks = order_book.get("asks") or []

        if not bids or not asks:
            return OrderBookDepthAnalysis(
                symbol=symbol,
                exchange=exchange,
                mid_price=0.0,
                micro_price=0.0,
                bid_depth_usd=0.0,
                ask_depth_usd=0.0,
                imbalance_ratio=0.0,
                spread=0.0,
                spread_bps=0.0,
                top_bids=[],
                top_asks=[],
            )

        best_bid = float(bids[0][0])
        best_bid_vol = float(bids[0][1])
        best_ask = float(asks[0][0])
        best_ask_vol = float(asks[0][1])

        spread = max(0.0, best_ask - best_bid)
        mid_price = (best_bid + best_ask) / 2.0
        spread_bps = (spread / mid_price * 10000.0) if mid_price > 0 else 0.0

        # Micro-price: volume-weighted top of book
        total_top_vol = best_bid_vol + best_ask_vol
        if total_top_vol > 0:
            micro_price = (best_ask * best_bid_vol + best_bid * best_ask_vol) / total_top_vol
        else:
            micro_price = mid_price

        # Calculate cumulative notional depth across top N levels
        bid_depth_usd = sum(float(b[0]) * float(b[1]) for b in bids[:depth_levels])
        ask_depth_usd = sum(float(a[0]) * float(a[1]) for a in asks[:depth_levels])

        total_depth = bid_depth_usd + ask_depth_usd
        if total_depth > 0:
            imbalance = (bid_depth_usd - ask_depth_usd) / total_depth
        else:
            imbalance = 0.0

        return OrderBookDepthAnalysis(
            symbol=symbol,
            exchange=exchange,
            mid_price=round(mid_price, 4),
            micro_price=round(micro_price, 4),
            bid_depth_usd=round(bid_depth_usd, 2),
            ask_depth_usd=round(ask_depth_usd, 2),
            imbalance_ratio=round(imbalance, 4),
            spread=round(spread, 4),
            spread_bps=round(spread_bps, 2),
            top_bids=[[float(b[0]), float(b[1])] for b in bids[:5]],
            top_asks=[[float(a[0]), float(a[1])] for a in asks[:5]],
        )
