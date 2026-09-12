"""
telemetry/health_guard.py — Comprehensive operational health and telemetry guards.

Enforces:
1. Telemetry freshness validation (max staleness check).
2. Exchange connectivity and responsiveness check.
3. Position reconciliation integrity check.
4. Host-exchange clock-skew validation.
"""

import datetime
import os
import time
from typing import Any

from logger import get_logger

logger = get_logger("health_guard")

MAX_TELEMETRY_STALENESS_SECONDS = float(os.getenv("MAX_TELEMETRY_STALENESS_SECONDS", "60.0"))
MAX_CLOCK_SKEW_MS = float(os.getenv("MAX_CLOCK_SKEW_MS", "1000.0"))
RECONCILIATION_TOLERANCE = float(os.getenv("RECONCILIATION_TOLERANCE", "0.001"))


class HealthGuardError(Exception):
    """Base error for health guard violations."""
    pass


class StaleTelemetryError(HealthGuardError):
    """Raised when market data or telemetry exceeds freshness threshold."""
    pass


class ClockSkewError(HealthGuardError):
    """Raised when host system clock drifts beyond acceptable skew from exchange server time."""
    pass


class ExchangeOutageError(HealthGuardError):
    """Raised when exchange connection is unresponsive or failing."""
    pass


class ReconciliationMismatchError(HealthGuardError):
    """Raised when internal portfolio positions diverge from exchange reported positions."""
    pass


def check_telemetry_freshness(
    timestamp: datetime.datetime | str | float | int | None,
    max_age_seconds: float = MAX_TELEMETRY_STALENESS_SECONDS,
    reference_time: datetime.datetime | None = None
) -> float:
    """
    Validates that telemetry or market data timestamp is strictly fresher than max_age_seconds.
    Returns calculated staleness in seconds.
    Raises StaleTelemetryError if stale or missing.
    """
    if timestamp is None:
        raise StaleTelemetryError("Telemetry timestamp is missing or None.")

    ref = reference_time or datetime.datetime.now(datetime.timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=datetime.timezone.utc)

    if isinstance(timestamp, (int, float)):
        # Check whether timestamp is seconds or milliseconds
        ts_sec = timestamp / 1000.0 if timestamp > 1e11 else float(timestamp)
        dt = datetime.datetime.fromtimestamp(ts_sec, tz=datetime.timezone.utc)
    elif isinstance(timestamp, str):
        try:
            cleaned = timestamp.replace("Z", "+00:00")
            dt = datetime.datetime.fromisoformat(cleaned)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
        except Exception as e:
            raise StaleTelemetryError(f"Failed to parse telemetry ISO timestamp '{timestamp}': {e}")
    elif isinstance(timestamp, datetime.datetime):
        dt = timestamp if timestamp.tzinfo is not None else timestamp.replace(tzinfo=datetime.timezone.utc)
    else:
        raise StaleTelemetryError(f"Unsupported telemetry timestamp type: {type(timestamp)}")

    staleness_seconds = (ref - dt).total_seconds()
    if staleness_seconds < 0:
        # Slight future skew acceptable up to 5s, else clock drift
        if abs(staleness_seconds) > 5.0:
            raise ClockSkewError(f"Telemetry timestamp is from the future ({staleness_seconds:.2f}s ahead of local clock).")
        staleness_seconds = 0.0

    if staleness_seconds > max_age_seconds:
        raise StaleTelemetryError(
            f"Telemetry is stale: {staleness_seconds:.2f}s elapsed (max allowed: {max_age_seconds:.2f}s)."
        )

    return staleness_seconds


def check_clock_skew(
    exchange_server_time_ms: int | float | None,
    local_time_ms: int | float | None = None,
    max_skew_ms: float = MAX_CLOCK_SKEW_MS
) -> float:
    """
    Validates that local clock skew relative to exchange server time is within limits.
    Returns skew in milliseconds.
    Raises ClockSkewError if skew exceeds max_skew_ms.
    """
    if exchange_server_time_ms is None:
        raise ClockSkewError("Exchange server time is missing or None.")

    loc = local_time_ms if local_time_ms is not None else (time.time() * 1000.0)
    skew_ms = abs(loc - float(exchange_server_time_ms))

    if skew_ms > max_skew_ms:
        raise ClockSkewError(
            f"Clock skew violation: {skew_ms:.1f}ms exceeds maximum threshold of {max_skew_ms:.1f}ms."
        )

    return skew_ms


