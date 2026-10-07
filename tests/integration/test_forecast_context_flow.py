"""Integration flow: Futuris forecast context enrichment & accuracy feedback.

These tests exercise the REAL FuturisMarketClient logic (parsing, caching,
validity windows, accuracy bookkeeping) against a SIMULATED Futuris peer: the
HTTP transport is mocked so the suite is hermetic (no outbound network), while
every line of client-side behaviour still runs. A live-network variant of this
flow belongs in deployment smoke checks, never in the unit/integration suite.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from advisory_telemetry import build_telemetry_payload
from intelligence.futuris_client import get_futuris_client
from monitoring.metrics import get_metrics_registry

# A contract-shaped Futuris response: all three sections present and non-empty,
# volatility probability below the 0.50 spike threshold.
_SIMULATED_FUTURIS_RESPONSE = {
    "volatility_forecast": {"probability": 0.42, "expected_move_pct": 1.31},
    "drawdown_risk": {"probability": 0.11, "expected_drawdown_pct": 2.4},
    "regime_outlook": {"current": "NEUTRAL", "predicted_direction": "SIDEWAYS"},
}


class _SimulatedPeerResponse:
    status_code = 200

    def json(self):
        return json.loads(json.dumps(_SIMULATED_FUTURIS_RESPONSE))


@pytest.fixture()
def simulated_futuris_peer():
    """Patch the transport and clear any cached/stale forecast state."""
    client = get_futuris_client()
    with client._lock:
        client.cache.clear()
    with patch("intelligence.futuris_client.requests.post", return_value=_SimulatedPeerResponse()) as post:
        yield post
    with client._lock:
        client.cache.clear()


def test_forecast_context_enrichment_flow(simulated_futuris_peer):
    client = get_futuris_client()
    forecast = client.fetch_forecast('BTCUSDT')

    assert forecast.symbol == 'BTCUSDT'
    assert forecast.status == 'LIVE'
    assert 'probability' in forecast.volatility_forecast
    assert 'probability' in forecast.drawdown_risk
    assert 'current' in forecast.regime_outlook
    # The simulated peer must actually have been consulted over the wire layer.
    assert simulated_futuris_peer.call_count >= 1

    # Advisory telemetry integration includes futuris_context
    payload = build_telemetry_payload(consultation_reason='VOLATILITY_SPIKE_PREDICTED')
    assert 'futuris_context' in payload
    assert payload['futuris_context'] is not None
    assert payload['futuris_context']['volatility_forecast']['probability'] >= 0.0

    # Metrics increment
    metrics = get_metrics_registry()
    assert metrics.futuris_context_included_consultations_total >= 1
    assert metrics.forecast_accuracy_pct >= 0.0


def test_forecast_accuracy_feedback_cycle(simulated_futuris_peer):
    client = get_futuris_client()
    # Establish a fresh LIVE forecast from the simulated peer first: without it
    # there is legitimately nothing to evaluate (the client must refuse, not
    # invent, an outcome record).
    forecast = client.fetch_forecast('BTCUSDT')
    assert forecast.status == 'LIVE'

    # Forecast probability is 0.42 (< 0.50), so no spike was predicted.
    outcome = client.record_actual_outcome('BTCUSDT', actual_volatility_spike=False, actual_drawdown_pct=0.02)
    assert outcome['status'] == 'RECORDED'
    assert 'prediction_correct' in outcome
    assert outcome['prediction_correct'] is True

    acc = client.get_accuracy_metrics()
    assert acc['total_evaluated'] >= 1
    assert acc['accuracy_pct'] > 0


def test_record_outcome_refuses_without_live_forecast():
    """No fresh LIVE forecast => the client must report UNAVAILABLE, never invent accuracy."""
    client = get_futuris_client()
    with client._lock:
        client.cache.clear()
    # No transport patch: whatever happens, no LIVE forecast can be established
    # in the hermetic environment, so evaluation must be refused.
    with patch("intelligence.futuris_client.requests.post", side_effect=ConnectionError("hermetic")):
        client.fetch_forecast('BTCUSDT')
        outcome = client.record_actual_outcome('BTCUSDT', actual_volatility_spike=True, actual_drawdown_pct=0.05)
    assert outcome['status'] == 'UNAVAILABLE'
    assert 'prediction_correct' not in outcome
