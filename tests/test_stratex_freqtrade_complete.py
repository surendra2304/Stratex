"""tests/test_stratex_freqtrade_complete.py

Comprehensive test suite verifying Freqtrade capabilities integrated into Stratex:
1. IStrategy contract & lifecycle
2. FreqtradeStrategyAdapter & SignalResult translation
3. ROIEngine time-decayed profit targets
4. TrailingStopEngine state machine
5. VolumePairList, PriceFilter, SpreadFilter, VolatilityFilter & PairListManager
6. Enhanced ProtectionManager with pair & global locking
7. Built-in canonical strategies
8. Unified 'ft' facade
9. Flask REST API endpoints (/api/v1/freqtrade/*)
"""

from datetime import datetime, timedelta, timezone
import pytest
import numpy as np
import pandas as pd

from stratex_freqtrade_adapter import (
    ft,
    IStrategy,
    FreqtradeStrategyAdapter,
    ROIEngine,
    TrailingStopEngine,
    VolumePairList,
    StaticPairList,
    PriceFilter,
    SpreadFilter,
    VolatilityFilter,
    PairListManager,
    ProtectionManager,
    registry,
)
from dashboard import app


# ------------------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------------------
@pytest.fixture
def sample_ohlcv():
    """Generates synthetic 100-bar OHLCV dataframe."""
    np.random.seed(42)
    dates = pd.date_range("2026-01-01", periods=100, freq="5min", tz="UTC")
    base = 100.0
    returns = np.random.normal(0.0005, 0.01, 100)
    prices = base * np.exp(np.cumsum(returns))

    df = pd.DataFrame({
        "timestamp": dates,
        "open": prices * (1.0 - np.random.uniform(0, 0.001, 100)),
        "high": prices * (1.0 + np.random.uniform(0.001, 0.005, 100)),
        "low": prices * (1.0 - np.random.uniform(0.001, 0.005, 100)),
        "close": prices,
        "volume": np.random.uniform(500, 2000, 100),
    })
    return df


@pytest.fixture
def flask_client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


# ------------------------------------------------------------------------------
# 1. IStrategy Contract & Adapter Tests
# ------------------------------------------------------------------------------
def test_istrategy_and_adapter_execution(sample_ohlcv):
    class DummyStrategy(IStrategy):
        timeframe = "5m"
        minimal_roi = {"0": 0.04, "30": 0.02, "60": 0.0}
        stoploss = -0.05
        can_short = True

        def populate_indicators(self, dataframe, metadata=None):
            dataframe["sma_5"] = dataframe["close"].rolling(5).mean()
            return dataframe

        def populate_entry_trend(self, dataframe, metadata=None):
            dataframe["enter_long"] = 0
            dataframe["enter_short"] = 0
            dataframe["enter_tag"] = ""
            # Force buy signal on the last bar
            dataframe.iloc[-1, dataframe.columns.get_loc("enter_long")] = 1
            dataframe.iloc[-1, dataframe.columns.get_loc("enter_tag")] = "dummy_entry"
            return dataframe

        def populate_exit_trend(self, dataframe, metadata=None):
            dataframe["exit_long"] = 0
            dataframe["exit_short"] = 0
            return dataframe

    strat = DummyStrategy()
    adapter = FreqtradeStrategyAdapter(strat, name="dummy_test")

    sig = adapter.get_signal(sample_ohlcv, pair="BTCUSDT")
    assert sig.side == "BUY"
    assert sig.strategy_type == "FREQTRADE_RULE"
    assert sig.confidence == 0.55
    # SL is 5% below close price
    last_close = float(sample_ohlcv["close"].iloc[-1])
    assert pytest.approx(sig.sl, rel=1e-2) == last_close * 0.95
    # TP is 4% above close price (from minimal_roi["0"] = 0.04)
    assert pytest.approx(sig.tp, rel=1e-2) == last_close * 1.04


# ------------------------------------------------------------------------------
# 2. ROI Engine Tests
# ------------------------------------------------------------------------------
def test_roi_engine_time_decay():
    roi = ROIEngine({"0": 0.05, "30": 0.03, "60": 0.015, "120": 0.0})

    # Duration = 10 min -> target is 0.05
    exit_needed, target = roi.should_exit(duration_minutes=10, current_profit=0.04)
    assert exit_needed is False
    assert target == 0.05

    exit_needed, _ = roi.should_exit(duration_minutes=10, current_profit=0.052)
    assert exit_needed is True

    # Duration = 45 min -> target is 0.03
    exit_needed, target = roi.should_exit(duration_minutes=45, current_profit=0.028)
    assert exit_needed is False
    assert target == 0.03

    exit_needed, _ = roi.should_exit(duration_minutes=45, current_profit=0.031)
    assert exit_needed is True

    # Duration = 130 min -> target is 0.0
    exit_needed, target = roi.should_exit(duration_minutes=130, current_profit=0.001)
    assert exit_needed is True
    assert target == 0.0


