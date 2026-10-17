import pytest

from stratex_agents.order_intent import TradeIntent
from stratex_agents.portfolio_risk_gate import PortfolioRiskGate


def intent():
    return TradeIntent(
        venue="binance", symbol="BTCUSDT", side="BUY", order_type="MARKET",
        quantity=0.01, environment="binance_demo", stop_loss=95.0, take_profit=110.0,
        strategy="portfolio-risk-test", client_order_id="stx-port-risk-001",
    )


def snapshot(**overrides):
    value = {
        "status": "healthy",
        "captured_at": 1000.0,
        "reporting_currency": "USD",
        "currency_conversion_complete": True,
        "equity_basis": "account_equity",
        "reporting_equity": 1000.0,
        "day_start_equity": 1000.0,
        "high_water_equity": 1000.0,
        "positions": [],
    }
    value.update(overrides)
    return value


def context(portfolio=None, candidate_notional=10.0):
    return {"portfolio": portfolio or snapshot(), "candidate_notional": candidate_notional, "candidate_notional_currency": "USD"}


def gate(**kwargs):
    return PortfolioRiskGate(clock=lambda: 1000.0, **kwargs)


def test_portfolio_risk_gate_approves_healthy_small_demo_entry():
    allowed, reason, details = gate().evaluate(intent(), context())
    assert allowed is True
    assert reason == "PORTFOLIO_RISK_OK"
    assert "projected_exposure_pct=0.010000" in details


@pytest.mark.parametrize(
    ("updates", "expected"),
    [
        ({"status": "degraded"}, "PORTFOLIO_DEGRADED"),
        ({"currency_conversion_complete": False}, "PORTFOLIO_CURRENCY_CONVERSION_INCOMPLETE"),
        ({"captured_at": 900.0}, "PORTFOLIO_SNAPSHOT_STALE"),
        ({"day_start_equity": 1000.0, "reporting_equity": 970.0}, "DAILY_LOSS_LIMIT_REACHED"),
        ({"day_start_equity": 900.0, "high_water_equity": 1000.0, "reporting_equity": 940.0}, "MAX_DRAWDOWN_REACHED"),
        ({"equity_basis": "balance_total_estimate"}, "PORTFOLIO_ACCOUNT_EQUITY_REQUIRED"),
        ({"positions": [{"notional_available": False}]}, "POSITION_NOTIONAL_UNAVAILABLE"),
    ],
)
def test_portfolio_risk_gate_fails_closed_on_unsafe_snapshot(updates, expected):
    allowed, reason, _ = gate().evaluate(intent(), context(snapshot(**updates)))
    assert allowed is False
    assert reason == expected


def test_portfolio_risk_gate_rejects_missing_candidate_notional():
    allowed, reason, _ = gate().evaluate(intent(), {"portfolio": snapshot()})
    assert allowed is False
    assert reason == "PORTFOLIO_RISK_CONTEXT_INVALID"


def test_portfolio_risk_gate_rejects_gross_exposure_breach():
    portfolio = snapshot(positions=[{
        "notional_available": True, "notional": 49.0, "notional_currency": "USD",
    }])
    allowed, reason, _ = gate(max_gross_exposure_pct=0.05).evaluate(
        intent(), context(portfolio, candidate_notional=2.0)
    )
    assert allowed is False
    assert reason == "MAX_GROSS_EXPOSURE_REACHED"


def test_portfolio_risk_gate_rejects_maximum_position_count():
    portfolio = snapshot(positions=[
        {"notional_available": True, "notional": 1.0, "notional_currency": "USD"}
        for _ in range(5)
    ])
    allowed, reason, _ = gate(max_open_positions=5).evaluate(intent(), context(portfolio))
    assert allowed is False
    assert reason == "MAX_OPEN_POSITIONS_REACHED"


def test_portfolio_risk_gate_rejects_invalid_limits():
    with pytest.raises(ValueError):
        gate(max_daily_loss_pct=1.0)
