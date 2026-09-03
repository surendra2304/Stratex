"""Unit and safety tests for stratex_ccxt_adapter."""

import os
import pytest
import pandas as pd

from stratex_ccxt_adapter.models import NormalizedMarket, NormalizedTicker, NormalizedOrder
from stratex_ccxt_adapter.precision import PrecisionHelper
from stratex_ccxt_adapter.errors import CCXTErrorMapper
from stratex_ccxt_adapter.client import CCXTExchangeAdapter


# ==============================================================================
# 1. MODELS TESTS
# ==============================================================================

def test_normalized_market_model():
    m = NormalizedMarket(
        symbol="BTC/USDT",
        base="BTC",
        quote="USDT",
        active=True,
        market_type="spot",
        min_amount=0.0001,
        max_amount=1000.0,
        min_cost=10.0,
        price_precision=2,
        amount_precision=5,
    )
    assert m.symbol == "BTC/USDT"
    assert m.base == "BTC"
    assert m.quote == "USDT"
    assert m.active is True
    assert m.min_cost == 10.0


def test_normalized_ticker_model():
    t = NormalizedTicker(
        symbol="BTC/USDT",
        last=65000.0,
        bid=64999.0,
        ask=65001.0,
        base_volume=1200.5,
        quote_volume=78000000.0,
        timestamp_ms=1700000000000,
    )
    assert t.last == 65000.0
    assert t.ask > t.bid


# ==============================================================================
# 2. PRECISION AND LIMITS TESTS
# ==============================================================================

def test_precision_floor_step():
    assert PrecisionHelper.floor_step(0.123456, 0.001) == pytest.approx(0.123, 1e-6)
    assert PrecisionHelper.floor_step(10.55, 0.5) == pytest.approx(10.5, 1e-6)
    assert PrecisionHelper.floor_step(100.0, None) == 100.0


def test_precision_round_price_and_amount():
    assert PrecisionHelper.round_price(64250.789, precision=2) == 64250.79
    assert PrecisionHelper.round_price(64250.789, step=0.1) == pytest.approx(64250.7, 1e-6)
    assert PrecisionHelper.round_amount(0.0012345, precision=4) == 0.0012
    assert PrecisionHelper.round_amount(0.0012345, step=0.0005) == pytest.approx(0.0010, 1e-6)


def test_precision_validate_market_order():
    market = {
        "limits": {
            "amount": {"min": 0.001, "max": 10.0},
            "cost": {"min": 10.0},
        }
    }
    # Valid
    ok, reason = PrecisionHelper.validate_market_order(0.01, 50000.0, market)
    assert ok is True
    assert reason == "PRECISION_LIMITS_OK"

    # Below min amount
    ok, reason = PrecisionHelper.validate_market_order(0.0001, 50000.0, market)
    assert ok is False
    assert reason == "AMOUNT_BELOW_MINIMUM"

    # Above max amount
    ok, reason = PrecisionHelper.validate_market_order(15.0, 50000.0, market)
    assert ok is False
    assert reason == "AMOUNT_ABOVE_MAXIMUM"

    # Below min notional ($5 < $10)
    ok, reason = PrecisionHelper.validate_market_order(0.001, 5000.0, market)
    assert ok is False
    assert reason == "NOTIONAL_BELOW_MINIMUM"


# ==============================================================================
# 3. ERROR MAPPING TESTS
# ==============================================================================

def test_ccxt_error_mapper():
    class MockAuthenticationError(Exception): pass
    class MockRateLimitExceeded(Exception): pass
    class MockNetworkError(Exception): pass
    class MockInvalidOrder(Exception): pass
    class MockBadSymbol(Exception): pass
    class MockNotSupported(Exception): pass
    class MockBadRequest(Exception): pass
    class MockOtherError(Exception): pass

    assert CCXTErrorMapper.classify(MockAuthenticationError()) == "AUTHENTICATION_ERROR"
    assert CCXTErrorMapper.classify(MockRateLimitExceeded()) == "RATE_LIMIT"
    assert CCXTErrorMapper.classify(MockNetworkError()) == "NETWORK_ERROR"
    assert CCXTErrorMapper.classify(MockInvalidOrder()) == "INVALID_ORDER"
    assert CCXTErrorMapper.classify(MockBadSymbol()) == "BAD_SYMBOL"
    assert CCXTErrorMapper.classify(MockNotSupported()) == "NOT_SUPPORTED"
    assert CCXTErrorMapper.classify(MockBadRequest()) == "BAD_REQUEST"
    assert CCXTErrorMapper.classify(MockOtherError()) == "EXCHANGE_ERROR"


