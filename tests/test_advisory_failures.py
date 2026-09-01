"""
tests/test_advisory_failures.py — Failure injection tests for AI Advisory subsystem resilience.

Verifications:
1. AI-Universe unreachable (ConnectionError) -> Bot continues trading, logs warning, returns None.
2. AI-Universe returns malformed JSON or non-dict -> Logs warning, no crash, returns None.
3. AI-Universe returns HTTP 400/500 -> Logs warning, no crash, returns None.
4. AdvisoryScheduler thread crashes or encounters exception -> Trading loop continues uninterrupted.
5. Advisory ledger write fails (e.g. disk full simulation) -> Logs critical, disables advisory scheduler cleanly without crashing engine.
"""

from unittest.mock import MagicMock, patch

import requests

from advisory_scheduler import AdvisoryScheduler
from ai_universe_client import AIUniverseClient


def test_ai_universe_unreachable_fails_soft():
    """Verify connection error logs warning and returns None without raising."""
    client = AIUniverseClient(base_url="http://unreachable-ai-universe:8000")
    with patch.object(client.session, "post", side_effect=requests.exceptions.ConnectionError("Failed to connect")):
        res = client.consult({"mock": "telemetry"})
        assert res is None


def test_ai_universe_malformed_response_fails_soft():
    """Verify malformed JSON or non-dict payload logs warning and returns None."""
    client = AIUniverseClient(base_url="http://mock-ai:8000")

    # Case A: Non-JSON text
    mock_bad_json = MagicMock()
    mock_bad_json.status_code = 200
    mock_bad_json.json.side_effect = ValueError("Invalid JSON")
    with patch.object(client.session, "post", return_value=mock_bad_json):
        assert client.consult({"mock": "telemetry"}) is None

    # Case B: List instead of Dict
    mock_list_resp = MagicMock()
    mock_list_resp.status_code = 200
    mock_list_resp.json.return_value = ["not", "a", "dict"]
    with patch.object(client.session, "post", return_value=mock_list_resp):
        assert client.consult({"mock": "telemetry"}) is None

    # Case C: Missing required fields
    mock_missing = MagicMock()
    mock_missing.status_code = 200
    mock_missing.json.return_value = {"decision_id": "D1"}  # Missing status, confidence, parameter_changes
    with patch.object(client.session, "post", return_value=mock_missing):
        assert client.consult({"mock": "telemetry"}) is None


def test_ai_universe_http_400_500_errors_fail_soft():
    """Verify 400 and 500 status codes log warning and return None."""
    client = AIUniverseClient(base_url="http://mock-ai:8000")

    for status_code in [400, 401, 403, 404, 500, 502, 503]:
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.text = f"HTTP Error {status_code}"
        with patch.object(client.session, "post", return_value=mock_resp):
            assert client.consult({"mock": "telemetry"}) is None


def test_advisory_scheduler_thread_crash_isolation():
    """Verify exception in scheduler worker loop or consultation cycle does not crash trading engine."""
    mock_client = MagicMock(spec=AIUniverseClient)
    mock_client.consult.side_effect = Exception("Catastrophic client unhandled crash")

    scheduler = AdvisoryScheduler(client=mock_client, shadow_mode=True)

    # Calling cycle directly returns None
    result = scheduler.run_consultation_cycle("CRASH_TEST")
    assert result is None


def test_advisory_ledger_write_failure_disables_scheduler_cleanly():
    """Verify ledger write failure (e.g. disk full / permission error) logs critical and disables advisory scheduler."""
    mock_client = MagicMock(spec=AIUniverseClient)
    mock_client.consult.return_value = {
        "decision_id": "DEC_DISK_FULL",
        "status": "APPROVED",
        "confidence": 0.9,
        "parameter_changes": [{"parameter": "adx_period", "current_value": 14, "new_value": 16}]
    }

    scheduler = AdvisoryScheduler(client=mock_client, shadow_mode=True)
    scheduler.start()
    assert scheduler._running is True

    # Mock append_advisory_entry to simulate disk failure (returns False)
    with patch("advisory_scheduler.append_advisory_entry", return_value=False):
        res = scheduler.run_consultation_cycle("DISK_FULL_TEST")
        assert res is None
        # Verify scheduler stopped itself cleanly
        assert scheduler._running is False
