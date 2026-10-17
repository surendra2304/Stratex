from exchanges.base_exchange import UnifiedBalance
from stratex_agents.venue.binance_futures_portfolio_adapter import (
    BinanceFuturesPortfolioAdapter,
    BinancePortfolioReadOnlyError,
)


class FakeAgent:
    demo_trading = True

    def create_client(self, **kwargs):
        raise AssertionError("Injected test client should be used")


class FakeClient:
    stratex_environment = "demo_trading"

    def futures_account(self):
        return {"totalMarginBalance": "1210.5", "totalWalletBalance": "1200", "totalUnrealizedProfit": "10.5"}

    def futures_account_balance(self):
        return [
            {"asset": "USDT", "balance": "1200", "availableBalance": "1000"},
            {"asset": "USDC", "balance": "0", "availableBalance": "0"},
        ]

    def futures_position_information(self):
        return [
            {"symbol": "BTCUSDT", "positionAmt": "0.01", "positionSide": "LONG",
             "entryPrice": "60000", "markPrice": "61000", "unRealizedProfit": "10",
             "leverage": "5"},
            {"symbol": "ETHUSDT", "positionAmt": "-0.5", "positionSide": "SHORT",
             "entryPrice": "3000", "markPrice": "2900", "unRealizedProfit": "50",
             "leverage": "3"},
            {"symbol": "SOLUSDT", "positionAmt": "0", "positionSide": "BOTH",
             "entryPrice": "0", "markPrice": "0", "unRealizedProfit": "0", "leverage": "1"},
        ]


def test_binance_portfolio_adapter_reads_demo_usdm_balances_and_positions():
    adapter = BinanceFuturesPortfolioAdapter(client=FakeClient(), venue_agent=FakeAgent())
    balances = adapter.get_balance()
    assert balances["USDT"] == UnifiedBalance("USDT", free=1000, used=200, total=1200)

    positions = adapter.get_positions()
    assert len(positions) == 2
    assert positions[0].symbol == "BTCUSDT"
    assert positions[0].side == "LONG"
    assert positions[0].position_id == "BTCUSDT:LONG"
    assert positions[1].symbol == "ETHUSDT"
    assert positions[1].side == "SHORT"
    assert positions[1].quantity == 0.5


def test_binance_portfolio_adapter_refuses_unverified_client_environment():
    class UnverifiedClient(FakeClient):
        stratex_environment = None

    try:
        BinanceFuturesPortfolioAdapter(client=UnverifiedClient(), venue_agent=FakeAgent())
    except BinancePortfolioReadOnlyError as exc:
        assert "unverified or mismatched" in str(exc)
    else:
        raise AssertionError("Unverified Binance client must be refused")



def test_binance_portfolio_adapter_uses_broker_account_equity_including_unrealized_pnl():
    adapter = BinanceFuturesPortfolioAdapter(client=FakeClient(), venue_agent=FakeAgent())
    assert adapter.get_equity() == {"USDT": 1210.5}


def test_binance_portfolio_adapter_fails_closed_when_equity_is_missing():
    class MissingEquityClient(FakeClient):
        def futures_account(self):
            return {"totalWalletBalance": "1200"}

    adapter = BinanceFuturesPortfolioAdapter(client=MissingEquityClient(), venue_agent=FakeAgent())
    try:
        adapter.get_equity()
    except BinancePortfolioReadOnlyError as exc:
        assert "omitted totalMarginBalance" in str(exc)
    else:
        raise AssertionError("Missing account equity must be refused")
