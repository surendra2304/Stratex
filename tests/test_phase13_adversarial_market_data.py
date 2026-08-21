"""
tests/test_phase13_adversarial_market_data.py
Phase 13.5-13.7: Market data adversarial, data gap, and stale data tests.
"""
import time

import pandas as pd
import pytest

from paper_engine.market_data import DataException, DataStaleException, MarketDataFeed

# ─────────────────────────────────────────────────────────────
# 13.5 — INVALID PRICE / TICK REJECTION
# ─────────────────────────────────────────────────────────────

def _feed():
    f = MarketDataFeed(max_stale_seconds=300)
    # seed one valid tick so timestamps advance
    f.push_tick("BTCUSDT", 50000.0, 49990.0, 50010.0, time.time() - 10)
    return f


@pytest.mark.parametrize("price,bid,ask,label", [
    (float('nan'), 49990.0, 50010.0, "nan_price"),
    (float('inf'), 49990.0, 50010.0, "inf_price"),
    (-1.0,         49990.0, 50010.0, "negative_price"),
    (0.0,          49990.0, 50010.0, "zero_price"),
    (50000.0, float('nan'), 50010.0, "nan_bid"),
    (50000.0, float('inf'), 50010.0, "inf_bid"),
    (50000.0, 50010.0, 49990.0,     "bid_gt_ask"),  # high < low equivalent
    (50000.0, 0.0,     50010.0,     "zero_bid"),
    (50000.0, 49990.0, float('inf'),"inf_ask"),
])
def test_invalid_tick_rejected(price, bid, ask, label):
    """Every malformed tick must raise DataException — never silently accepted."""
    f = MarketDataFeed(max_stale_seconds=300)
    ts = time.time()
    with pytest.raises(DataException):
        f.push_tick("BTCUSDT", price, bid, ask, ts)


def test_duplicate_timestamp_rejected():
    """Duplicate tick timestamps must be rejected."""
    f = MarketDataFeed(max_stale_seconds=300)
    ts = time.time() - 100
    f.push_tick("BTCUSDT", 50000.0, 49990.0, 50010.0, ts)
    with pytest.raises(DataException, match="Out of order or duplicate"):
        f.push_tick("BTCUSDT", 50001.0, 49991.0, 50011.0, ts)


def test_out_of_order_timestamp_rejected():
    """Out-of-order (past) timestamps must be rejected."""
    f = MarketDataFeed(max_stale_seconds=300)
    ts = time.time()
    f.push_tick("BTCUSDT", 50000.0, 49990.0, 50010.0, ts)
    with pytest.raises(DataException, match="Out of order"):
        f.push_tick("BTCUSDT", 50001.0, 49991.0, 50011.0, ts - 5)


def test_future_timestamp_accepted_but_noted():
    """Future timestamps are technically accepted by the feed (exchange clock drift)
    but we verify the feed does NOT reject valid future timestamps as errors."""
    f = MarketDataFeed(max_stale_seconds=300)
    # A slightly future timestamp should not raise — it's normal clock drift
    future_ts = time.time() + 2.0
    f.push_tick("BTCUSDT", 50000.0, 49990.0, 50010.0, future_ts)
    # Feed accepted it — that is the expected behaviour for minor future drift


def test_nan_market_timestamp_rejected():
    """NaN market_timestamp must raise DataException."""
    f = MarketDataFeed(max_stale_seconds=300)
    with pytest.raises(DataException):
        f.push_tick("BTCUSDT", 50000.0, 49990.0, 50010.0, float('nan'))


def test_push_empty_dataframe_rejected():
    """Pushing an empty DataFrame must raise DataException."""
    f = MarketDataFeed(max_stale_seconds=300)
    with pytest.raises(DataException, match="Empty"):
        f.push_candle_df("BTCUSDT", pd.DataFrame())


def test_massive_price_jump_still_ingested():
    """A massive but finite price jump is technically valid data; the feed accepts it.
    Risk controls elsewhere must handle it."""
    f = MarketDataFeed(max_stale_seconds=300)
    ts = time.time() - 100
    f.push_tick("BTCUSDT", 50000.0, 49990.0, 50010.0, ts)
    # Jump of 100x should not be filtered at feed level
    ts2 = time.time()
    f.push_tick("BTCUSDT", 5_000_000.0, 4_999_000.0, 5_001_000.0, ts2)
    assert f.current_prices["BTCUSDT"] == 5_000_000.0


