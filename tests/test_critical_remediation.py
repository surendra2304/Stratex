"""Regression tests for the CRITICAL remediation batch (C1-C5), plus M6 and C6.

Each test pins behaviour that was broken before the fix, so the regression cannot
silently return:

C1  Authorization failed OPEN for control-scope actions when no API key was
    configured (the documented deployment default), and 17 mutating endpoints
    had no authentication at all.
C2  The research-job runner persisted hardcoded fake backtest results and
    marked the job COMPLETED.
C3  /api/v1/health/detailed reported fabricated exchange, streaming, evolution
    and disk values, and derived overall_status from only one pillar.
C4  execution/__init__.py swallowed every load error, silently shadowing the
    real execution.py and leaving ExecutionPolicy absent with no error.
C5  OBSERVE_ONLY_COOLDOWN_SECONDS was assigned but never enforced, so an
    OBSERVE-ONLY halt could resume 0.0s into a 7200s minimum, and a shrinking
    trade ledger could clear a fresh halt outright.
C6  StrategyVersion could not represent the ``evidence`` block carried by every
    record in the committed strategy_registry.json, so every registry read path
    raised TypeError and /api/strategy-registry returned 500.
M6  Monitoring returned invented resource numbers (12.5% CPU / 34.2% memory)
    whenever psutil was unavailable, which was always, because psutil was not
    declared in requirements.txt.
"""

import inspect
import json
import os
import time
from datetime import datetime, timedelta, timezone

import pytest

import config
import security_hardening
from security_hardening import SCOPE_CONTROL, SCOPE_READ


def _auth(scope, api_key=None):
    """Call authenticate_request inside a request context, as the app does."""
    from dashboard import app
    headers = {"X-API-KEY": api_key} if api_key else {}
    with app.test_request_context("/", headers=headers):
        return security_hardening.authenticate_request(required_scope=scope)


# --------------------------------------------------------------------------
# C1 - authorization must fail CLOSED for control actions
# --------------------------------------------------------------------------

