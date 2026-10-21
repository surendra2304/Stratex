"""
autonomy/async_pipeline.py — High-Throughput Asynchronous Pipeline & Circuit Breakers.

Provides enterprise-grade async execution primitives for Stratex inside the FRIDAY Universe:
1. Circuit Breaker (`CircuitBreaker`, `CircuitBreakerState`):
   - Fault-tolerant upstream failure isolation (CLOSED -> OPEN -> HALF_OPEN).
   - Configurable consecutive failure threshold, recovery timeout, and half-open probes.
   - Exponential backoff with jitter on network retries.
2. Async Market Data Engine (`AsyncMarketDataEngine`):
   - Non-blocking HTTP I/O powered by `httpx.AsyncClient`.
   - Connection pooling (HTTP keep-alive) and bounded concurrency semaphore.
   - Concurrent multi-symbol ticker and kline fetching via `asyncio.gather`.
3. Async Signal Pipeline (`AsyncSignalPipeline`):
   - Concurrent strategy signal generation over multi-symbol market universes.
"""
from __future__ import annotations

import asyncio
import datetime
import enum
import math
import random
import time
from typing import Any, Callable, Coroutine

import httpx

from logger import get_logger

logger = get_logger("async_pipeline")


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


# ==============================================================================
# 1. CIRCUIT BREAKER
# ==============================================================================

class CircuitBreakerState(str, enum.Enum):
    CLOSED = "CLOSED"        # Normal operation: requests pass through.
    OPEN = "OPEN"            # Tripped: requests immediately fail/short-circuit.
    HALF_OPEN = "HALF_OPEN"  # Probing: limited test requests to test recovery.


class CircuitBreakerOpenError(RuntimeError):
    """Raised when an operation is attempted while the circuit breaker is OPEN."""
    pass


