import os

from alerting.ops_alerts import OperationalAlertEngine
from logger.structured import get_structured_logger, scrub_sensitive_data
from monitoring.metrics import PrometheusMetricsRegistry


def test_prometheus_metrics_registry():
    reg = PrometheusMetricsRegistry()
    reg.set_equity(12500.50)
    reg.add_pnl_realized(350.25)
    reg.set_pnl_unrealized(45.00)
    reg.set_positions_open(2)
    reg.record_trade("supertrend", "WIN")
    reg.set_risk_metrics(daily_loss_pct=0.5, drawdown_pct=1.2)
    reg.record_advisory_recommendation("APPROVED")
    reg.set_strategy_allocation("supertrend", 0.35)
    reg.set_circuit_breaker_state("volatility", False)
    reg.set_circuit_breaker_state("execution_quality", True)

    text = reg.generate_prometheus_text()
    assert "trading_bot_equity 12500.50" in text
    assert "trading_bot_pnl_realized_total 350.25" in text
    assert "trading_bot_positions_open 2" in text
    assert 'trading_bot_circuit_breaker_state{type="execution_quality"} 1' in text

def test_operational_alert_engine():
    engine = OperationalAlertEngine()
    
    # Latency degradation
    alert_lat = engine.check_exchange_latency("binance", 1250.0)
    assert alert_lat is not None
    assert alert_lat.category == "EXCHANGE"
    assert alert_lat.severity == "WARNING"

    # WebSocket disconnect
    alert_ws = engine.check_websocket_disconnect("bybit", False)
    assert alert_ws is not None
    assert alert_ws.category == "WEBSOCKET"
    assert alert_ws.severity == "ERROR"

    # High disk space
    alert_disk = engine.check_disk_space(92.5)
    assert alert_disk is not None
    assert alert_disk.category == "DISK"

    # Active banners
    banners = engine.get_dashboard_alert_banners()
    assert len(banners) >= 3

def test_sensitive_data_scrubbing():
    raw_str = 'Client connected with api_key: "secret_api_12345" and secret_key="my_super_secret_key_pass"'
    scrubbed = scrub_sensitive_data(raw_str)
    assert "secret_api_12345" not in scrubbed
    assert "my_super_secret_key_pass" not in scrubbed
    assert "***REDACTED***" in scrubbed

def test_structured_logger_execution(tmp_path):
    log_dir = str(tmp_path / "logs")
    logger = get_structured_logger(name="test_audit", log_dir=log_dir)
    logger.info("System initiated test event", extra={"subsystem": "TEST_CORE", "correlation_id": "CORR_999"})
    log_file = os.path.join(log_dir, "test_audit.jsonl")
    assert os.path.exists(log_file)
    with open(log_file, "r", encoding="utf-8") as f:
        content = f.read()
        assert "CORR_999" in content
        assert "TEST_CORE" in content

def test_monitoring_and_ops_endpoints():
    from dashboard import app
    client = app.test_client()

    # /metrics endpoint
    res_m = client.get('/metrics')
    assert res_m.status_code == 200
    assert 'trading_bot_equity' in res_m.get_data(as_text=True)

    # /api/ops/dashboard endpoint
    res_ops = client.get('/api/ops/dashboard')
    assert res_ops.status_code == 200
    data = res_ops.get_json()
    assert data['status'] == 'OK'
    assert 'strategies_matrix' in data
    assert 'risk_gauges' in data
    assert 'evolution_lab' in data

    # /api/v1/health/detailed endpoint
    read_key = os.getenv("TRADING_BOT_API_KEY_READ", "read_key_default_secret_123")
    res_h = client.get('/api/v1/health/detailed', headers={'X-API-Key': read_key})
    assert res_h.status_code == 200
    h_data = res_h.get_json()
    assert 'version' in h_data['data']
    assert 'exchange_connectivity' in h_data['data']