class TestC1AuthorizationFailsClosed:
    KEY_ENV = ("BOT_API_KEY", "API_KEY_CONTROL", "API_KEY_READONLY", "API_KEY_FRIDAY")

    def setup_method(self):
        self._saved = {k: os.environ.get(k) for k in self.KEY_ENV}
        for k in self.KEY_ENV:
            os.environ[k] = ""

    def teardown_method(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_anonymous_control_scope_is_denied_when_no_keys_configured(self):
        """The documented deployment default must deny control, not grant it."""
        assert security_hardening.get_configured_api_keys() == {}
        ok, error_code, key_info = _auth(SCOPE_CONTROL)
        assert ok is False, "Control scope must not be granted with no configured keys"
        assert error_code == "AUTH_NOT_CONFIGURED"
        assert key_info is None or SCOPE_CONTROL not in key_info.get("scopes", [])

    def test_read_scope_still_available_anonymously(self):
        """Failing closed must not over-gate: public reads stay readable."""
        assert security_hardening.get_configured_api_keys() == {}
        ok, error_code, key_info = _auth(SCOPE_READ)
        assert ok is True, f"Anonymous read regressed: {error_code}"
        assert SCOPE_READ in key_info["scopes"]

    def test_configured_key_grants_control(self):
        os.environ["API_KEY_CONTROL"] = "ctl-key-0123456789abcdef"
        ok, error_code, key_info = _auth(SCOPE_CONTROL, api_key="ctl-key-0123456789abcdef")
        assert ok is True, error_code
        assert SCOPE_CONTROL in key_info["scopes"]

    def test_wrong_key_is_denied_even_when_keys_exist(self):
        os.environ["API_KEY_CONTROL"] = "ctl-key-0123456789abcdef"
        ok, error_code, key_info = _auth(SCOPE_CONTROL, api_key="wrong_key")
        assert ok is False
        assert key_info is None
        assert error_code != "AUTH_NOT_CONFIGURED", "Keys ARE configured; this is a bad-key denial"

    @pytest.mark.parametrize("endpoint", [
        "/api/live/emergency/halt",
        "/api/live/emergency/flatten",
        "/api/testnet/positions/close-all",
        "/api/strategy-registry/promote",
        "/api/config",
        "/api/settings",
        "/api/strategy-registry",
        "/api/research-jobs",
        "/api/alerts",
        "/api/agent-gateway/jobs",
        "/api/testnet/positions/close",
    ])
    def test_mutating_endpoints_refuse_anonymous_callers(self, endpoint):
        """Before C1 these executed real enforcer/exchange/registry actions for anyone."""
        from dashboard import app
        with app.test_client() as c:
            res = c.post(endpoint, json={"position_id": "x"})
            assert res.status_code in (401, 403, 503), (
                f"{endpoint} accepted an anonymous mutating request: {res.status_code}"
            )

    @pytest.mark.parametrize("endpoint", [
        "/api/config", "/api/settings", "/api/strategy-registry",
        "/api/research-jobs", "/api/alerts", "/api/agent-gateway/jobs",
    ])
    def test_get_branches_stay_readable_while_post_is_gated(self, endpoint):
        """Gating the mutating branch must not turn GET endpoints into 401/500s."""
        from dashboard import app
        with app.test_client() as c:
            res = c.get(endpoint)
            assert res.status_code == 200, f"GET {endpoint} regressed to {res.status_code}"

    def test_authenticated_control_caller_reaches_the_handler(self):
        """A valid key must be accepted, otherwise the guard walls out everyone."""
        from dashboard import app
        key = "ctl-key-0123456789abcdef-0123456789abcdef"
        os.environ["API_KEY_CONTROL"] = key
        with app.test_client() as c:
            res = c.post("/api/config", json={"max_open_trades": 3}, headers={"X-API-KEY": key})
            body = res.get_data(as_text=True)
            assert res.status_code != 503, f"Authenticated caller told auth is unconfigured: {body[:300]}"
            assert res.status_code != 401, f"Authenticated control caller was rejected: {body[:300]}"


# --------------------------------------------------------------------------
# C2 - research jobs must not persist fabricated results
# --------------------------------------------------------------------------

class TestC2NoFabricatedResearchResults:
    # These were the invented figures the old mock runner persisted as COMPLETED.
    FABRICATED_NUMBERS = (1.42, 58.3, 145.20)

    def test_runner_code_contains_no_fabricated_result_literals(self):
        """The numbers may be *mentioned* in a docstring, but must not be produced."""
        import ast
        import dashboard

        tree = ast.parse(inspect.getsource(dashboard.handle_research_jobs))
        literals = [n.value for n in ast.walk(tree)
                    if isinstance(n, ast.Constant) and isinstance(n.value, float)]
        for bad in self.FABRICATED_NUMBERS:
            assert bad not in literals, (
                f"Fabricated result literal {bad} is still produced by the runner"
            )

    def test_no_mock_runner_is_callable_anywhere_in_dashboard(self):
        import dashboard
        assert not hasattr(dashboard, "mock_backtest_runner"), \
            "The fabricated runner must be removed, not left importable"

    def test_runner_uses_the_real_backtest_engine(self):
        import dashboard
        src = inspect.getsource(dashboard.handle_research_jobs)
        for required in ("BacktestEngine", "calculate_metrics", "DataValidator", "get_candles"):
            assert required in src, f"Runner no longer uses {required}"
        assert "provenance" in src, "Results must be labelled with their evidence class"
        assert "in_sample_backtest" in src, \
            "An in-sample backtest must not be presented as out-of-sample evidence"

    def test_unmeasurable_job_is_recorded_failed_not_completed(self, tmp_path, monkeypatch, control_auth):
        """End-to-end: a job that cannot measure must land FAILED with a real reason."""
        from dashboard import app
        from stratex_quantdinger.jobs import JobStore

        monkeypatch.chdir(tmp_path)
        job_id = "regression_c2_job"
        with app.test_client() as c:
            res = c.post("/api/research-jobs", headers=control_auth, json={
                "job_type": "BACKTEST",
                "strategy_id": "definitely_not_a_real_strategy",
                "job_id": job_id,
                "metadata": {"symbol": "BTCUSDT", "timeframe": "1h", "candles": 50},
            })
            assert res.status_code == 202, res.get_data(as_text=True)[:400]

        store = JobStore()
        job = None
        for _ in range(200):
            job = store.get(job_id)
            if job.status in ("COMPLETED", "FAILED"):
                break
            time.sleep(0.05)

        assert job is not None
        assert job.status == "FAILED", f"Unmeasurable job recorded as {job.status}"
        assert "STRATEGY_MODULE_UNAVAILABLE" in (job.error or ""), job.error

        payload = json.loads((tmp_path / "experiment_jobs.json").read_text(encoding="utf-8"))
        assert "1.42" not in json.dumps(payload), "Fabricated profit_factor reached the durable store"

    def test_anonymous_research_job_submission_is_refused(self, tmp_path, monkeypatch):
        from dashboard import app
        monkeypatch.chdir(tmp_path)
        with app.test_client() as c:
            res = c.post("/api/research-jobs", json={"job_type": "BACKTEST"})
            assert res.status_code in (401, 403, 503)


# --------------------------------------------------------------------------
# C3 - health must report measured truth only
# --------------------------------------------------------------------------

class TestC3HealthReportsMeasuredTruth:
    @pytest.fixture(autouse=True)
    def _isolated(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self.tmp = tmp_path

    def test_disk_free_comes_from_shutil_not_a_convenient_fifty(self, monkeypatch):
        import api.health as h
        monkeypatch.setattr(h.shutil, "disk_usage",
                            lambda p: type("D", (), {"total": 100 * 1024**3,
                                                     "used": 83 * 1024**3,
                                                     "free": 17 * 1024**3})())
        data, status = h._measure_storage()
        assert data["disk_free_gb"] == 17.0, "disk_free_gb must come from shutil.disk_usage"

    def test_storage_probes_are_actually_performed(self):
        import api.health as h
        data, status = h._measure_storage()
        assert data["state_accessible"] is True, data
        assert data["ledgers_appendable"] is True, data
        assert status == "HEALTHY"

    def test_storage_reports_critical_when_writes_fail(self, monkeypatch):
        import api.health as h
        monkeypatch.setattr(h.tempfile, "mkdtemp",
                            lambda **kw: (_ for _ in ()).throw(OSError("read-only filesystem")))
        data, status = h._measure_storage()
        assert data["state_accessible"] is False
        assert "OSError" in data["state_accessible_error"]
        assert status == "CRITICAL", "A failed write probe must degrade the pillar"

    def test_exchanges_without_credentials_are_not_configured(self, monkeypatch):
        import api.health as h
        for var in ("BYBIT_API_KEY", "OKX_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        data, worst = h._measure_exchange_connectivity()
        assert data["bybit"] == "NOT_CONFIGURED"
        assert data["okx"] == "NOT_CONFIGURED"
        assert data["coinbase"] == "NOT_CONFIGURED"
        assert "binance" in data, "Binance must be reported as measured, never omitted"

    def test_exchanges_with_credentials_are_unverified_not_healthy(self, monkeypatch):
        """Configured is not the same as reachable; the label must say so."""
        import api.health as h
        monkeypatch.setenv("BYBIT_API_KEY", "k")
        monkeypatch.setenv("OKX_API_KEY", "k")
        data, _ = h._measure_exchange_connectivity()
        assert data["bybit"] == "UNVERIFIED"
        assert data["okx"] == "UNVERIFIED"

    def test_feed_is_unavailable_without_heartbeat_evidence(self, monkeypatch):
        import api.health as h
        monkeypatch.delenv("TESTNET_HEARTBEAT_FILE", raising=False)
        data, status = h._measure_feed_freshness()
        assert status == "UNAVAILABLE"
        assert data["feed_status"] == "UNAVAILABLE"

    def test_stale_heartbeat_is_reported_stale_not_streaming(self, monkeypatch):
        """Before C3 this reported REAL_TIME_STREAMING during a total blackout."""
        import api.health as h
        hb = self.tmp / "hb.json"
        stale = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
        hb.write_text(json.dumps({"last_market_update": stale}), encoding="utf-8")
        monkeypatch.setenv("TESTNET_HEARTBEAT_FILE", str(hb))
        data, status = h._measure_feed_freshness()
        assert status == "STALE", data
        assert data["feed_status"] == "STALE"
        assert data["newest_feed_evidence_age_sec"] > 900

    def test_fresh_heartbeat_is_reported_streaming(self, monkeypatch):
        import api.health as h
        hb = self.tmp / "hb.json"
        fresh = datetime.now(timezone.utc).isoformat()
        hb.write_text(json.dumps({"last_market_update": fresh}), encoding="utf-8")
        monkeypatch.setenv("TESTNET_HEARTBEAT_FILE", str(hb))
        data, status = h._measure_feed_freshness()
        assert status == "HEALTHY", data
        assert data["feed_status"] == "REAL_TIME_STREAMING"

    def test_heartbeat_without_timestamps_is_unavailable(self, monkeypatch):
        import api.health as h
        hb = self.tmp / "hb.json"
        hb.write_text(json.dumps({"pid": 1}), encoding="utf-8")
        monkeypatch.setenv("TESTNET_HEARTBEAT_FILE", str(hb))
        data, status = h._measure_feed_freshness()
        assert status == "UNAVAILABLE"
        assert data["reason"] == "NO_TIMESTAMPED_FEED_EVIDENCE"

    def test_evolution_engine_reports_not_running_instead_of_gen_14(self):
        import api.health as h
        data, status = h._measure_evolution_engine()
        assert status == "NOT_RUNNING"
        assert data["current_generation"] is None
        assert data["active_population"] is None
        assert "14" not in json.dumps(data)

    def test_overall_status_folds_every_pillar(self):
        """Before C3 a degraded pillar still reported overall OK."""
        import api.health as h
        assert h._worst("HEALTHY", "HEALTHY") == "HEALTHY"
        assert h._worst("HEALTHY", "UNAVAILABLE") == "UNAVAILABLE"
        assert h._worst("HEALTHY", "STALE") == "STALE"
        assert h._worst("HEALTHY", "NOT_RUNNING") == "NOT_RUNNING"
        assert h._worst("STALE", "WARNING") == "WARNING"
        assert h._worst("WARNING", "CRITICAL") == "CRITICAL"
        assert h._worst() == "UNVERIFIED"

    def test_unrecognised_status_is_treated_as_bad_not_good(self):
        """An unknown pillar status must not fold into HEALTHY (fail-safe default)."""
        import api.health as h
        assert h._SEVERITY["HEALTHY"] < h._SEVERITY.get("SOME_FUTURE_STATUS", 99)
        assert h._worst("HEALTHY", "SOME_FUTURE_STATUS") == "SOME_FUTURE_STATUS"

    def test_endpoint_marks_integration_claims_unverified(self, monkeypatch):
        import api.health as h
        from flask import Flask

        # /detailed sits behind api/auth.py, which (correctly) fails closed and
        # demands a read key; present one so the payload itself is what is tested.
        monkeypatch.setenv("TRADING_BOT_API_KEY_READ", "read_key_abc")
        probe = Flask("probe")
        probe.register_blueprint(h.health_bp)
        headers = {"X-API-Key": "read_key_abc"}
        with probe.test_client() as c:
            res = c.get("/api/v1/health/detailed", headers=headers)
            assert res.status_code == 200, res.get_data(as_text=True)[:400]
            body = res.get_json()
            text = json.dumps(body)
            # No fabricated generation counter, no invented free-disk constant.
            assert '"current_generation": 14' not in text
            assert '"disk_free_gb": 50.0' not in text

            integ = c.get("/api/v1/health/integrations", headers=headers)
            assert integ.status_code == 200, integ.get_data(as_text=True)[:400]
            itext = json.dumps(integ.get_json())
            assert "UNVERIFIED" in itext, \
                "Integration report must state that configuration is not reachability evidence"

    def test_detailed_endpoint_still_fails_closed_without_a_read_key(self, monkeypatch):
        """Gating must stay intact - this is the behaviour C1 converged on."""
        import api.health as h
        from flask import Flask
        monkeypatch.delenv("TRADING_BOT_API_KEY_READ", raising=False)
        probe = Flask("probe")
        probe.register_blueprint(h.health_bp)
        with probe.test_client() as c:
            res = c.get("/api/v1/health/detailed")
            assert res.status_code in (401, 503), res.status_code


# --------------------------------------------------------------------------
# C4 - execution package must not swallow load failures
# --------------------------------------------------------------------------

class TestC4ExecutionPackageIsNotSilentlyEmpty:
    def test_real_api_is_exposed_after_import(self):
        import execution
        assert hasattr(execution, "ExecutionPolicy"), \
            "execution/ silently shadowed execution.py again"
        assert hasattr(execution, "place_market_order")
        assert hasattr(execution, "get_exchange_client")

    def test_loader_is_a_callable_that_can_be_probed(self):
        import execution
        assert callable(execution._load_execution_module)

    def test_missing_execution_py_raises_instead_of_passing(self, monkeypatch, tmp_path):
        import execution
        missing = tmp_path / "execution.py"  # never created
        with pytest.raises(ImportError, match="is missing"):
            execution._load_execution_module(str(missing), target={})

    def test_unreadable_execution_py_raises_instead_of_passing(self, tmp_path):
        import execution
        real = tmp_path / "execution.py"
        real.write_text("ExecutionPolicy = object()\n", encoding="utf-8")
        real.chmod(0o000)
        try:
            with pytest.raises(ImportError, match="failed to read"):
                execution._load_execution_module(str(real), target={})
        finally:
            real.chmod(0o644)

    def test_broken_execution_py_raises_instead_of_passing(self, tmp_path):
        import execution
        broken = tmp_path / "execution.py"
        broken.write_text("raise ValueError('simulated transitive import failure')\n",
                          encoding="utf-8")
        with pytest.raises(ImportError, match="failed to load") as exc_info:
            execution._load_execution_module(str(broken), target={})
        assert isinstance(exc_info.value.__cause__, ValueError)

    def test_module_without_policy_is_rejected(self, tmp_path):
        """An execution.py that loads but defines no ExecutionPolicy is still a failure."""
        import execution
        empty = tmp_path / "execution.py"
        empty.write_text("SOME_UNRELATED_NAME = 1\n", encoding="utf-8")
        with pytest.raises(ImportError, match="ExecutionPolicy"):
            execution._load_execution_module(str(empty), target={})

    def test_valid_module_loads_into_the_supplied_target(self, tmp_path):
        import execution
        good = tmp_path / "execution.py"
        good.write_text("class ExecutionPolicy:\n    pass\n\nplace_market_order = lambda: 1\n",
                        encoding="utf-8")
        target: dict = {}
        ns = execution._load_execution_module(str(good), target=target)
        assert ns is target
        assert "ExecutionPolicy" in target and "place_market_order" in target


# --------------------------------------------------------------------------
# C6 - StrategyVersion must carry the committed evidence block
# --------------------------------------------------------------------------

class TestC6RegistryEvidenceRoundTrips:
    def test_committed_registry_loads_without_typeerror(self):
        """Every record in strategy_registry.json carries 'evidence'; reads used to 500."""
        from stratex_quantdinger.registry import StrategyRegistry
        root = os.path.join(os.path.dirname(__file__), "..")
        registry = StrategyRegistry(os.path.join(root, "strategy_registry.json"))
        versions = registry.list_versions()
        assert versions, "Registry returned nothing"
        with_evidence = [v for v in versions if v.evidence]
        assert with_evidence, "No record carried its evidence block"

    def test_evidence_is_preserved_not_discarded(self):
        from stratex_quantdinger.registry import StrategyRegistry
        root = os.path.join(os.path.dirname(__file__), "..")
        registry = StrategyRegistry(os.path.join(root, "strategy_registry.json"))
        adx = [v for v in registry.list_versions() if v.strategy_id == "adx_ema"]
        assert adx
        ev = adx[0].evidence
        for key in ("artifact", "git_sha", "evidence_grade", "promotion_status"):
            assert key in ev, f"Reproducibility provenance lost field {key!r}"

    def test_evidence_defaults_to_empty_for_legacy_records(self):
        from stratex_quantdinger.models import StrategyVersion
        v = StrategyVersion(strategy_id="s", version="v1", source_hash="h",
                            created_at="t", parameters={})
        assert v.evidence == {}

    def test_endpoint_returns_200_for_the_committed_artifact(self):
        from dashboard import app
        with app.test_client() as c:
            res = c.get("/api/strategy-registry")
            assert res.status_code == 200, res.get_data(as_text=True)[:400]


# --------------------------------------------------------------------------
# C5 - degradation guard must honour its cooldown
# --------------------------------------------------------------------------

def _ledger(tmp_path, *, strategy_trades, win_rate_target, admin_trades=60,
            win_pnl=200.0, loss_pnl=-5.0):
    """Write a ledger in the exact shape ``_check_degradation`` filters on.

    Only rows whose ``action`` contains "CLOSE" count, and rows tagged RECOVERED
    are excluded as administrative scratch entries. Payoffs are asymmetric by
    default so a low win rate can still produce positive net PnL — that is the
    dangerous case, because positive PnL is what used to resume trading while the
    win rate was still below the threshold.
    """
    rows = []
    wins = int(round(strategy_trades * win_rate_target))
    for i in range(admin_trades):
        rows.append({
            "action": "CLOSE_POSITION",
            "strategy": "RECOVERED",
            "exit_reason": "RECOVERED_CLOSE",
            "pnl": -0.05,
        })
    for i in range(strategy_trades):
        rows.append({
            "action": "CLOSE_POSITION",
            "strategy": "supertrend",
            "exit_reason": "PROFIT_HARVEST" if i < wins else "STOP_LOSS",
            "pnl": win_pnl if i < wins else loss_pnl,
        })
    path = tmp_path / "closed_trades_ledger.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return path


def _service(ledger_path, observe_only, observe_only_since):
    from testnet_engine.service import TestnetService
    svc = TestnetService.__new__(TestnetService)
    svc.ledger_file = str(ledger_path)
    svc.observe_only = observe_only
    svc.observe_only_since = observe_only_since
    svc._strategy_performance_gate = lambda: None
    return svc


class TestC5DegradationGuardCooldown:
    @pytest.fixture(autouse=True)
    def _guard_config(self, monkeypatch):
        monkeypatch.setattr(config, "DEGRADATION_GUARD_ENABLED", True)
        monkeypatch.setattr(config, "DEGRADATION_WINDOW", 20)
        monkeypatch.setattr(config, "MIN_WIN_RATE_THRESHOLD", 0.30)
        monkeypatch.setattr(config, "OBSERVE_ONLY_COOLDOWN_SECONDS", 7200.0)

    def test_cooldown_blocks_premature_resume(self, tmp_path):
        """STEP 3 reproduction: 20% win rate, positive net PnL, halted 0.0s ago."""
        path = _ledger(tmp_path, strategy_trades=20, win_rate_target=0.20)
        svc = _service(path, observe_only=True, observe_only_since=time.time())

        # Positive PnL must not mask a failing win rate.
        rows = [json.loads(line) for line in open(path, encoding="utf-8")]
        strat = [r for r in rows if r.get("strategy") != "RECOVERED"]
        assert len(strat) == 20
        assert sum(r["pnl"] for r in strat) > 0, "Scenario must have positive net PnL"
        assert sum(1 for r in strat if r["pnl"] > 0) / len(strat) == 0.20, \
            "Scenario must have a win rate below MIN_WIN_RATE_THRESHOLD"

        svc._check_degradation()
        assert svc.observe_only is True, \
            "Guard resumed 0.0s into a 7200s minimum halt - cooldown still not enforced"

    def test_cooldown_allows_resume_once_elapsed(self, tmp_path):
        """Same degraded sample, but the minimum halt has genuinely elapsed."""
        path = _ledger(tmp_path, strategy_trades=20, win_rate_target=0.20)
        svc = _service(path, observe_only=True, observe_only_since=time.time() - 8000)
        svc._check_degradation()
        assert svc.observe_only is False, "Guard must resume once the cooldown has elapsed"

    def test_missing_halt_timestamp_does_not_resume_immediately(self, tmp_path):
        """A halt with no recorded start time must be treated as fresh, not ancient."""
        path = _ledger(tmp_path, strategy_trades=20, win_rate_target=0.20)
        svc = _service(path, observe_only=True, observe_only_since=None)
        svc._check_degradation()
        assert svc.observe_only is True, \
            "A halt of unknown age resumed immediately - fail-open on missing timestamp"

    def test_epoch_zero_is_a_real_timestamp_not_an_unknown_one(self, tmp_path):
        """``observe_only_since = 0`` means 'halted at epoch', not 'never recorded'.

        Production only ever assigns ``time.time()`` here, so 0 is an explicit
        ancient timestamp and the cooldown has genuinely elapsed. Conflating it
        with None would wedge a legitimate recovery permanently.
        """
        path = _ledger(tmp_path, strategy_trades=5, win_rate_target=1.0)
        svc = _service(path, observe_only=True, observe_only_since=0)
        svc._check_degradation()
        assert svc.observe_only is False, \
            "A halt recorded at epoch must recover once the cooldown has elapsed"
        assert svc.observe_only_since == 0, "A real timestamp must not be overwritten"

    def test_shrinking_sample_cannot_clear_a_fresh_halt(self, tmp_path):
        """Ledger truncation must not instantly disarm the gate."""
        path = _ledger(tmp_path, strategy_trades=5, win_rate_target=1.0)
        svc = _service(path, observe_only=True, observe_only_since=time.time())
        svc._check_degradation()
        assert svc.observe_only is True, \
            "A 5-trade sample cleared a fresh OBSERVE-ONLY halt"

    def test_shrinking_sample_recovers_after_cooldown(self, tmp_path):
        """Insufficient evidence still recovers once the minimum halt has passed."""
        path = _ledger(tmp_path, strategy_trades=5, win_rate_target=1.0)
        svc = _service(path, observe_only=True, observe_only_since=time.time() - 8000)
        svc._check_degradation()
        assert svc.observe_only is False

    def test_healthy_sample_stays_active(self, tmp_path):
        path = _ledger(tmp_path, strategy_trades=30, win_rate_target=0.60)
        svc = _service(path, observe_only=False, observe_only_since=None)
        svc._check_degradation()
        assert svc.observe_only is False, "A healthy ledger must not trigger a halt"


# --------------------------------------------------------------------------
# M6 - monitoring must not invent resource numbers
# --------------------------------------------------------------------------

class TestM6MonitoringDoesNotInventResources:
    def test_no_psutil_reports_unavailable_not_fake_numbers(self, monkeypatch):
        import monitoring_system
        from monitoring_system import ProductionMonitoringSystem

        monkeypatch.setattr(monitoring_system, "_HAS_PSUTIL", False)
        data = ProductionMonitoringSystem().get_system_resource_metrics()

        assert data["resources_source"] == "UNAVAILABLE:psutil_not_installed"
        for field in ("cpu_percent", "memory_percent", "memory_used_mb", "memory_total_mb"):
            assert data[field] is None, f"{field} invented a value: {data[field]!r}"
        # Disk is measured from shutil, so it must still be real.
        assert isinstance(data["disk_free_gb"], float)

    def test_psutil_failure_is_reported_not_masked(self, monkeypatch):
        import monitoring_system
        from monitoring_system import ProductionMonitoringSystem

        class Boom:
            @staticmethod
            def cpu_percent(interval=None):
                raise RuntimeError("psutil exploded")

        monkeypatch.setattr(monitoring_system, "_HAS_PSUTIL", True)
        monkeypatch.setattr(monitoring_system, "psutil", Boom)
        data = ProductionMonitoringSystem().get_system_resource_metrics()
        assert data["resources_source"] == "UNAVAILABLE:RuntimeError"
        assert data["cpu_percent"] is None

    def test_psutil_values_are_reported_when_available(self, monkeypatch):
        import monitoring_system
        from monitoring_system import ProductionMonitoringSystem

        class FakePsutil:
            @staticmethod
            def cpu_percent(interval=None):
                return 42.0

            @staticmethod
            def virtual_memory():
                return type("M", (), {"percent": 55.0,
                                      "used": 2 * 1024**3,
                                      "total": 8 * 1024**3})()

        monkeypatch.setattr(monitoring_system, "_HAS_PSUTIL", True)
        monkeypatch.setattr(monitoring_system, "psutil", FakePsutil)
        data = ProductionMonitoringSystem().get_system_resource_metrics()
        assert data["resources_source"] == "psutil"
        assert data["cpu_percent"] == 42.0
        assert data["memory_percent"] == 55.0
        assert data["memory_total_mb"] == 8192.0

    def test_psutil_is_declared_as_a_dependency(self):
        req = open(os.path.join(os.path.dirname(__file__), "..", "requirements.txt"),
                   encoding="utf-8").read()
        assert "psutil" in req, "psutil must be declared or monitoring can only report UNAVAILABLE"
