import pytest
import time
import uuid
import os
from paper_engine.portfolio import PaperPortfolio
from paper_engine.market_data import MarketDataFeed
from paper_engine.simulator import PaperSimulator
from research_phase9.cost_engine import CostEngine
from paper_engine.signal_logger import Signal

def test_paper_trading_lifecycle():
    """
    Part 38: FINAL ACCEPTANCE TEST
    """
    for f in ["test_paper_portfolio.json", "test_paper_ledger.jsonl", "test_paper_equity.jsonl"]:
        if os.path.exists(f):
            os.remove(f)
        
    portfolio = PaperPortfolio(filename="test_paper_portfolio.json", ledger_file="test_paper_ledger.jsonl", equity_file="test_paper_equity.jsonl")
    market_data = MarketDataFeed()
    cost = CostEngine(entry_fee=0.001, exit_fee=0.001, entry_slip=0.0005, exit_slip=0.0005)
    sim = PaperSimulator(portfolio, market_data, cost)
    
    # 1. Provide Market Data
    ts = time.time()
    market_data.push_tick("BTCUSDT", 10000.0, 9999.0, 10001.0, ts)
    
    # 2. Generate a paper BUY
    order_id = sim.submit_market_order("BTCUSDT", "BUY", 1.0, ts)
    assert order_id in sim.orders
    
    order = sim.orders[order_id]
    assert order['status'] == "FILLED"
    # Buy market pays ask (10001) + slip (0.0005) = 10001 * 1.0005 = 10006.0005
    assert order['fill_price'] > 10001.0 
    
    # Verify fee deducted
    assert portfolio.cash < 10000.0
    
    # Register position in portfolio
    pos_id = "pos_1"
    portfolio.add_position(pos_id, "BTCUSDT", "BUY", order['fill_price'], 1.0)
    
    # 3. Update Market Data (Price goes up to 11000)
    market_data.push_tick("BTCUSDT", 11000.0, 10999.0, 11001.0, ts + 10)
    
    # 4. Update unrealized PnL
    eq = portfolio.get_equity({"BTCUSDT": 11000.0})
    print(f"Cash: {portfolio.cash}, Unrealized: {portfolio.get_unrealized_pnl({'BTCUSDT': 11000.0})}, Equity: {eq}")
    # Entry was ~10006, Current is 11000, UR PnL is ~994
    assert eq > 10900.0
    
    # 5. Generate Exit
    exit_id = sim.submit_market_order("BTCUSDT", "SELL", 1.0, ts + 10)
    exit_order = sim.orders[exit_id]
    
    # Sell market pays bid (10999) - slip (0.0005) = 10999 * 0.9995 = 10993.5005
    assert exit_order['fill_price'] < 10999.0
    
    realized = (exit_order['fill_price'] - order['fill_price']) * 1.0
    portfolio.close_position(pos_id, exit_order['fill_price'], exit_order['fee'], ts + 10)
    
    # Settle realized PnL
    portfolio.add_realized_pnl(realized, f"close_{pos_id}")
    
    # 6. Verify Restart
    portfolio._save()
    
    portfolio_restarted = PaperPortfolio(filename="test_paper_portfolio.json", ledger_file="test_paper_ledger.jsonl", equity_file="test_paper_equity.jsonl")
    assert portfolio_restarted.cash == portfolio.cash
    assert portfolio_restarted.realized_pnl == portfolio.realized_pnl
    assert "pos_1" in portfolio_restarted.positions
    assert portfolio_restarted.positions["pos_1"]["status"] == "CLOSED"
    
    # Clean up
    for f in ["test_paper_portfolio.json", "test_paper_ledger.jsonl", "test_paper_equity.jsonl"]:
        if os.path.exists(f):
            os.remove(f)
