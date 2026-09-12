"""stratex_freqtrade_adapter/pairlist/interface.py

Abstract base class for Freqtrade-style dynamic PairList handlers and filters.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class IPairList(ABC):
    """Base interface for all pairlist generators and filters."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}

    @abstractmethod
    def filter_pairlist(
        self,
        pairlist: List[str],
        ticker_data: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> List[str]:
        """Filters or generates the list of trading pairs.
        Returns the resulting list of pairs.
        """
        return pairlist
