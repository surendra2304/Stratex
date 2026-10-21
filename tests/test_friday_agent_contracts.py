"""
tests/test_friday_agent_contracts.py — Contract tests for FRIDAY Universe Cross-Agent Contracts.

Validates Phase E requirements:
1. Futuris Forecast Contract: horizons, calibration, uncertainty bounds, staleness rejection.
2. IntelX Research Contract: sentiment bounds, citation requirements, untrusted text sanitization.
3. Inference Gateway Contract: explanation routing, resilient fallback on outage.
4. Sentinel Governance Contract: capability-scoped verification and emergency halt dispatch.
5. Memora Scoped Audit Client: Private, Shared, Universal scopes and non-blocking delivery.
6. Advisory Supervisor: consensus evaluation, dampening, skip recommendations, and HARD RISK INVARIANT (0.0 <= size_multiplier <= 1.0).
"""
from __future__ import annotations

import datetime
import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from autonomy.agent_contracts import (
    AdvisoryEvaluation,
    AgentAdvisorySupervisor,
    FuturisContractClient,
    FuturisForecast,
    InferenceContractClient,
    InferenceExplanation,
    IntelXContractClient,
    IntelXResearch,
    MemoraScope,
    MemoraScopedClient,
    SentinelContractClient,
    SentinelPolicyResult,
)


def _fresh_iso(seconds_ago: float = 0.0) -> str:
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seconds_ago)
    return dt.isoformat().replace("+00:00", "Z")


def _make_mock_response(status: int = 200, data: dict | None = None) -> MagicMock:
    mock = MagicMock()
    mock.__enter__.return_value = mock
    mock.status = status
    if data is not None:
        mock.read.return_value = json.dumps(data).encode("utf-8")
    else:
        mock.read.return_value = b""
    return mock


# ==============================================================================
# 1. FUTURIS FORECAST TESTS
# ==============================================================================

def test_futuris_forecast_valid():
    fc = FuturisForecast(
        forecast_id="fut-001",
        symbol="BTCUSDT",
        horizon="15m",
        direction="BULLISH",
        expected_return=0.015,
        uncertainty=0.005,
        calibration_score=0.85,
        timestamp=_fresh_iso(10),
    )
    valid, msg = fc.validate()
    assert valid is True
    assert msg == "valid"


def test_futuris_forecast_invalid_horizon_and_direction():
    fc_bad_horizon = FuturisForecast(
        forecast_id="fut-002", symbol="BTCUSDT", horizon="2y", direction="BULLISH",
        expected_return=0.01, uncertainty=0.01, calibration_score=0.8, timestamp=_fresh_iso(),
    )
    valid, msg = fc_bad_horizon.validate()
    assert valid is False
    assert "horizon" in msg

    fc_bad_dir = FuturisForecast(
        forecast_id="fut-003", symbol="BTCUSDT", horizon="15m", direction="MOON",
        expected_return=0.01, uncertainty=0.01, calibration_score=0.8, timestamp=_fresh_iso(),
    )
    valid, msg = fc_bad_dir.validate()
    assert valid is False
    assert "direction" in msg


def test_futuris_forecast_invalid_numbers_and_bounds():
    # Negative uncertainty
    fc_neg_unc = FuturisForecast(
        forecast_id="fut-004", symbol="BTCUSDT", horizon="15m", direction="BULLISH",
        expected_return=0.01, uncertainty=-0.05, calibration_score=0.8, timestamp=_fresh_iso(),
    )
    valid, msg = fc_neg_unc.validate()
    assert valid is False
    assert "uncertainty must be non-negative" in msg

    # Calibration score > 1.0
    fc_calib_high = FuturisForecast(
        forecast_id="fut-005", symbol="BTCUSDT", horizon="15m", direction="BULLISH",
        expected_return=0.01, uncertainty=0.01, calibration_score=1.5, timestamp=_fresh_iso(),
    )
    valid, msg = fc_calib_high.validate()
    assert valid is False
    assert "calibration_score must be between 0.0 and 1.0" in msg

    # NaN expected return
    fc_nan = FuturisForecast(
        forecast_id="fut-006", symbol="BTCUSDT", horizon="15m", direction="BULLISH",
        expected_return=float("nan"), uncertainty=0.01, calibration_score=0.8, timestamp=_fresh_iso(),
    )
    valid, msg = fc_nan.validate()
    assert valid is False
    assert "must be a finite float" in msg


