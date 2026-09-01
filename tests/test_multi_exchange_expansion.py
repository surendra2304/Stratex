from exchanges.exchange_implementations import (
    BinanceExchangeAdapter,
    BybitExchangeAdapter,
    CoinbaseExchangeAdapter,
    OKXExchangeAdapter,
)
from exchanges.health import MultiExchangeHealthMonitor
from execution.router import MultiExchangeRouter
from portfolio.unified import UnifiedPortfolioManager, UnifiedRiskLimits


def test_symbol_normalization():
    ex = BinanceExchangeAdapter()
    assert ex.normalize_symbol('BTCUSDT') == 'BTC/USDT'
    assert ex.normalize_symbol('ETHUSDC') == 'ETH/USDC'
    assert ex.normalize_symbol('SOL-USDT') == 'SOL/USDT'
    assert ex.normalize_symbol('XBTUSD') == 'BTC/USD'
    assert ex.denormalize_symbol('BTC/USDT') == 'BTCUSDT'

def test_okx_symbol_denormalization():
    ex = OKXExchangeAdapter()
    assert ex.denormalize_symbol('BTC/USDT') == 'BTC-USDT'

def test_exchange_capabilities():
    binance = BinanceExchangeAdapter()
    cb = CoinbaseExchangeAdapter()
    assert binance.capabilities.supports_futures is True
    assert binance.capabilities.supports_shorting is True
    assert cb.capabilities.supports_futures is False

def test_exchange_implementations_data():
    for ex_cls in [BinanceExchangeAdapter, BybitExchangeAdapter, OKXExchangeAdapter, CoinbaseExchangeAdapter]:
        ex = ex_cls()
        bals = ex.get_balance()
        assert len(bals) > 0
        t = ex.get_ticker('BTC/USDT')
        assert t.bid > 0 and t.ask > 0
        ob = ex.get_orderbook('BTC/USDT')
        assert len(ob['bids']) > 0 and len(ob['asks']) > 0
        fees = ex.get_fees('BTC/USDT')
        assert len(fees) == 2
        hist = ex.get_historical_data('BTC/USDT')
        assert len(hist) > 0

def test_order_placement():
    ex = BinanceExchangeAdapter()
    res = ex.place_order('BTC/USDT', 'BUY', 'MARKET', 0.01)
    assert res.status == 'FILLED'
    assert res.executed_qty == 0.01

def test_unified_portfolio_aggregation():
    exchanges = {
        'binance': BinanceExchangeAdapter(),
        'bybit': BybitExchangeAdapter(),
        'okx': OKXExchangeAdapter(),
        'coinbase': CoinbaseExchangeAdapter()
    }
    pm = UnifiedPortfolioManager(exchanges)
    eq = pm.get_unified_equity()
    assert eq['total_portfolio_equity'] > 0
    assert 'binance' in eq['exchange_breakdown']

    pos = pm.get_cross_exchange_positions()
    assert pos['total_positions_count'] >= 2
    assert 'BTC/USDT' in pos['net_asset_exposures']

def test_unified_risk_limits():
    exchanges = {'binance': BinanceExchangeAdapter()}
    pm = UnifiedPortfolioManager(
        exchanges,
        risk_limits=UnifiedRiskLimits(
            max_total_portfolio_risk_pct=0.75,
            max_asset_exposure_pct=0.60
        )
    )
    allowed, msg = pm.validate_global_risk_limits('BTC/USDT', 'BUY', 500.0)
    assert allowed is True
    blocked, b_msg = pm.validate_global_risk_limits('BTC/USDT', 'BUY', 50000.0)
    assert blocked is False

def test_health_monitor_circuit_breaker():
    hm = MultiExchangeHealthMonitor(['binance', 'bybit', 'okx'])
    assert hm.is_exchange_available('okx') is True
    for _ in range(5):
        hm.record_heartbeat('okx', latency_ms=1000.0, is_success=False)
    assert hm.is_exchange_available('okx') is False
    assert hm.get_flow_allocation_multiplier('okx') == 0.0
    hm.reset_circuit_breaker('okx')
    assert hm.is_exchange_available('okx') is True

def test_intelligent_order_router():
    exchanges = {
        'binance': BinanceExchangeAdapter(),
        'bybit': BybitExchangeAdapter(),
        'okx': OKXExchangeAdapter(),
        'coinbase': CoinbaseExchangeAdapter()
    }
    hm = MultiExchangeHealthMonitor(list(exchanges.keys()))
    router = MultiExchangeRouter(exchanges, health_monitor=hm, large_order_threshold_usd=10000.0)

    # Standard order
    res = router.route_and_execute_order('BTC/USDT', 'BUY', 'MARKET', 0.05)
    assert res.status == 'FILLED'
    assert res.executed_qty == 0.05

    # Large order split
    split_res = router.route_and_execute_order('BTC/USDT', 'BUY', 'MARKET', 0.5)
    assert split_res.exchange == 'MULTI_VENUE'
    assert len(router.split_orders_history) >= 1

def test_multiexchange_api_routes():
    from dashboard import app
    client = app.test_client()
    assert client.get('/api/multiexchange/status').status_code == 200
    assert client.get('/api/multiexchange/portfolio').status_code == 200
    assert client.get('/api/multiexchange/router').status_code == 200
    assert client.get('/api/multiexchange/health').status_code == 200
