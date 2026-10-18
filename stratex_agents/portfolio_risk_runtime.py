"""Build a fresh, persistent portfolio-risk context for the existing strategy runner."""
from __future__ import annotations

import json
import os
import time
from typing import Any

from .order_intent import TradeIntent
from .portfolio_agent import PortfolioAgent
from .portfolio_risk_state import PortfolioRiskStateError, PortfolioRiskStateStore


def _fx_rates_from_environment() -> dict[str, float]:
    raw = os.getenv("STRATEX_FX_RATES_JSON", "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise PortfolioRiskStateError("STRATEX_FX_RATES_JSON must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise PortfolioRiskStateError("STRATEX_FX_RATES_JSON must map currencies to {rate,timestamp} objects")
    max_age = float(os.getenv("STRATEX_FX_MAX_AGE_SECONDS", "300"))
    if not 1 <= max_age <= 3600:
        raise PortfolioRiskStateError("STRATEX_FX_MAX_AGE_SECONDS must be in [1, 3600]")
    now = time.time()
    rates: dict[str, float] = {}
    for currency, record in payload.items():
        code = str(currency).strip().upper()
        if not isinstance(record, dict) or "rate" not in record or "timestamp" not in record:
            raise PortfolioRiskStateError(f"FX rate for {code} must include rate and timestamp")
        try:
            rate = float(record["rate"])
            timestamp = float(record["timestamp"])
        except (TypeError, ValueError) as exc:
            raise PortfolioRiskStateError(f"FX rate/timestamp for {code} must be numeric") from exc
        if not code or rate <= 0 or not all(map(lambda n: n == n and abs(n) != float("inf"), (rate, timestamp))):
            raise PortfolioRiskStateError("FX rates and timestamps must be finite; rates must be positive")
        age = now - timestamp
        if age < -2 or age > max_age:
            raise PortfolioRiskStateError(f"FX rate for {code} is stale or future-dated")
        rates[code] = rate
    return rates


def build_portfolio_risk_context(
    intent: TradeIntent,
    base_context: dict[str, Any],
    *,
    portfolio_agent: PortfolioAgent | None = None,
    state_store: PortfolioRiskStateStore | None = None,
) -> dict[str, Any]:
    """Refresh account state and attach durable baselines before deterministic authorization.

    Binance candidate notional is derived from the caller's validated entry price
    and converted only with an explicit configured FX rate. MT5 callers must
    supply broker-calculated notional and its currency because lot units are not
    comparable to crypto base quantity.
    """
    if not isinstance(base_context, dict):
        raise PortfolioRiskStateError("base risk context must be a dictionary")
    context = dict(base_context)
    reporting_currency = os.getenv("STRATEX_REPORTING_CURRENCY", "USD").strip().upper()
    account_key = os.getenv("STRATEX_PORTFOLIO_ACCOUNT_KEY", "stratex-demo").strip()
    fx_rates: dict[str, float] = {}
    agent = portfolio_agent
    try:
        fx_rates = _fx_rates_from_environment()
        agent = agent or PortfolioAgent.from_environment(
            currency_to_reporting_rate=fx_rates,
        )
        snapshot = agent.snapshot(reporting_currency=reporting_currency)
        store = state_store or PortfolioRiskStateStore(
            os.getenv("STRATEX_PORTFOLIO_RISK_DB", "state/portfolio_risk.sqlite3")
        )
        context["portfolio"] = store.enrich_snapshot(snapshot, account_key=account_key)
    except Exception as exc:
        context["portfolio"] = {
            "status": "degraded",
            "reason": type(exc).__name__,
            "reporting_currency": reporting_currency,
            "currency_conversion_complete": False,
            "equity_basis": "unavailable",
        }

    if "candidate_notional" not in context:
        try:
            if intent.venue == "binance":
                price = float(context["entry_price"])
                symbol = intent.symbol.upper()
                raw_currency = next(
                    (quote for quote in ("USDT", "USDC", "FDUSD", "BUSD") if symbol.endswith(quote)),
                    "",
                )
                if not raw_currency:
                    raise ValueError("unsupported Binance quote currency for portfolio exposure conversion")
            elif intent.venue == "mt5":
                adapter = getattr(agent, "venues", {}).get("mt5") if agent is not None else None
                if adapter is None or not callable(getattr(adapter, "get_symbol_info", None)):
                    raise ValueError("authenticated MT5 symbol metadata is unavailable")
                info = adapter.get_symbol_info(intent.symbol)
                calc_mode = int(info.get("calc_mode", -1))
                # Known linear FX/CFD/stocks calculation modes only. Unknown/inverse
                # contract types require a dedicated notional calculator.
                if calc_mode not in {0, 1, 3, 4, 5, 32}:
                    raise ValueError(f"unsupported MT5 contract calculation mode {calc_mode}")
                contract_size = float(info.get("contract_size", 0))
                price = float(adapter.get_ticker(intent.symbol).last)
                raw_currency = str(info.get("profit_currency", "")).strip().upper()
                if contract_size <= 0 or not raw_currency:
                    raise ValueError("MT5 contract size/profit currency is unavailable")
                raw_notional = float(intent.quantity) * contract_size * price
            else:
                raise ValueError("unsupported venue for candidate notional")
            if intent.venue == "binance":
                raw_notional = float(intent.quantity) * price
            if price <= 0 or price != price or abs(price) == float("inf"):
                raise ValueError("entry/mark price is invalid")
            if raw_notional <= 0 or raw_notional != raw_notional or abs(raw_notional) == float("inf"):
                raise ValueError("candidate notional is invalid")
            rate = 1.0 if raw_currency == reporting_currency else fx_rates.get(raw_currency)
            if rate is None:
                raise ValueError(f"no explicit FX rate for candidate notional currency {raw_currency}")
            converted_notional = raw_notional * rate
            if converted_notional <= 0 or converted_notional != converted_notional or abs(converted_notional) == float("inf"):
                raise ValueError("converted candidate notional is invalid")
            # Publish candidate_notional only after the full currency conversion
            # has passed validation. A failed FX check must never leave a usable
            # unconverted notional in the risk context.
            context["candidate_notional"] = converted_notional
            context["candidate_notional_currency"] = reporting_currency
        except Exception as exc:
            context["candidate_notional_error"] = type(exc).__name__
    return context