def test_futuris_forecast_staleness_rejection():
    # Stale: 400s old when max_age is 300s
    fc_stale = FuturisForecast(
        forecast_id="fut-007", symbol="BTCUSDT", horizon="15m", direction="BULLISH",
        expected_return=0.01, uncertainty=0.01, calibration_score=0.8,
        timestamp=_fresh_iso(400),
    )
    valid, msg = fc_stale.validate(max_age_sec=300.0)
    assert valid is False
    assert "stale" in msg


def test_futuris_client_fetch_and_error_handling():
    client = FuturisContractClient(base_url="http://futuris.test:8000", api_key="test-key")

    mock_resp = _make_mock_response(200, {
        "data": {
            "forecast_id": "fut-100",
            "symbol": "BTCUSDT",
            "horizon": "15m",
            "direction": "BULLISH",
            "expected_return": 0.02,
            "uncertainty": 0.01,
            "calibration_score": 0.9,
            "timestamp": _fresh_iso(5),
        }
    })

    with patch("urllib.request.urlopen", return_value=mock_resp):
        fc = client.get_forecast("BTCUSDT", horizon="15m")
        assert fc is not None
        assert fc.forecast_id == "fut-100"
        assert fc.direction == "BULLISH"

    # Network failure fails soft
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Network down")):
        fc_fail = client.get_forecast("BTCUSDT")
        assert fc_fail is None


# ==============================================================================
# 2. INTELX RESEARCH TESTS
# ==============================================================================

def test_intelx_research_valid():
    res = IntelXResearch(
        research_id="int-001",
        symbol="BTCUSDT",
        sentiment_score=0.75,
        confidence=0.85,
        evidence_summary="Institutional ETF inflows reached new weekly highs.",
        citations=[{"url": "https://reuters.com/markets/crypto-etf", "title": "Reuters Inflow Report"}],
        timestamp=_fresh_iso(),
    )
    valid, msg = res.validate()
    assert valid is True
    assert res.is_grounded is True


def test_intelx_research_invalid_sentiment_and_confidence():
    res_bad_sent = IntelXResearch(
        research_id="int-002", symbol="BTCUSDT", sentiment_score=2.5, confidence=0.8,
        evidence_summary="test", citations=[{"url": "https://example.com"}], timestamp=_fresh_iso(),
    )
    valid, msg = res_bad_sent.validate()
    assert valid is False
    assert "sentiment_score must be between -1.0 and 1.0" in msg

    res_bad_conf = IntelXResearch(
        research_id="int-003", symbol="BTCUSDT", sentiment_score=0.5, confidence=-0.1,
        evidence_summary="test", citations=[{"url": "https://example.com"}], timestamp=_fresh_iso(),
    )
    valid, msg = res_bad_conf.validate()
    assert valid is False
    assert "confidence must be between 0.0 and 1.0" in msg


def test_intelx_research_missing_citations_ungrounded():
    res_no_cites = IntelXResearch(
        research_id="int-004", symbol="BTCUSDT", sentiment_score=0.5, confidence=0.8,
        evidence_summary="Unverified rumors on telegram.", citations=[], timestamp=_fresh_iso(),
    )
    valid, msg = res_no_cites.validate()
    assert valid is False
    assert res_no_cites.is_grounded is False
    assert "citations must be a non-empty list" in msg


def test_intelx_sanitize_untrusted_text():
    raw = "<script>alert('pwn')</script>Market is <b>bullish</b>!\x00\x1f"
    sanitized = IntelXContractClient.sanitize_untrusted_text(raw)
    assert "<script>" not in sanitized
    assert "<b>" not in sanitized
    assert "\x00" not in sanitized
    assert "Market is bullish!" in sanitized


def test_intelx_client_fetch_and_error_handling():
    client = IntelXContractClient(base_url="http://intelx.test:8000")

    mock_resp = _make_mock_response(200, {
        "data": {
            "research_id": "int-101",
            "symbol": "ETHUSDT",
            "sentiment_score": 0.45,
            "confidence": 0.78,
            "evidence_summary": "<p>Staking yields remain stable.</p>",
            "citations": [{"source_id": "src-eth-01", "snippet": "Staking yield metric"}],
            "timestamp": _fresh_iso(),
        }
    })

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res = client.get_market_intelligence("ETHUSDT")
        assert res is not None
        assert res.research_id == "int-101"
        assert "<p>" not in res.evidence_summary

    # Network failure fails soft
    with patch("urllib.request.urlopen", side_effect=TimeoutError("Connection timed out")):
        res_fail = client.get_market_intelligence("ETHUSDT")
        assert res_fail is None


