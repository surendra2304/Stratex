"""Safety and accounting tests for the isolated historical paper lab."""

import hashlib
import json

import pandas as pd
import pytest

from stratex_paper_lab import STRATEGIES, _load_verified_candles, _validate_output_dir, evaluate_candidate


def candles():
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=4, freq="h", tz="UTC"),
        "open": [100.0, 101.0, 103.0, 104.0],
        "high": [100.5, 103.0, 105.0, 106.0],
        "low": [99.5, 100.5, 102.0, 103.0],
        "close": [100.0, 102.0, 104.0, 105.0],
        "volume": [10.0] * 4,
    })


def signal_on_first_bar(history):
    if len(history) == 1:
        return type("Signal", (), {"side": "BUY", "sl": 99.0, "tp": 110.0})()
    return type("Signal", (), {"side": None, "sl": None, "tp": None})()


def test_candidate_uses_next_open_and_accounts_for_all_taker_costs():
    result = evaluate_candidate(candles(), "test", signal_fn=signal_on_first_bar)
    trade = result["trades"][0]
    assert trade["entry_price"] == 101.0
    assert trade["entry_timestamp"] == "2026-01-01T01:00:00+00:00"
    assert trade["exit_price"] == 105.0
    assert trade["exit_reason"] == "END_OF_DATA"
    assert trade["fees"] > 0
    assert trade["slippage"] > 0
    assert trade["spread"] > 0
    assert trade["net_pnl"] == pytest.approx(
        trade["gross_pnl"] - trade["fees"] - trade["slippage"] - trade["spread"]
    )


def test_stop_wins_when_stop_and_target_touch_same_candle():
    data = candles()
    data.loc[2, ["high", "low"]] = [111.0, 98.0]
    result = evaluate_candidate(data, "test", signal_fn=signal_on_first_bar)
    assert result["trades"][0]["exit_reason"] == "STOP"
    assert result["trades"][0]["exit_price"] == 99.0


def test_verified_input_rejects_hash_mismatch_and_fixture_source(tmp_path):
    path = tmp_path / "real.csv"
    candles().to_csv(path, index=False)
    sidecar = tmp_path / "real.json"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    meta = {"sha256": digest, "source": "BINANCE_PUBLIC", "symbol": "BTCUSDT", "timeframe": "1h", "fetched_at_utc": "2026-01-01T00:00:00Z"}
    sidecar.write_text(json.dumps(meta), encoding="utf-8")
    frame, provenance = _load_verified_candles(path, sidecar)
    assert len(frame) == 4
    assert provenance["sha256"] == digest
    meta["source"] = "fixture"
    sidecar.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(ValueError, match="real market-data source"):
        _load_verified_candles(path, sidecar)
    meta["source"] = "BINANCE_PUBLIC"
    meta["sha256"] = "0" * 64
    sidecar.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256"):
        _load_verified_candles(path, sidecar)


def test_lab_output_path_is_isolated_and_forward_paths_are_rejected(tmp_path):
    isolated = _validate_output_dir(tmp_path / "lab-output")
    assert isolated == (tmp_path / "lab-output").resolve()
    with pytest.raises(ValueError, match="forward experiment directories"):
        _validate_output_dir(tmp_path / "experiments" / "oops")


def test_all_research_candidates_dispatch_without_promising_trades():
    expected = {"donchian20", "swing", "adx_ema", "bb_reversion", "rsi_burst", "vwap_trend", "supertrend"}
    assert set(STRATEGIES) == expected
    for strategy in sorted(expected):
        result = evaluate_candidate(candles(), strategy)
        assert result["evidence_status"] == "HISTORICAL_PAPER_RESEARCH_ONLY"
        assert result["trade_count"] == 0
