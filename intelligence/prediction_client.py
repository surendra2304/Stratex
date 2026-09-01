"""
intelligence/prediction_client.py — Predictive Intelligence Client & In-Memory TTL Cache.

Fetches deep learning / LLM multi-horizon directional forecasts from AI-Universe.
Constraints:
- Predictions are advisory inputs only.
- In-memory cache with 15-minute TTL to prevent stale signals.
- Graceful soft fallback if AI-Universe prediction endpoint is offline or times out.
"""

import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import requests

from logger import get_logger

logger = get_logger("prediction_client")


@dataclass
class AssetPrediction:
    symbol: str
    direction: str  # "BULLISH", "BEARISH", "NEUTRAL"
    confidence: float  # 0.0 to 1.0
    horizon_minutes: int  # e.g. 15, 60, 240
    target_price_change_pct: float
    timestamp: float = field(default_factory=time.time)
    expires_at: float = 0.0

    def is_valid(self) -> bool:
        return time.time() < self.expires_at


class PredictionClient:
    """
    Client for fetching and caching predictive intelligence from AI-Universe.
    """

    def __init__(
        self,
        base_url: str | None = None,
        cache_ttl_seconds: int = 900,  # 15 minutes
        timeout_seconds: int = 5
    ):
        self.base_url = (os.getenv("INFERENCE_URL") or os.getenv("AI_UNIVERSE_URL") or base_url or "https://inference-3i2b.onrender.com").rstrip("/")
        self.cache_ttl_seconds = cache_ttl_seconds
        self.timeout_seconds = timeout_seconds
        self.cache: dict[str, AssetPrediction] = {}
        self.history: list[dict[str, Any]] = []

    def get_prediction(self, symbol: str) -> AssetPrediction | None:
        """Returns cached prediction if valid, otherwise fetches fresh from AI-Universe."""
        cached = self.cache.get(symbol)
        if cached and cached.is_valid():
            return cached

        return self.fetch_prediction(symbol)

    def fetch_prediction(self, symbol: str) -> AssetPrediction | None:
        """Polls prediction endpoint with graceful degradation."""
        endpoint = f"{self.base_url}/v1/trading/predictions/{symbol}"
        now = time.time()

        try:
            resp = requests.get(endpoint, timeout=self.timeout_seconds)
            if resp.status_code == 200:
                data = resp.json()
                pred = AssetPrediction(
                    symbol=symbol,
                    direction=data.get("direction", "NEUTRAL").upper(),
                    confidence=float(data.get("confidence", 0.5)),
                    horizon_minutes=int(data.get("horizon_minutes", 60)),
                    target_price_change_pct=float(data.get("target_price_change_pct", 0.0)),
                    timestamp=now,
                    expires_at=now + self.cache_ttl_seconds
                )
                self.cache[symbol] = pred
                self.history.append(asdict(pred))
                return pred
        except Exception as e:
            logger.debug(f"[PREDICTION_CLIENT] Could not fetch prediction for {symbol}: {e}")

        # Fallback heuristic prediction when offline
        fallback_pred = AssetPrediction(
            symbol=symbol,
            direction="NEUTRAL",
            confidence=0.50,
            horizon_minutes=60,
            target_price_change_pct=0.0,
            timestamp=now,
            expires_at=now + self.cache_ttl_seconds
        )
        self.cache[symbol] = fallback_pred
        return fallback_pred

    def get_all_cached_predictions(self) -> dict[str, dict[str, Any]]:
        """Returns snapshot of current cached predictions."""
        return {
            sym: asdict(pred) for sym, pred in self.cache.items() if pred.is_valid()
        }
