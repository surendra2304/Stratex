"""
tests/test_autonomous_orchestration.py — Tests for Operations Director, Self-Healing, Degradation Matrix & Compliance.

Verifies:
1. EcosystemStateMachine state transitions and summary history.
2. SelfHealingEngine checksum validation, atomic file restore, exponential backoff, and memory monitoring.
3. DegradationPolicyMatrix fallback execution across AI-Universe, exchange, dashboard, and monitoring disruptions.
4. AutonomousOperationsDirector multi-frequency decision cycles and autonomy levels (Levels 1, 2, 3).
5. ComplianceReporter multi-format report generation (JSON, Markdown, HTML) with voice summaries and HMAC signatures.
6. Master Control API endpoints.
"""

import tempfile

from autonomy.compliance_reporting import ComplianceReporter
from autonomy.degradation_matrix import DegradationPolicyMatrix, SubsystemHealth
from autonomy.ecosystem_state import EcosystemStateMachine
from autonomy.operations_director import AutonomousOperationsDirector
from autonomy.self_healing import SelfHealingEngine


def test_ecosystem_state_machine():
    assert EcosystemStateMachine().current_state == "UNKNOWN"
    sm = EcosystemStateMachine(initial_state="FULL_AUTONOMY")
    assert sm.current_state == "FULL_AUTONOMY"

    # Transition to PROTECTED
    ok = sm.transition_to("PROTECTED", "Drawdown at 9.5%")
    assert ok is True
    assert sm.current_state == "PROTECTED"
    assert len(sm.transition_history) == 1

    # Transition to HALTED
    sm.transition_to("HALTED", "Operator manual panic")
    assert sm.current_state == "HALTED"

    summary = sm.get_state_summary()
    assert summary["current_state"] == "HALTED"
    assert len(summary["recent_transitions"]) == 2


def test_halted_state_is_latched_until_named_operator_release():
    import datetime as _dt

    sm = EcosystemStateMachine(initial_state="FULL_AUTONOMY")
    assert sm.transition_to("HALTED", "kill switch") is True

    # Automatic or anonymous actors can never clear the lockout.
    for operator in ("SYSTEM_AUTOMATIC", "system_automatic", "", "   ", None, 42):
        assert sm.transition_to("FULL_AUTONOMY", "auto recovery", operator=operator) is False
        assert sm.current_state == "HALTED"
    assert sm.transition_to("FULL_AUTONOMY", "auto recovery") is False
    assert len(sm.transition_history) == 1

    # Unknown states are rejected without side effects.
    assert sm.transition_to("NOT_A_STATE", "typo", operator="alice") is False
    assert sm.current_state == "HALTED"

    # A named operator release is allowed and audited.
    assert sm.transition_to("UNKNOWN", "post-incident review", operator=" alice ") is True
    assert sm.current_state == "UNKNOWN"
    release = sm.transition_history[-1]
    assert (release.from_state, release.to_state, release.operator) == ("HALTED", "UNKNOWN", "alice")

    ids = [t.transition_id for t in sm.transition_history]
    assert len(ids) == len(set(ids)), "transition ids must be unique even within one millisecond"

    # Timestamps are real UTC, not local time mislabelled with a trailing Z.
    summary = sm.get_state_summary()
    stamp = summary["last_transition_time"]
    assert stamp.endswith("Z")
    parsed = _dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    assert abs(parsed.timestamp() - sm.last_transition_time) < 1e-3


