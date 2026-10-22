"""
stratex_openbb/providers/sentiment_free.py — Free Sentiment Data Provider.
Fetches the Crypto Fear & Greed Index from the public Alternative.me API without credentials.
"""

import datetime
import json
import logging
import threading
import time
from typing import Optional

import requests

from stratex_openbb.models import SentimentReading

logger = logging.getLogger("openbb.sentiment")

_CACHE_TTL_SECONDS = 900  # 15 minutes
_ENDPOINT = "https://api.alternative.me/fng/?limit=5"


class FreeSentimentProvider:
    """100% Free Public Sentiment Provider (Alternative.me Fear & Greed)."""

    def __init__(self, timeout: float = 5.0):
        self.timeout = timeout
        self._lock = threading.Lock()
        self._cached_reading: Optional[SentimentReading] = None
        self._cached_at: float = 0.0

    def get_fear_and_greed(self, force_refresh: bool = False) -> SentimentReading:
        """
        Returns the latest Crypto Fear & Greed index reading.
        Uses in-memory cache if within TTL, and falls back gracefully on network errors.
        """
        now = time.time()
        with self._lock:
            if not force_refresh and self._cached_reading and (now - self._cached_at < _CACHE_TTL_SECONDS):
                return self._cached_reading

        # Fetch from public API
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Stratex-OpenBB/1.0"}
            resp = requests.get(_ENDPOINT, headers=headers, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("data", [])
                if items:
                    latest = items[0]
                    score = int(latest.get("value", 50))
                    classification = str(latest.get("value_classification", "Neutral"))
                    ts_epoch = int(latest.get("timestamp", int(now)))
                    ts_iso = datetime.datetime.fromtimestamp(ts_epoch, datetime.timezone.utc).isoformat()

                    reading = SentimentReading(
                        score=score,
                        classification=classification,
                        timestamp=ts_iso,
                        source="Alternative.me (Public Free API)"
                    )
                    with self._lock:
                        self._cached_reading = reading
                        self._cached_at = now
                    logger.debug(f"[OPENBB_SENTIMENT] Updated Fear & Greed: {score} ({classification})")
                    return reading
        except Exception as e:
            logger.warning(f"[OPENBB_SENTIMENT] Error fetching Fear & Greed from public API: {e}")

        # Graceful fallback: return existing cache or default neutral
        with self._lock:
            if self._cached_reading:
                return self._cached_reading

        fallback = SentimentReading(
            score=50,
            classification="Neutral",
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            source="Offline Fallback"
        )
        return fallback
