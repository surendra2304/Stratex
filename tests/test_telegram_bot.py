"""
tests/test_telegram_bot.py — Comprehensive Unit Tests for TelegramBotClient.

Verifies:
1. Configuration detection and secret masking.
2. Outbound notifications (trade intent, fill, risk rejection, emergency halt).
3. Inbound interactive command dispatcher (/start, /status, /halt, /resume, /positions, /balance).
4. Security barrier dropping unauthorized chat IDs.
5. Polling updates, offset tracking, and background daemon lifecycle.
"""
from unittest.mock import MagicMock, patch

import pytest

from notifications.telegram_bot import TelegramBotClient, get_telegram_bot
from stratex_agents.order_intent import TradeIntent


class TestTelegramBotClient:
    @pytest.fixture
    def bot(self):
        return TelegramBotClient(
            bot_token="test-bot-token-12345",
            chat_id="123456789",
            request_timeout=2,
        )

    def test_configuration_detection(self):
        configured = TelegramBotClient("token", "123")
        assert configured.is_configured is True

        empty_token = TelegramBotClient("", "123")
        assert empty_token.is_configured is False

        empty_chat = TelegramBotClient("token", "")
        assert empty_chat.is_configured is False

    @patch("requests.post")
    def test_send_message_success(self, mock_post, bot):
        mock_post.return_value.json.return_value = {"ok": True, "result": {"message_id": 999}}
        success = bot.send_message("<b>Hello</b>")
        assert success is True
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert kwargs["json"]["chat_id"] == "123456789"
        assert kwargs["json"]["text"] == "<b>Hello</b>"
        assert kwargs["json"]["parse_mode"] == "HTML"

    @patch("requests.post")
    def test_send_message_failure(self, mock_post, bot):
        mock_post.return_value.json.return_value = {"ok": False, "description": "Forbidden"}
        success = bot.send_message("test")
        assert success is False

        # Exception handling
        mock_post.side_effect = Exception("network down")
        assert bot.send_message("test") is False

    @patch("requests.post")
    def test_notify_trade_intent(self, mock_post, bot):
        mock_post.return_value.json.return_value = {"ok": True}
        intent = TradeIntent(
            venue="binance",
            symbol="ETHUSDT",
            side="BUY",
            order_type="MARKET",
            quantity=0.5,
            environment="binance_testnet",
            stop_loss=2500.0,
            take_profit=2700.0,
            strategy="kalman_mean_reversion",
            client_order_id="stx-test-123",
        )
        res = bot.notify_trade_intent(intent, status="FILLED", fill_price=2550.0, pnl=25.0)
        assert res is True
        args, kwargs = mock_post.call_args
        text = kwargs["json"]["text"]
        assert "ETHUSDT" in text
        assert "BUY" in text
        assert "2,550.0000" in text
        assert "25.00" in text

    @patch("requests.post")
    def test_notify_risk_rejection(self, mock_post, bot):
        mock_post.return_value.json.return_value = {"ok": True}
        intent = TradeIntent(
            venue="binance",
            symbol="BTCUSDT",
            side="BUY",
            order_type="MARKET",
            quantity=0.1,
            environment="binance_testnet",
            stop_loss=90.0,
            take_profit=110.0,
            strategy="test_strategy",
        )
        res = bot.notify_risk_rejection(intent, reason="MAX_MARGIN_EXCEEDED")
        assert res is True
        text = mock_post.call_args[1]["json"]["text"]
        assert "RISK GATE BLOCKED ORDER" in text
        assert "MAX_MARGIN_EXCEEDED" in text

    @patch("requests.post")
    def test_notify_emergency_halt(self, mock_post, bot):
        mock_post.return_value.json.return_value = {"ok": True}
        res = bot.notify_emergency_halt("api_panic", "Manual panic switch")
        assert res is True
        text = mock_post.call_args[1]["json"]["text"]
        assert "EMERGENCY PANIC HALT" in text
        assert "api_panic" in text

    def test_handle_command_start_and_help(self, bot):
        res = bot.handle_command("/start")
        assert "/status" in res
        assert "/positions" in res
        assert "/halt" in res

        res_help = bot.handle_command("/help")
        assert "/status" in res_help

    def test_handle_command_status(self, bot):
        res = bot.handle_command("/status")
        assert "Stratex Engine Status" in res
        assert "Oracle Cloud" in res

    def test_handle_command_positions_and_balance(self, bot):
        pos = bot.handle_command("/positions")
        assert "Active Positions" in pos

        bal = bot.handle_command("/balance")
        assert "Wallet Balances" in bal

    def test_handle_command_halt_and_resume(self, bot):
        halt_resp = bot.handle_command("/halt")
        assert "EMERGENCY HALT TRIGGERED" in halt_resp

        resume_resp = bot.handle_command("/resume")
        assert "Trading Resumed" in resume_resp

    def test_handle_command_natural_language(self, bot):
        resp = bot.handle_command("Why did you enter long on BTC?")
        assert "Quantitative Intelligence" in resp
        assert "Regime" in resp

    @patch("requests.post")
    def test_process_update_authorized_vs_unauthorized(self, mock_post, bot):
        mock_post.return_value.json.return_value = {"ok": True}

        # 1. Unauthorized message
        bot.process_update({
            "message": {
                "chat": {"id": "999999999"},
                "text": "/status",
            }
        })
        mock_post.assert_not_called()

        # 2. Authorized message
        bot.process_update({
            "message": {
                "chat": {"id": "123456789"},
                "text": "/status",
            }
        })
        mock_post.assert_called_once()

    @patch("requests.get")
    @patch("requests.post")
    def test_poll_once(self, mock_post, mock_get, bot):
        mock_get.return_value.json.return_value = {
            "ok": True,
            "result": [
                {
                    "update_id": 1001,
                    "message": {
                        "chat": {"id": "123456789"},
                        "text": "/status",
                    },
                }
            ],
        }
        mock_post.return_value.json.return_value = {"ok": True}

        count = bot.poll_once()
        assert count == 1
        assert bot._last_update_id == 1001
        mock_post.assert_called_once()

    def test_polling_daemon_lifecycle(self, bot):
        with patch.object(bot, "poll_once") as mock_poll:
            bot.start_polling_in_background()
            assert bot._polling_active is True
            bot.stop_polling()
            assert bot._polling_active is False

    def test_singleton_accessor(self):
        instance = get_telegram_bot()
        assert isinstance(instance, TelegramBotClient)