def test_self_healing_engine():
    with tempfile.TemporaryDirectory() as tmpdir:
        healer = SelfHealingEngine(backup_dir=tmpdir)

        # 1. State restore
        state_file = f"{tmpdir}/bot_state.json"
        with open(state_file, "w") as f:
            f.write('{"balance": 5000.0}')

        bak_file = f"{tmpdir}/bot_state.json.123456789.bak"
        with open(bak_file, "w") as f:
            f.write('{"balance": 5000.0, "recovered": true}')

        with open(state_file, "w") as f:
            f.write('CORRUPTED')

        restored = healer.restore_latest_good_state(state_file)
        assert restored is True

        # 2. Checksum validation
        is_valid = healer.validate_and_repair_state_file(state_file)
        assert is_valid is True

        # 3. Exchange API failure backoff
        fail_res = healer.handle_exchange_api_failure(consecutive_errors=6)
        assert fail_res["halt_new_entries"] is True
        assert fail_res["manage_open_positions_only"] is True

        # 4. Strategy crash restart
        assert healer.isolate_and_restart_strategy("strategy_scalper") is True

        # 5. Memory check
        mem_res = healer.check_memory_usage(current_rss_mb=900.0, is_low_activity_window=True)
        assert mem_res["restart_recommended"] is True


def test_degradation_policy_matrix():
    matrix = DegradationPolicyMatrix()

    # 1. AI-Universe Down
    h1 = SubsystemHealth(ai_universe_online=False, exchange_healthy=True, dashboard_online=True, monitoring_online=True)
    res1 = matrix.evaluate_degradation_policy(h1)
    assert res1["position_size_multiplier"] == 1.0

    # 2. Exchange Degraded
    h2 = SubsystemHealth(ai_universe_online=True, exchange_healthy=False, dashboard_online=True, monitoring_online=True)
    res2 = matrix.evaluate_degradation_policy(h2)
    assert res2["position_size_multiplier"] == 0.50
    assert res2["stop_loss_multiplier"] == 1.20
    assert res2["halt_scalpers"] is True

    # 3. Complete Degradation
    h3 = SubsystemHealth(exchange_healthy=False, monitoring_online=False)
    res3 = matrix.evaluate_degradation_policy(h3)
    assert res3["emergency_flatten"] is True
    assert res3["halt_all_entries"] is True


def test_autonomous_operations_director():
    director = AutonomousOperationsDirector(autonomy_level=2)
    assert director.autonomy_level == 2

    # Drawdown alone is not subsystem-health proof; default startup is UNKNOWN.
    res = director.run_high_frequency_cycle(current_drawdown_pct=2.0, active_positions_count=2)
    assert res["status"] == "HEALTH_UNVERIFIED"
    assert res["action"] == "HOLD_NEW_ENTRIES"
    assert director.state_machine.current_state == "UNKNOWN"

    # Only an explicit health attestation allows the all-clear transition.
    res = director.run_high_frequency_cycle(
        current_drawdown_pct=2.0, active_positions_count=2, health_verified=True
    )
    assert res["status"] == "NOMINAL"
    assert director.state_machine.current_state == "FULL_AUTONOMY"

    # A low drawdown without health proof must remove stale all-clear.
    res = director.run_high_frequency_cycle(current_drawdown_pct=2.0, active_positions_count=2)
    assert res["status"] == "HEALTH_UNVERIFIED"
    assert director.state_machine.current_state == "UNKNOWN"

    # High frequency cycle (Warning corridor -> PROTECTED)
    res = director.run_high_frequency_cycle(current_drawdown_pct=6.0, active_positions_count=2)
    assert director.state_machine.current_state == "PROTECTED"

    # High frequency cycle (Critical breach -> DEFENSIVE)
    res = director.run_high_frequency_cycle(current_drawdown_pct=13.0, active_positions_count=0)
    assert res["status"] == "CRITICAL_DRAWDOWN"
    assert director.state_machine.current_state == "DEFENSIVE"

    # Nominal drawdown cannot clear a latched HALTED state.
    director.state_machine.transition_to("HALTED", "test emergency stop")
    res = director.run_high_frequency_cycle(
        current_drawdown_pct=2.0, active_positions_count=0, health_verified=True
    )
    assert res == {"status": "HALTED", "action": "AWAIT_OPERATOR"}
    assert director.state_machine.current_state == "HALTED"

    # Even a critical drawdown reading cannot rewrite the latched HALTED posture.
    res = director.run_high_frequency_cycle(current_drawdown_pct=20.0, active_positions_count=0)
    assert res == {"status": "HALTED", "action": "AWAIT_OPERATOR"}
    assert director.state_machine.current_state == "HALTED"
    assert director.state_machine.transition_to("UNKNOWN", "operator release", operator="ops-oncall")

    # Medium frequency cycle (Hourly rebalancing)
    weights = director.run_medium_frequency_cycle({"supertrend": 1.8, "scalper": 1.2})
    assert "supertrend" in weights

    # Set autonomy level
    new_lvl = director.set_autonomy_level(3)
    assert new_lvl == 3
    assert director.autonomy_level == 3


