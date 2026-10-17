import json
import time

import pytest

from stratex_agents.order_intent import TradeIntent
from stratex_agents.portfolio_risk_runtime import build_portfolio_risk_context
from stratex_agents.portfolio_risk_state import PortfolioRiskStateStore


class FakePortfolioAgent:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def snapshot(self, reporting_currency="USD"):
        self.calls.append(reporting_currency)
        return dict(self.result)


def demo_intent(venue="binance", quantity=0.01):
    return TradeIntent(
        venue=venue,
        symbol="BTCUSDT" if venue == "binance" else "XAUUSD.a",
        side="BUY", order_type="MARKET", quantity=quantity,
        environment="binance_demo" if venue == "binance" else "mt5_demo",
        stop_loss=95.0, take_profit=110.0, strategy="risk-runtime-test",
        client_order_id="stx-risk-runtime-001",
    )


def healthy_snapshot():
    return {
        "status": "healthy",
        "captured_at": 1_800_000_000.0,
        "reporting_currency": "USD",
        "currency_conversion_complete": True,
        "equity_basis": "account_equity",
        "reporting_equity": 1000.0,
        "positions": [],
        "venues": {"binance": {"status": "healthy"}},
    }


def test_runtime_builder_adds_persisted_portfolio_and_explicitly_converted_candidate(monkeypatch, tmp_path):
    monkeypatch.setenv("STRATEX_REPORTING_CURRENCY", "USD")
    monkeypatch.setenv("STRATEX_FX_RATES_JSON", json.dumps({"USDT": {"rate": 1.0, "timestamp": time.time()}}))
    agent = FakePortfolioAgent(healthy_snapshot())
    store = PortfolioRiskStateStore(str(tmp_path / "risk.sqlite3"))
    context = build_portfolio_risk_context(
        demo_intent(),
        {"entry_price": 100.0},
        portfolio_agent=agent,
        state_store=store,
    )
    assert context["portfolio"]["day_start_equity"] == 1000.0
    assert context["portfolio"]["high_water_equity"] == 1000.0
    assert context["candidate_notional"] == pytest.approx(1.0)
    assert context["candidate_notional_currency"] == "USD"
    assert agent.calls == ["USD"]


def test_runtime_builder_fails_closed_when_fx_rate_is_not_explicit(monkeypatch, tmp_path):
    monkeypatch.setenv("STRATEX_REPORTING_CURRENCY", "USD")
    monkeypatch.delenv("STRATEX_FX_RATES_JSON", raising=False)
    agent = FakePortfolioAgent(healthy_snapshot())
    context = build_portfolio_risk_context(
        demo_intent(),
        {"entry_price": 100.0},
        portfolio_agent=agent,
        state_store=PortfolioRiskStateStore(str(tmp_path / "risk.sqlite3")),
    )
    assert context["portfolio"]["status"] == "healthy"
    assert "candidate_notional" not in context
    assert context["candidate_notional_error"] == "ValueError"


def test_runtime_builder_requires_explicit_mt5_candidate_notional(monkeypatch, tmp_path):
    monkeypatch.setenv("STRATEX_REPORTING_CURRENCY", "USD")
    monkeypatch.setenv("STRATEX_FX_RATES_JSON", json.dumps({"USD": {"rate": 1.0, "timestamp": time.time()}}))
    context = build_portfolio_risk_context(
        demo_intent("mt5", quantity=0.1),
        {},
        portfolio_agent=FakePortfolioAgent(healthy_snapshot()),
        state_store=PortfolioRiskStateStore(str(tmp_path / "risk.sqlite3")),
    )
    assert "candidate_notional" not in context



def test_runtime_builder_rejects_stale_fx_rate(monkeypatch, tmp_path):
    monkeypatch.setenv("STRATEX_REPORTING_CURRENCY", "USD")
    monkeypatch.setenv(
        "STRATEX_FX_RATES_JSON",
        json.dumps({"USDT": {"rate": 1.0, "timestamp": time.time() - 3600}}),
    )
    context = build_portfolio_risk_context(
        demo_intent(),
        {"entry_price": 100.0},
        portfolio_agent=FakePortfolioAgent(healthy_snapshot()),
        state_store=PortfolioRiskStateStore(str(tmp_path / "risk.sqlite3")),
    )
    assert context["portfolio"]["status"] == "degraded"
    assert "candidate_notional" not in context
    assert context["candidate_notional_error"] == "ValueError"
