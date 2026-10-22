"""Item 9 regression tests — malformed market data through the indicator /
strategy / signal path.

NaN / inf / zero prices, high < low, negative volume, duplicate and
out-of-order bars, stale or future feeds and short or flat history must be
rejected or neutralized before a strategy can act on them — and never be
"repaired" into fabricated candles.
"""

from __future__ import annotations

import math
import time
from decimal import Decimal
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from market_data_quality import (
    DEGRADED,
    OK,
    UNUSABLE,
    interval_to_timedelta,
    sanitize_ohlcv,
    validate_kline_values,
)
from numeric_safety import signal_levels_valid


def _frame(n=60, start="2026-01-01", freq="15min", tz=None, seed=3):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({
        "timestamp": pd.date_range(start, periods=n, freq=freq, tz=tz),
        "open": open_,
        "high": np.maximum(open_, close) * 1.002,
        "low": np.minimum(open_, close) * 0.998,
        "close": close,
        "volume": rng.uniform(1, 50, n),
    })


# ── sanitize_ohlcv ──────────────────────────────────────────────────────────

def test_clean_frame_is_ok_and_dtype_preserved():
    for tz in (None, "UTC"):
        out, report = sanitize_ohlcv(_frame(tz=tz), interval="15m")
        assert report.status == OK and report.reasons == []
        assert len(out) == 60
        assert (out["timestamp"].dt.tz is None) == (tz is None)


@pytest.mark.parametrize(
    "column,value,counter",
    [
        ("close", np.nan, "dropped_non_finite"),
        ("high", np.inf, "dropped_non_finite"),
        ("low", 0.0, "dropped_non_positive"),
        ("open", -3.0, "dropped_non_positive"),
        ("volume", -1.0, "dropped_negative_volume"),
        ("volume", np.nan, "dropped_non_finite"),
    ],
)
def test_invalid_historical_row_is_dropped_not_repaired(column, value, counter):
    df = _frame(n=100)
    df.loc[40, column] = value
    out, report = sanitize_ohlcv(df, interval="15m")
    assert getattr(report, counter) == 1
    assert report.status == DEGRADED and report.usable
    assert len(out) == 99
    assert df.loc[40, "timestamp"] not in set(out["timestamp"])


def test_high_below_low_is_inconsistent():
    df = _frame(n=100)
    df.loc[10, ["high", "low"]] = [90.0, 110.0]
    df.loc[20, "high"] = df.loc[20, "close"] * 0.9  # high below close
    out, report = sanitize_ohlcv(df)
    assert report.dropped_inconsistent == 2 and len(out) == 98


def test_invalid_newest_bar_makes_the_frame_unusable():
    df = _frame(n=200)
    df.loc[199, "close"] = np.nan
    out, report = sanitize_ohlcv(df, interval="15m")
    assert report.last_row_dropped and report.status == UNUSABLE
    assert "newest bar invalid" in report.reasons[0]


def test_too_many_invalid_rows_make_the_frame_unusable():
    df = _frame(n=100)
    df.loc[10:20, "close"] = np.nan
    assert sanitize_ohlcv(df)[1].status == UNUSABLE


def test_exact_duplicates_collapse_silently_conflicts_keep_latest_revision():
    df = _frame(n=50)
    with_exact = pd.concat([df, df.iloc[[10, 11]]], ignore_index=True)
    out, report = sanitize_ohlcv(with_exact)
    assert report.status == OK and report.exact_duplicates == 2 and len(out) == 50

    revised = df.iloc[[30]].copy()
    revised["close"] = revised["close"] * 1.001
    revised["high"] = revised[["high", "close"]].max(axis=1)
    out, report = sanitize_ohlcv(pd.concat([df, revised], ignore_index=True))
    assert report.conflicting_duplicates == 1 and report.status == DEGRADED
    assert out.loc[out["timestamp"] == df.loc[30, "timestamp"], "close"].item() == revised["close"].item()


def test_out_of_order_rows_are_sorted_and_reported():
    df = _frame(n=50).sample(frac=1.0, random_state=7).reset_index(drop=True)
    out, report = sanitize_ohlcv(df)
    assert out["timestamp"].is_monotonic_increasing
    assert report.out_of_order > 0 and report.status == DEGRADED


def test_gaps_are_counted_with_the_interval():
    df = _frame(n=50).drop(index=[10, 11, 30]).reset_index(drop=True)
    assert sanitize_ohlcv(df, interval="15m")[1].gaps == 2


