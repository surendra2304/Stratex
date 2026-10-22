"""
Unit Tests for Real-time Trade and Account Event Telemetry and Balance Timeline.
"""
import pytest

from dashboard import app


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


def test_activity_endpoint_structure(client):
    res = client.get('/api/activity?limit=50')
    assert res.status_code == 200
    data = res.get_json()
    assert 'activity' in data
    assert 'count' in data
    assert isinstance(data['activity'], list)

    if len(data['activity']) > 0:
        entry = data['activity'][0]
        assert 'timestamp' in entry
        assert 'event' in entry
        assert 'type' in entry
        assert 'balance' in entry
        assert 'equity' in entry
        assert 'symbol' in entry
        assert 'trade_id' in entry


def test_equity_timeline_timeframes(client):
    for tf in ['1H', '6H', '1D', '7D', 'ALL']:
        res = client.get(f'/api/equity?timeframe={tf}')
        assert res.status_code == 200
        data = res.get_json()
        assert isinstance(data, list)
        if len(data) > 0:
            point = data[0]
            assert 'time' in point
            assert 'equity' in point
            assert 'cash' in point
            assert 'managed_assets' in point
            assert 'realized_pnl' in point
            assert 'unrealized_pnl' in point


def test_balance_timeline_endpoint(client):
    res = client.get('/api/balance-timeline?timeframe=1D')
    assert res.status_code == 200
    data = res.get_json()
    assert isinstance(data, list)
    if len(data) > 0:
        point = data[0]
        assert 'time' in point
        assert 'equity' in point
        assert 'cash' in point

