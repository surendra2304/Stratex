from dashboard import api_v1_portfolio_snapshot, app
from stratex_agents.portfolio_agent import PortfolioAgent


class FakePortfolioAgent:
    def snapshot(self, reporting_currency="USD"):
        return {
            "agent_id": "portfolio",
            "status": "degraded",
            "reporting_currency": reporting_currency,
            "reporting_equity": None,
            "currency_conversion_complete": False,
            "native_balance_totals": {"USD": 1000, "USDT": 900},
            "open_position_count": 2,
            "execution_authority": False,
        }


def test_portfolio_snapshot_endpoint_is_read_only_and_explicit_about_fx(monkeypatch):
    monkeypatch.setattr(
        PortfolioAgent, "from_environment",
        classmethod(lambda cls: FakePortfolioAgent()),
    )
    with app.test_request_context("/api/v1/portfolio/snapshot?currency=USD"):
        response = app.make_response(api_v1_portfolio_snapshot.__wrapped__())
    payload = response.get_json()
    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["portfolio"]["reporting_equity"] is None
    assert payload["portfolio"]["currency_conversion_complete"] is False
    assert payload["portfolio"]["native_balance_totals"] == {"USD": 1000, "USDT": 900}
    assert payload["portfolio"]["execution_authority"] is False


def test_portfolio_snapshot_endpoint_normalizes_reporting_currency(monkeypatch):
    observed = {}

    class CurrencyAgent:
        def snapshot(self, reporting_currency="USD"):
            observed["currency"] = reporting_currency
            return {"status": "healthy", "reporting_currency": reporting_currency}

    monkeypatch.setattr(
        PortfolioAgent, "from_environment",
        classmethod(lambda cls: CurrencyAgent()),
    )
    with app.test_request_context("/api/v1/portfolio/snapshot?currency=usd"):
        response = app.make_response(api_v1_portfolio_snapshot.__wrapped__())
    assert response.status_code == 200
    assert observed["currency"] == "USD"
    assert response.get_json()["portfolio"]["reporting_currency"] == "USD"


def test_portfolio_snapshot_endpoint_rejects_invalid_currency_codes():
    from api.validation import RequestValidationError

    with app.test_request_context("/api/v1/portfolio/snapshot?currency=USD%3BDELETE"):
        try:
            api_v1_portfolio_snapshot.__wrapped__()
        except RequestValidationError as exc:
            assert exc.field == "currency"
        else:
            raise AssertionError("Invalid reporting currency should be rejected")
