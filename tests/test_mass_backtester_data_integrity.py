"""Research candle loader must fail closed instead of inventing market history."""

import json

import pandas as pd
import pytest

from research.strategy_factory import mass_backtester


class _OfflineMarketClient:
    data_source = "BINANCE_TESTNET_READ_ONLY"

    def futures_historical_klines(self, *_args, **_kwargs):
        raise ConnectionError("exchange unavailable")


class _OneCandleMarketClient:
    data_source = "BINANCE_TESTNET_READ_ONLY"

    def futures_historical_klines(self, *_args, **_kwargs):
        return [[
            1_790_000_000_000, "100", "102", "99", "101", "12",
            1_790_000_299_999, "1200", 10, "6", "600", "0",
        ]]


def test_market_data_failure_does_not_create_substitute_candles(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mass_backtester, "MarketDataClient", _OfflineMarketClient)

    with pytest.raises(RuntimeError, match="No substitute data was generated"):
        mass_backtester.load_market_data(["BTCUSDT"], ["5m"])

    cache_dir = tmp_path / "data_cache" / "factory_data"
    assert list(cache_dir.iterdir()) == []


def test_downloaded_candles_are_cached_with_source_and_hash(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mass_backtester, "MarketDataClient", _OneCandleMarketClient)

    first = mass_backtester.load_market_data(["BTCUSDT"], ["5m"])
    metadata_path = tmp_path / "data_cache/factory_data/BTCUSDT_5m.csv.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert len(first[("BTCUSDT", "5m")]) == 1
    assert metadata["source"] == "BINANCE_TESTNET_READ_ONLY"
    assert metadata["rows"] == 1
    assert len(metadata["sha256"]) == 64

    cached = mass_backtester.load_market_data(["BTCUSDT"], ["5m"])
    pd.testing.assert_frame_equal(first[("BTCUSDT", "5m")], cached[("BTCUSDT", "5m")])


def test_unprovenanced_or_modified_cache_is_rejected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cache_dir = tmp_path / "data_cache" / "factory_data"
    cache_dir.mkdir(parents=True)
    cache_file = cache_dir / "BTCUSDT_5m.csv"
    cache_file.write_text("timestamp,open,high,low,close,volume\n2026-01-01,100,101,99,100,1\n")
    monkeypatch.setattr(mass_backtester, "MarketDataClient", _OneCandleMarketClient)

    with pytest.raises(RuntimeError, match="missing provenance sidecar"):
        mass_backtester.load_market_data(["BTCUSDT"], ["5m"])

    sidecar = cache_file.with_suffix(".csv.json")
    sidecar.write_text(json.dumps({
        "symbol": "BTCUSDT",
        "timeframe": "5m",
        "requested_start": "2025-01-01",
        "source": "BINANCE_TESTNET_READ_ONLY",
        "rows": 1,
        "sha256": "0" * 64,
    }))
    with pytest.raises(RuntimeError, match="hash mismatch"):
        mass_backtester.load_market_data(["BTCUSDT"], ["5m"])