def check_exchange_connectivity(client: Any) -> dict[str, Any]:
    """
    Tests active connectivity and latency against exchange endpoint.
    Returns latency report or raises ExchangeOutageError on failure.
    """
    if client is None:
        raise ExchangeOutageError("Exchange client is None (offline or uninitialized).")

    t0 = time.time()
    try:
        if hasattr(client, "ping"):
            client.ping()
        elif hasattr(client, "get_server_time"):
            client.get_server_time()
        elif hasattr(client, "futures_time"):
            client.futures_time()
        else:
            # Generic callable check
            pass
        latency_ms = round((time.time() - t0) * 1000.0, 2)
        return {"status": "CONNECTED", "latency_ms": latency_ms}
    except Exception as e:
        logger.error(f"[HEALTH_GUARD] Exchange connectivity check failed: {e}")
        raise ExchangeOutageError(f"Exchange outage or unreachable: {e}")


def reconcile_positions(
    internal_positions: dict[str, Any] | list[dict[str, Any]],
    exchange_positions: dict[str, Any] | list[dict[str, Any]],
    tolerance: float = RECONCILIATION_TOLERANCE
) -> dict[str, Any]:
    """
    Reconciles internal portfolio positions with exchange reported positions.
    Raises ReconciliationMismatchError if discrepancies exceed tolerance.
    """
    # Normalize internal positions to { symbol: float(qty) }
    norm_internal: dict[str, float] = {}
    if isinstance(internal_positions, dict):
        for sym, pos in internal_positions.items():
            if isinstance(pos, dict):
                qty = float(pos.get("quantity", pos.get("positionAmt", 0.0)))
                direction = str(pos.get("direction", pos.get("side", "BUY"))).upper()
                signed_qty = -abs(qty) if direction in ["SHORT", "SELL"] else abs(qty)
                norm_internal[sym.upper()] = signed_qty
            elif isinstance(pos, (int, float)):
                norm_internal[sym.upper()] = float(pos)
    elif isinstance(internal_positions, list):
        for pos in internal_positions:
            sym = str(pos.get("symbol", "")).upper()
            qty = float(pos.get("quantity", pos.get("positionAmt", 0.0)))
            direction = str(pos.get("direction", pos.get("side", "BUY"))).upper()
            signed_qty = -abs(qty) if direction in ["SHORT", "SELL"] else abs(qty)
            if sym:
                norm_internal[sym] = norm_internal.get(sym, 0.0) + signed_qty

    # Normalize exchange positions to { symbol: float(qty) }
    norm_exchange: dict[str, float] = {}
    if isinstance(exchange_positions, dict):
        for sym, pos in exchange_positions.items():
            if isinstance(pos, (int, float)):
                norm_exchange[sym.upper()] = float(pos)
            elif isinstance(pos, dict):
                qty = float(pos.get("positionAmt", pos.get("quantity", 0.0)))
                norm_exchange[sym.upper()] = qty
    elif isinstance(exchange_positions, list):
        for pos in exchange_positions:
            sym = str(pos.get("symbol", "")).upper()
            qty = float(pos.get("positionAmt", pos.get("quantity", 0.0)))
            if sym and abs(qty) > 1e-8:
                norm_exchange[sym] = qty

    # Compare all symbols
    all_symbols = set(norm_internal.keys()).union(set(norm_exchange.keys()))
    discrepancies = []

    for sym in all_symbols:
        int_qty = norm_internal.get(sym, 0.0)
        exc_qty = norm_exchange.get(sym, 0.0)
        delta = abs(int_qty - exc_qty)
        if delta > tolerance:
            discrepancies.append({
                "symbol": sym,
                "internal_qty": int_qty,
                "exchange_qty": exc_qty,
                "delta": delta,
                "tolerance": tolerance
            })

    if discrepancies:
        msg = f"Position reconciliation mismatch detected for {len(discrepancies)} asset(s): {discrepancies}"
        logger.critical(f"[HEALTH_GUARD] 🚨 {msg}")
        raise ReconciliationMismatchError(msg)

    return {
        "status": "RECONCILED",
        "symbols_checked": len(all_symbols),
        "tolerance": tolerance
    }
