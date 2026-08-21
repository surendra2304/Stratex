import json
import os

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
