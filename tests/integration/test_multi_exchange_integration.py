import os
import json
import pytest
from exchanges.exchange_implementations import BinanceExchangeAdapter, BybitExchangeAdapter, OKXExchangeAdapter
from portfolio.unified import UnifiedPortfolioManager
from execution.router import MultiExchangeRouter

def test_multi_exchange_routing_and_failover():
    exchanges = {
        'binance': BinanceExchangeAdapter(),
        'bybit': BybitExchangeAdapter(),
        'okx': OKXExchangeAdapter()
    }
    pm = UnifiedPortfolioManager(exchanges)
    eq = pm.get_unified_equity()
    assert eq['total_portfolio_equity'] > 0
    assert len(eq['exchange_breakdown']) == 3

    router = MultiExchangeRouter(exchanges)
    best_ex, best_price, cost, slip = router.find_best_execution_venue(
        symbol='BTC/USDT',
        side='BUY',
        quantity=0.1
    )
    assert best_ex in ['binance', 'bybit', 'okx']
    assert best_price > 0
    assert cost > 0

    # Simulate failover
    router.unhealthy_exchanges.append(best_ex)
    second_ex, second_price, _, _ = router.find_best_execution_venue(
        symbol='BTC/USDT',
        side='BUY',
        quantity=0.1
    )
    assert second_ex != best_ex
    assert second_ex in ['binance', 'bybit', 'okx']
