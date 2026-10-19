import pytest

from stratex_agents.portfolio_risk_state import PortfolioRiskStateError, PortfolioRiskStateStore


def healthy_snapshot(equity=1000.0, captured_at=1_800_000_000.0):
    return {
        "status": "healthy",
        "captured_at": captured_at,
        "reporting_currency": "USD",
        "currency_conversion_complete": True,
        "equity_basis": "account_equity",
        "reporting_equity": equity,
    }


def test_risk_state_persists_day_start_and_high_water_across_restart(tmp_path):
    path = str(tmp_path / "risk.sqlite3")
    first = PortfolioRiskStateStore(path)
    one = first.observe(account_key="demo", reporting_currency="USD", equity=1000, timestamp=1_800_000_000)
    two = PortfolioRiskStateStore(path).observe(
        account_key="demo", reporting_currency="USD", equity=950, timestamp=1_800_000_100
    )
    three = PortfolioRiskStateStore(path).observe(
        account_key="demo", reporting_currency="USD", equity=1020, timestamp=1_800_000_200
    )
    assert one["day_start_equity"] == 1000
    assert two["day_start_equity"] == 1000
    assert two["high_water_equity"] == 1000
    assert three["day_start_equity"] == 1000
    assert three["high_water_equity"] == 1020


def test_risk_state_resets_daily_baseline_at_utc_day_boundary(tmp_path):
    store = PortfolioRiskStateStore(str(tmp_path / "risk.sqlite3"))
    one = store.observe(account_key="demo", reporting_currency="USD", equity=1000, timestamp=1_800_000_000)
    next_day = store.observe(
        account_key="demo", reporting_currency="USD", equity=900,
        timestamp=1_800_000_000 + 86400,
    )
    assert next_day["utc_day"] != one["utc_day"]
    assert next_day["day_start_equity"] == 900
    assert next_day["high_water_equity"] == 900


def test_risk_state_refuses_estimated_or_degraded_portfolio(tmp_path):
    store = PortfolioRiskStateStore(str(tmp_path / "risk.sqlite3"))
    with pytest.raises(PortfolioRiskStateError, match="healthy"):
        store.enrich_snapshot({"status": "degraded"}, account_key="demo")
    estimated = healthy_snapshot()
    estimated["equity_basis"] = "balance_total_estimate"
    with pytest.raises(PortfolioRiskStateError, match="broker account equity"):
        store.enrich_snapshot(estimated, account_key="demo")


def test_risk_state_enriches_verified_snapshot(tmp_path):
    store = PortfolioRiskStateStore(str(tmp_path / "risk.sqlite3"))
    result = store.enrich_snapshot(healthy_snapshot(), account_key="binance-demo")
    assert result["equity_basis"] == "account_equity"
    assert result["day_start_equity"] == 1000
    assert result["high_water_equity"] == 1000
    assert result["reporting_equity"] == 1000
