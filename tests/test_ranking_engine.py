import pytest
import time
import pandas as pd
import datetime
from testnet_engine.service import TestnetService

@pytest.fixture
def ranking_service(mocker):
    # Mock environment
    mocker.patch("testnet_engine.service.TRADING_MODE", "TESTNET")
    import os
    os.environ["TESTNET_ONLY"] = "TRUE"
    os.environ["PAPER_SAFE_MODE"] = "False"
    os.environ["API_KEY"] = "dummy"
    os.environ["SECRET_KEY"] = "dummy"

    import config
    # Set limit to 200 USDT to test limits blocking lower ranks
    config.MAX_TESTNET_EXPOSURE = 0.02
    config.MAX_SINGLE_ASSET_EXPOSURE = 0.02
    config.MINIMUM_EXPECTED_EDGE = 0.0001
    
    mock_client = mocker.MagicMock()
    mock_client.get_account.return_value = {"balances": [{"asset": "USDT", "free": "10000.0", "locked": "0.0"}]}
    
    mocker.patch("testnet_engine.service.get_exchange_client", return_value=mock_client)
    mocker.patch("testnet_engine.service.place_market_order", return_value={"_executed_qty": 0.01})
    mocker.patch("execution._load_active_trades", return_value=[])
    mocker.patch("testnet_engine.discovery.SymbolDiscoveryService")
    
    service = TestnetService()
    service.scanner = mocker.MagicMock()
    
    # Mock candle caches
    df_btc = pd.DataFrame({"close": [50000.0, 50000.0]})
    df_eth = pd.DataFrame({"close": [3000.0, 3000.0]})
    df_sol = pd.DataFrame({"close": [100.0, 100.0]})
    
    service.scanner.candle_cache = {
        ("BTCUSDT", "4h"): df_btc,
        ("ETHUSDT", "4h"): df_eth,
        ("SOLUSDT", "4h"): df_sol
    }
    service.scanner.data_health_status = {"BTCUSDT": "OK", "ETHUSDT": "OK", "SOLUSDT": "OK"}
    service.symbol_filters = {
        "BTCUSDT": {"stepSize": 0.001, "minNotional": 10.0},
        "ETHUSDT": {"stepSize": 0.01, "minNotional": 10.0},
        "SOLUSDT": {"stepSize": 0.1, "minNotional": 10.0}
    }
    return service

def test_opportunity_ranking_and_limits(ranking_service, mocker):
    service = ranking_service

    # Mock the profitability gate to return different expected net returns
    # BTC = 0.05 (Rank 1), ETH = 0.03 (Rank 2), SOL = 0.01 (Rank 3)
    def mock_eval(symbol, side, entry, sl, tp, signal_result):
        edge = {"BTCUSDT": 0.05, "ETHUSDT": 0.03, "SOLUSDT": 0.01}.get(symbol, 0.0)
        return True, {"expected_net_return": edge, "reason": "OK",
                      "prob_win": 0.9, "confidence": 0.9,
                      "strategy_type": "RULE_BASED", "prob_source": "TEST"}

    mocker.patch.object(service.profitability_gate, "evaluate_signal", side_effect=mock_eval)

    service.opportunity_pool.put({
        "signal_id": "sol", "symbol": "SOLUSDT", "tf": "4h", "side": "BUY", "sl": 90, "tp": 110,
        "signal_result": None,
        "metrics": {"expected_net_return": 0.01, "confidence": 0.9, "prob_win": 0.9}, "timestamp": time.time()
    })
    service.opportunity_pool.put({
        "signal_id": "btc", "symbol": "BTCUSDT", "tf": "4h", "side": "BUY", "sl": 49000, "tp": 52000,
        "signal_result": None,
        "metrics": {"expected_net_return": 0.05, "confidence": 0.9, "prob_win": 0.9}, "timestamp": time.time()
    })
    service.opportunity_pool.put({
        "signal_id": "eth", "symbol": "ETHUSDT", "tf": "4h", "side": "BUY", "sl": 2900, "tp": 3200,
        "signal_result": None,
        "metrics": {"expected_net_return": 0.03, "confidence": 0.9, "prob_win": 0.9}, "timestamp": time.time()
    })

    # We trigger the execution thread event manually
    service.pool_event.set()

    # Wait for execution loop to process them
    time.sleep(0.5)

    # BTC ranked first; MAX_TESTNET_EXPOSURE=0.02 means ETH/SOL get risk-rejected
    assert "BTCUSDT" in service.active_positions
    assert "ETHUSDT" not in service.active_positions
    assert "SOLUSDT" not in service.active_positions

def test_revalidation_spread_expansion(ranking_service, mocker):
    service = ranking_service

    # Before execution loop processes: revalidation returns FALSE (spread expansion)
    def mock_eval_revalidation(symbol, side, entry, sl, tp, signal_result):
        return False, {"expected_net_return": -0.01, "reason": "NEGATIVE_EXPECTED_NET_RETURN",
                       "prob_win": 0.9, "confidence": 0.9,
                       "strategy_type": "RULE_BASED", "prob_source": "TEST"}

    service.profitability_gate.evaluate_signal = mock_eval_revalidation

    # Queue BTC
    service.opportunity_pool.put({
        "signal_id": "btc", "symbol": "BTCUSDT", "tf": "4h", "side": "BUY", "sl": 49000, "tp": 52000,
        "signal_result": None,
        "metrics": {"expected_net_return": 0.05, "confidence": 0.9, "prob_win": 0.9}, "timestamp": time.time()
    })

    service.pool_event.set()
    time.sleep(0.5)

    # Should not execute because revalidation failed
    assert "BTCUSDT" not in service.active_positions
    assert service.stats["JIT_REJECTED"] > 0
