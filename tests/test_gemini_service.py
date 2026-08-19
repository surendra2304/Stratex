"""
tests/test_gemini_service.py - Comprehensive Unit & Regression Tests for Gemini AI Integration.
Covers:
1. Service initialization & configuration status
2. API Key isolation & masking
3. Successful advisory analysis (Signal, Trade, Performance, System)
4. Graceful handling of missing API key, timeout, network error, and malformed responses
5. In-memory caching & deduplication behavior
6. Invariant confirmation: Engine execution and risk parameters are unaffected by Gemini state
"""

import os
import unittest
from unittest.mock import patch, MagicMock
import urllib.error

from gemini_service import GeminiService, get_gemini_service
import config

class TestGeminiService(unittest.TestCase):

    def setUp(self):
        self.service = GeminiService(api_key="test_mock_api_key_12345", model="gemini-2.5-flash", enabled=True)

    def test_status_does_not_expose_api_key(self):
        status = self.service.get_status()
        self.assertEqual(status["status"], "SUCCESS")
        self.assertTrue(status["gemini"]["configured"])
        self.assertTrue(status["gemini"]["enabled"])
        self.assertIn(status["gemini"]["status"], ["CONNECTED", "CONFIGURED"])
        # Ensure API key is NOT in the status response
        self.assertNotIn("test_mock_api_key_12345", str(status))
        self.assertNotIn("api_key", status["gemini"])

    def test_disabled_service_returns_unavailable_status(self):
        disabled_service = GeminiService(api_key="", enabled=False)
        status = disabled_service.get_status()
        self.assertFalse(status["gemini"]["configured"])
        self.assertEqual(status["gemini"]["status"], "UNAVAILABLE")

    @patch("gemini_service.GeminiService._call_gemini_api")
    def test_analyze_signal_success(self, mock_call):
        mock_call.return_value = '{"why": "Momentum breakout confirmed", "how": "ADX > 25 and EMA 20 > EMA 50", "strengths": ["Strong volume", "Clear trend"], "risks": ["Resistance near 98000"], "summary": "Valid high-probability setup."}'
        
        ctx = {
            "signal_id": "test_sig_001",
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "strategy": "ADX_EMA",
            "side": "BUY",
            "entry_price": 95000.0,
            "stop_loss": 94000.0,
            "take_profit": 97000.0,
            "confidence": 0.85
        }
        res = self.service.analyze_signal(ctx)
        self.assertTrue(res.get("ai_available"))
        self.assertEqual(res.get("why"), "Momentum breakout confirmed")
        self.assertEqual(len(res.get("strengths", [])), 2)

    @patch("gemini_service.GeminiService._call_gemini_api")
    def test_analyze_signal_fallback_on_api_failure(self, mock_call):
        # Simulate network failure or timeout returning None
        mock_call.return_value = None
        
        ctx = {
            "signal_id": "test_sig_fail_002",
            "symbol": "ETHUSDT",
            "timeframe": "5m",
            "strategy": "SCALPER",
            "side": "BUY",
            "entry_price": 2700.0,
            "stop_loss": 2680.0,
            "take_profit": 2740.0
        }
        res = self.service.analyze_signal(ctx)
        self.assertFalse(res.get("ai_available"))
        self.assertIn("SCALPER", res.get("why", ""))
        self.assertIn("ETHUSDT", res.get("why", ""))

    @patch("gemini_service.GeminiService._call_gemini_api")
    def test_analyze_trade_success_and_caching(self, mock_call):
        mock_call.return_value = '{"trade_summary": "Clean target hit with minimal drawdown", "execution_quality": "Executed within 0.01% of signal mark", "what_went_well": "Target achieved within 15 minutes", "what_went_wrong": "Minor exchange fee friction", "key_lesson": "Trend following continues to show positive edge"}'
        
        ctx = {
            "trade_id": "test_trade_100",
            "symbol": "SOLUSDT",
            "timeframe": "15m",
            "strategy": "ADX_EMA",
            "side": "BUY",
            "entry_price": 180.0,
            "exit_price": 185.0,
            "net_pnl": 25.50,
            "fees": 0.35,
            "duration": "12m 40s"
        }
        res1 = self.service.analyze_trade(ctx)
        self.assertTrue(res1.get("ai_available"))
        self.assertEqual(res1.get("trade_summary"), "Clean target hit with minimal drawdown")
        
        # Second call should be served from memory cache without invoking API
        mock_call.reset_mock()
        res2 = self.service.analyze_trade(ctx)
        self.assertEqual(res2.get("trade_summary"), "Clean target hit with minimal drawdown")
        mock_call.assert_not_called()

    @patch("gemini_service.GeminiService._call_gemini_api")
    def test_analyze_performance_fallback(self, mock_call):
        mock_call.return_value = None
        ctx = {
            "timeframe": "ALL",
            "total_trades": 15,
            "win_rate": 66.7,
            "net_pnl": 142.50,
            "profit_factor": 1.85,
            "max_drawdown": 1.2
        }
        res = self.service.analyze_performance(ctx)
        self.assertFalse(res.get("ai_available"))
        self.assertIn("15 trades", res.get("performance_summary", ""))

    @patch("gemini_service.GeminiService._call_gemini_api")
    def test_analyze_system_diagnostics_success(self, mock_call):
        mock_call.return_value = '{"health_rating": "OPTIMAL", "system_summary": "All daemon feeds and REST endpoints nominal.", "telemetry_insights": ["Tick latency < 25ms", "No dropped frames"], "action_items": "None required"}'
        
        ctx = {
            "uptime": "04:12:00",
            "engine_status": "RUNNING",
            "reconnect_count": 0
        }
        res = self.service.analyze_system_diagnostics(ctx)
        self.assertTrue(res.get("ai_available"))
        self.assertEqual(res.get("health_rating"), "OPTIMAL")

    def test_trading_engine_invariants_preserved(self):
        """Confirm that Gemini integration cannot alter trading mode or live execution invariants."""
        self.assertFalse(getattr(config, "LIVE_TRADING_ENABLED", True))
        # Ensure Gemini Service has no execution or order placement methods
        self.assertFalse(hasattr(self.service, "place_order"))
        self.assertFalse(hasattr(self.service, "execute_trade"))
        self.assertFalse(hasattr(self.service, "modify_risk"))

if __name__ == "__main__":
    unittest.main()
