"""
tests/test_ai_advisor.py — Unit & Integration Tests for Stratex AI Strategic Advisor.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from autonomy.ai_advisor import AITradeVerdict, StratexAIAdvisor, get_ai_advisor


class TestStratexAIAdvisor:
    def test_initialization(self):
        advisor = StratexAIAdvisor(gemini_api_key="AIzaSyTestKey123", openrouter_key="sk-or-test")
        assert advisor.gemini_key == "AIzaSyTestKey123"
        assert advisor.openrouter_key == "sk-or-test"

    @patch("google.genai.Client")
    def test_call_llm_direct_gemini_success(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.text = "Gemini Response"
        mock_client.models.generate_content.return_value = mock_resp

        advisor = StratexAIAdvisor(gemini_api_key="AIzaSyTestKey123")
        res = advisor._call_llm("sys", "user")
        assert res == "Gemini Response"

    @patch("requests.post")
    def test_call_llm_openrouter_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "OpenRouter Response"}}]
        }
        mock_post.return_value = mock_resp

        advisor = StratexAIAdvisor(gemini_api_key="", openrouter_key="sk-or-test")
        res = advisor._call_llm("sys", "user")
        assert res == "OpenRouter Response"

    def test_call_llm_fallback_when_unconfigured(self):
        advisor = StratexAIAdvisor(gemini_api_key="", openrouter_key="")
        advisor.gemini_key = ""
        advisor.openrouter_key = ""
        res = advisor._call_llm("sys", "user")
        assert res is None

    @patch.object(StratexAIAdvisor, "_call_llm")
    def test_evaluate_trade_candidate_approved(self, mock_call):
        mock_call.return_value = json.dumps({
            "approved": True,
            "verdict": "APPROVED",
            "confidence": 0.88,
            "thesis": "Strong momentum confirmed by volume expansion.",
            "risk_factors": ["High spread volatility"],
        })

        advisor = StratexAIAdvisor()
        verdict = advisor.evaluate_trade_candidate(
            symbol="EURUSD",
            timeframe="15m",
            action="BUY",
            current_price=1.1050,
            stop_loss=1.1000,
            take_profit=1.1150,
            risk_reward_ratio=2.0,
            indicators={"regime": "LOW_VOL_TRENDING"},
        )
        assert verdict.approved is True
        assert verdict.verdict == "APPROVED"
        assert verdict.confidence == 0.88
        assert "Strong momentum" in verdict.thesis
        assert verdict.risk_factors == ["High spread volatility"]

    @patch.object(StratexAIAdvisor, "_call_llm", return_value=None)
    def test_evaluate_trade_candidate_deterministic_fallback(self, mock_call):
        advisor = StratexAIAdvisor()
        verdict = advisor.evaluate_trade_candidate(
            symbol="GBPUSD",
            timeframe="5m",
            action="SELL",
            current_price=1.2500,
            stop_loss=1.2550,
            take_profit=1.2400,
            risk_reward_ratio=2.0,
            indicators={},
        )
        assert verdict.approved is False
        assert verdict.verdict == "REJECTED"
        assert verdict.confidence == 0.0
        assert verdict.model_used == "unavailable"
        assert "approval withheld" in verdict.thesis.lower()

    @pytest.mark.parametrize(
        "raw",
        [
            "{not-json",
            "[]",
            '{"approved": "yes", "verdict": "APPROVED", "confidence": 0.9, "thesis": "ok", "risk_factors": []}',
            '{"approved": true, "verdict": "APPROVED", "confidence": 1.5, "thesis": "ok", "risk_factors": []}',
            '{"approved": true, "verdict": "APPROVED", "confidence": NaN, "thesis": "ok", "risk_factors": []}',
            '{"approved": true, "verdict": "REJECTED", "confidence": 0.5, "thesis": "ok", "risk_factors": []}',
            '{"approved": false, "verdict": "APPROVED", "confidence": 0.5, "thesis": "ok", "risk_factors": []}',
            '{"approved": true, "verdict": "APPROVED", "confidence": 0.5, "thesis": "ok", "risk_factors": "not-a-list"}',
            '{"approved": true, "verdict": "APPROVED", "confidence": 0.5, "risk_factors": []}',
        ],
    )
    @patch.object(StratexAIAdvisor, "_call_llm")
    def test_invalid_ai_verdict_fails_closed(self, mock_call, raw):
        mock_call.return_value = raw
        verdict = StratexAIAdvisor().evaluate_trade_candidate(
            symbol="EURUSD",
            timeframe="15m",
            action="BUY",
            current_price=1.1050,
            stop_loss=1.1000,
            take_profit=1.1150,
            risk_reward_ratio=2.0,
            indicators={},
        )

        assert verdict.approved is False
        assert verdict.verdict == "REJECTED"
        assert verdict.confidence == 0.0
        assert verdict.model_used == "unavailable"

    @patch.object(StratexAIAdvisor, "_call_llm", side_effect=RuntimeError("provider unavailable"))
    def test_ai_provider_exception_fails_closed(self, mock_call):
        verdict = StratexAIAdvisor().evaluate_trade_candidate(
            symbol="EURUSD",
            timeframe="15m",
            action="BUY",
            current_price=1.1050,
            stop_loss=1.1000,
            take_profit=1.1150,
            risk_reward_ratio=2.0,
            indicators={},
        )
        assert verdict.approved is False
        assert verdict.verdict == "REJECTED"
        assert verdict.model_used == "unavailable"

    @patch.object(StratexAIAdvisor, "_call_llm")
    def test_generate_chat_response_with_llm(self, mock_call):
        mock_call.return_value = "Hello! Stratex AI is active."
        advisor = StratexAIAdvisor()
        reply = advisor.generate_chat_response("Hi there")
        assert reply == "Hello! Stratex AI is active."

    @patch.object(StratexAIAdvisor, "_call_llm", return_value=None)
    def test_generate_chat_response_fallback(self, mock_call):
        advisor = StratexAIAdvisor()
        reply = advisor.generate_chat_response("What is your status?")
        assert "Stratex Quantitative Intelligence" in reply
        assert "Monitoring MetaTrader 5 markets" in reply

    @patch("requests.post")
    def test_call_llm_inference_mesh_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "Inference Response"}}]
        }
        mock_post.return_value = mock_resp

        advisor = StratexAIAdvisor(gemini_api_key="", inference_url="https://friday-universe.duckdns.org/inference", inference_key="inf-test-key")
        res = advisor._call_inference_mesh("sys", "user")
        assert res == "Inference Response"
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert "Bearer inf-test-key" in kwargs["headers"]["Authorization"]

    @patch.object(StratexAIAdvisor, "_call_llm")
    def test_evaluate_trade_candidate_dampened_and_rejected(self, mock_call):
        advisor = StratexAIAdvisor()

        # Dampened
        mock_call.return_value = json.dumps({
            "approved": True,
            "verdict": "DAMPENED",
            "confidence": 0.65,
            "thesis": "High volatility dampens position size.",
            "risk_factors": ["Event risk ahead"],
        })
        v1 = advisor.evaluate_trade_candidate(
            symbol="EURUSD", timeframe="15m", action="BUY",
            current_price=1.1050, stop_loss=1.1000, take_profit=1.1150,
            risk_reward_ratio=2.0, indicators={},
        )
        assert v1.approved is True
        assert v1.verdict == "DAMPENED"

        # Rejected
        mock_call.return_value = json.dumps({
            "approved": False,
            "verdict": "REJECTED",
            "confidence": 0.35,
            "thesis": "Unfavorable distribution and macro headwind.",
            "risk_factors": ["Fed rate announcement"],
        })
        v2 = advisor.evaluate_trade_candidate(
            symbol="EURUSD", timeframe="15m", action="BUY",
            current_price=1.1050, stop_loss=1.1000, take_profit=1.1150,
            risk_reward_ratio=2.0, indicators={},
        )
        assert v2.approved is False
        assert v2.verdict == "REJECTED"

    def test_singleton_accessor(self):
        a1 = get_ai_advisor()
        a2 = get_ai_advisor()
        assert a1 is a2
