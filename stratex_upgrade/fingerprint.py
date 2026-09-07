from __future__ import annotations

import hashlib
import json
from typing import Any


def stable_fingerprint(payload: dict[str, Any]) -> str:
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(normalized.encode()).hexdigest()


def signal_fingerprint(
    strategy: str,
    symbol: str,
    timeframe: str,
    candle_close_timestamp: int,
    side: str,
    feature_hash: str = "",
) -> str:
    return stable_fingerprint({
        "strategy": strategy,
        "symbol": symbol,
        "timeframe": timeframe,
        "candle_close_timestamp": candle_close_timestamp,
        "side": side,
        "feature_hash": feature_hash,
    })


def feature_hash(features: dict[str, Any]) -> str:
    safe = {k: features[k] for k in sorted(features)}
    return stable_fingerprint(safe)[:24]