class CircuitBreaker:
    """
    Resilient state-machine circuit breaker for external service endpoints.
    Protects downstream trading loops from upstream dependency outages.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_timeout_sec: float = 30.0,
        half_open_success_threshold: int = 2,
    ):
        self.name = name
        self.failure_threshold = max(1, failure_threshold)
        self.recovery_timeout_sec = max(0.01, recovery_timeout_sec)
        self.half_open_success_threshold = max(1, half_open_success_threshold)

        self._state = CircuitBreakerState.CLOSED
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._last_state_change = time.time()
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitBreakerState:
        # Check for state transition from OPEN to HALF_OPEN after timeout
        if self._state == CircuitBreakerState.OPEN:
            if time.time() - self._last_state_change >= self.recovery_timeout_sec:
                self._state = CircuitBreakerState.HALF_OPEN
                self._consecutive_successes = 0
                self._last_state_change = time.time()
                logger.info(f"[CIRCUIT_BREAKER] '{self.name}' transitioned from OPEN to HALF_OPEN (probing).")
        return self._state

    def record_success(self) -> None:
        """Record a successful upstream operation."""
        self._consecutive_failures = 0
        if self.state == CircuitBreakerState.HALF_OPEN:
            self._consecutive_successes += 1
            if self._consecutive_successes >= self.half_open_success_threshold:
                self._state = CircuitBreakerState.CLOSED
                self._last_state_change = time.time()
                logger.info(f"[CIRCUIT_BREAKER] '{self.name}' recovered: CLOSED.")

    def record_failure(self, error: Exception | None = None) -> None:
        """Record an upstream failure."""
        self._consecutive_failures += 1
        self._consecutive_successes = 0
        if self._consecutive_failures >= self.failure_threshold or self.state == CircuitBreakerState.HALF_OPEN:
            self._state = CircuitBreakerState.OPEN
            self._last_state_change = time.time()
            logger.warning(
                f"[CIRCUIT_BREAKER] '{self.name}' tripped to OPEN after {self._consecutive_failures} failures. "
                f"Error: {error}"
            )

    async def execute(self, coro_func: Callable[[], Coroutine[Any, Any, Any]], *args, **kwargs) -> Any:
        """Execute a coroutine protected by this circuit breaker."""
        async with self._lock:
            current_state = self.state
            if current_state == CircuitBreakerState.OPEN:
                raise CircuitBreakerOpenError(
                    f"Circuit breaker '{self.name}' is OPEN. Fast-failing request."
                )

        try:
            result = await coro_func(*args, **kwargs)
            async with self._lock:
                self.record_success()
            return result
        except Exception as exc:
            async with self._lock:
                self.record_failure(exc)
            raise


# ==============================================================================
# 2. ASYNC MARKET DATA ENGINE
# ==============================================================================

class AsyncMarketDataEngine:
    """
    High-throughput asynchronous market data client for Binance USDⓈ-M Futures.
    Features:
    - Connection pooling via httpx.AsyncClient with persistent keep-alive.
    - Concurrency semaphore to throttle requests and avoid rate-limiting.
    - Circuit breaker protection on upstream HTTP calls.
    - Retry logic with exponential backoff and randomized jitter.
    """

    def __init__(
        self,
        base_url: str = "https://testnet.binancefuture.com",
        max_connections: int = 50,
        max_keepalive: int = 20,
        concurrency_limit: int = 10,
        timeout_sec: float = 5.0,
        circuit_breaker: CircuitBreaker | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec
        self.semaphore = asyncio.Semaphore(concurrency_limit)
        self.breaker = circuit_breaker or CircuitBreaker("binance_futures_market_data")

        # Configure connection pool limits
        limits = httpx.Limits(max_connections=max_connections, max_keepalive_connections=max_keepalive)
        self._client = httpx.AsyncClient(limits=limits, timeout=timeout_sec)

    async def close(self) -> None:
        """Gracefully close underlying client session."""
        if not self._client.is_closed:
            await self._client.aclose()

    async def _request_with_retry(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        max_retries: int = 2,
    ) -> Any:
        """Execute HTTP request with concurrency bounds, retries, and circuit breaker."""
        url = f"{self.base_url}{path}"

        async def _do_req():
            async with self.semaphore:
                for attempt in range(max_retries + 1):
                    try:
                        resp = await self._client.request(method, url, params=params)
                        resp.raise_for_status()
                        return resp.json()
                    except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                        if attempt == max_retries:
                            raise
                        # Exponential backoff with jitter
                        delay = (2 ** attempt) * 0.1 + random.uniform(0.01, 0.05)
                        await asyncio.sleep(delay)

        return await self.breaker.execute(_do_req)

    async def get_ticker(self, symbol: str) -> dict[str, Any]:
        """Fetch current symbol ticker price."""
        return await self._request_with_retry("GET", "/fapi/v1/ticker/price", params={"symbol": symbol})

    async def get_klines(self, symbol: str, interval: str = "15m", limit: int = 100) -> list[dict[str, Any]]:
        """Fetch historical klines/candles."""
        raw = await self._request_with_retry(
            "GET", "/fapi/v1/klines", params={"symbol": symbol, "interval": interval, "limit": limit}
        )
        candles = []
        for row in raw:
            candles.append({
                "open_time": row[0],
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "close_time": row[6],
            })
        return candles

    async def get_multi_tickers(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        """Fetch multiple symbol tickers concurrently."""
        tasks = [self.get_ticker(sym) for sym in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        res_map = {}
        for sym, r in zip(symbols, results):
            if isinstance(r, Exception):
                logger.warning(f"[ASYNC_ENGINE] Failed fetching ticker for {sym}: {r}")
                res_map[sym] = {"symbol": sym, "price": None, "error": str(r)}
            else:
                res_map[sym] = r
        return res_map

    async def get_multi_klines(
        self, symbols: list[str], interval: str = "15m", limit: int = 100
    ) -> dict[str, list[dict[str, Any]]]:
        """Fetch klines for multiple symbols concurrently."""
        tasks = [self.get_klines(sym, interval=interval, limit=limit) for sym in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        res_map = {}
        for sym, r in zip(symbols, results):
            if isinstance(r, Exception):
                logger.warning(f"[ASYNC_ENGINE] Failed fetching klines for {sym}: {r}")
                res_map[sym] = []
            else:
                res_map[sym] = r
        return res_map


# ==============================================================================
# 3. ASYNC SIGNAL PIPELINE
# ==============================================================================

class AsyncSignalPipeline:
    """
    Evaluates multi-symbol universe asynchronously against trading strategies.
    Produces standardized, candidate signal records without blocking on network queries.
    """

    def __init__(self, market_engine: AsyncMarketDataEngine):
        self.engine = market_engine

    async def scan_universe(
        self,
        symbols: list[str],
        strategy_fn: Callable[[str, list[dict[str, Any]]], dict[str, Any] | None],
        interval: str = "15m",
        candle_limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Concurrently load candles and evaluate a strategy across a universe of symbols.
        Returns all actionable signal candidates.
        """
        all_klines = await self.engine.get_multi_klines(symbols, interval=interval, limit=candle_limit)
        candidates = []

        for symbol, klines in all_klines.items():
            if not klines or len(klines) < 10:
                continue
            try:
                sig = strategy_fn(symbol, klines)
                if sig and isinstance(sig, dict) and sig.get("action") in {"BUY", "SELL", "LONG", "SHORT"}:
                    candidates.append({
                        **sig,
                        "symbol": symbol,
                        "evaluated_at": _utc_now_iso(),
                    })
            except Exception as exc:
                logger.warning(f"[SIGNAL_PIPELINE] Strategy evaluation failed on {symbol}: {exc}")

        return candidates
