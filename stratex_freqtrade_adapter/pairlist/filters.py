"""stratex_freqtrade_adapter/pairlist/filters.py

Filters for pairlists:
- PriceFilter: filters out dust/sub-cent or excessively expensive tokens
- SpreadFilter: filters out wide bid-ask spreads
- VolatilityFilter: filters out dead or violently unstable coins
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from .interface import IPairList


class PriceFilter(IPairList):
    """Filters pairs based on current price bounds."""

    def __init__(
        self,
        min_price: float = 0.0001,
        max_price: float = 1_000_000.0,
        config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(config=config)
        self.min_price = float(min_price)
        self.max_price = float(max_price)

    def filter_pairlist(
        self,
        pairlist: List[str],
        ticker_data: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> List[str]:
        if not ticker_data:
            return pairlist

        filtered = []
        for p in pairlist:
            t = ticker_data.get(p)
            if not t:
                filtered.append(p)
                continue
            price = float(t.get("lastPrice", 0.0))
            if self.min_price <= price <= self.max_price:
                filtered.append(p)
        return filtered


class SpreadFilter(IPairList):
    """Filters out pairs where the bid/ask spread ratio exceeds max_spread_ratio."""

    def __init__(
        self,
        max_spread_ratio: float = 0.005,  # 0.5% max spread
        config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(config=config)
        self.max_spread_ratio = float(max_spread_ratio)

    def filter_pairlist(
        self,
        pairlist: List[str],
        ticker_data: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> List[str]:
        if not ticker_data:
            return pairlist

        filtered = []
        for p in pairlist:
            t = ticker_data.get(p)
            if not t:
                filtered.append(p)
                continue
            bid = float(t.get("bidPrice", 0.0))
            ask = float(t.get("askPrice", 0.0))
            if ask > 0 and bid > 0:
                spread_ratio = (ask - bid) / ask
                if spread_ratio <= self.max_spread_ratio:
                    filtered.append(p)
            else:
                filtered.append(p)
        return filtered


class VolatilityFilter(IPairList):
    """Filters out pairs with price change percentage exceeding bounds."""

    def __init__(
        self,
        min_change_pct: float = -25.0,
        max_change_pct: float = 35.0,
        config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(config=config)
        self.min_change_pct = float(min_change_pct)
        self.max_change_pct = float(max_change_pct)

    def filter_pairlist(
        self,
        pairlist: List[str],
        ticker_data: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> List[str]:
        if not ticker_data:
            return pairlist

        filtered = []
        for p in pairlist:
            t = ticker_data.get(p)
            if not t:
                filtered.append(p)
                continue
            change = float(t.get("priceChangePercent", 0.0))
            if self.min_change_pct <= change <= self.max_change_pct:
                filtered.append(p)
        return filtered
