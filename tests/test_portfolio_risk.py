import importlib
import os

import pytest

import config
import testnet_engine.risk_gate


def reload_modules():
    importlib.reload(config)
    importlib.reload(testnet_engine.risk_gate)
    return testnet_engine.risk_gate

@pytest.fixture
def risk_gate():
    os.environ["TRADING_MODE"] = "TESTNET"
    os.environ["TESTNET_ONLY"] = "TRUE"
    os.environ["PAPER_SAFE_MODE"] = "False"
    os.environ["API_KEY"] = "dummy"
    os.environ["SECRET_KEY"] = "dummy"
    
    rsg = reload_modules()
    
    # Temporarily set test configs explicitly
    import config
    config.MAX_TESTNET_RISK_PER_TRADE = 0.01  
    config.MAX_SINGLE_ASSET_EXPOSURE = 0.05
    config.MAX_NET_DIRECTIONAL_EXPOSURE = 0.10
    config.MAX_TESTNET_EXPOSURE = 0.20
    config.MAX_OPEN_POSITIONS = 5
    config.MAX_DAILY_LOSS_PCT = 0.02
    config.MAX_TESTNET_DRAWDOWN_PCT = 0.05
    
    gate = rsg.RiskGate(starting_balance=10000.0)
    return gate

def test_position_sizing_lot_size_rounding(risk_gate):
    config.MAX_SINGLE_ASSET_EXPOSURE = 1.0 # Temporarily bypass for this specific test
    current_equity = 10000.0
    entry = 50000.0
    sl = 49500.0 # $500 risk
    
    # Max risk allowed = 10000 * 0.01 = $100
    # Expected qty = 100 / 500 = 0.2
    
    # If step size is 0.03, then 0.2 / 0.03 = 6.666 -> floor to 6 -> 6 * 0.03 = 0.18
    filters_weird = {"stepSize": 0.03, "minNotional": 10.0, "tickSize": 0.01}
    qty = risk_gate.calculate_position_size(current_equity, entry, sl, filters_weird)
    assert qty == 0.18
    
def test_position_sizing_min_notional(risk_gate):
    filters = {"stepSize": 1.0, "minNotional": 15.0, "tickSize": 0.01}
    
    # Entry $10, Qty 1 -> Notional $10. Should reject since minNotional is 15.
    current_equity = 100.0
    entry = 10.0
    sl = 9.0 # risk = $1
    # Max risk allowed = 100 * 0.01 = $1. Qty = 1
    
    qty = risk_gate.calculate_position_size(current_equity, entry, sl, filters)
    assert qty == 0.0 # Blocked by min notional
    
def test_single_asset_exposure_limit(risk_gate):
    # Limit is 0.05 (5%) -> 10000 * 0.05 = $500 max exposure per asset
    active = {
        "BTCUSDT": {"quantity": 0.008, "entry_price": 50000.0, "side": "LONG"} # 0.008 * 50000 = $400
    }
    
    # Try adding $200 more of BTC
    passed, reason, _ = risk_gate.evaluate_risk("BTCUSDT", "LONG", 10000.0, active, proposed_qty=0.004, entry_price=50000.0, data_health_status="OK")
    assert passed is False
    assert reason == "MAX_SINGLE_ASSET_EXPOSURE"
    
def test_net_directional_correlation_limit(risk_gate):
    # Limit is 0.10 (10%) -> $1000
    active = {
        "BTCUSDT": {"quantity": 0.01, "entry_price": 50000.0, "side": "LONG"}, # $500 Long
        "ETHUSDT": {"quantity": 0.2, "entry_price": 2000.0, "side": "LONG"},  # $400 Long
    } # Net Long = $900
    
    # Try adding $200 more Long on SOLUSDT. Should breach 1000 net directional
    passed, reason, _ = risk_gate.evaluate_risk("SOLUSDT", "LONG", 10000.0, active, proposed_qty=2.0, entry_price=100.0, data_health_status="OK")
    assert passed is False
    assert reason == "MAX_CORRELATION_EXPOSURE"
    
    # Try adding $200 SHORT on SOLUSDT. Should pass, as it reduces net directional exposure to $700 Long
    passed, reason, _ = risk_gate.evaluate_risk("SOLUSDT", "SHORT", 10000.0, active, proposed_qty=2.0, entry_price=100.0, data_health_status="OK")
    assert passed is True
    
def test_max_total_exposure(risk_gate):
    config.MAX_TESTNET_EXPOSURE = 0.15 # 15% = 1500
    active = {
        "BTCUSDT": {"quantity": 0.01, "entry_price": 50000.0, "side": "LONG"}, # 500 Long
        "ETHUSDT": {"quantity": 0.2, "entry_price": 2000.0, "side": "SHORT"},  # 400 Short
        "SOLUSDT": {"quantity": 4.0, "entry_price": 100.0, "side": "LONG"},    # 400 Long
    } 
    # Total gross exposure = 500 + 400 + 400 = 1300
    # Net exposure = 500 - 400 + 400 = 500
    
    # Add $300 more gross exposure.
    # Total gross will be 1600 > 1500. So MAX_EXPOSURE_REACHED.
    passed, reason, _ = risk_gate.evaluate_risk("XRPUSDT", "LONG", 10000.0, active, proposed_qty=300.0, entry_price=1.0, data_health_status="OK")
    assert passed is False
    assert reason == "MAX_EXPOSURE_REACHED"
