"""
tests/test_async_pipeline.py — Unit and integration tests for Async Pipeline & Circuit Breaker.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

from autonomy.async_pipeline import (
    AsyncMarketDataEngine,
    AsyncSignalPipeline,
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitBreakerState,
)


@pytest.mark.asyncio
async def test_circuit_breaker_transitions():
    cb = CircuitBreaker("test_breaker", failure_threshold=2, recovery_timeout_sec=0.1, half_open_success_threshold=1)
    assert cb.state == CircuitBreakerState.CLOSED

    # Failure 1: Still CLOSED
    cb.record_failure(ValueError("fail 1"))
    assert cb.state == CircuitBreakerState.CLOSED

    # Failure 2: Trips to OPEN
    cb.record_failure(ValueError("fail 2"))
    assert cb.state == CircuitBreakerState.OPEN

    # Trying to execute when OPEN raises CircuitBreakerOpenError
    async def dummy():
        return "ok"

    with pytest.raises(CircuitBreakerOpenError):
        await cb.execute(dummy)

    # Wait for recovery timeout
    await asyncio.sleep(0.15)
    assert cb.state == CircuitBreakerState.HALF_OPEN

    # In HALF_OPEN, a success resets to CLOSED
    res = await cb.execute(dummy)
    assert res == "ok"
    assert cb.state == CircuitBreakerState.CLOSED


@pytest.mark.asyncio
async def test_circuit_breaker_half_open_failure_re_opens():
    cb = CircuitBreaker("test_reopen", failure_threshold=1, recovery_timeout_sec=0.05)
    cb.record_failure(ValueError("fail initial"))
    assert cb.state == CircuitBreakerState.OPEN

    await asyncio.sleep(0.08)
    assert cb.state == CircuitBreakerState.HALF_OPEN

    async def faulty():
        raise RuntimeError("failed again in half open")

    with pytest.raises(RuntimeError):
        await cb.execute(faulty)

    assert cb.state == CircuitBreakerState.OPEN


@pytest.mark.asyncio
async def test_async_market_data_engine_ticker_and_klines():
    engine = AsyncMarketDataEngine(base_url="https://mock.testnet.binance")

    req = httpx.Request("GET", "https://mock.testnet.binance")
    mock_ticker_resp = httpx.Response(200, json={"symbol": "BTCUSDT", "price": "65000.50"}, request=req)
    mock_klines_resp = httpx.Response(
        200,
        json=[
            [1700000000000, "65000", "65100", "64900", "65050", "12.5", 1700000900000],
            [1700000900000, "65050", "65200", "65000", "65180", "15.0", 1700001800000],
        ],
        request=req,
    )

    with patch.object(engine._client, "request", side_effect=[mock_ticker_resp, mock_klines_resp]):
        ticker = await engine.get_ticker("BTCUSDT")
        assert ticker["symbol"] == "BTCUSDT"
        assert ticker["price"] == "65000.50"

        klines = await engine.get_klines("BTCUSDT", interval="15m")
        assert len(klines) == 2
        assert klines[0]["open"] == 65000.0
        assert klines[1]["close"] == 65180.0

    await engine.close()


@pytest.mark.asyncio
async def test_async_market_data_engine_multi_symbol_concurrent():
    engine = AsyncMarketDataEngine(base_url="https://mock.testnet.binance")

    def mock_handler(request: httpx.Request):
        url = str(request.url)
        if "BTCUSDT" in url:
            return httpx.Response(200, json={"symbol": "BTCUSDT", "price": "65000.0"})
        elif "ETHUSDT" in url:
            return httpx.Response(200, json={"symbol": "ETHUSDT", "price": "3500.0"})
        return httpx.Response(404, json={"error": "not found"})

    transport = httpx.MockTransport(mock_handler)
    engine._client = httpx.AsyncClient(transport=transport)

    res = await engine.get_multi_tickers(["BTCUSDT", "ETHUSDT"])
    assert "BTCUSDT" in res
    assert res["BTCUSDT"]["price"] == "65000.0"
    assert "ETHUSDT" in res
    assert res["ETHUSDT"]["price"] == "3500.0"

    await engine.close()


@pytest.mark.asyncio
async def test_async_signal_pipeline_scan():
    engine = AsyncMarketDataEngine(base_url="https://mock.testnet.binance")

    candles = [
        {"open": 100 + i, "high": 102 + i, "low": 99 + i, "close": 101 + i, "volume": 10}
        for i in range(15)
    ]
    with patch.object(engine, "get_multi_klines", return_value={"BTCUSDT": candles, "SOLUSDT": candles}):
        pipeline = AsyncSignalPipeline(engine)

        def mock_strategy(symbol, klines):
            if symbol == "BTCUSDT":
                return {"action": "BUY", "confidence": 0.9, "stop_loss": 95.0, "take_profit": 110.0}
            return None

        candidates = await pipeline.scan_universe(["BTCUSDT", "SOLUSDT"], mock_strategy)
        assert len(candidates) == 1
        assert candidates[0]["symbol"] == "BTCUSDT"
        assert candidates[0]["action"] == "BUY"
        assert candidates[0]["confidence"] == 0.9

    await engine.close()
