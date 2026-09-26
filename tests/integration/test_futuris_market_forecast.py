import os
import pytest

from advisory_telemetry import build_telemetry_payload
from dashboard import app
from intelligence.futuris_client import (
    FuturisMarketClient,
)


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c

@pytest.mark.skipif(os.getenv('STRATEX_LIVE_PEER_TESTS') != '1', reason='set STRATEX_LIVE_PEER_TESTS=1 to call the live Futuris service')
def test_futuris_forecast_generation_and_context():
    futuris = FuturisMarketClient()
    forecast = futuris.fetch_forecast('BTCUSDT')

    assert forecast.symbol == 'BTCUSDT'
    assert 'probability' in forecast.volatility_forecast
    assert 'probability' in forecast.drawdown_risk
    assert 'current' in forecast.regime_outlook
    assert forecast.is_valid() is True

    # Test Advisory Telemetry integration
    payload = build_telemetry_payload(consultation_reason='REGULAR_POLL')
    assert 'futuris_context' in payload
    assert payload['futuris_context'] is not None
    assert 'volatility_forecast' in payload['futuris_context']
    assert 'drawdown_risk' in payload['futuris_context']
    assert 'regime_outlook' in payload['futuris_context']

def test_futuris_accuracy_tracking():
    futuris = FuturisMarketClient()
    # No resolved outcome is present here; the client must not invent an accuracy sample.
    r1 = futuris.record_actual_outcome('BTCUSDT', actual_volatility_spike=True, actual_drawdown_pct=0.015)
    assert r1['status'] == 'UNAVAILABLE'

    metrics = futuris.get_accuracy_metrics()
    assert metrics['total_evaluated'] == 0
    assert metrics['accuracy_pct'] is None
    assert metrics['status'] == 'AWAITING_DATA'

def test_futuris_dashboard_endpoints(client):
    res = client.get('/api/v1/futuris/forecast?symbol=BTCUSDT')
    assert res.status_code == 200
    data = res.get_json()
    assert data['status'] == 'OK'
    assert 'forecast' in data
    assert 'volatility_forecast' in data['forecast']

    res_acc = client.get('/api/v1/futuris/accuracy')
    assert res_acc.status_code == 200
    acc_data = res_acc.get_json()
    assert 'accuracy_pct' in acc_data