# ==============================================================================
# 4. SYMBOL NORMALIZATION TESTS
# ==============================================================================

def test_symbol_normalization():
    adapter = CCXTExchangeAdapter(exchange_id="binance", sandbox=True)
    assert adapter.to_ccxt_symbol("BTCUSDT") == "BTC/USDT"
    assert adapter.to_ccxt_symbol("ETHUSDT") == "ETH/USDT"
    assert adapter.to_ccxt_symbol("SOLUSDC") == "SOL/USDC"
    assert adapter.to_ccxt_symbol("BTC/USDT") == "BTC/USDT"

    assert adapter.to_stratex_symbol("BTC/USDT") == "BTCUSDT"
    assert adapter.to_stratex_symbol("ETH/USDT:USDT") == "ETHUSDT"
    assert adapter.to_stratex_symbol("BTCUSDT") == "BTCUSDT"


# ==============================================================================
# 5. EXECUTION SAFETY TESTS
# ==============================================================================

def test_ccxt_create_order_blocks_in_live_mode():
    adapter = CCXTExchangeAdapter(exchange_id="binance", sandbox=True)
    os.environ["TRADING_MODE"] = "LIVE"
    with pytest.raises(PermissionError, match="LIVE trading is permanently blocked"):
        adapter.create_order(symbol="BTCUSDT", order_type="LIMIT", side="BUY", amount=0.01, price=50000.0)
    os.environ["TRADING_MODE"] = "TESTNET"


def test_ccxt_create_order_blocks_in_paper_mode():
    adapter = CCXTExchangeAdapter(exchange_id="binance", sandbox=True)
    os.environ["TRADING_MODE"] = "PAPER"
    with pytest.raises(PermissionError, match="Cannot place real orders in PAPER"):
        adapter.create_order(symbol="BTCUSDT", order_type="LIMIT", side="BUY", amount=0.01, price=50000.0)
    os.environ["TRADING_MODE"] = "TESTNET"


def test_ccxt_create_order_blocks_in_research_mode():
    adapter = CCXTExchangeAdapter(exchange_id="binance", sandbox=True)
    os.environ["RESEARCH_MODE"] = "1"
    with pytest.raises(PermissionError, match="Research mode"):
        adapter.create_order(symbol="BTCUSDT", order_type="LIMIT", side="BUY", amount=0.01, price=50000.0)
    os.environ.pop("RESEARCH_MODE", None)


def test_ccxt_create_order_enforces_authorization_gate():
    adapter = CCXTExchangeAdapter(exchange_id="binance", sandbox=True)
    
    def rejecting_gate(sym, side, amt, px):
        return False, "RISK_GATE_DAILY_LOSS_LIMIT"

    with pytest.raises(PermissionError, match="STRATEX_ORDER_BLOCKED:RISK_GATE_DAILY_LOSS_LIMIT"):
        adapter.create_order(
            symbol="BTCUSDT",
            order_type="LIMIT",
            side="BUY",
            amount=0.01,
            price=50000.0,
            authorize_fn=rejecting_gate
        )


# ==============================================================================
# 6. OHLCV DATAFRAME PARSING TESTS
# ==============================================================================

def test_ccxt_fetch_ohlcv_dataframe(monkeypatch):
    adapter = CCXTExchangeAdapter(exchange_id="binance", sandbox=True)
    mock_raw = [
        [1700000000000, 60000.0, 61000.0, 59500.0, 60500.0, 150.0],
        [1700003600000, 60500.0, 62000.0, 60200.0, 61800.0, 200.0],
    ]
    monkeypatch.setattr(adapter.exchange, "fetch_ohlcv", lambda sym, tf, since, limit: mock_raw)

    df = adapter.fetch_ohlcv_dataframe("BTCUSDT", timeframe="1h")
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert df["close"].iloc[-1] == 61800.0
    assert pd.api.types.is_datetime64_any_dtype(df["timestamp"])


# ==============================================================================
# 7. HEALTH TELEMETRY TESTS
# ==============================================================================

def test_ccxt_health_telemetry():
    adapter = CCXTExchangeAdapter(exchange_id="binance", sandbox=True, enable_rate_limit=True)
    health = adapter.get_health_status()
    assert health["provider"] == "ccxt"
    assert health["exchange_id"] == "binance"
    assert health["sandbox"] is True
    assert health["rate_limit_enabled"] is True
    assert health["status"] == "HEALTHY"
    # Ensure no secrets leak
    assert "apiKey" not in health
    assert "secret" not in health