# ------------------------------------------------------------------------------
# 3. Trailing Stop Engine Tests
# ------------------------------------------------------------------------------
def test_trailing_stop_engine_with_offset_gate():
    engine = TrailingStopEngine(
        trailing_stop=True,
        trailing_stop_positive=0.01,         # 1% trailing
        trailing_stop_positive_offset=0.02,  # 2% offset gate
        trailing_only_offset_is_reached=True,
    )

    open_rate = 100.0

    # 1. Price moved to 101.5 (+1.5%), offset (+2%) NOT reached -> should not exit
    should_exit, stop = engine.should_exit(
        side="BUY", open_rate=open_rate, current_rate=101.5, max_rate=101.5
    )
    assert should_exit is False
    assert stop is None

    # 2. Price moved to 103.0 (+3.0%), offset reached -> stop is 103.0 * (1 - 0.01) = 101.97
    should_exit, stop = engine.should_exit(
        side="BUY", open_rate=open_rate, current_rate=102.5, max_rate=103.0
    )
    assert should_exit is False
    assert pytest.approx(stop, rel=1e-3) == 101.97

    # 3. Price drops to 101.5 (below 101.97) -> trailing stop triggers exit!
    should_exit, stop = engine.should_exit(
        side="BUY", open_rate=open_rate, current_rate=101.5, max_rate=103.0
    )
    assert should_exit is True
    assert pytest.approx(stop, rel=1e-3) == 101.97


# ------------------------------------------------------------------------------
# 4. Pairlist Handlers and Filters Tests
# ------------------------------------------------------------------------------
def test_static_pairlist():
    pl = StaticPairList(pairs=["BTCUSDT", "ETHUSDT", "XRPUSDT"], blacklist=["XRPUSDT"])
    res = pl.filter_pairlist([])
    assert res == ["BTCUSDT", "ETHUSDT"]


def test_volume_pairlist_and_filters():
    # Mock ticker data
    tickers = {
        "BTCUSDT": {"symbol": "BTCUSDT", "lastPrice": 65000.0, "quoteVolume": 5000000.0, "bidPrice": 64990.0, "askPrice": 65010.0, "priceChangePercent": 2.5},
        "ETHUSDT": {"symbol": "ETHUSDT", "lastPrice": 3500.0, "quoteVolume": 4000000.0, "bidPrice": 3499.0, "askPrice": 3501.0, "priceChangePercent": -1.2},
        "TINYUSDT": {"symbol": "TINYUSDT", "lastPrice": 0.00001, "quoteVolume": 1000000.0, "bidPrice": 0.00001, "askPrice": 0.00001, "priceChangePercent": 5.0},
        "SPREADUSDT": {"symbol": "SPREADUSDT", "lastPrice": 10.0, "quoteVolume": 2000000.0, "bidPrice": 9.5, "askPrice": 10.5, "priceChangePercent": 0.5},
        "WILDUSDT": {"symbol": "WILDUSDT", "lastPrice": 50.0, "quoteVolume": 3000000.0, "bidPrice": 50.0, "askPrice": 50.1, "priceChangePercent": 65.0},
    }

    vol_handler = VolumePairList(number_assets=5)
    sorted_pairs = vol_handler.filter_pairlist([], ticker_data=tickers)
    # Order by quoteVolume: BTC, ETH, WILD, SPREAD, TINY
    assert sorted_pairs == ["BTCUSDT", "ETHUSDT", "WILDUSDT", "SPREADUSDT", "TINYUSDT"]

    # Price Filter (min price 0.001) should drop TINYUSDT
    price_filter = PriceFilter(min_price=0.001)
    res_price = price_filter.filter_pairlist(sorted_pairs, ticker_data=tickers)
    assert "TINYUSDT" not in res_price

    # Spread Filter (max spread 0.01) should drop SPREADUSDT (spread (10.5 - 9.5)/10.5 = ~0.095)
    spread_filter = SpreadFilter(max_spread_ratio=0.01)
    res_spread = spread_filter.filter_pairlist(res_price, ticker_data=tickers)
    assert "SPREADUSDT" not in res_spread

    # Volatility Filter (max change 40%) should drop WILDUSDT (change = 65%)
    vol_filter = VolatilityFilter(max_change_pct=40.0)
    res_vol = vol_filter.filter_pairlist(res_spread, ticker_data=tickers)
    assert "WILDUSDT" not in res_vol

    # Final remaining pairs should be BTC and ETH
    assert res_vol == ["BTCUSDT", "ETHUSDT"]


