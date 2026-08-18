import json
import pytest
from dashboard import app

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c

def test_api_risk_ten_top_metrics(client):
    """Verify /api/risk returns all top 10 risk and capital allocation metrics."""
    res = client.get('/api/risk')
    assert res.status_code == 200
    data = json.loads(res.data)
    assert data["status"] == "SUCCESS"
    assert "risk" in data
    r = data["risk"]
    
    assert "total_equity" in r
    assert "cash_usdt" in r
    assert "deployed_capital" in r
    assert "managed_asset_value" in r
    assert "risk_used_pct" in r
    assert "available_risk_pct" in r
    assert "max_exposure_pct" in r
    assert "current_open_positions" in r
    assert "max_open_positions" in r
    assert "daily_pnl" in r
    assert "max_drawdown_pct" in r

def test_api_risk_events_structure(client):
    """Verify /api/risk-events returns decision log with risk buffer fields."""
    res = client.get('/api/risk-events')
    assert res.status_code == 200
    data = json.loads(res.data)
    assert data["status"] == "SUCCESS"
    assert "events" in data
    
    for e in data["events"]:
        assert "timestamp" in e
        assert "symbol" in e
        assert "strategy" in e
        assert "timeframe" in e
        assert "decision" in e
        assert "reason" in e
        assert "requested_risk" in e
        assert "available_risk" in e
        assert "exposure" in e

def test_api_analytics_metrics_and_timeframe_controls(client):
    """Verify /api/analytics returns quantitative KPIs, distribution, and handles timeframe filters."""
    for tf in ["1D", "7D", "30D", "ALL"]:
        res = client.get(f'/api/analytics?timeframe={tf}')
        assert res.status_code == 200
        data = json.loads(res.data)
        assert data["status"] in ["OK", "SUCCESS"]
        assert data["timeframe"] == tf
        assert "analytics" in data
        
        a = data["analytics"]
        assert "total_trades" in a
        assert "win_rate" in a
        assert "net_pnl" in a
        assert "realized_pnl" in a
        assert "unrealized_pnl" in a
        assert "profit_factor" in a
        assert "avg_trade" in a
        assert "avg_win" in a
        assert "avg_loss" in a
        assert "largest_win" in a
        assert "largest_loss" in a
        assert "total_fees" in a
        assert "max_drawdown" in a
        
        assert "daily_pnl" in data
        assert "pnl_distribution" in data
        assert "strategy_comparison" in data
        assert "timeframe_comparison" in data
        assert "symbol_comparison" in data

def test_why_didnt_it_trade_diagnostic_panel(client):
    """Verify /api/analytics returns 'Why Didn't It Trade?' conversion diagnostics."""
    res = client.get('/api/analytics')
    data = json.loads(res.data)
    assert "why_didnt_it_trade" in data
    d = data["why_didnt_it_trade"]
    
    assert "candles" in d
    assert "evaluations" in d
    assert "signals" in d
    assert "profitability_accepted" in d
    assert "profitability_rejected" in d
    assert "risk_accepted" in d
    assert "risk_rejected" in d
    assert "execution_eligible" in d
    assert "orders_submitted" in d
    assert "orders_failed" in d
    assert "orders_filled" in d
    assert "dominant_reason" in d
    assert len(d["dominant_reason"]) > 0