def test_compliance_reporter():
    with tempfile.TemporaryDirectory() as tmpdir:
        reporter = ComplianceReporter(reports_dir=tmpdir, retention_days=90)
        dossier = reporter.generate_daily_compliance_dossier(
            trades_count=25,
            daily_pnl=145.50,
            max_drawdown_reached=3.2,
            decisions_count=12
        )
        assert dossier["report_type"] == "DAILY_COMPLIANCE_DOSSIER"
        assert dossier["metrics"]["net_pnl_dollars"] == 145.50
        assert "signature" in dossier
        assert "voice_summary" in dossier


def test_compliance_dossier_invariants_are_computed_not_hardcoded():
    """Regression (2026-10-07): compliance reports must never fabricate PASS."""
    with tempfile.TemporaryDirectory() as tmpdir:
        reporter = ComplianceReporter(reports_dir=tmpdir, retention_days=90)

        # Healthy day: all invariants pass and the signature self-verifies.
        ok_dossier = reporter.generate_daily_compliance_dossier(
            trades_count=5, daily_pnl=12.0, max_drawdown_reached=3.0, decisions_count=4
        )
        inv = ok_dossier["regulatory_invariants"]
        assert inv["zero_live_order_policy_honored"] is True
        assert inv["max_drawdown_limit_within_bounds"] is True
        assert inv["cryptographic_signatures_verified"] is True

        # Drawdown breach must flip the corridor invariant and the markdown verdict.
        import datetime as _dt
        import json as _json
        import os as _os
        breach = reporter.generate_daily_compliance_dossier(
            trades_count=5, daily_pnl=-40.0, max_drawdown_reached=22.5, decisions_count=4
        )
        assert breach["regulatory_invariants"]["max_drawdown_limit_within_bounds"] is False
        day_tag = _dt.datetime.utcnow().strftime("%Y-%m-%d")
        md = open(_os.path.join(tmpdir, f"compliance_daily_{day_tag}.md")).read()
        assert "Drawdown Corridor: FAIL" in md
        on_disk = _json.load(open(_os.path.join(tmpdir, f"compliance_daily_{day_tag}.json")))
        assert on_disk["regulatory_invariants"]["max_drawdown_limit_within_bounds"] is False

        # Evidence of a live order must flip the live-order invariant.
        live = reporter.generate_daily_compliance_dossier(
            trades_count=1, daily_pnl=0.0, max_drawdown_reached=0.0, decisions_count=0,
            live_order_evidence=True
        )
        assert live["regulatory_invariants"]["zero_live_order_policy_honored"] is False


def test_quarterly_package_status_is_honest():
    with tempfile.TemporaryDirectory() as tmpdir:
        reporter = ComplianceReporter(reports_dir=tmpdir, retention_days=90)
        pkg = reporter.generate_quarterly_audit_package()
        assert pkg["status"] == "GENERATED_SIGNED"
        assert "signature" in pkg