def test_stale_and_future_feeds_are_unusable():
    df = _frame(n=50, start="2026-01-01")
    last_open = df["timestamp"].iloc[-1]
    fresh_now = last_open + pd.Timedelta(minutes=20)
    assert sanitize_ohlcv(df, interval="15m", now=fresh_now)[1].status == OK
    stale = sanitize_ohlcv(df, interval="15m", now=last_open + pd.Timedelta(hours=3))[1]
    assert stale.status == UNUSABLE and any("stale feed" in r for r in stale.reasons)
    future = sanitize_ohlcv(df, interval="15m", now=last_open - pd.Timedelta(hours=1))[1]
    assert future.status == UNUSABLE and any("future" in r for r in future.reasons)


def test_epoch_millisecond_strings_and_missing_columns():
    df = _frame(n=30)
    df["timestamp"] = (df["timestamp"].astype("int64") // 10**6).astype(str)
    out, report = sanitize_ohlcv(df)
    assert report.status == OK and len(out) == 30
    assert sanitize_ohlcv(df.drop(columns=["high"]))[1].status == UNUSABLE
    assert sanitize_ohlcv(pd.DataFrame())[1].status == UNUSABLE
    assert sanitize_ohlcv(None)[1].status == UNUSABLE


def test_interval_parsing():
    assert interval_to_timedelta("15m") == pd.Timedelta(minutes=15)
    assert interval_to_timedelta("4h") == pd.Timedelta(hours=4)
    assert interval_to_timedelta("1w") == pd.Timedelta(days=7)
    for bad in ("1M", "", None, "m", "0h", "xh", "5y"):
        assert interval_to_timedelta(bad) is None


@pytest.mark.parametrize(
    "values,reason",
    [
        (("100", "110", "90", "105", "10"), None),
        ((None, "110", "90", "105", "10"), "missing open"),
        (("100", "abc", "90", "105", "10"), "non-numeric high"),
        (("100", "110", "nan", "105", "10"), "non-finite low"),
        (("100", "110", "90", "0", "10"), "non-positive price"),
        (("100", "110", "90", "105", "-1"), "negative volume"),
        (("100", "95", "90", "105", "10"), "inconsistent high/low"),
        ((True, "110", "90", "105", "10"), "missing open"),
    ],
)
def test_validate_kline_values(values, reason):
    assert validate_kline_values(*values) == reason


# ── indicators ──────────────────────────────────────────────────────────────

def _flat(n=60):
    ts = pd.date_range("2026-01-01", periods=n, freq="15min")
    return pd.DataFrame({"timestamp": ts, "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 0.0})


def test_flat_market_is_neutral_not_overbought_or_at_lower_band():
    from features import add_features

    out = add_features(_flat())
    assert out["rsi_14"].iloc[-1] == 50.0, "no movement must not read as RSI 100"
    assert out["bb_pos"].iloc[-1] == 0.5, "zero-width bands must not read as 'at lower band'"
    assert out["rsi_14"].iloc[:13].isna().all()


def test_features_never_emit_infinities():
    from features import add_features

    df = _frame(n=40)
    df.loc[20, "open"] = 0.0  # malformed raw row reaching add_features directly
    out = add_features(df)
    numeric = out.select_dtypes(include="number").to_numpy(dtype=float)
    assert not np.isinf(numeric).any()


def test_strict_tail_refuses_to_shift_the_decision_bar():
    from data import add_indicators

    df = _frame(n=80)
    df.loc[79, "close"] = np.nan  # newest bar undefined
    assert add_indicators(df.copy(), strict_tail=True).empty
    lenient = add_indicators(df.copy())
    assert not lenient.empty and lenient["timestamp"].iloc[-1] == df["timestamp"].iloc[78]
    ok = add_indicators(_frame(n=80), strict_tail=True)
    assert ok["timestamp"].iloc[-1] == _frame(n=80)["timestamp"].iloc[-1]


# ── data.get_candles ────────────────────────────────────────────────────────

def _klines(n, step_ms=60_000, end_open_ms=None, start_price=100.0):
    end_open_ms = end_open_ms if end_open_ms is not None else (int(time.time() * 1000) // step_ms - 2) * step_ms
    rows = []
    for i in range(n):
        open_ms = end_open_ms - (n - 1 - i) * step_ms
        price = start_price + i * 0.1
        rows.append([open_ms, f"{price}", f"{price + 0.5}", f"{price - 0.5}", f"{price + 0.2}", "10",
                     open_ms + step_ms - 1, "1000", 5, "4", "400", "0"])
    return rows


def _get_candles(rows, interval="1m"):
    from data import get_candles

    with patch("data_client.MarketDataClient.is_available", return_value=True), \
         patch("data_client.MarketDataClient.get_klines", return_value=rows):
        return get_candles("BTCUSDT", interval, limit=len(rows))


def test_get_candles_drops_malformed_rows_and_reports_quality():
    rows = _klines(120)
    rows[50][4] = "nan"
    rows[60][2] = "1"  # high below low
    rows.append(list(rows[70]))  # pagination overlap
    df = _get_candles(rows)
    assert len(df) == 118
    quality = df.attrs["data_quality"]
    assert quality["status"] == DEGRADED and quality["dropped_invalid"] == 2 and quality["exact_duplicates"] == 1
    assert df["close"].notna().all() and (df["high"] >= df["low"]).all()


def test_get_candles_refuses_an_invalid_newest_bar():
    rows = _klines(50)
    rows[-1][3] = "0"
    assert _get_candles(rows).empty


def test_get_candles_clamps_impossible_taker_volume():
    rows = _klines(30)
    rows[-1][9] = "999"  # taker buy volume above total volume
    df = _get_candles(rows)
    assert df["buy_vol"].iloc[-1] == pytest.approx(5.0)


# ── market scanner ──────────────────────────────────────────────────────────

def _scanner(tf="1m"):
    from testnet_engine.market_scanner import MarketScanner

    scanner = MarketScanner(["BTCUSDT"], timeframes=[tf])
    scanner.client = MagicMock()
    scanner.prod_client = None
    events = []
    scanner.register_callback(lambda sym, t, df, health: events.append((sym, t, df, health)))
    return scanner, events


def test_poll_rejects_stale_window_without_regressing_the_cache():
    scanner, events = _scanner()
    step = 60_000
    newest = (int(time.time() * 1000) // step - 2) * step
    scanner.client.get_klines.return_value = _klines(60, step, newest)
    scanner._poll_single_symbol_tf("BTCUSDT", "1m")
    assert len(events) == 1
    cached_ts = scanner.candle_cache[("BTCUSDT", "1m")]["timestamp"].iloc[-1]

    scanner.client.get_klines.return_value = _klines(60, step, newest - 5 * step)  # lagging replica
    scanner._poll_single_symbol_tf("BTCUSDT", "1m")
    assert scanner.candle_cache[("BTCUSDT", "1m")]["timestamp"].iloc[-1] == cached_ts
    assert len(events) == 1 and scanner.rejected_payloads[("BTCUSDT", "1m")] == 1


def test_poll_with_invalid_newest_bar_triggers_nothing():
    scanner, events = _scanner()
    rows = _klines(60)
    rows[-1][4] = "inf"
    scanner.client.get_klines.return_value = rows
    scanner._poll_single_symbol_tf("BTCUSDT", "1m")
    assert events == [] and ("BTCUSDT", "1m") not in scanner.candle_cache
    assert scanner.data_health_status["BTCUSDT"] == "DATA_INVALID"
    assert scanner.data_quality[("BTCUSDT", "1m")]["status"] == UNUSABLE


def test_poll_of_a_stale_feed_triggers_nothing():
    scanner, events = _scanner()
    step = 60_000
    old = (int(time.time() * 1000) // step - 600) * step  # newest bar ten hours old
    scanner.client.get_klines.return_value = _klines(60, step, old)
    scanner._poll_single_symbol_tf("BTCUSDT", "1m")
    assert events == [] and scanner.data_health_status["BTCUSDT"] == "DATA_INVALID"


def _kline_msg(open_ms, tf="1m", **overrides):
    k = {"t": open_ms, "T": open_ms + 59_999, "o": "100", "h": "101", "l": "99", "c": "100.5", "v": "3", "V": "1",
         "x": True, "i": tf}
    k.update(overrides)
    return {"data": {"e": "kline", "s": "BTCUSDT", "k": k}}


def test_socket_rejects_malformed_and_out_of_order_klines():
    scanner, events = _scanner()
    base = 1_790_000_000_000
    scanner._handle_socket_message(_kline_msg(base))
    scanner._handle_socket_message(_kline_msg(base + 60_000))
    assert len(events) == 2
    scanner._handle_socket_message(_kline_msg(base + 30_000))  # late, out of order
    scanner._handle_socket_message(_kline_msg(base + 120_000, o=None))  # missing open
    scanner._handle_socket_message(_kline_msg(base + 120_000, h="98"))  # high < low
    scanner._handle_socket_message(_kline_msg(base + 120_000, c="0"))
    no_open_time = _kline_msg(base + 120_000)
    no_open_time["data"]["k"]["t"] = None
    scanner._handle_socket_message(no_open_time)
    assert len(events) == 2
    cache = scanner.candle_cache[("BTCUSDT", "1m")]
    assert list(cache["timestamp"]) == list(pd.to_datetime([base, base + 60_000], unit="ms"))
    assert scanner.rejected_payloads[("BTCUSDT", "1m")] == 5


def test_socket_derives_missing_close_time_from_the_interval():
    scanner, events = _scanner("5m")
    msg = _kline_msg(1_790_000_000_000, tf="5m")
    del msg["data"]["k"]["T"]
    scanner._handle_socket_message(msg)
    row = scanner.candle_cache[("BTCUSDT", "5m")].iloc[-1]
    assert int(row["close_time"].value // 10**6) == 1_790_000_000_000 + 300_000 - 1


def test_socket_clamps_bad_taker_volume():
    scanner, events = _scanner()
    scanner._handle_socket_message(_kline_msg(1_790_000_000_000, V="nan"))
    assert scanner.candle_cache[("BTCUSDT", "1m")]["buy_vol"].iloc[-1] == pytest.approx(1.5)


# ── service freshness gate ──────────────────────────────────────────────────

def _bare_service():
    from testnet_engine.service import TestnetService

    svc = TestnetService.__new__(TestnetService)
    svc.safety_halt = False
    return svc


def test_service_skips_stale_tz_aware_frames(caplog):
    svc = _bare_service()
    df = _frame(n=60, start="2026-01-01", tz="UTC")
    with caplog.at_level("WARNING"):
        assert svc.on_candle_closed("BTCUSDT", "15m", df, "OK") is None
    assert "reason=STALE_MARKET_DATA" in caplog.text


def test_service_skips_future_candles(caplog):
    svc = _bare_service()
    start = pd.Timestamp.now(tz="UTC").floor("15min") + pd.Timedelta(days=2)
    df = _frame(n=60, start=start)
    with caplog.at_level("WARNING"):
        svc.on_candle_closed("BTCUSDT", "15m", df, "OK")
    assert "reason=FUTURE_CANDLE_TIMESTAMP" in caplog.text


def test_service_skips_unparseable_timestamps(caplog):
    svc = _bare_service()
    df = _frame(n=60)
    df["timestamp"] = "not a time"
    with caplog.at_level("WARNING"):
        svc.on_candle_closed("BTCUSDT", "15m", df, "OK")
    assert "reason=BAD_CANDLE_TIMESTAMP" in caplog.text


# ── strategies ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "side,entry,sl,tp,expected",
    [
        ("BUY", 100, 95, 110, True),
        ("LONG", 100, 95, 110, True),
        ("SELL", 100, 105, 90, True),
        ("BUY", 100, 100, 110, False),
        ("BUY", 100, 105, 110, False),
        ("SELL", 100, 95, 90, False),
        ("SELL", 100, 105, 0, False),
        ("BUY", 100, float("nan"), 110, False),
        ("BUY", 100, 95, float("inf"), False),
        ("HOLD", 100, 95, 110, False),
        (None, 100, 95, 110, False),
        ("BUY", 0, -1, 1, False),
    ],
)
def test_signal_levels_valid(side, entry, sl, tp, expected):
    assert signal_levels_valid(side, entry, sl, tp) is expected


def test_scalper_keeps_levels_for_sub_cent_assets():
    import strategy_aggressive_scalper as scalper

    df = pd.DataFrame({"open": [0.00001200, 0.00001210], "close": [0.00001210, 0.00001234],
                       "high": [0.0000124, 0.0000124], "low": [0.0000119, 0.0000120]})
    res = scalper.get_signal(df)
    assert res.side == "BUY"
    assert signal_levels_valid(res.side, 0.00001234, res.sl, res.tp), (res.sl, res.tp)


def test_scalper_rejects_non_finite_candles():
    import strategy_aggressive_scalper as scalper

    df = pd.DataFrame({"open": [1.0, 1.0], "close": [1.0, float("nan")]})
    assert scalper.get_signal(df).side is None


def test_mtf_strategy_accepts_add_indicators_frames():
    import strategy_adx_ema_mtf as mtf
    from data import add_indicators

    df = add_indicators(_frame(n=260, seed=11))
    res = mtf.get_signal(df, df_1h=add_indicators(_frame(n=260, freq="1h", seed=12)))
    assert res.side in (None, "BUY", "SELL")


@pytest.mark.parametrize("module_name", ["strategy_hybrid", "strategy_scalper"])
def test_strategies_never_emit_nan_levels_on_inf_highs(module_name):
    import importlib

    module = importlib.import_module(module_name)
    for seed in range(12):
        df = _frame(n=260, seed=seed)
        df.loc[259, "high"] = np.inf
        res = module.get_signal(df)
        if res.side:
            assert signal_levels_valid(res.side, df["close"].iloc[-1], res.sl, res.tp)


# ── tick monitors ───────────────────────────────────────────────────────────

def test_data_monitor_watermark_never_regresses():
    from paper_engine.data_monitor import DataMonitor

    monitor = DataMonitor()
    assert monitor.process_tick("BTC", 100.0, 1_000.0) == "ACCEPTED"
    assert monitor.process_tick("BTC", 101.0, 1_060.0) == "ACCEPTED"
    assert monitor.process_tick("BTC", 50.0, 1_010.0) == "OUT_OF_ORDER"
    state = monitor.symbols["BTC"]
    assert state["last_timestamp"] == 1_060.0 and state["last_price"] == 101.0
    assert monitor.process_tick("BTC", 102.0, 1_060.0) == "DUPLICATE"
    assert monitor.process_tick("BTC", 102.0, 1_120.0) == "ACCEPTED"
    assert state["gaps"] == 0, "an in-order tick after a late one is not a gap"
    assert monitor.process_tick("BTC", float("nan"), 1_180.0) == "INVALID"
    assert monitor.process_tick("BTC", 0.0, 1_180.0) == "INVALID"
    assert state["invalid"] == 2 and state["last_price"] == 102.0


def test_data_monitor_rejected_ticks_do_not_refresh_liveness():
    from paper_engine.data_monitor import DataMonitor

    monitor = DataMonitor()
    monitor.process_tick("BTC", 100.0, 2_000.0)
    monitor._last_received = time.time() - 120  # feed went quiet
    monitor.process_tick("BTC", 100.0, 1_000.0)  # replayed old tick
    monitor.process_tick("BTC", 100.0, 2_000.0)  # duplicate
    assert monitor.get_status() == "CRITICAL"


def test_market_data_guard_rejects_future_nan_and_bad_timestamps():
    from stratex_upgrade.market import FreshnessPolicy, MarketDataGuard

    guard = MarketDataGuard(FreshnessPolicy(max_age_seconds=10.0))
    now = time.time_ns()
    assert guard.accept("BTC", now + 60 * 10**9, Decimal("1"), Decimal("2"), 1) == (False, "FUTURE_TIMESTAMP")
    assert guard.accept("BTC", now, Decimal("NaN"), Decimal("2"), 2) == (False, "INVALID_PRICE")
    assert guard.accept("BTC", now, Decimal("1"), Decimal("Infinity"), 3) == (False, "INVALID_PRICE")
    assert guard.accept("BTC", "now", Decimal("1"), Decimal("2"), 4) == (False, "INVALID_TIMESTAMP")
    assert guard.accept("BTC", now, Decimal("1"), Decimal("2"), 5) == (True, "OK")


# ── forward runner ──────────────────────────────────────────────────────────

def test_forward_runner_refuses_invalid_newest_bar():
    import paper_forward_runner as pfr

    rows = _klines(80, 3_600_000)
    with patch.object(pfr, "MarketDataClient") as client_cls:
        client_cls.return_value.get_klines.return_value = rows
        good = pfr.fetch_candles("BTCUSDT", "1h", limit=80)
        assert good is not None and len(good) == 80
        rows[-1][2] = "-5"
        client_cls.return_value.get_klines.return_value = rows
        assert pfr.fetch_candles("BTCUSDT", "1h", limit=80) is None


def test_math_guard_helpers_reject_bool_prices():
    assert signal_levels_valid("BUY", True, 0.5, 2) is False
    assert not math.isnan(sanitize_ohlcv(_frame(n=5))[0]["close"].iloc[-1])