# ─────────────────────────────────────────────────────────────
# 13.6 — DATA GAP TESTING (candle continuity)
# ─────────────────────────────────────────────────────────────

def _make_candle_df(n_candles, interval_secs=60, start_ts=None, gaps=None):
    """Build a synthetic candle DataFrame. gaps is list of indices to skip."""
    if start_ts is None:
        start_ts = time.time() - n_candles * interval_secs
    timestamps = []
    idx = 0
    ts = start_ts
    while idx < n_candles:
        if gaps and idx in gaps:
            ts += interval_secs  # advance but don't add candle
        timestamps.append(pd.Timestamp(ts, unit='s'))
        ts += interval_secs
        idx += 1
    return pd.DataFrame({
        "timestamp": timestamps,
        "open": [50000.0] * n_candles,
        "high": [50100.0] * n_candles,
        "low":  [49900.0] * n_candles,
        "close":[50000.0] * n_candles,
        "volume":[100.0] * n_candles,
    })


def test_candle_df_with_gaps_detectable():
    """Gaps in candle data must be detectable."""
    df = _make_candle_df(100, gaps=[10, 20, 30])
    # Detect gaps: expected 1-minute intervals
    diffs = df['timestamp'].diff().dropna()
    expected = pd.Timedelta(seconds=60)
    # There are 3 doubled gaps
    gaps = (diffs > expected).sum()
    assert gaps == 3, f"Expected 3 gaps, found {gaps}"


def test_single_missing_candle_detected():
    df = _make_candle_df(50, gaps=[25])
    diffs = df['timestamp'].diff().dropna()
    assert (diffs > pd.Timedelta(seconds=60)).sum() == 1


def test_large_gap_detected():
    df = _make_candle_df(20, gaps=[5, 6, 7, 8, 9, 10, 11, 12, 13, 14])
    diffs = df['timestamp'].diff().dropna()
    # All 10 consecutive gaps should be in the diff sequence
    assert (diffs > pd.Timedelta(seconds=60)).sum() >= 1


# ─────────────────────────────────────────────────────────────
# 13.7 — STALE DATA HEALTH TRANSITIONS
# ─────────────────────────────────────────────────────────────

def test_uninitialized_feed_raises_stale():
    """A feed with no ticks must raise DataStaleException on price request."""
    f = MarketDataFeed(max_stale_seconds=60)
    with pytest.raises(DataStaleException, match="uninitialized"):
        f.get_price("BTCUSDT")


def test_fresh_feed_ok():
    f = MarketDataFeed(max_stale_seconds=60)
    f.push_tick("BTCUSDT", 50000.0, 49990.0, 50010.0, time.time() - 1)
    price = f.get_price("BTCUSDT")
    assert price == 50000.0


def test_stale_feed_raises():
    """A feed whose last tick is beyond max_stale_seconds must raise DataStaleException."""
    f = MarketDataFeed(max_stale_seconds=1)
    f.push_tick("BTCUSDT", 50000.0, 49990.0, 50010.0, time.time() - 100)
    # Manually wind back last_received_time
    f.last_received_time = time.time() - 200
    with pytest.raises(DataStaleException, match="stale"):
        f.get_price("BTCUSDT")


def test_health_transitions_via_data_monitor():
    """DataMonitor must transition HEALTHY -> DEGRADED -> CRITICAL."""
    from paper_engine.data_monitor import DataMonitor
    monitor = DataMonitor(degraded_threshold=10, critical_threshold=30, offline_threshold=120)

    # Fresh — HEALTHY
    monitor.record_data_received()
    assert monitor.get_status() == "HEALTHY"

    # Simulate time passing — DEGRADED
    monitor._last_received = time.time() - 15
    assert monitor.get_status() == "DEGRADED"

    # CRITICAL
    monitor._last_received = time.time() - 60
    assert monitor.get_status() == "CRITICAL"

    # OFFLINE
    monitor._last_received = time.time() - 200
    assert monitor.get_status() == "OFFLINE"