def test_pairlist_manager():
    manager = PairListManager()
    status = manager.get_status()
    assert status["handlers_count"] >= 3
    pairs = manager.generate_pairlist()
    assert isinstance(pairs, list)
    assert len(pairs) > 0


# ------------------------------------------------------------------------------
# 5. Enhanced Protection Manager Tests
# ------------------------------------------------------------------------------
def test_pair_and_global_locks():
    pm = ProtectionManager()

    # Initially unlocked
    locked, reason = pm.is_pair_locked("SOLUSDT")
    assert locked is False

    # Lock pair explicitly
    unlock_time = datetime.now(timezone.utc) + timedelta(minutes=15)
    pm.lock_pair("SOLUSDT", unlock_time, reason="VOLATILITY_LOCK")

    locked, reason = pm.is_pair_locked("SOLUSDT")
    assert locked is True
    assert reason == "VOLATILITY_LOCK"

    # Other pairs not locked
    locked_btc, _ = pm.is_pair_locked("BTCUSDT")
    assert locked_btc is False

    # Global lock
    global_unlock = datetime.now(timezone.utc) + timedelta(minutes=30)
    pm.lock_all(global_unlock, reason="CIRCUIT_BREAKER")

    locked_btc, reason_btc = pm.is_pair_locked("BTCUSDT")
    assert locked_btc is True
    assert "CIRCUIT_BREAKER" in reason_btc

    # Clear cooldowns
    pm.clear_cooldowns()
    locked_cleared, _ = pm.is_pair_locked("BTCUSDT")
    assert locked_cleared is False


# ------------------------------------------------------------------------------
# 6. Built-in Canonical Strategies Tests
# ------------------------------------------------------------------------------
def test_canonical_strategies_registered(sample_ohlcv):
    strats = registry.list_strategies()
    names = [s["name"] for s in strats]
    assert "sample_strategy" in names
    assert "bband_rsi" in names
    assert "awesome_macd" in names

    # Instantiate SampleFreqtradeStrategy
    sample_strat = registry.create("sample_strategy")
    assert sample_strat is not None
    assert sample_strat.timeframe == "5m"
    assert sample_strat.stoploss == -0.04

    df = sample_strat.populate_indicators(sample_ohlcv.copy())
    assert "ema_fast" in df.columns
    assert "rsi" in df.columns
    assert "bb_upper" in df.columns

    df = sample_strat.populate_entry_trend(df)
    assert "enter_long" in df.columns
    assert "enter_short" in df.columns


# ------------------------------------------------------------------------------
# 7. Unified Facade Tests
# ------------------------------------------------------------------------------
def test_ft_facade_interface():
    status = ft.status()
    assert status["status"] == "HEALTHY"
    assert status["registered_strategies"] >= 3
    assert "100% Free Public Sources" in status["mode"]

    # Wrap canonical strategy
    strat_cls = ft.strategies.get("bband_rsi")
    assert strat_cls is not None
    adapter = ft.strategies.wrap(strat_cls())
    assert adapter.__name__ == "BbandRsiStrategy"


# ------------------------------------------------------------------------------
# 8. Flask REST API Endpoints Tests
# ------------------------------------------------------------------------------
def test_flask_freqtrade_routes(flask_client):
    # GET /status
    res = flask_client.get("/api/v1/freqtrade/status")
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "OK"
    assert data["data"]["status"] == "HEALTHY"

    # GET /strategies
    res = flask_client.get("/api/v1/freqtrade/strategies")
    assert res.status_code == 200
    data = res.get_json()
    assert data["count"] >= 3

    # GET /protections/status
    res = flask_client.get("/api/v1/freqtrade/protections/status")
    assert res.status_code == 200
    data = res.get_json()
    assert "active_cooldowns" in data["data"]

    # GET /pairlists/evaluate
    res = flask_client.get("/api/v1/freqtrade/pairlists/evaluate?limit=5")
    assert res.status_code == 200
    data = res.get_json()
    assert len(data["data"]) > 0

    # POST /backtest
    res = flask_client.post("/api/v1/freqtrade/backtest", json={
        "strategy": "sample_strategy",
        "symbol": "BTCUSDT",
        "timeframe": "5m",
        "candles": 100
    })
    assert res.status_code == 200
    data = res.get_json()
    assert data["strategy"] == "sample_strategy"
    assert "candles_analyzed" in data
    assert "roi_table" in data
