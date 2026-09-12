"""stratex_freqtrade_adapter/pairlist/static_pairlist.py

Static whitelist/blacklist pairlist handler.
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from .interface import IPairList


class StaticPairList(IPairList):
    """Generates a predefined static pairlist, excluding blacklisted pairs."""

    def __init__(
        self,
        pairs: Optional[List[str]] = None,
        blacklist: Optional[List[str]] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(config=config)
        self.pairs = list(pairs or [
            "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "ADAUSDT",
            "XRPUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "NEARUSDT"
        ])
        self.blacklist = set(blacklist or [])

    def filter_pairlist(
        self,
        pairlist: List[str],
        ticker_data: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> List[str]:
        # If upstream gave us pairs, filter them; otherwise use our base list
        source = pairlist if pairlist else self.pairs
        return [p for p in source if p not in self.blacklist]
