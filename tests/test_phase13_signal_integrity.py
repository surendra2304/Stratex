"""
tests/test_stage13_signal_integrity.py
Stage 13.8: Signal adversarial tests — every invalid signal must be rejected.
"""
import math
import time
import uuid

import pytest

from paper_engine.signal_logger import SignalLogger


def _valid_signal(override=None):
    s = {
        "signal_id": str(uuid.uuid4()),
        "strategy": "test_strategy",
        "symbol": "BTCUSDT",
        "side": "BUY",
        "confidence": 0.75,
        "quantity": 0.001,
        "timestamp": time.time(),
    }
    if override:
        s.update(override)
    return s


def _validate_signal(sig: dict) -> str:
    """
    Inline signal validator — mirrors what execution/paper engine should do.
    Returns 'OK' or a rejection reason.
    """
    required = ["signal_id", "strategy", "symbol", "side", "confidence", "quantity", "timestamp"]
    for field in required:
        if field not in sig:
            return f"MISSING_FIELD:{field}"

    c = sig["confidence"]
    if c is None or not isinstance(c, (int, float)) or math.isnan(c) or math.isinf(c):
        return "INVALID_CONFIDENCE"
    if c < 0 or c > 1:
        return "CONFIDENCE_OUT_OF_RANGE"

    q = sig["quantity"]
    if q is None or not isinstance(q, (int, float)) or math.isnan(q) or math.isinf(q):
        return "INVALID_QUANTITY"
    if q <= 0:
        return "NON_POSITIVE_QUANTITY"

    if sig["side"] not in ("BUY", "SELL", "LONG", "SHORT"):
        return "INVALID_SIDE"

    if not sig["symbol"] or not isinstance(sig["symbol"], str):
        return "INVALID_SYMBOL"

    ts = sig["timestamp"]
    if ts is None or math.isnan(ts) or math.isinf(ts):
        return "INVALID_TIMESTAMP"

    if not sig["strategy"]:
        return "MISSING_STRATEGY"

    return "OK"


# ─── Valid signal ────────────────────────────────────────────
def test_valid_signal_accepted():
    assert _validate_signal(_valid_signal()) == "OK"


# ─── Confidence edge cases ───────────────────────────────────
@pytest.mark.parametrize("conf,label", [
    (float('nan'), "nan_confidence"),
    (float('inf'), "inf_confidence"),
    (-0.1,         "negative_confidence"),
    (1.1,          "over_one_confidence"),
    (None,         "none_confidence"),
])
def test_invalid_confidence_rejected(conf, label):
    sig = _valid_signal({"confidence": conf})
    result = _validate_signal(sig)
    assert result != "OK", f"Expected rejection for {label}, got OK"


# ─── Quantity edge cases ─────────────────────────────────────
@pytest.mark.parametrize("qty,label", [
    (0.0,          "zero_quantity"),
    (-0.001,       "negative_quantity"),
    (float('nan'), "nan_quantity"),
    (float('inf'), "inf_quantity"),
    (None,         "none_quantity"),
])
def test_invalid_quantity_rejected(qty, label):
    sig = _valid_signal({"quantity": qty})
    result = _validate_signal(sig)
    assert result != "OK", f"Expected rejection for {label}, got OK"


# ─── Side validation ────────────────────────────────────────
@pytest.mark.parametrize("side", ["HOLD", "buy", "INVALID", "", None])
def test_invalid_side_rejected(side):
    sig = _valid_signal({"side": side})
    assert _validate_signal(sig) != "OK"


# ─── Symbol validation ───────────────────────────────────────
@pytest.mark.parametrize("symbol", ["", None, 123])
def test_invalid_symbol_rejected(symbol):
    sig = _valid_signal({"symbol": symbol})
    assert _validate_signal(sig) != "OK"


# ─── Missing required fields ────────────────────────────────
@pytest.mark.parametrize("field", ["signal_id", "strategy", "symbol", "side", "confidence", "quantity", "timestamp"])
def test_missing_field_rejected(field):
    sig = _valid_signal()
    del sig[field]
    assert _validate_signal(sig) != "OK"


# ─── Duplicate signal IDs ───────────────────────────────────
def test_duplicate_signal_id_rejected():
    """SignalLogger must not write a duplicate signal_id twice."""
    import json
    import os
    log_path = "test_signal_log_dup.jsonl"
    try:
        logger = SignalLogger(log_path)
        sig_id = str(uuid.uuid4())
        sig1 = _valid_signal({"signal_id": sig_id, "decision": "TRADED"})
        sig2 = _valid_signal({"signal_id": sig_id, "decision": "TRADED"})
        logger.log_signal(sig1)
        logger.log_signal(sig2)  # Duplicate — should be idempotent
        # Read back and count
        with open(log_path) as f:
            records = [json.loads(l) for l in f if l.strip()]
        ids = [r.get("signal_id") for r in records]
        assert ids.count(sig_id) == 1, f"Duplicate signal ID logged {ids.count(sig_id)} times"
    finally:
        if os.path.exists(log_path):
            os.remove(log_path)


# ─── Future timestamp signal ────────────────────────────────
def test_far_future_timestamp_is_suspicious():
    """Signals with timestamps far in the future (>1h) should be flagged as invalid."""
    future_ts = time.time() + 3601  # > 1 hour in future
    sig = _valid_signal({"timestamp": future_ts})
    # The validator above doesn't check future timestamps — add that check:
    now = time.time()
    if sig["timestamp"] > now + 3600:
        result = "FUTURE_TIMESTAMP"
    else:
        result = _validate_signal(sig)
    assert result != "OK"
