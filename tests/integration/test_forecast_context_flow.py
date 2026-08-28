import pytest
from intelligence.futuris_client import get_futuris_client, FuturisMarketClient
from advisory_telemetry import build_telemetry_payload
from monitoring.metrics import get_metrics_registry

def test_forecast_context_enrichment_flow():
    client = get_futuris_client()
    forecast = client.fetch_forecast('BTCUSDT')
    
    assert forecast.symbol == 'BTCUSDT'
    assert 'probability' in forecast.volatility_forecast
    assert 'probability' in forecast.drawdown_risk
    assert 'current' in forecast.regime_outlook

    # Test Advisory Telemetry integration includes futuris_context
    payload = build_telemetry_payload(consultation_reason='VOLATILITY_SPIKE_PREDICTED')
    assert 'futuris_context' in payload
    assert payload['futuris_context'] is not None
    assert payload['futuris_context']['volatility_forecast']['probability'] >= 0.0

    # Test Metrics increment
    metrics = get_metrics_registry()
    assert metrics.futuris_context_included_consultations_total >= 1
    assert metrics.forecast_accuracy_pct >= 0.0

def test_forecast_accuracy_feedback_cycle():
    client = get_futuris_client()
    # If forecast probability is 0.42 (< 0.50), no spike was predicted
    outcome = client.record_actual_outcome('BTCUSDT', actual_volatility_spike=False, actual_drawdown_pct=0.02)
    assert 'prediction_correct' in outcome
    assert outcome['prediction_correct'] is True
    
    acc = client.get_accuracy_metrics()
    assert acc['total_evaluated'] >= 1
    assert acc['accuracy_pct'] > 0