# ==============================================================================
# 3. INFERENCE GATEWAY TESTS
# ==============================================================================

def test_inference_client_explain_success():
    client = InferenceContractClient(base_url="http://inference.test:8000")

    mock_resp = _make_mock_response(200, {
        "data": {
            "explanation_id": "inf-999",
            "rationale": "EMA crossover supported by positive volume divergence.",
            "confidence": 0.88,
            "provider": "anthropic/claude-3.5-sonnet",
        }
    })

    with patch("urllib.request.urlopen", return_value=mock_resp):
        expl = client.explain_decision(
            symbol="BTCUSDT",
            strategy="trend_ema",
            indicators={"ema_9": 65000, "ema_21": 64500},
            proposed_action="BUY",
        )
        assert expl.explanation_id == "inf-999"
        assert expl.is_fallback is False
        assert "positive volume divergence" in expl.explanation


def test_inference_client_fallback_on_error():
    client = InferenceContractClient(base_url="http://inference.test:8000")

    with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("url", 503, "Unavailable", {}, None)):
        expl = client.explain_decision(
            symbol="BTCUSDT",
            strategy="rsi_mean_reversion",
            indicators={"rsi": 28.5},
            proposed_action="BUY",
        )
        assert expl.is_fallback is True
        assert "Deterministic rule triggered" in expl.explanation
        assert expl.model_provider == "local_fallback"


# ==============================================================================
# 4. SENTINEL GOVERNANCE TESTS
# ==============================================================================

def test_sentinel_policy_verification():
    client = SentinelContractClient(base_url="http://sentinel.test:8000")

    mock_resp = _make_mock_response(200, {
        "allowed": True,
        "reason": "action_within_bounded_policy",
        "policy_id": "sentinel-trading-policy-v1",
        "audit_id": "aud-12345",
    })

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res = client.verify_action_authorization("submit_order", "stratex_oms")
        assert res.allowed is True
        assert res.policy_id == "sentinel-trading-policy-v1"


def test_sentinel_dispatch_emergency_halt():
    client = SentinelContractClient(base_url="http://sentinel.test:8000")

    mock_resp = _make_mock_response(202)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        ok = client.dispatch_emergency_halt("admin", "max daily drawdown triggered")
        assert ok is True

    with patch("urllib.request.urlopen", side_effect=Exception("network err")):
        ok_fail = client.dispatch_emergency_halt("admin", "reason")
        assert ok_fail is False


# ==============================================================================
# 5. MEMORA SCOPED AUDIT CLIENT TESTS
# ==============================================================================

def test_memora_scoped_client_record_event():
    client = MemoraScopedClient(base_url="http://memora.test:8000", api_key="memora-key")

    mock_resp = _make_mock_response(200)

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        ok = client.record_scoped_event(
            event_type="trade_intent_authorized",
            scope=MemoraScope.SHARED,
            summary="BUY 0.01 BTCUSDT authorized under trend_ema",
            details={"client_order_id": "stx-001", "venue": "binance"},
        )
        assert ok is True
        req = mock_urlopen.call_args[0][0]
        body = json.loads(req.data.decode("utf-8"))
        assert body["scope"] == "SHARED"
        assert body["agent_name"] == "stratex"
        assert body["metadata"]["provenance"] == "stratex_oms"


# ==============================================================================
# 6. AGENT ADVISORY SUPERVISOR & HARD RISK INVARIANT TESTS
# ==============================================================================

def test_advisory_supervisor_proceed_when_agents_offline():
    # When both Futuris and IntelX are offline, default to PROCEED at 1.0x
    supervisor = AgentAdvisorySupervisor(
        futuris_client=FuturisContractClient(),
        intelx_client=IntelXContractClient(),
    )
    with patch.object(supervisor.futuris, "get_forecast", return_value=None), \
         patch.object(supervisor.intelx, "get_market_intelligence", return_value=None):
        eval_res = supervisor.evaluate_strategy_candidate("BTCUSDT", "BUY", "ema_cross", 60000.0)
        assert eval_res.verdict == "PROCEED"
        assert eval_res.size_multiplier == 1.0
        assert len(eval_res.reasons) == 0


