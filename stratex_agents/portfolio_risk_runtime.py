"""Build a fresh, persistent portfolio-risk context for the existing strategy runner."""
from __future__ import annotations

import json
import os
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
        raise PortfolioRiskStateError("STRATEX_FX_RATES_JSON must be a currency-to-rate object")
    rates: dict[str, float] = {}
    for currency, raw_rate in payload.items():
        code = str(currency).strip().upper()
        rate = float(raw_rate)
        if not code or rate <= 0 or rate != rate or abs(rate) == float("inf"):
            raise PortfolioRiskStateError("FX rates must be positive finite numbers keyed by currency")
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
    try:
        fx_rates = _fx_rates_from_environment()
        agent = portfolio_agent or PortfolioAgent.from_environment(
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
        if intent.venue == "binance":
            try:
                price = float(context["entry_price"])
                quantity = float(intent.quantity)
                if price <= 0 or price != price or abs(price) == float("inf"):
                    raise ValueError("entry price is invalid")
                raw_notional = price * quantity
                raw_currency = str(context.get("candidate_notional_currency", "USDT")).strip().upper()
                rate = 1.0 if raw_currency == reporting_currency else fx_rates.get(raw_currency)
                if rate is None:
                    raise ValueError(f"no explicit FX rate for candidate notional currency {raw_currency}")
                context["candidate_notional"] = raw_notional * rate
                context["candidate_notional_currency"] = reporting_currency
            except Exception as exc:
                context["candidate_notional_error"] = type(exc).__name__
    return context
