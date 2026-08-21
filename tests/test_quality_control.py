import importlib
import json
import os

import pytest

import config
import testnet_engine.service
from research_phase9.cost_engine import CostEngine
from testnet_engine.profitability_gate import ProfitabilityGate


def reload_modules():
    importlib.reload(config)
    importlib.reload(testnet_engine.service)
    return testnet_engine.service

@pytest.fixture
def quality_service(tmp_path):
    os.environ["TRADING_MODE"] = "TESTNET"
    os.environ["TESTNET_ONLY"] = "TRUE"
    os.environ["PAPER_SAFE_MODE"] = "False"
    os.environ["API_KEY"] = "dummy"
    os.environ["SECRET_KEY"] = "dummy"
    
    svc = reload_modules()
    
    # Redirect ledger to tmp
    ledger_file = tmp_path / "testnet_trade_ledger.jsonl"
    
    import config
    config.DEGRADATION_WINDOW = 5
    config.MIN_WIN_RATE_THRESHOLD = 0.40
    
    from unittest import mock
    with mock.patch("testnet_engine.service.get_exchange_client") as mock_get_client:
        mock_client = mock.MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_account.return_value = {"balances": [{"asset": "USDT", "free": "10000.0", "locked": "0.0"}]}
        mock_client.get_open_orders.return_value = []
        
        service = svc.TestnetService()
        service.ledger_file = str(ledger_file)
        return service, str(ledger_file)

def test_profitability_gate_edge_minimum():
    cost_engine = CostEngine(
        entry_fee=0.001, exit_fee=0.001,
        spread=0.0005, entry_slip=0.0005, exit_slip=0.0005
    ) # Total friction = 0.0035
    # Actually taker_fee=0.1%, entry + exit = 0.2%. spread = 0.05%. slippage=0.1%. Total = 0.35%

    gate = ProfitabilityGate(cost_engine=cost_engine)
    import config
    config.MINIMUM_EXPECTED_EDGE = 0.0010 # 0.1% minimum edge

    # Gross expected: reward 1%, risk 1%, prob 0.6 ->
    # (0.6 * 0.01) - (0.4 * 0.01) = 0.002; Net: 0.002 - 0.0035 = -0.0015 -> Reject
    # Pass as float (legacy PROBABILISTIC path)
    passed, metrics = gate.evaluate_signal("BTCUSDT", "BUY", 100, 99, 101, 0.6)
    assert passed is False
    assert metrics["reason"] == "NEGATIVE_EXPECTED_NET_RETURN"
    assert metrics["decision"] == "REJECTED"

    # Gross expected: reward 2%, risk 1%, prob 0.8 ->
    # (0.8 * 0.02) - (0.2 * 0.01) = 0.016 - 0.002 = 0.014; Net: 0.014 - 0.0035 = 0.0105 -> Accept
    passed, metrics = gate.evaluate_signal("BTCUSDT", "BUY", 100, 99, 102, 0.8)
    assert passed is True
    assert metrics["reason"] == "POSITIVE_EDGE"

def test_degradation_trigger_observe_only(quality_service):
    service, ledger_path = quality_service
    
    # Simulate 5 trades: 4 losses, 1 win -> 20% win rate
    # Window is 5. Threshold is 40%.
    with open(ledger_path, "w") as f:
        f.writelines(json.dumps({"action": "CLOSE_LOSS", "pnl": -10.0}) + "\n" for i in range(4))
        f.write(json.dumps({"action": "CLOSE_WIN", "pnl": 20.0}) + "\n")
        
    assert service.observe_only is False
    
    service._check_degradation()
    
    assert service.observe_only is True

def test_degradation_safe_win_rate(quality_service):
    service, ledger_path = quality_service
    
    # Simulate 5 trades: 2 losses, 3 wins -> 60% win rate
    with open(ledger_path, "w") as f:
        f.writelines(json.dumps({"action": "CLOSE_LOSS", "pnl": -10.0}) + "\n" for i in range(2))
        f.writelines(json.dumps({"action": "CLOSE_WIN", "pnl": 20.0}) + "\n" for i in range(3))
            
    assert service.observe_only is False
    
    service._check_degradation()
    
    # Win rate > 40%, should remain False
    assert service.observe_only is False