def test_ecosystem_report_uses_real_ledger_metrics_not_fabricated(tmp_path, monkeypatch):
    """Regression (2026-10-07): /api/ecosystem/report must reflect the actual
    ledger — it previously returned hardcoded trades_count=24, pnl=68.50."""
    import datetime as _dt
    import json as _json

    from dashboard import app as flask_app

    ledger = tmp_path / "ledger.jsonl"
    now_iso = _dt.datetime.utcnow().isoformat() + "Z"
    records = [
        {"timestamp": now_iso, "net_pnl": 1.25, "strategy": "alpha"},
        {"timestamp": now_iso, "net_pnl": -3.75, "strategy": "beta"},
        {"timestamp": "2020-01-01T00:00:00Z", "net_pnl": 999.0, "strategy": "beta"},
    ]
    ledger.write_text("\n".join(_json.dumps(r) for r in records) + "\n")
    portfolio = tmp_path / "portfolio.json"
    portfolio.write_text(_json.dumps({"initial_deposit": 100.0}))
    monkeypatch.setenv("TESTNET_LEDGER_FILE", str(ledger))
    monkeypatch.setenv("TESTNET_PORTFOLIO_FILE", str(portfolio))

    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        res = c.get("/api/ecosystem/report")
    assert res.status_code == 200
    payload = res.get_json()
    data = payload.get("data", payload)
    assert data["metrics"]["total_trades"] == 2
    assert data["metrics"]["net_pnl_dollars"] == -2.5
    assert data["metrics"]["peak_drawdown_pct"] == 3.75
    assert data["metrics_source"] == "LEDGER"
    assert data["drawdown_source"] == "LEDGER_PATH_VS_INITIAL_DEPOSIT"
    assert data["metrics"]["best_strategy"] == "alpha"


def test_ecosystem_report_without_ledger_is_honest(monkeypatch, tmp_path):
    import json as _json

    from dashboard import app as flask_app

    monkeypatch.setenv("TESTNET_LEDGER_FILE", str(tmp_path / "missing.jsonl"))
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        res = c.get("/api/ecosystem/report")
    assert res.status_code == 200
    payload = res.get_json()
    data = payload.get("data", payload)
    assert data["metrics"]["total_trades"] == 0
    assert data["metrics"]["net_pnl_dollars"] == 0.0
    assert data["metrics_source"] == "NO_DATA"
    assert data["drawdown_source"] == "UNVERIFIED_NO_EQUITY_BASE"
    assert data["metrics"]["best_strategy"] == "NONE_DETERMINED"
    assert "No single strategy could be confirmed" in data["voice_summary"]


def test_operations_director_fails_closed_on_unusable_risk_inputs():
    import math as _math

    director = AutonomousOperationsDirector(autonomy_level=2)
    assert director.run_high_frequency_cycle(2.0, 0, health_verified=True)["status"] == "NOMINAL"
    assert director.state_machine.current_state == "FULL_AUTONOMY"

    for bad in (_math.nan, _math.inf, -_math.inf, -13.5, None, "abc", object()):
        res = director.run_high_frequency_cycle(bad, 0, health_verified=True)
        assert res == {"status": "INVALID_RISK_INPUT", "action": "HOLD_NEW_ENTRIES"}, bad
        # A stale all-clear is withdrawn; nothing implies trading may continue.
        assert director.state_machine.current_state == "UNKNOWN"

    # Known risk postures are preserved rather than downgraded by bad telemetry.
    director.run_high_frequency_cycle(9.0, 1)
    assert director.state_machine.current_state == "PROTECTED"
    res = director.run_high_frequency_cycle(_math.nan, 1, health_verified=True)
    assert res["status"] == "INVALID_RISK_INPUT"
    assert director.state_machine.current_state == "PROTECTED"

    # Numeric strings are accepted as numbers; only the literal True attests health.
    assert director.run_high_frequency_cycle("13.0", 0)["status"] == "CRITICAL_DRAWDOWN"
    for loose_flag in ("false", "true", 1, "yes"):
        res = director.run_high_frequency_cycle(1.0, 0, health_verified=loose_flag)
        assert res["status"] == "HEALTH_UNVERIFIED", loose_flag
    assert director.state_machine.current_state == "DEFENSIVE"
    assert director.run_high_frequency_cycle(1.0, 0, health_verified=True)["status"] == "NOMINAL"
