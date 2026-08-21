# quantum/portfolio_optimizer.py
"""Quantum portfolio/opportunity optimizer using a QUBO formulation.
All optimization is advisory – the result is a list of selected candidate IDs.
The optimizer respects hard constraints via penalty terms and falls back to a
simple classical heuristic when the quantum backend is unavailable or fails.
"""
import logging
from typing import Any

from .config import DEFAULT_SHOTS, USE_QISKIT

logger = logging.getLogger(__name__)


def _build_quadratic_program(candidates: list[dict[str, Any]]) -> "QuadraticProgram":
    """Construct a Qiskit :class:`QuadraticProgram` representing the portfolio QUBO.
    Variables are binary selection flags for each candidate.
    The objective balances expected return and risk (and simple transaction cost).
    Hard constraints are encoded as large penalty terms.
    """
    try:
        from qiskit_optimization import QuadraticProgram
    except Exception:
        raise RuntimeError("Qiskit Optimization package unavailable")

    qp = QuadraticProgram()
    n = len(candidates)
    # Add binary variables x0..x_{n-1}
    for i in range(n):
        qp.binary_var(name=f"x{i}")

    # Coefficients
    # Linear terms: maximize expected_net_edge - risk_weight * risk - cost_weight * transaction_cost
    # We'll use weights that can be tuned; for now fixed.
    risk_weight = 1.0
    cost_weight = 0.1

    linear = {}
    for i, cand in enumerate(candidates):
        expected = cand.get("expected_net_edge", 0.0)
        risk = cand.get("risk", 0.0)
        # transaction cost approximated as a function of volatility
        volatility = cand.get("volatility", 0.0)
        cost = volatility * cost_weight
        linear[f"x{i}"] = expected - risk_weight * risk - cost
    qp.minimize(linear=linear)  # Qiskit solves minimization; we maximize reward, so minimize -reward

    # Quadratic penalty for exceeding total position size limit.
    # Sum(position_size_limit * xi) <= total_limit (set to 1.0 for simplicity).
    total_limit = 1.0
    coeffs = []
    for i, cand in enumerate(candidates):
        limit = cand.get("position_size_limit", 0.0)
        coeffs.append(limit)
    # Build penalty: (sum(limit*xi) - total_limit)^2 * penalty_weight
    penalty_weight = 10.0
    # Expand the square: sum a_i a_j x_i x_j - 2 total_limit sum a_i x_i + total_limit^2
    # The constant term can be ignored.
    for i in range(n):
        for j in range(i, n):
            a_i = coeffs[i]
            a_j = coeffs[j]
            if i == j:
                qp.objective.quadratic[f"x{i}"][f"x{i}"] = qp.objective.quadratic.get(f"x{i}", {}).get(f"x{i}", 0) + penalty_weight * (a_i * a_i)
            else:
                qp.objective.quadratic.setdefault(f"x{i}", {})[f"x{j}"] = qp.objective.quadratic.get(f"x{i}", {}).get(f"x{j}", 0) + penalty_weight * (2 * a_i * a_j)
    # Linear part from the penalty
    for i in range(n):
        qp.objective.linear[f"x{i}"] = qp.objective.linear.get(f"x{i}", 0) - 2 * penalty_weight * total_limit * coeffs[i]
    return qp


def _solve_with_qaoa(qp) -> list[int]:
    """Solve the QuadraticProgram using QAOA on the Aer simulator.
    Returns a list of selected indices (where variable == 1).
    """
    from qiskit import Aer
    from qiskit.algorithms import QAOA
    from qiskit.utils import QuantumInstance
    from qiskit_optimization.algorithms import MinimumEigenOptimizer

    backend = Aer.get_backend('aer_simulator_statevector')
    quantum_instance = QuantumInstance(backend, shots=DEFAULT_SHOTS, seed_simulator=42, seed_transpiler=42)
    qaoa = QAOA(quantum_instance=quantum_instance)
    optimizer = MinimumEigenOptimizer(qaoa)
    result = optimizer.solve(qp)
    # result.x is a list of 0/1 selections
    selected = [i for i, val in enumerate(result.x) if round(val) == 1]
    return selected


def _fallback_classical(candidates: list[dict[str, Any]]) -> list[int]:
    """Simple greedy heuristic: sort by expected_net_edge / risk and pick until limit.
    This is deterministic because it uses a fixed sort key.
    """
    limit = 1.0
    used = 0.0
    selected = []
    # Compute score ratio
    sorted_cands = sorted(
        enumerate(candidates),
        key=lambda iv: (
            iv[1].get("expected_net_edge", 0.0) / max(iv[1].get("risk", 1e-6), 1e-6)
        ),
        reverse=True,
    )
    for idx, cand in sorted_cands:
        size = cand.get("position_size_limit", 0.0)
        if used + size <= limit:
            selected.append(idx)
            used += size
    return selected


def select_opportunities(candidates: list[dict[str, Any]]) -> list[int]:
    """Public API: return the indices of selected candidates.
    Tries the quantum optimizer first; on any exception or missing backend,
    falls back to the deterministic classical heuristic.
    """
    if not candidates:
        return []
    if not USE_QISKIT:
        logger.info("Qiskit not available – using classical fallback for portfolio selection")
        return _fallback_classical(candidates)
    try:
        qp = _build_quadratic_program(candidates)
        selected = _solve_with_qaoa(qp)
        return selected
    except Exception as exc:
        logger.warning(f"Quantum portfolio optimization failed ({exc}) – falling back to classical")
        return _fallback_classical(candidates)
