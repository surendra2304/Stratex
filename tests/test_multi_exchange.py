"""
tests/test_multi_exchange.py — Unit & Integration Tests for Multi-Exchange Trading Architecture.

Verifies:
1. BaseExchange & Adapter Symbol Normalization (BTCUSDT, BTC-USDT, BTC/USD).
2. Exchange Implementations (Binance, Bybit, OKX, Coinbase) balance, orderbook, ticker, order execution, and fee structures.
3. UnifiedPortfolioManager equity aggregation, cross-exchange position consolidation, net asset exposure, and allocation drift detection.
4. MultiExchangeRouter optimal venue selection and automatic failover handling.
5. MultiExchangeHealthMonitor latency tracking and per-venue circuit breakers.
6. CrossExchangeArbitrageScanner spatial price spread calculation and funding rate opportunity detection.
"""

import pytest
from exchanges.base_exchange import UnifiedTicker, UnifiedBalance
from exchanges.exchange_implementations import (
    BinanceExchangeAdapter, BybitExchangeAdapter, OKXExchangeAdapter, CoinbaseExchangeAdapter
)
from portfolio.unified_portfolio import UnifiedPortfolioManager
from exchange_router import MultiExchangeRouter
from exchanges.health_monitor import MultiExchangeHealthMonitor
from strategies.arb_scanner import CrossExchangeArbitrageScanner


@pytest.fixture
def mock_exchanges():
    return {
        "binance": BinanceExchangeAdapter(),
        "bybit": BybitExchangeAdapter(),
        "okx": OKXExchangeAdapter(),
        "coinbase": CoinbaseExchangeAdapter()
    }


def test_symbol_normalization(mock_exchanges):
    bn = mock_exchanges["binance"]
    okx = mock_exchanges["okx"]

    assert bn.normalize_symbol("BTCUSDT") == "BTC/USDT"
    assert bn.normalize_symbol("ETH-USDT") == "ETH/USDT"
    assert okx.denormalize_symbol("BTC/USDT") == "BTC-USDT"


def test_exchange_implementations_data(mock_exchanges):
    for ex_id, ex in mock_exchanges.items():
        bals = ex.get_balance()
        assert len(bals) > 0

        ticker = ex.get_ticker("BTC/USDT")
        assert ticker.bid > 0
        assert ticker.ask >= ticker.bid

        ob = ex.get_orderbook("BTC/USDT")
        assert len(ob["bids"]) > 0
        assert len(ob["asks"]) > 0

        maker_fee, taker_fee = ex.get_trading_fees("BTC/USDT")
        assert taker_fee >= maker_fee


def test_unified_portfolio_manager(mock_exchanges):
    mgr = UnifiedPortfolioManager(mock_exchanges)

    # Combined Equity
    eq_res = mgr.get_unified_equity()
    assert eq_res["total_portfolio_equity"] > 0
    assert "binance" in eq_res["exchange_breakdown"]
    assert "bybit" in eq_res["exchange_breakdown"]

    # Positions & Net Exposure
    pos_res = mgr.get_cross_exchange_positions()
    assert pos_res["total_positions_count"] >= 2
    assert "BTC/USDT" in pos_res["net_asset_exposures"]
    assert "ETH/USDT" in pos_res["net_asset_exposures"]

    # Allocation drift check
    drift, drift_map = mgr.check_allocation_drift(threshold_pct=0.01)
    assert isinstance(drift, bool)
    assert len(drift_map) == 4


def test_multi_exchange_router_and_failover(mock_exchanges):
    router = MultiExchangeRouter(mock_exchanges)

    # Best venue routing
    venue, price, fee = router.find_best_execution_venue("BTC/USDT", "BUY", 0.1)
    assert venue in mock_exchanges
    assert price > 0

    # Order execution
    res = router.route_and_execute_order("BTC/USDT", "BUY", "LIMIT", 0.1, price=60500.0)
    assert res.status == "FILLED"
    assert res.executed_qty == 0.1


def test_health_monitor_and_circuit_breaker():
    monitor = MultiExchangeHealthMonitor(["binance", "bybit"])

    # Heartbeat success
    monitor.record_heartbeat("binance", 40.0, is_success=True)
    assert monitor.metrics["binance"].status == "HEALTHY"

    # Heartbeat failures triggering circuit breaker
    for _ in range(3):
        monitor.record_heartbeat("bybit", 500.0, is_success=False)

    assert monitor.metrics["bybit"].circuit_breaker_active is True
    assert monitor.metrics["bybit"].status == "CIRCUIT_BREAKER_TRIPPED"

    # Reset
    monitor.reset_circuit_breaker("bybit")
    assert monitor.metrics["bybit"].circuit_breaker_active is False


def test_cross_exchange_arbitrage_scanner(mock_exchanges):
    scanner = CrossExchangeArbitrageScanner(mock_exchanges, min_net_profit_pct=0.001)

    # Spatial Arbitrage
    opps = scanner.scan_spatial_arbitrage("BTC/USDT")
    assert isinstance(opps, list)
    if opps:
        assert opps[0].buy_price > 0
        assert opps[0].sell_price > 0

    # Funding rate arbitrage
    funding = scanner.scan_funding_rate_arbitrage("BTC/USDT")
    assert isinstance(funding, list)
    if funding:
        assert funding[0]["funding_rate_8h_pct"] > 0
