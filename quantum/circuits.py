# quantum/circuits.py
"""Quantum circuit construction utilities.
Supports both PennyLane and Qiskit backends with a simple hardware‑efficient ansatz.
"""
import numpy as np
from .config import DEFAULT_QUBIT_COUNT, DEFAULT_CIRCUIT_DEPTH, USE_PENNYLANE, USE_QISKIT

if USE_PENNYLANE:
    import pennylane as qml
elif USE_QISKIT:
    from qiskit import QuantumCircuit
else:
    qml = None
    QuantumCircuit = None


def build_ansatz(params: np.ndarray):
    """Build a parameterised circuit.
    * params: 1‑D array of rotation angles; length should be ``qubits * depth * 2``.
    Returns a PennyLane ``QNode`` if Pennylane is available, otherwise a Qiskit ``QuantumCircuit``.
    """
    n_qubits = DEFAULT_QUBIT_COUNT
    depth = DEFAULT_CIRCUIT_DEPTH
    if USE_PENNYLANE:
        dev = qml.device("default.qubit", wires=n_qubits)
        @qml.qnode(dev)
        def circuit(param_vector):
            idx = 0
            for d in range(depth):
                for q in range(n_qubits):
                    qml.RX(param_vector[idx], wires=q)
                    idx += 1
                    qml.RY(param_vector[idx], wires=q)
                    idx += 1
                # entangling layer: chain of CNOTs
                for q in range(n_qubits - 1):
                    qml.CNOT(wires=[q, q + 1])
            return qml.expval(qml.PauliZ(0))
        return circuit
    elif USE_QISKIT:
        qc = QuantumCircuit(n_qubits)
        idx = 0
        for d in range(depth):
            for q in range(n_qubits):
                qc.rx(params[idx], q)
                idx += 1
                qc.ry(params[idx], q)
                idx += 1
            for q in range(n_qubits - 1):
                qc.cx(q, q + 1)
        qc.measure_all()
        return qc
    else:
        # Graceful fallback when neither PennyLane nor Qiskit is installed
        return None
