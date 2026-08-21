# quantum/simulator.py
"""Local quantum simulator utilities.
Supports PennyLane and Qiskit Aer. If neither backend is available,
returns a deterministic fallback result.
"""
import time

from .config import DEFAULT_SHOTS, SIMULATION_TIMEOUT, USE_PENNYLANE, USE_QISKIT

if USE_PENNYLANE:
    import pennylane as qml
elif USE_QISKIT:
    from qiskit import Aer, execute
    from qiskit.providers.aer import AerSimulator
else:
    qml = None
    Aer = None
    execute = None
    AerSimulator = None


def run_circuit(circuit, shots: int = DEFAULT_SHOTS):
    """Execute the given circuit and return the raw result.
    * If ``circuit`` is a PennyLane QNode, it is called directly and the
      expectation value is returned wrapped in a simple namespace mimicking
      Qiskit result for compatibility.
    * If ``circuit`` is a Qiskit QuantumCircuit, it is executed on the Aer
      simulator.
    * If no backend is available, returns a dummy object with ``get_counts``
      yielding a uniform distribution.
    """
    time.time()
    if USE_PENNYLANE and callable(circuit):
        # PennyLane QNode expects parameters; we pass the stored params via closure
        exp_val = circuit()
        class _Result:
            def get_counts(self):
                # Convert expectation to pseudo counts for compatibility
                prob_one = (exp_val + 1) / 2.0
                return {"0" * circuit.device.wires.num_wires: int((1 - prob_one) * shots),
                        "1" * circuit.device.wires.num_wires: int(prob_one * shots)}
        return _Result()
    elif USE_QISKIT:
        backend = Aer.get_backend('aer_simulator')
        job = execute(circuit, backend=backend, shots=shots, timeout=SIMULATION_TIMEOUT)
        return job.result()
    else:
        # Fallback deterministic result
        class _FallbackResult:
            def get_counts(self):
                # Return all zeros counts
                return {"0" * 4: shots}
        return _FallbackResult()
