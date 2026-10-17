from exchanges.base_exchange import UnifiedBalance, UnifiedPosition
from stratex_agents.portfolio_agent import PortfolioAgent


class FakePortfolioVenue:
    def __init__(self, balances=None, positions=None, balance_error=None, position_error=None, equity=None):
        self.balances = balances or {}
        self.positions = positions or []
        self.balance_error = balance_error
        self.position_error = position_error
        self.equity = equity

    def get_equity(self):
        if self.equity is None:
            raise RuntimeError("broker account equity unavailable")
        return self.equity

    def get_balance(self):
        if self.balance_error:
            raise self.balance_error
        return self.balances

    def get_positions(self):
        if self.position_error:
            raise self.position_error
        return self.positions


def position(symbol="XAUUSD.a", venue="mt5", ticket="202", side="LONG", quantity=0.1, price=2300.0):
    return UnifiedPosition(
        symbol=symbol, side=side, quantity=quantity, entry_price=price,
        mark_price=price + 1.0, unrealized_pnl=0.1, leverage=1,
        exchange=venue, position_id=ticket,
    )


def test_portfolio_agent_keeps_mixed_account_currencies_explicit():
    binance = FakePortfolioVenue(
        balances={"USDT": UnifiedBalance("USDT", free=900, used=100, total=1000)},
        positions=[],
    )
    mt5 = FakePortfolioVenue(
        balances={"USD": UnifiedBalance("USD", free=600, used=400, total=1000)},
        positions=[position()],
    )
    agent = PortfolioAgent({"binance": binance, "mt5": mt5})
    snapshot = agent.snapshot("USD")
    assert snapshot["status"] == "healthy"
    assert snapshot["currency_conversion_complete"] is False
    assert snapshot["reporting_equity"] is None
    assert snapshot["native_balance_totals"] == {"USD": 1000.0, "USDT": 1000.0}
    assert snapshot["open_position_count"] == 1
    assert snapshot["positions"][0]["native_symbol"] == "XAUUSD.a"
    assert snapshot["positions"][0]["position_id"] == "202"
    assert snapshot["positions"][0]["notional"] is None


def test_portfolio_agent_converts_only_with_explicit_currency_rates():
    venue = FakePortfolioVenue(
        balances={
            "USD": UnifiedBalance("USD", free=900, used=100, total=1000),
            "EUR": UnifiedBalance("EUR", free=100, used=0, total=100),
        },
        positions=[position(symbol="EURUSD", ticket="11")],
    )
    agent = PortfolioAgent(
        {"mt5": venue},
        currency_to_reporting_rate={"EUR": 1.1},
        notional_calculators={"mt5": lambda pos: (pos.quantity * 100000 * pos.mark_price, "USD")},
    )
    snapshot = agent.snapshot("USD")
    assert snapshot["currency_conversion_complete"] is True
    assert snapshot["reporting_equity"] == 1110.0
    assert snapshot["reporting_free_cash"] == 1010.0
    assert snapshot["positions"][0]["notional_available"] is True
    assert snapshot["positions"][0]["notional"] == 23010000.0


def test_portfolio_agent_does_not_hide_venue_failures_as_zero_equity():
    healthy = FakePortfolioVenue(
        balances={"USD": UnifiedBalance("USD", free=100, used=0, total=100)},
        positions=[],
    )
    broken = FakePortfolioVenue(balance_error=TimeoutError(), position_error=TimeoutError())
    snapshot = PortfolioAgent({"binance": healthy, "mt5": broken}).snapshot("USD")
    assert snapshot["status"] == "degraded"
    assert snapshot["venues"]["mt5"]["status"] == "degraded"
    assert snapshot["venues"]["mt5"]["balance_error"] == "TimeoutError"
    assert snapshot["reporting_equity"] is None
    assert snapshot["currency_conversion_complete"] is False


def test_portfolio_agent_reports_no_venues_as_unavailable():
    snapshot = PortfolioAgent({}).snapshot("USD")
    assert snapshot["status"] == "no_venues_configured"
    assert snapshot["reporting_equity"] is None
    assert snapshot["execution_authority"] is False



def test_portfolio_agent_prefers_broker_account_equity_when_available():
    venue = FakePortfolioVenue(
        balances={"USD": UnifiedBalance("USD", free=700, used=300, total=1000)},
        positions=[position()],
        equity={"USD": 975.0},
    )
    snapshot = PortfolioAgent({"mt5": venue}).snapshot("USD")
    assert snapshot["status"] == "healthy"
    assert snapshot["reporting_equity"] == 975.0
    assert snapshot["equity_basis"] == "account_equity"
    assert snapshot["currency_conversion_complete"] is True


def test_portfolio_agent_does_not_call_wallet_balance_sum_verified_equity():
    venue = FakePortfolioVenue(
        balances={"USD": UnifiedBalance("USD", free=700, used=300, total=1000)},
        positions=[],
    )
    snapshot = PortfolioAgent({"mt5": venue}).snapshot("USD")
    assert snapshot["reporting_equity"] == 1000.0
    assert snapshot["equity_basis"] == "balance_total_estimate"
