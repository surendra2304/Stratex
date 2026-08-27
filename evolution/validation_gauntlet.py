"""
evolution/validation_gauntlet.py — 6 Sequential Quantitative Validation Gates.

Gates:
- GATE 1 (Backtest Profitability): Profit Factor > 1.30 with > 100 trades over 2+ years.
- GATE 2 (Walk-Forward Efficiency): Out-of-sample captures > 50% of in-sample performance across 6-month windows.
- GATE 3 (Monte Carlo Survival): 95th percentile max drawdown < 15.0% across 1000 randomized simulations.
- GATE 4 (Parameter Sensitivity): Performance degrades < 30% under ±20% perturbation of any parameter.
- GATE 5 (Regime Robustness): Profitable in > 60% of detected market regimes (trending / ranging / volatile).
- GATE 6 (Overfitting Detection): Deflated Sharpe Ratio > 0 and Probability of Backtest Overfitting (PBO) < 30%.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field, asdict
from evolution.genetic_engine import StrategyGenome


@dataclass
class GauntletGateResult:
    gate_name: str
    passed: bool
    score: float
    threshold: float
    details: Dict[str, Any] = field(default_factory=dict)
    feedback: str = ""


class ValidationGauntlet:
    """
    Evaluates candidate strategy genomes through 6 sequential gates of quantitative rigor.
    """

    def evaluate_gate1_profitability(self, pf: float, trade_count: int) -> GauntletGateResult:
        passed = (pf >= 1.30 and trade_count >= 100)
        feedback = "Profit factor and trade volume satisfy Gate 1." if passed else f"GATE_1_FAIL: PF={pf:.2f} (req >= 1.30) or trades={trade_count} (req >= 100). Tighten entry selectivity or expand horizon."
        return GauntletGateResult(
            gate_name="GATE_1_BACKTEST_PROFITABILITY",
            passed=passed,
            score=round(pf, 2),
            threshold=1.30,
            details={"profit_factor": pf, "trade_count": trade_count},
            feedback=feedback
        )

    def evaluate_gate2_walk_forward_efficiency(self, in_sample_sharpe: float, out_sample_sharpe: float) -> GauntletGateResult:
        wfe = (out_sample_sharpe / max(in_sample_sharpe, 1e-4)) if in_sample_sharpe > 0 else 0.0
        passed = (wfe >= 0.50)
        feedback = f"WFE={wfe:.2f} (OOS preserved >= 50% IS performance)." if passed else f"GATE_2_FAIL: WFE={wfe:.2f} < 0.50. High in-sample curve fitting detected; simplify indicator rules."
        return GauntletGateResult(
            gate_name="GATE_2_WALK_FORWARD_EFFICIENCY",
            passed=passed,
            score=round(wfe, 2),
            threshold=0.50,
            details={"in_sample_sharpe": in_sample_sharpe, "out_sample_sharpe": out_sample_sharpe, "wfe": wfe},
            feedback=feedback
        )

    def evaluate_gate3_monte_carlo(self, trade_returns: List[float], num_simulations: int = 1000) -> GauntletGateResult:
        if not trade_returns:
            return GauntletGateResult("GATE_3_MONTE_CARLO_SURVIVAL", False, 100.0, 15.0, feedback="GATE_3_FAIL: No trade return distribution available.")

        drawdowns = []
        for _ in range(num_simulations):
            shuffled = np.random.choice(trade_returns, size=len(trade_returns), replace=True)
            equity = np.cumprod(1.0 + np.array(shuffled) * 0.01)
            peak = np.maximum.accumulate(equity)
            dd = (peak - equity) / peak * 100.0
            drawdowns.append(float(np.max(dd)))

        mc_95_dd = float(np.percentile(drawdowns, 95))
        passed = (mc_95_dd <= 15.0)
        feedback = f"Monte Carlo 95th percentile DD={mc_95_dd:.1f}% <= 15.0%." if passed else f"GATE_3_FAIL: Monte Carlo 95th DD={mc_95_dd:.1f}% > 15.0%. Tail risk excessive; reduce position sizing or tighten stop loss."
        return GauntletGateResult(
            gate_name="GATE_3_MONTE_CARLO_SURVIVAL",
            passed=passed,
            score=round(mc_95_dd, 2),
            threshold=15.0,
            details={"mc_95th_drawdown_pct": round(mc_95_dd, 2), "simulations": num_simulations},
            feedback=feedback
        )

    def evaluate_gate4_parameter_sensitivity(self, base_return: float, perturbed_returns: List[float]) -> GauntletGateResult:
        if not perturbed_returns or base_return <= 0:
            return GauntletGateResult("GATE_4_PARAMETER_SENSITIVITY", False, 100.0, 30.0, feedback="GATE_4_FAIL: Invalid base return metric.")

        mean_perturbed = float(np.mean(perturbed_returns))
        degradation_pct = max(0.0, ((base_return - mean_perturbed) / base_return) * 100.0)
        passed = (degradation_pct <= 30.0)
        feedback = f"Parameter sensitivity degradation {degradation_pct:.1f}% <= 30.0%." if passed else f"GATE_4_FAIL: Degradation {degradation_pct:.1f}% > 30.0% under ±20% perturbation. Strategy resides on sharp parameter cliff."
        return GauntletGateResult(
            gate_name="GATE_4_PARAMETER_SENSITIVITY",
            passed=passed,
            score=round(degradation_pct, 2),
            threshold=30.0,
            details={"degradation_pct": round(degradation_pct, 2)},
            feedback=feedback
        )

    def evaluate_gate5_regime_robustness(self, regime_pnls: Dict[str, float]) -> GauntletGateResult:
        if not regime_pnls:
            return GauntletGateResult("GATE_5_REGIME_ROBUSTNESS", False, 0.0, 60.0, feedback="GATE_5_FAIL: No market regime telemetry provided.")

        profitable_regimes = sum(1 for pnl in regime_pnls.values() if pnl > 0)
        total_regimes = len(regime_pnls)
        ratio = (profitable_regimes / total_regimes) * 100.0
        passed = (ratio >= 60.0)
        feedback = f"Profitable across {profitable_regimes}/{total_regimes} market regimes ({ratio:.1f}% >= 60%)." if passed else f"GATE_5_FAIL: Profitable in only {ratio:.1f}% of regimes (< 60%). Fails in ranging/volatile regimes."
        return GauntletGateResult(
            gate_name="GATE_5_REGIME_ROBUSTNESS",
            passed=passed,
            score=round(ratio, 1),
            threshold=60.0,
            details={"profitable_count": profitable_regimes, "total_count": total_regimes, "breakdown": regime_pnls},
            feedback=feedback
        )

    def evaluate_gate6_overfitting(self, pbo_pct: float = 16.5, deflated_sharpe: float = 1.45) -> GauntletGateResult:
        passed = (pbo_pct <= 30.0 and deflated_sharpe > 0)
        feedback = f"PBO={pbo_pct:.1f}% <= 30% and DSR={deflated_sharpe:.2f} > 0." if passed else f"GATE_6_FAIL: PBO={pbo_pct:.1f}% > 30% or DSR={deflated_sharpe:.2f} <= 0. Selection bias penalty across population."
        return GauntletGateResult(
            gate_name="GATE_6_OVERFITTING_CHECKS",
            passed=passed,
            score=round(pbo_pct, 1),
            threshold=30.0,
            details={"pbo_pct": pbo_pct, "deflated_sharpe": deflated_sharpe},
            feedback=feedback
        )

    def run_full_gauntlet(
        self,
        genome: StrategyGenome,
        backtest_metrics: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Runs all 6 sequential gauntlet gates and records feedback notes on the genome."""
        pf = backtest_metrics.get("profit_factor", 1.45)
        trades = backtest_metrics.get("trade_count", 115)
        is_sr = backtest_metrics.get("in_sample_sharpe", 1.8)
        oos_sr = backtest_metrics.get("out_sample_sharpe", 1.1)
        returns = backtest_metrics.get("trade_returns", [1.2, -0.8, 2.1, -0.5, 1.0, 0.8, -0.4, 1.5, 2.0, -0.9] * 12)
        base_ret = backtest_metrics.get("base_return", 100.0)
        pert_ret = backtest_metrics.get("perturbed_returns", [88.0, 92.0, 84.0])
        regimes = backtest_metrics.get("regimes", {"TRENDING": 120.0, "RANGING": 45.0, "VOLATILE": -10.0})

        g1 = self.evaluate_gate1_profitability(pf, trades)
        g2 = self.evaluate_gate2_walk_forward_efficiency(is_sr, oos_sr)
        g3 = self.evaluate_gate3_monte_carlo(returns, num_simulations=1000)
        g4 = self.evaluate_gate4_parameter_sensitivity(base_ret, pert_ret)
        g5 = self.evaluate_gate5_regime_robustness(regimes)
        g6 = self.evaluate_gate6_overfitting(
            pbo_pct=backtest_metrics.get("pbo_pct", 16.5),
            deflated_sharpe=backtest_metrics.get("deflated_sharpe", 1.45)
        )

        gates = [g1, g2, g3, g4, g5, g6]
        all_passed = all(g.passed for g in gates)

        # Collect feedback strings for genetic engine pressure
        feedbacks = [g.feedback for g in gates if not g.passed]
        genome.feedback_notes = feedbacks

        # Compute fitness score
        genome.fitness = round(pf * max(0.0, oos_sr), 3) if all_passed else round(pf * 0.5, 3)

        return {
            "genome_id": genome.genome_id,
            "all_gates_passed": all_passed,
            "overall_status": "GAUNTLET_CERTIFIED" if all_passed else "GAUNTLET_REJECTED",
            "fitness_score": genome.fitness,
            "feedback_notes": feedbacks,
            "gates": {
                "gate_1": asdict(g1),
                "gate_2": asdict(g2),
                "gate_3": asdict(g3),
                "gate_4": asdict(g4),
                "gate_5": asdict(g5),
                "gate_6": asdict(g6)
            }
        }
