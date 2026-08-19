import pytest
import os
import json
import importlib
from unittest import mock
import config
import execution
from dashboard import app

class TestForensicHardening:

    def test_live_trading_impossible_by_design(self):
        """Proof that LIVE execution is blocked by design across all entrypoints."""
        # 1. config.py actively refuses to load or validate TRADING_MODE='LIVE'
        with mock.patch.dict(os.environ, {"TRADING_MODE": "LIVE"}):
            with pytest.raises(ValueError, match="Invalid TRADING_MODE 'LIVE'"):
                importlib.reload(config)

        # 2. Even if execution is monkeypatched with TRADING_MODE='LIVE', it is rejected
        with mock.patch("execution.TRADING_MODE", "LIVE"), \
             mock.patch("execution.LIVE_TRADING_ENABLED", True):
            allowed, reason = execution.ExecutionPolicy.can_place_order()
            assert allowed is False
            assert "LIVE" in reason or "FORBIDDEN" in reason

            with pytest.raises(RuntimeError, match="LIVE trading is permanently disabled by design"):
                execution.get_exchange_client()

            with pytest.raises(RuntimeError, match="LIVE trading is permanently disabled by design"):
                execution.place_market_order("scalper", "BUY", "BTCUSDT", 0.001)

    def test_risk_configuration_authoritative(self):
        """Proof that risk configuration parameters are distinct and authoritative."""
        assert config.MAX_TESTNET_RISK_PER_TRADE == 0.005
        assert config.MAX_TESTNET_EXPOSURE == 0.05
        assert config.MAX_SINGLE_ASSET_EXPOSURE == 0.02
        assert config.MAX_NET_DIRECTIONAL_EXPOSURE == 0.04
        assert config.MAX_OPEN_POSITIONS == 5
        assert config.MAX_DAILY_LOSS_PCT == 0.02
        assert config.MAX_TESTNET_DRAWDOWN_PCT == 0.05
        assert config.BACKTEST_RISK_PER_TRADE == 0.01
        assert config.RISK_PER_TRADE == 0.01

    def test_api_config_security_and_validation(self):
        """Proof that /api/config rejects invalid types, live mode, and values exceeding safety ceilings."""
        client = app.test_client()

        # 1. GET returns valid config with live_trading_enabled: False
        res = client.get('/api/config')
        assert res.status_code == 200
        data = res.get_json()
        assert data["live_trading_enabled"] is False
        assert data["status"] == "OK"

        # 2. POST attempting to enable live trading is rejected (403)
        res = client.post('/api/config', json={"live_trading_enabled": True})
        assert res.status_code == 403
        assert "Live trading is permanently disabled" in res.get_json()["error"]

        # 3. POST attempting to change trading mode to LIVE is rejected (403)
        res = client.post('/api/config', json={"trading_mode": "LIVE"})
        assert res.status_code == 403

        # 4. POST with negative max_open_trades is rejected (400)
        res = client.post('/api/config', json={"max_open_trades": -5})
        assert res.status_code == 400

        # 5. POST with max_open_trades > safety ceiling (20) is rejected (400)
        res = client.post('/api/config', json={"max_open_trades": 25})
        assert res.status_code == 400

        # 6. POST with invalid string for max_open_trades is rejected (400)
        res = client.post('/api/config', json={"max_open_trades": "invalid_num"})
        assert res.status_code == 400

        # 7. POST with max_trades_per_day > safety ceiling (200) is rejected (400)
        res = client.post('/api/config', json={"max_trades_per_day": 500})
        assert res.status_code == 400

        # 8. POST with valid bounded parameters succeeds (200)
        res = client.post('/api/config', json={"max_open_trades": 8, "max_trades_per_day": 40})
        assert res.status_code == 200
        assert res.get_json()["status"] == "success"
        assert config.MAX_OPEN_TRADES == 8
        assert config.TARGET_TRADE_COUNT == 40

    def test_cors_policy_hardened(self):
        """Proof that CORS headers restrict untrusted domains while allowing trusted origins."""
        client = app.test_client()

        # Trusted Render production origin
        res = client.get('/api/config', headers={"Origin": "https://algorithmic-trading-bot-fra.onrender.com"})
        assert res.headers.get("Access-Control-Allow-Origin") == "https://algorithmic-trading-bot-fra.onrender.com"

        # Local development origin
        res = client.get('/api/config', headers={"Origin": "http://localhost:5000"})
        assert res.headers.get("Access-Control-Allow-Origin") == "http://localhost:5000"

        # Untrusted external origin is not allowed for /api routes
        res = client.get('/api/config', headers={"Origin": "https://malicious-external-site.com"})
        assert res.headers.get("Access-Control-Allow-Origin") != "https://malicious-external-site.com"
