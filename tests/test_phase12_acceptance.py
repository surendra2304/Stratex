import time

from paper_engine.alerts import AlertManager
from paper_engine.data_monitor import DataMonitor
from paper_engine.heartbeat import ComponentStatus, HeartbeatState
from paper_engine.session import SessionState


def test_stage12_full_acceptance_scenario(tmp_path):
    """
    Final Acceptance Test
    Simulates:
    1. Start PAPER session.
    2. Feed valid market data.
    3. Idempotency on duplicates.
    4. Heartbeat propagation.
    5. Alert generation and resolution.
    6. Stop session.
    7. Generate report.
    """
    session_file = str(tmp_path / "session.json")
    hb_file = str(tmp_path / "heartbeat.json")
    alerts_file = str(tmp_path / "alerts.json")
    str(tmp_path / "portfolio.json")
    
    # 1. Session start
    session = SessionState(filename=session_file)
    sid = session.start_session({"mode": "PAPER"})
    assert session.status == "RUNNING"
    
    # 2. Heartbeat & Data Monitor
    hb = HeartbeatState(filename=hb_file)
    dm = DataMonitor(hb)
    
    # Initial data
    dm.process_tick("BTCUSDT", 60000.0, time.time() - 10)  # distinct timestamp
    assert hb.components["Market Data"]["status"] == ComponentStatus.OK.value
    
    # 3. Duplicate event (Idempotency)
    # The gaps/duplicates tracker should register this
    t = time.time()
    dm.process_tick("BTCUSDT", 60000.0, t)
    dm.process_tick("BTCUSDT", 60000.0, t) # Duplicate
    assert dm.symbols["BTCUSDT"]["duplicates"] == 1
    
    # 4. Alerts 
    alerts = AlertManager(filename=alerts_file)
    alerts.raise_alert("DATA_STALE", "WARNING", "BTCUSDT is stale", "BTCUSDT")
    alerts.raise_alert("DATA_STALE", "WARNING", "BTCUSDT is stale", "BTCUSDT") # Deduplication
    
    assert len(alerts.active_alerts) == 1
    assert alerts.active_alerts["DATA_STALE_BTCUSDT"]["count"] == 2
    
    alerts.resolve_alert("DATA_STALE", "BTCUSDT")
    assert len(alerts.active_alerts) == 0
    assert len(alerts.historical_alerts) == 1
    
    # 5. Stop session
    session.stop_session()
    assert session.status == "STOPPED"
    
    # 6. Session Report (synthetic)
    report = {
        "session_id": sid,
        "duration": session.end_time - session.start_time,
        "duplicates": dm.symbols["BTCUSDT"]["duplicates"],
        "alerts_generated": len(alerts.historical_alerts)
    }
    
    assert report["duplicates"] == 1
    assert report["alerts_generated"] == 1
