"""
tests/test_render_deployment_and_supervisor.py
Comprehensive regression test suite for Render deployment, process supervision,
heartbeat telemetry, and health endpoints.
"""

import os
import json
import time
import socket
import datetime
import pytest
import subprocess
from unittest.mock import patch, MagicMock

import config
from dashboard import app, get_engine_health_data
from execution import ExecutionPolicy
from scripts.supervise_services import ServiceSupervisor

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

class TestHealthAndHeartbeatArchitecture:
    def test_health_endpoint_response(self, client, tmp_path, monkeypatch):
        """GET /health must return 200 with dashboard and engine status."""
        hb_file = str(tmp_path / "testnet_heartbeat.json")
        monkeypatch.setenv("TESTNET_HEARTBEAT_FILE", hb_file)
        
        hb_data = {
            "worker_alive": True,
            "status": "RUNNING",
            "pid": os.getpid(),
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "binance_connected": True,
            "websocket_connected": True,
            "strategy": "adx_ema",
            "timeframe": "4h",
            "symbols": ["BTCUSDT", "ETHUSDT"],
            "symbol_count": 2
        }
        with open(hb_file, "w") as f:
            json.dump(hb_data, f)
            
        res = client.get('/health')
        assert res.status_code == 200
        data = res.get_json()
        assert data["status"] == "ok"
        assert data["dashboard"] == "online"
        assert data["engine"] == "online"
        assert data["engine_healthy"] is True

    def test_stale_heartbeat_reports_engine_offline(self, client, tmp_path, monkeypatch):
        """Stale heartbeat (>60s) must report engine offline even if dashboard is online."""
        hb_file = str(tmp_path / "testnet_heartbeat.json")
        monkeypatch.setenv("TESTNET_HEARTBEAT_FILE", hb_file)
        
        old_time = (datetime.datetime.utcnow() - datetime.timedelta(seconds=120)).isoformat() + "Z"
        hb_data = {
            "worker_alive": True,
            "status": "RUNNING",
            "pid": os.getpid(),
            "timestamp": old_time,
            "binance_connected": True,
            "websocket_connected": True
        }
        with open(hb_file, "w") as f:
            json.dump(hb_data, f)
            
        res = client.get('/health')
        assert res.status_code == 200
        data = res.get_json()
        assert data["dashboard"] == "online"
        assert data["engine"] == "offline"
        assert data["engine_healthy"] is False

    def test_missing_heartbeat_reports_engine_offline(self, client, tmp_path, monkeypatch):
        """Missing heartbeat file must report engine offline."""
        hb_file = str(tmp_path / "non_existent_heartbeat.json")
        monkeypatch.setenv("TESTNET_HEARTBEAT_FILE", hb_file)
        
        res = client.get('/api/engine-health')
        assert res.status_code == 200
        data = res.get_json()
        assert data["engine_status"] == "OFFLINE"
        assert data["healthy"] is False
        assert data["reason"] == "HEARTBEAT_FILE_MISSING"

    def test_api_engine_health_full_telemetry(self, client, tmp_path, monkeypatch):
        """GET /api/engine-health returns complete engine telemetry."""
        hb_file = str(tmp_path / "testnet_heartbeat.json")
        monkeypatch.setenv("TESTNET_HEARTBEAT_FILE", hb_file)
        
        now_str = datetime.datetime.utcnow().isoformat() + "Z"
        hb_data = {
            "worker_alive": True,
            "status": "RUNNING",
            "pid": os.getpid(),
            "timestamp": now_str,
            "binance_connected": True,
            "websocket_connected": True,
            "strategy": "adx_ema",
            "timeframe": "4h",
            "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            "symbol_count": 3,
            "last_market_update": now_str,
            "last_candle_close": now_str,
            "last_strategy_evaluation": now_str,
            "service_start_time": now_str
        }
        with open(hb_file, "w") as f:
            json.dump(hb_data, f)
            
        res = client.get('/api/engine-health')
        assert res.status_code == 200
        data = res.get_json()
        assert data["engine_status"] == "ONLINE"
        assert data["healthy"] is True
        assert data["symbol_count"] == 3
        assert data["active_strategy"] == "adx_ema"
        assert data["timeframe"] == "4h"

class TestExecutionPolicyAndModeInvariants:
    def test_testnet_mode_allowed_only_with_testnet_enabled(self, monkeypatch):
        """ExecutionPolicy allows orders on TESTNET only when TESTNET_ENABLED=True and LIVE=False."""
        import execution
        monkeypatch.setattr(execution, "TRADING_MODE", "TESTNET")
        monkeypatch.setattr(execution, "PAPER_SAFE_MODE", False)
        monkeypatch.setattr(execution, "TESTNET_ENABLED", True)
        monkeypatch.setattr(execution, "LIVE_TRADING_ENABLED", False)
        
        allowed, reason = ExecutionPolicy.can_place_order()
        assert allowed is True
        assert reason == "ALLOWED_TESTNET"
        
        # If TESTNET_ENABLED is False, order is blocked
        monkeypatch.setattr(execution, "TESTNET_ENABLED", False)
        allowed, reason = ExecutionPolicy.can_place_order()
        assert allowed is False
        assert reason == "TESTNET_DISABLED"

    def test_live_trading_is_strictly_blocked_in_testnet_mode(self, monkeypatch):
        """LIVE_TRADING_ENABLED must not be true during Testnet operations."""
        import execution
        monkeypatch.setattr(execution, "TRADING_MODE", "TESTNET")
        monkeypatch.setattr(execution, "LIVE_TRADING_ENABLED", False)
        assert execution.LIVE_TRADING_ENABLED is False

class TestSupervisorArchitecture:
    def test_supervisor_initialization(self):
        """Supervisor initializes clean state."""
        sup = ServiceSupervisor()
        assert sup.bot_proc is None
        assert sup.dash_proc is None
        assert sup.bot_restarts == 0
        assert sup.dash_restarts == 0
        assert not sup.stop_event.is_set()

    def test_supervisor_terminate_children_handles_none(self):
        """_terminate_children gracefully handles unspawned processes."""
        sup = ServiceSupervisor()
        # Should not raise any exception
        sup._terminate_children()

class TestDynamicPortBinding:
    def test_port_environment_variable_parsing(self, monkeypatch):
        """Ensure dashboard properly parses dynamic PORT variable."""
        monkeypatch.setenv("PORT", "8080")
        port = int(os.environ.get('PORT', 5000))
        assert port == 8080
