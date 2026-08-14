import pytest
import time
import os
import json
import math
from paper_engine.portfolio import PaperPortfolio
from paper_engine.market_data import MarketDataFeed, DataException, DataStaleException
from paper_engine.simulator import PaperSimulator
from paper_engine.funding_simulator import FundingPaperSimulator
from paper_engine.pairs_simulator import PairsPaperSimulator
from research_phase9.cost_engine import CostEngine
from paper_engine.heartbeat import HeartbeatState

def test_phase11_1_acceptance():
    """
    Comprehensive acceptance test for Phase 11.1 updates.
    """
    # 1. Cleanup
    for f in ["test_port.json", "test_ledger.jsonl", "test_equity.jsonl", "test_heartbeat.json"]:
        if os.path.exists(f):
            os.remove(f)
            
    # 2. Init
    port = PaperPortfolio(filename="test_port.json")
    port.ledger_file = "test_ledger.jsonl"
    port.equity_file = "test_equity.jsonl"
    
    md = MarketDataFeed(max_stale_seconds=5)
    cost = CostEngine(entry_fee=0.001, exit_fee=0.001, entry_slip=0.0005, exit_slip=0.0005)
    
    # 3. Market Data Checks
    # NaN check
    with pytest.raises(DataException):
        md.push_tick("BTC", math.nan, 100, 101, time.time())
    
    # Invalid spread check
    with pytest.raises(DataException):
        md.push_tick("BTC", 100, 101, 100, time.time())
        
    # Valid push
    ts1 = time.time()
    md.push_tick("BTC", 10000.0, 9999.0, 10001.0, ts1)
    
    # Backwards time check
    with pytest.raises(DataException):
        md.push_tick("BTC", 10000.0, 9999.0, 10001.0, ts1 - 10)
        
    # 4. Simulator Trade
    sim = PaperSimulator(port, md, cost)
    order_id = sim.submit_market_order("BTC", "BUY", 1.0, ts1)
    order = sim.orders[order_id]
    assert order["status"] == "FILLED"
    assert order["fee"] > 0
    assert order["cost_scenario"] in ["LOW", "BASE", "HIGH"]
    
    # Portfolio mapping
    pos_id = "pos_1"
    port.add_position(pos_id, "BTC", "BUY", order["fill_price"], 1.0)
    
    # Check Equity before close
    md.push_tick("BTC", 11000.0, 10999.0, 11001.0, ts1 + 10)
    eq1 = port.get_equity({"BTC": 11000.0})
    assert eq1 > 10900.0
    
    # Record equity snapshot
    port.record_equity_snapshot(ts1 + 10, {"BTC": 11000.0})
    
    # Close Position
    exit_order_id = sim.submit_market_order("BTC", "SELL", 1.0, ts1 + 10)
    exit_order = sim.orders[exit_order_id]
    
    port.close_position(pos_id, exit_order["fill_price"], exit_order["fee"], ts1 + 10)
    
    # Realized Pnl check
    assert port.realized_pnl != 0
    assert port.daily_realized_pnl == port.realized_pnl
    assert port.cumulative_fees > 0
    
    # 5. Ledger & Drawdown Check
    assert os.path.exists("test_ledger.jsonl")
    assert os.path.exists("test_equity.jsonl")
    
    mdd = port.get_max_drawdown()
    assert mdd >= 0.0
    
    # 6. Restart Simulation
    port2 = PaperPortfolio(filename="test_port.json")
    assert port2.cash == port.cash
    assert port2.realized_pnl == port.realized_pnl
    assert port2.cumulative_fees == port.cumulative_fees
    
    # 7. Heartbeat
    hb = HeartbeatState(filename="test_heartbeat.json", timeout_seconds=1)
    hb.ping("Bot")
    hb.ping("Market Data")
    bh = hb.get_overall_health()
    assert bh.value == "HEALTHY"
    
    time.sleep(1.5)
    bh2 = hb.get_overall_health()
    assert bh2.value == "OFFLINE"
    
    # 8. Funding Simulator
    f_sim = FundingPaperSimulator(port, md, cost)
    ft_id = f_sim.submit_funding_arbitrage("BTC", 1.0, 1.0, ts1 + 15)
    assert f_sim.funding_trades[ft_id]["status"] == "OPEN"
    
    # Cleanup
    for f in ["test_port.json", "test_ledger.jsonl", "test_equity.jsonl", "test_heartbeat.json"]:
        if os.path.exists(f):
            try:
                os.remove(f)
            except:
                pass
