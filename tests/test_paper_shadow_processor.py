"""Focused safety tests for the pure, in-memory paper shadow processor."""

from types import SimpleNamespace

import pandas as pd
import pytest

import paper_shadow_processor as shadow


def candles(rows=None):
    rows = rows or [
        (100, 101, 99, 100),
        (101, 102, 100, 101),
        (101, 105, 97, 103),
    ]
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=len(rows), freq="h", tz="UTC"),
            "open": [row[0] for row in rows],
            "high": [row[1] for row in rows],
            "low": [row[2] for row in rows],
            "close": [row[3] for row in rows],
            "volume": [100.0] * len(rows),
        }
    )


def test_unvalidated_candidate_fills_next_open_and_stop_wins_ambiguous_bar(monkeypatch):
    data = candles()
    first_ts = data.loc[0, "timestamp"]
    monkeypatch.setattr(shadow, "_add_strategy_features", lambda _name, frame: frame)

    def signal(name, frame):
        if frame.iloc[-1]["timestamp"] == first_ts:
            return SimpleNamespace(side="BUY", sl=98.0, tp=104.0)
        return SimpleNamespace(side=None, sl=None, tp=None)

    monkeypatch.setattr(shadow, "_get_signal", signal)
    processor = shadow.ShadowPaperProcessor()

    queued = processor.process_closed_candle("adx_ema", "BTCUSDT", "4h", data.iloc[:1])
    assert queued["decision"] == "SIGNAL_QUEUED_NEXT_OPEN"
    assert queued["evidence_status"] == shadow.EVIDENCE_STATUS

    opened = processor.process_closed_candle("adx_ema", "BTCUSDT", "4h", data.iloc[:2])
    assert opened["decision"] == "PAPER_ENTRY"
    assert opened["entry"] == 101.0
    position = processor.positions[("adx_ema", "BTCUSDT", "4h")]
    assert position["quantity"] == pytest.approx(25.0 / 3.0)
    assert position["entry_notional"] <= processor.realized_equity * 0.10

    closed = processor.process_closed_candle("adx_ema", "BTCUSDT", "4h", data)
    assert closed["decision"] == "PAPER_EXIT"
    trade = closed["trade"]
    assert trade["exit_reason"] == "STOP"  # both stop and target touched
    assert trade["exit_price"] == 98.0
    assert trade["fees"] > 0
    assert trade["slippage"] > 0
    assert trade["spread"] > 0
    assert trade["net_pnl"] < 0
    assert trade["evidence_status"] == "UNVALIDATED_PAPER_SHADOW"


def test_duplicate_and_out_of_order_candles_are_not_reprocessed(monkeypatch):
    data = candles()
    monkeypatch.setattr(shadow, "_add_strategy_features", lambda _name, frame: frame)
    monkeypatch.setattr(
        shadow, "_get_signal",
        lambda *_: SimpleNamespace(side=None, sl=None, tp=None),
    )
    processor = shadow.ShadowPaperProcessor()

    first = processor.process_closed_candle("adx_ema", "BTCUSDT", "4h", data.iloc[:1])
    duplicate = processor.process_closed_candle("adx_ema", "BTCUSDT", "4h", data.iloc[:1])
    out_of_order = processor.process_closed_candle("adx_ema", "BTCUSDT", "4h", data.iloc[:1])
    assert first["decision"] == "NO_SIGNAL"
    assert duplicate["decision"] == "DUPLICATE_OR_OUT_OF_ORDER"
    assert out_of_order["decision"] == "DUPLICATE_OR_OUT_OF_ORDER"
    assert len(processor.seen_signal_ids) == 1


def test_rejects_synthetic_or_malformed_inputs_and_unsafe_risk():
    with pytest.raises(ValueError, match="risk_fraction"):
        shadow.ShadowPaperProcessor(risk_fraction=0.01)
    with pytest.raises(ValueError, match="non-empty DataFrame"):
        shadow._validate_candles(pd.DataFrame())
    malformed = candles()
    malformed.loc[1, "high"] = 0.0
    with pytest.raises(ValueError, match="high is inconsistent"):
        shadow._validate_candles(malformed)
    unsorted = candles().iloc[::-1]
    with pytest.raises(ValueError, match="strictly chronological"):
        shadow._validate_candles(unsorted)


def test_candidate_set_is_observe_only_and_default_matrix_is_bounded():
    assert set(shadow.SHADOW_CANDIDATES) == {
        "donchian20", "adx_ema", "bb_reversion", "rsi_burst", "vwap_trend", "supertrend"
    }
    assert len(shadow.DEFAULT_SHADOW_STREAMS) == 24
    assert {symbol for _, symbol, _ in shadow.DEFAULT_SHADOW_STREAMS} == {
        "BTCUSDT", "ETHUSDT", "SOLUSDT"
    }
    with pytest.raises(ValueError, match="not an observe-only"):
        shadow.ShadowPaperProcessor().process_closed_candle(
            "factory_winner_1", "BTCUSDT", "1h", candles().iloc[:1]
        )
