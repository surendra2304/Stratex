import pytest
from intelligence.futuris_client import get_futuris_client, FuturisMarketClient, FuturisForecastContext
from advisory_telemetry import build_telemetry_payload
from dashboard import app

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c

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
    
    # Record test outcomes
    r1 = futuris.record_actual_outcome('BTCUSDT', actual_volatility_spike=True, actual_drawdown_pct=0.015)
    assert 'prediction_correct' in r1
    
    metrics = futuris.get_accuracy_metrics()
    assert metrics['total_evaluated'] >= 1
    assert 'accuracy_pct' in metrics
    assert metrics['status'] == 'ACTIVE'

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