def test_advisory_supervisor_dampen_on_high_uncertainty():
    supervisor = AgentAdvisorySupervisor()
    forecast = FuturisForecast(
        forecast_id="fut-unc-1", symbol="BTCUSDT", horizon="15m", direction="BULLISH",
        expected_return=0.01, uncertainty=0.045, calibration_score=0.8, timestamp=_fresh_iso(),
    )
    with patch.object(supervisor.futuris, "get_forecast", return_value=forecast), \
         patch.object(supervisor.intelx, "get_market_intelligence", return_value=None):
        eval_res = supervisor.evaluate_strategy_candidate("BTCUSDT", "BUY", "ema_cross", 60000.0)
        assert eval_res.verdict == "DAMPEN"
        assert eval_res.size_multiplier == 0.7
        assert any("uncertainty" in r for r in eval_res.reasons)


def test_advisory_supervisor_skip_on_strong_forecast_contradiction():
    supervisor = AgentAdvisorySupervisor()
    # Strategy says BUY, but Futuris says high-confidence BEARISH
    forecast = FuturisForecast(
        forecast_id="fut-contra-1", symbol="BTCUSDT", horizon="15m", direction="BEARISH",
        expected_return=-0.03, uncertainty=0.01, calibration_score=0.88, timestamp=_fresh_iso(),
    )
    with patch.object(supervisor.futuris, "get_forecast", return_value=forecast), \
         patch.object(supervisor.intelx, "get_market_intelligence", return_value=None):
        eval_res = supervisor.evaluate_strategy_candidate("BTCUSDT", "BUY", "ema_cross", 60000.0)
        assert eval_res.verdict == "SKIP"
        assert eval_res.size_multiplier == 0.0
        assert any("strongly contradicts" in r for r in eval_res.reasons)


def test_advisory_supervisor_dampen_on_negative_intelx_sentiment():
    supervisor = AgentAdvisorySupervisor()
    research = IntelXResearch(
        research_id="int-neg-1", symbol="BTCUSDT", sentiment_score=-0.75, confidence=0.82,
        evidence_summary="Macro regulatory headwinds announced.",
        citations=[{"url": "https://wsj.com/news"}], timestamp=_fresh_iso(),
    )
    with patch.object(supervisor.futuris, "get_forecast", return_value=None), \
         patch.object(supervisor.intelx, "get_market_intelligence", return_value=research):
        eval_res = supervisor.evaluate_strategy_candidate("BTCUSDT", "BUY", "ema_cross", 60000.0)
        assert eval_res.verdict == "DAMPEN"
        assert eval_res.size_multiplier == 0.5
        assert any("negative market sentiment" in r for r in eval_res.reasons)


def test_hard_risk_invariant_never_enlarges_size():
    """
    CRITICAL SAFETY INVARIANT:
    Even if Futuris is 100% confident and IntelX is 100% bullish,
    the advisory size_multiplier CANNOT exceed 1.0. It can never enlarge a trade.
    """
    supervisor = AgentAdvisorySupervisor()
    forecast = FuturisForecast(
        forecast_id="fut-bull-1", symbol="BTCUSDT", horizon="15m", direction="BULLISH",
        expected_return=0.10, uncertainty=0.001, calibration_score=0.99, timestamp=_fresh_iso(),
    )
    research = IntelXResearch(
        research_id="int-bull-1", symbol="BTCUSDT", sentiment_score=0.99, confidence=0.99,
        evidence_summary="Massive institutional inflow.",
        citations=[{"url": "https://bloomberg.com"}], timestamp=_fresh_iso(),
    )
    with patch.object(supervisor.futuris, "get_forecast", return_value=forecast), \
         patch.object(supervisor.intelx, "get_market_intelligence", return_value=research):
        eval_res = supervisor.evaluate_strategy_candidate("BTCUSDT", "BUY", "ema_cross", 60000.0)
        assert eval_res.size_multiplier <= 1.0
        assert eval_res.size_multiplier == 1.0
        assert eval_res.verdict == "PROCEED"
