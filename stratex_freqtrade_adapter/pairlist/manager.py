"""stratex_freqtrade_adapter/pairlist/manager.py

Coordinates and chains dynamic pairlist handlers and filters.
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from .interface import IPairList
from .volume_pairlist import VolumePairList
from .filters import PriceFilter, SpreadFilter, VolatilityFilter


class PairListManager:
    """Manages the lifecycle and execution chain of dynamic pairlists."""

    def __init__(self, handlers: Optional[List[IPairList]] = None):
        self.handlers: List[IPairList] = handlers or [
            VolumePairList(number_assets=20),
            PriceFilter(min_price=0.0001, max_price=500_000.0),
            SpreadFilter(max_spread_ratio=0.008),
            VolatilityFilter(min_change_pct=-30.0, max_change_pct=50.0),
        ]

    def add_handler(self, handler: IPairList) -> None:
        self.handlers.append(handler)

    def generate_pairlist(self, ticker_data: Optional[Dict[str, Dict[str, Any]]] = None) -> List[str]:
        """Runs the entire chain of handlers to produce the active trading pairlist."""
        current_pairs: List[str] = []

        # If ticker_data not provided and first handler is VolumePairList, let it fetch
        if ticker_data is None:
            for h in self.handlers:
                if isinstance(h, VolumePairList):
                    ticker_data = h.fetch_tickers()
                    break

        for handler in self.handlers:
            current_pairs = handler.filter_pairlist(current_pairs, ticker_data)

        return current_pairs

    def get_status(self) -> Dict[str, Any]:
        return {
            "handlers_count": len(self.handlers),
            "handler_types": [h.__class__.__name__ for h in self.handlers],
        }
