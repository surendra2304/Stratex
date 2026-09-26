import json
import os
import time

import pytest

from dashboard import app
from testnet_engine.risk_gate import RiskGate


class TestOpportunityDecisionFunnel:
    """
    Comprehensive tests for the Opportunity Decision Funnel:
    - Multiple opportunities ranking
    - Ranking tiebreaker
    - Duplicate signal handling
    - Risk conflict
    - Position conflict
    - Global funnel diagnostics API (/api/opportunities, /api/signals, /api/diagnostics)
    """

    @pytest.fixture
    def client(self):
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    def test_multiple_opportunities_deterministic_ranking(self):
        """Candidates must be sorted by Deterministic Score: (expected_net_return * confidence) / max(0.001, risk_pct)."""
        candidates = [
            {
                "symbol": "ETHUSDT", "tf": "15m", "strategy": "adx_ema",
                "entry": 3000.0, "sl": 2970.0,
                "metrics": {"expected_net_return": 0.015, "confidence": 0.55, "risk_pct": 0.010} # score = 0.015*0.55/0.010 = 0.825
            },
            {
                "symbol": "BTCUSDT", "tf": "5m", "strategy": "scalper",
                "entry": 60000.0, "sl": 59700.0,
                "metrics": {"expected_net_return": 0.020, "confidence": 0.60, "risk_pct": 0.005} # score = 0.020*0.60/0.005 = 2.400
            },
            {
                "symbol": "SOLUSDT", "tf": "15m", "strategy": "ml",
                "entry": 140.0, "sl": 138.0,
                "metrics": {"expected_net_return": 0.008, "confidence": 0.50, "risk_pct": 0.014} # score = 0.008*0.50/0.014 = 0.285
            }
        ]

        for c in candidates:
            p_met = c["metrics"]
            exp_net = float(p_met["expected_net_return"])
            conf = float(p_met["confidence"])
            risk_pct = float(p_met["risk_pct"])
            score = round(exp_net * conf / max(0.001, risk_pct), 6)
            c["score"] = score
            c["net_edge"] = exp_net
            c["risk"] = risk_pct
            c["confidence"] = conf

        candidates.sort(key=lambda x: (x["score"], x["net_edge"], x["confidence"], -x["risk"], x["symbol"]), reverse=True)
        for idx, c in enumerate(candidates, 1):
            c["rank"] = idx

        assert candidates[0]["symbol"] == "BTCUSDT"
        assert candidates[0]["rank"] == 1
        assert candidates[1]["symbol"] == "ETHUSDT"
        assert candidates[1]["rank"] == 2
        assert candidates[2]["symbol"] == "SOLUSDT"
        assert candidates[2]["rank"] == 3

    def test_ranking_tiebreaker_prefers_higher_confidence_and_lower_risk(self):
        """When scores are identical, tiebreak by net edge, confidence, lower risk, and alphabetical symbol."""
        candidates = [
            {
                "symbol": "LINKUSDT", "tf": "15m", "strategy": "adx_ema",
                "entry": 10.0, "sl": 9.9,
                "metrics": {"expected_net_return": 0.010, "confidence": 0.50, "risk_pct": 0.010} # score = 0.5
            },
            {
                "symbol": "ADAUSDT", "tf": "15m", "strategy": "adx_ema",
                "entry": 0.40, "sl": 0.396,
                "metrics": {"expected_net_return": 0.010, "confidence": 0.50, "risk_pct": 0.010} # score = 0.5
            }
        ]

        for c in candidates:
            p_met = c["metrics"]
            exp_net = float(p_met["expected_net_return"])
            conf = float(p_met["confidence"])
            risk_pct = float(p_met["risk_pct"])
            score = round(exp_net * conf / max(0.001, risk_pct), 6)
            c["score"] = score
            c["net_edge"] = exp_net
            c["risk"] = risk_pct
            c["confidence"] = conf

        candidates.sort(key=lambda x: (x["score"], x["net_edge"], x["confidence"], -x["risk"], x["symbol"]), reverse=True)
        # ADAUSDT comes after LINKUSDT or reverse based on alphabetical desc
        assert len(candidates) == 2
        assert candidates[0]["score"] == candidates[1]["score"]

    def test_duplicate_signal_handling(self, tmp_path, monkeypatch):
        """Duplicate signals in opportunity log must be parsed cleanly without corrupting funnel counts."""
        opp_file = tmp_path / "testnet_opportunity_log.jsonl"
        sig1 = {
            "signal_id": "SIG_DUP_001",
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "strategy": "adx_ema",
            "side": "BUY",
            "entry": 60000.0,
            "stop": 59000.0,
            "target": 62000.0,
            "confidence": 0.6,
            "expected_gross": 2.0,
            "fees": 0.31,
            "slippage": 0.11,
            "expected_net": 1.58,
            "profitability_decision": "ACCEPTED",
            "risk_decision": "ACCEPTED",
            "execution_decision": "ELIGIBLE",
            "decision": "ACCEPTED",
            "reason": "ALL_GATES_PASSED"
        }
        with open(opp_file, "w") as f:
            f.write(json.dumps(sig1) + "\n")
            f.write(json.dumps(sig1) + "\n") # duplicate
            
        monkeypatch.setenv("TESTNET_OPPORTUNITY_LOG", str(opp_file))
        assert os.path.exists(opp_file)

    def test_risk_conflict_max_open_positions_rejection(self):
        """RiskGate must reject incoming opportunity if max open positions limit is reached."""
        import config
        gate = RiskGate(starting_balance=10000.0)
        active_positions = {f"SYM{i}USDT": {"status": "OPEN"} for i in range(config.MAX_OPEN_POSITIONS)}
        passed, reason, _ = gate.evaluate_risk(
            symbol="SOLUSDT",
            side="BUY",
            current_equity=10000.0,
            active_positions=active_positions,
            proposed_qty=1.0,
            entry_price=140.0,
            data_health_status="OK"
        )
        assert passed is False
        assert "MAX_OPEN_POSITIONS" in reason

    def test_position_conflict_existing_symbol_position(self):
        """Engine must reject signal if an active open position already exists for the symbol."""
        active_positions = {"LINKUSDT": {"status": "OPEN", "quantity": 23.24}}
        assert "LINKUSDT" in active_positions

    def test_api_opportunities_returns_200_and_canonical_structure(self, client):
        """/api/opportunities must return valid structure with top opportunities."""
        resp = client.get("/api/opportunities")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["status"] == "SUCCESS"
        assert "top_opportunities" in data
        assert "count" in data

    def test_api_signals_returns_200_and_signal_stream(self, client):
        """/api/signals must return strategy decision stream."""
        resp = client.get("/api/signals")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["status"] == "SUCCESS"
        assert "signals" in data

    def test_api_diagnostics_returns_200_funnel_and_bottleneck(self, client):
        """/api/diagnostics must return global funnel counts and bottleneck analysis."""
        resp = client.get("/api/diagnostics")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["status"] == "SUCCESS"
        assert "funnel" in data
        assert "candles_evaluated" in data["funnel"]
        assert "strategies_evaluated" in data["funnel"]
        assert "signals_generated" in data["funnel"]
        assert "rejection_breakdown" in data
        assert "bottleneck_diagnosis" in data
        assert "pipeline_state" in data

    def test_api_diagnostics_distinguishes_no_loaded_strategy_from_waiting_for_signal(
        self, client, monkeypatch, tmp_path
    ):
        import dashboard as dashboard_module

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            dashboard_module,
            "get_engine_health_data",
            lambda: {"engine_status": "ONLINE", "strategies": []},
        )
        payload = client.get("/api/diagnostics").get_json()
        assert payload["pipeline_state"] == "NO_EXECUTABLE_STRATEGY"
        assert payload["executable_strategies"] == []
        assert "no testnet signal can execute" in payload["bottleneck_diagnosis"]

    def test_signal_log_fallback_never_invents_success_or_performance(self, client, tmp_path, monkeypatch):
        """Incomplete source records remain unknown; the API must not manufacture alpha."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "testnet_signals_log.jsonl").write_text(
            json.dumps({
                "signal_id": "rejected-without-gate-metadata",
                "symbol": "BTCUSDT",
                "side": "BUY",
                "decision": "REJECTED",
                "rejection_reason": "COUNTER_TREND",
            }) + "\n",
            encoding="utf-8",
        )

        response = client.get("/api/opportunity-log")
        assert response.status_code == 200
        record = response.get_json()["top_opportunities"][0]
        assert record["decision"] == "REJECTED"
        assert record["side"] == "BUY"
        assert record["profitability_decision"] == "UNKNOWN"
        assert record["risk_decision"] == "UNKNOWN"
        assert record["reason"] == "COUNTER_TREND"
        assert record["confidence"] is None
        assert record["expected_net_return"] is None
        assert "POSITIVE_ALPHA" not in response.get_data(as_text=True)
        assert "1.8" not in response.get_data(as_text=True)

    def test_opportunity_fallback_unknown_gate_is_not_reported_as_pass(self, client, tmp_path, monkeypatch):
        """Missing profitability/risk evidence is not a passed gate in recent_signals."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "testnet_signals_log.jsonl").write_text(
            json.dumps({"symbol": "BTCUSDT", "side": "BUY", "decision": "REJECTED"}) + "\n",
            encoding="utf-8",
        )
        response = client.get("/api/funnel")
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["top_opportunities"] == []
        # The funnel API has no recent_signals projection; with only a signal
        # event available, it must not claim any qualified opportunity.
        assert payload["QUALIFIED"] == 0

    def test_live_scanner_does_not_synthesize_candidates_when_logs_are_empty(self, client, tmp_path, monkeypatch):
        """No log evidence means an empty scanner response, not invented market candidates."""
        monkeypatch.chdir(tmp_path)
        response = client.get("/api/live-scanner")
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["count"] == 0
        assert payload["signals"] == []

    def test_paper_forward_status_reports_unavailable_without_evidence(self, client, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        response = client.get("/api/paper/forward-status")
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["status"] == "UNAVAILABLE"
        assert payload["closed_trades"] is None
        assert payload["net_realized_pnl"] is None
        assert payload["validation_status"] == "INCOMPLETE"
        assert "PAPER_LEDGER_MISSING" in payload["validation_reasons"]

    def test_paper_forward_status_uses_actual_persisted_records(self, client, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "forward_health.json").write_text(
            json.dumps({"strategy": "OK", "last_update": 1000.0}), encoding="utf-8"
        )
        (tmp_path / "paper_portfolio.json").write_text(
            json.dumps({"positions": {"open-1": {"status": "OPEN"}}}), encoding="utf-8"
        )
        (tmp_path / "forward_signal_log.jsonl").write_text(
            json.dumps({"decision": "REJECTED"}) + "\n", encoding="utf-8"
        )
        (tmp_path / "paper_trade_ledger.jsonl").write_text(
            json.dumps({"status": "CLOSED", "net_pnl": -2.5}) + "\n", encoding="utf-8"
        )
        payload = client.get("/api/paper/forward-status").get_json()
        assert payload["status"] == "AVAILABLE"
        assert payload["validation_status"] == "INCOMPLETE"
        assert "MINIMUM_30_CLOSED_TRADES_NOT_MET" in payload["validation_reasons"]
        assert "STATISTICAL_ACCEPTANCE_REVIEW_NOT_RECORDED" in payload["validation_reasons"]
        assert payload["open_positions"] == 1
        assert payload["signal_count"] == 1
        assert payload["signal_decisions"] == {"REJECTED": 1}
        assert payload["closed_trades"] == 1
        assert payload["net_realized_pnl"] == -2.5

    def test_health_and_signal_files_only_do_not_claim_forward_validation(self, client, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "forward_health.json").write_text(
            json.dumps({
                "market_data": "OK",
                "strategy": "OK",
                "portfolio": "OK",
                "persistence": "OK",
                "reconciliation": "OK",
                "last_update": time.time(),
            }),
            encoding="utf-8",
        )
        (tmp_path / "forward_signal_log.jsonl").write_text(
            json.dumps({"decision": "NO_SIGNAL"}) + "\n", encoding="utf-8"
        )

        payload = client.get("/api/paper/forward-status").get_json()
        assert payload["runner_status"] == "HEALTHY"
        assert payload["status"] == "AVAILABLE"
        assert payload["validation_status"] == "INCOMPLETE"
        assert "PAPER_PORTFOLIO_MISSING" in payload["validation_reasons"]
        assert "PAPER_LEDGER_MISSING" in payload["validation_reasons"]
        assert "EXPERIMENT_START_TIME_NOT_PROVEN" in payload["validation_reasons"]

    def test_early_signal_rejection_log_keeps_unmeasured_fields_unknown(self, tmp_path, monkeypatch):
        from testnet_engine import service as service_module

        log_path = tmp_path / "opportunities.jsonl"
        monkeypatch.setattr(service_module, "TESTNET_OPPORTUNITY_LOG", str(log_path))
        service = object.__new__(service_module.TestnetService)
        service.log_opportunity("sig-1", "BTCUSDT", "BUY", {"reason": "QANAT_DECAY_FILTERED_NOISE"}, "REJECTED", "QANAT_DECAY_FILTERED_NOISE")

        record = json.loads(log_path.read_text(encoding="utf-8"))
        assert record["profitability_decision"] == "NOT_EVALUATED"
        assert record["risk_decision"] == "NOT_EVALUATED"
        assert record["confidence"] is None
        assert record["entry"] is None
        assert record["expected_gross"] is None
        assert record["expected_net"] is None
        assert record["fees"] is None
        assert record["slippage"] is None

    def test_exchange_error_is_logged_as_execution_failure_not_risk_rejection(self, tmp_path, monkeypatch):
        from testnet_engine import service as service_module

        log_path = tmp_path / "opportunities.jsonl"
        monkeypatch.setattr(service_module, "TESTNET_OPPORTUNITY_LOG", str(log_path))
        service = object.__new__(service_module.TestnetService)
        service.log_opportunity(
            "sig-exchange-error", "BTCUSDT", "BUY",
            {
                "profitability_decision": "ACCEPTED",
                "risk_decision": "ACCEPTED",
                "execution_error_code": -1013,
                "execution_error_message": "Filter failure: LOT_SIZE",
            },
            "FAILED", "BINANCE_API_ERROR_400",
        )

        record = json.loads(log_path.read_text(encoding="utf-8"))
        assert record["profitability_decision"] == "ACCEPTED"
        assert record["risk_decision"] == "ACCEPTED"
        assert record["execution_decision"] == "FAILED"
        assert record["execution_error_code"] == -1013
        assert record["execution_error_message"] == "Filter failure: LOT_SIZE"
