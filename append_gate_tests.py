"""
Appends the TestClassificationGate class to the correction test file.
Run once: python append_gate_tests.py
"""
import os

TEST_FILE = r"d:\MT5\python_bot\tests\test_phase13_15_corrections.py"

CONTENT = r'''

# ═══════════════════════════════════════════════════════════════════════════
# 8. CLASSIFICATION GATE — DUAL CONDITION ENFORCEMENT
#
# RULE: Classification requires BOTH:
#   (1) wall-clock duration >= planned_duration_days (default 30)
#   (2) closed trades >= min_required_trades (default 30)
#
# Tests prove that early classification is STRUCTURALLY IMPOSSIBLE.
# ═══════════════════════════════════════════════════════════════════════════

class TestClassificationGate:
    """
    Regression tests proving that neither 30 trades alone nor 30 days alone
    can trigger classification. Both must be satisfied simultaneously.
    """

    def _make_cfg(self, planned_days=30, min_trades=30):
        return FrozenExperimentConfig(
            experiment_name="gate_test",
            planned_duration_days=planned_days,
            min_required_trades=min_trades,
        )

    def test_neither_gate_blocks_classification(self):
        from paper_engine.statistical_report import can_classify
        cfg = self._make_cfg()
        result = can_classify(cfg, elapsed_days=5.0, n_closed_trades=5)
        assert result["allowed"] is False
        assert result["verdict"] == "BLOCKED_BOTH"

    def test_trades_alone_cannot_trigger_classification(self):
        """CRITICAL: 30+ trades before 30 days must NOT allow classification."""
        from paper_engine.statistical_report import can_classify
        cfg = self._make_cfg()
        result = can_classify(cfg, elapsed_days=10.0, n_closed_trades=35)
        assert result["allowed"] is False, (
            "Early classification must be BLOCKED even with 35 trades — "
            "duration gate not met."
        )
        assert result["verdict"] == "BLOCKED_DURATION"
        assert result["trade_gate"] is True
        assert result["duration_gate"] is False

    def test_evaluate_returns_blocked_when_trades_met_duration_not(self):
        from paper_engine.statistical_report import evaluate_against_acceptance_criteria
        cfg = self._make_cfg()
        returns = [0.01] * 35
        result = evaluate_against_acceptance_criteria(
            cfg, returns, [10000.0, 10350.0], {}, elapsed_days=10.0
        )
        assert result["overall_verdict"] == "CLASSIFICATION_BLOCKED", (
            "Expected CLASSIFICATION_BLOCKED, got " + str(result["overall_verdict"])
        )

    def test_duration_met_insufficient_trades_yields_inconclusive(self):
        from paper_engine.statistical_report import evaluate_against_acceptance_criteria
        cfg = self._make_cfg()
        returns = [0.01] * 5
        result = evaluate_against_acceptance_criteria(
            cfg, returns, [10000.0], {}, elapsed_days=31.0
        )
        assert result["overall_verdict"] == "INCONCLUSIVE"
        assert result.get("inconclusive_reason") == "INSUFFICIENT_SAMPLE"

    def test_high_pnl_cannot_override_insufficient_sample_gate(self):
        """Even great returns cannot compensate for insufficient sample size."""
        from paper_engine.statistical_report import evaluate_against_acceptance_criteria
        cfg = self._make_cfg()
        returns = [0.05] * 10  # only 10 trades, very profitable
        result = evaluate_against_acceptance_criteria(
            cfg, returns, [10000.0, 10500.0], {}, elapsed_days=31.0
        )
        assert result["overall_verdict"] in ("INCONCLUSIVE", "CLASSIFICATION_BLOCKED"), (
            "Must NEVER be PASS or FAIL with fewer than 30 trades after 30 days. "
            "Got: " + str(result["overall_verdict"])
        )

    def test_both_gates_met_allows_classification(self):
        from paper_engine.statistical_report import can_classify
        cfg = self._make_cfg()
        result = can_classify(cfg, elapsed_days=31.0, n_closed_trades=35)
        assert result["allowed"] is True
        assert result["verdict"] == "ALLOWED"

    def test_exactly_on_boundary_both_gates(self):
        from paper_engine.statistical_report import can_classify
        cfg = self._make_cfg()
        result = can_classify(cfg, elapsed_days=30.0, n_closed_trades=30)
        assert result["allowed"] is True
        assert result["verdict"] == "ALLOWED"

    def test_evaluate_full_with_both_gates_returns_real_verdict(self):
        from paper_engine.statistical_report import evaluate_against_acceptance_criteria
        cfg = self._make_cfg()
        rng = np.random.default_rng(42)
        returns = list(rng.normal(0.005, 0.02, 35))
        equity = [10000.0 * (1 + sum(returns[:i])) for i in range(36)]
        result = evaluate_against_acceptance_criteria(
            cfg, returns, equity, {}, elapsed_days=31.0
        )
        assert result["overall_verdict"] in ("PASS", "FAIL", "INCONCLUSIVE"), (
            "With both gates met, result must be a real verdict, not "
            + str(result["overall_verdict"])
        )
        assert result["overall_verdict"] != "CLASSIFICATION_BLOCKED"

    def test_can_classify_required_keys_all_four_cases(self):
        from paper_engine.statistical_report import can_classify
        cfg = self._make_cfg()
        cases = [(1.0, 1), (10.0, 35), (31.0, 5), (31.0, 35)]
        required_keys = ["allowed", "verdict", "message", "duration_gate",
                         "trade_gate", "elapsed_days", "n_closed_trades"]
        for elapsed, trades in cases:
            result = can_classify(cfg, elapsed, trades)
            for key in required_keys:
                assert key in result, (
                    f"Missing key '{key}' for elapsed={elapsed}, trades={trades}"
                )

    # ── Strict User Requested Boundary Tests ──────────────────────────────

    def test_user_requested_exact_boundaries(self):
        """
        Verify exact scenarios requested:
        1. 30 trades + 29 days -> RUNNING (BLOCKED_DURATION)
        2. 29 trades + 30 days -> INCONCLUSIVE (BLOCKED_TRADES)
        3. 30 trades + 30 days -> FINAL EVALUATION (ALLOWED)
        4. 31 trades + 10 days -> RUNNING (BLOCKED_DURATION)
        5. 10 trades + 31 days -> INCONCLUSIVE (BLOCKED_TRADES)
        6. 31 trades + 31 days -> FINAL EVALUATION (ALLOWED)
        """
        from paper_engine.statistical_report import can_classify
        cfg = self._make_cfg(planned_days=30, min_trades=30)
        
        # 1. 30 trades + 29 days -> RUNNING
        r1 = can_classify(cfg, elapsed_days=29.0, n_closed_trades=30)
        assert r1["allowed"] is False
        assert r1["verdict"] == "BLOCKED_DURATION"
        
        # 2. 29 trades + 30 days -> INCONCLUSIVE (BLOCKED_TRADES)
        r2 = can_classify(cfg, elapsed_days=30.0, n_closed_trades=29)
        assert r2["allowed"] is True
        assert r2["verdict"] == "BLOCKED_TRADES"
        
        # 3. 30 trades + 30 days -> FINAL EVALUATION
        r3 = can_classify(cfg, elapsed_days=30.0, n_closed_trades=30)
        assert r3["allowed"] is True
        assert r3["verdict"] == "ALLOWED"
        
        # 4. 31 trades + 10 days -> RUNNING
        r4 = can_classify(cfg, elapsed_days=10.0, n_closed_trades=31)
        assert r4["allowed"] is False
        assert r4["verdict"] == "BLOCKED_DURATION"
        
        # 5. 10 trades + 31 days -> INCONCLUSIVE
        r5 = can_classify(cfg, elapsed_days=31.0, n_closed_trades=10)
        assert r5["allowed"] is True
        assert r5["verdict"] == "BLOCKED_TRADES"
        
        # 6. 31 trades + 31 days -> FINAL EVALUATION
        r6 = can_classify(cfg, elapsed_days=31.0, n_closed_trades=31)
        assert r6["allowed"] is True
        assert r6["verdict"] == "ALLOWED"
'''

with open(TEST_FILE, "a", encoding="utf-8") as f:
    f.write(CONTENT)

print("Done — classification gate tests appended.")
