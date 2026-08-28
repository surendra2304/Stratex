import pytest
from intelligence.intelx_client import get_intelx_client, IntelXMarketClient, MarketResearchReport
from advisory_telemetry import build_telemetry_payload
from monitoring.metrics import get_metrics_registry

def test_intelx_trigger_conditions():
    client = IntelXMarketClient()
    
    # 1. Volatility > 2.0 sigma
    trig_vol, reason_vol = client.should_trigger_research('BTCUSDT', volatility_z_score=2.5)
    assert trig_vol is True
    assert 'VOLATILITY_2_SIGMA' in reason_vol

    # 2. Advisory confidence < 0.60
    trig_conf, reason_conf = client.should_trigger_research('ETHUSDT', advisory_confidence=0.45)
    assert trig_conf is True
    assert 'LOW_ADVISORY_CONFIDENCE' in reason_conf

    # 3. Drawdown > 3.0%
    trig_dd, reason_dd = client.should_trigger_research('SOLUSDT', current_drawdown_pct=0.04)
    assert trig_dd is True
    assert 'DRAWDOWN_THRESHOLD' in reason_dd

    # 4. Nominal condition
    trig_nom, reason_nom = client.should_trigger_research('BTCUSDT', volatility_z_score=0.5, advisory_confidence=0.85, current_drawdown_pct=0.01)
    assert trig_nom is False
    assert reason_nom == 'NOMINAL'

def test_intelx_research_query_and_advisory_context_integration():
    client = get_intelx_client()
    report = client.query_market_research('BTCUSDT', trigger_reason='VOLATILITY_2_SIGMA')
    
    assert report.symbol == 'BTCUSDT'
    assert report.is_valid() is True
    assert 'regulatory_changes' in report.findings or len(report.regulatory_changes) > 0
    assert len(report.sentiment_drivers) > 0

    # Build telemetry payload and verify market_context enrichment
    payload = build_telemetry_payload(consultation_reason='VOLATILITY_SURGE')
    assert 'market_context' in payload
    assert payload['market_context'] is not None
    assert payload['market_context']['symbol'] == 'BTCUSDT'
    assert 'summary' in payload['market_context']

    # Verify metrics incremented
    metrics = get_metrics_registry()
    assert metrics.intelx_market_research_total >= 1
    assert metrics.market_context_enriched_consultations_total >= 1
