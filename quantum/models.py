# quantum/models.py
"""Simple hybrid quantum model wrapper.
Encapsulates trainable parameters and provides a ``predict`` method that
returns a probability score between 0 and 1.
"""
import numpy as np

from .circuits import build_ansatz
from .config import (
    DEFAULT_CIRCUIT_DEPTH,
    DEFAULT_QUBIT_COUNT,
    DEFAULT_SHOTS,
    USE_PENNYLANE,
    USE_QISKIT,
)
from .simulator import run_circuit


class QuantumModel:
    def __init__(self, params: np.ndarray = None):
        # Number of parameters = qubits * depth * 2 (RX,RY per qubit per layer)
        n_params = DEFAULT_QUBIT_COUNT * DEFAULT_CIRCUIT_DEPTH * 2
        if params is None:
            # deterministic seed for reproducibility
            rng = np.random.default_rng(42)
            self.params = rng.uniform(0, 2 * np.pi, size=n_params).astype(np.float32)
        else:
            self.params = np.array(params, dtype=np.float32)
        # Build circuit (PennyLane QNode or Qiskit circuit)
        self.circuit = build_ansatz(self.params)

    def predict(self, feature_vec: np.ndarray) -> float:
        """Run the circuit and map the expectation to a probability.
        For PennyLane we return the expectation value ([-1,1]) scaled to [0,1].
        For Qiskit we use the measured bitstring frequencies to compute probability of ``1``.
        """
        if USE_PENNYLANE:
            exp_val = self.circuit(self.params)
            prob = (exp_val + 1) / 2.0
            return float(prob)
        elif USE_QISKIT:
            # Execute via simulator utility
            result = run_circuit(self.circuit, shots=DEFAULT_SHOTS)
            # result is counts dict
            counts = result.get_counts()
            total = sum(counts.values())
            ones = sum(v for k, v in counts.items() if k[-1] == "1")
            prob = ones / total if total > 0 else 0.0
            return float(prob)
        else:
            return 0.5
