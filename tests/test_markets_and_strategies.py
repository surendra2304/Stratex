import json

import pytest

from dashboard import app


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c

def test_api_candles_supported_timeframes(client):
    """Test that candles can be retrieved for standard timeframe configuration without fabrication."""
    for tf in ["5m", "15m", "30m", "1h", "2h", "4h"]:
        res = client.get(f'/api/candles?symbol=BTCUSDT&tf={tf}&limit=10')
        if res.status_code == 200:
            data = json.loads(res.data)
            assert isinstance(data, list)
            if len(data) > 0:
                c = data[0]
                assert "time" in c
                assert "open" in c
                assert "high" in c
                assert "low" in c
                assert "close" in c
                assert "volume" in c
                assert c.get("source") == "BINANCE"
                assert c.get("verified") is True
        else:
            assert res.status_code == 503
            data = json.loads(res.data)
            assert data.get("status") == "DATA_UNAVAILABLE"
            assert data.get("source") == "BINANCE"
            assert data.get("candles") == []

def test_api_strategy_metrics_covers_all_six_strategies(client):
    """Verify that all 6 required strategies are present in /api/strategy-metrics."""
    res = client.get('/api/strategy-metrics')
    assert res.status_code == 200
    data = json.loads(res.data)
    assert data["status"] == "SUCCESS"
    assert "strategies" in data
    
    required_strats = ["aggressor", "scalper", "supertrend", "ml", "swing", "adx_ema"]
    for s in required_strats:
        assert s in data["strategies"], f"Strategy {s} missing from strategy-metrics"
        strat_info = data["strategies"][s]
        assert "status" in strat_info
        assert "timeframes" in strat_info
        assert "evaluations" in strat_info
        assert "BUY" in strat_info
        assert "SELL" in strat_info
        assert "HOLD" in strat_info
        assert "qualified" in strat_info
        assert "profitability_rejected" in strat_info
        assert "risk_rejected" in strat_info
        assert "orders" in strat_info
        assert "fills" in strat_info
        assert "net_pnl" in strat_info

def test_api_strategy_metrics_matrix_structure(client):
    """Verify Strategy x Timeframe matrix structure."""
    res = client.get('/api/strategy-metrics')
    assert res.status_code == 200
    data = json.loads(res.data)
    
    assert "matrix" in data
    assert "timeframe_keys" in data
    assert data["timeframe_keys"] == ["5m", "15m", "30m", "1h", "2h", "4h"]
    
    required_strats = ["aggressor", "scalper", "supertrend", "ml", "swing", "adx_ema"]
    for s in required_strats:
        assert s in data["matrix"]
        for tf in data["timeframe_keys"]:
            assert tf in data["matrix"][s]
            cell = data["matrix"][s][tf]
            assert "active" in cell
            assert "signals" in cell
            assert "trades" in cell

def test_strategy_insufficient_data_integrity(client):
    """Ensure strategies with zero trades do NOT fabricate 0% win rate or average trade."""
    res = client.get('/api/strategy-metrics')
    data = json.loads(res.data)
    
    for s in data["strategies"].values():
        if s["trades"] == 0:
            assert s["win_rate"] is None or s["win_rate"] == 0.0 or s["trades"] == 0
            assert s["avg_trade"] is None
            assert s["best_trade"] is None
            assert s["worst_trade"] is None

def test_strategy_timeframe_metrics_endpoint(client):
    """Verify /api/timeframe-metrics returns proper structure."""
    res = client.get('/api/timeframe-metrics')
    assert res.status_code == 200
    data = json.loads(res.data)
    assert data["status"] == "SUCCESS"
    assert "timeframes" in data
    for tf in ["5m", "15m", "30m", "1h", "2h", "4h"]:
        assert tf in data["timeframes"]
