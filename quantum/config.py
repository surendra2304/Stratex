# quantum/config.py
"""Configuration defaults for the Quantum Research Subsystem.
All values are deterministic and safe for production environments.
"""

# Number of qubits for the hybrid model (4-8 as per spec)
DEFAULT_QUBIT_COUNT = 4

# Circuit depth (shallow)
DEFAULT_CIRCUIT_DEPTH = 3

# Number of shots for simulation
DEFAULT_SHOTS = 1024

# Simulation timeout seconds
SIMULATION_TIMEOUT = 30

# Fallback values when optional dependencies are missing
USE_QISKIT = False
USE_PENNYLANE = False

# Attempt to import optional libraries; set flags accordingly
try:
    import qiskit  # noqa: F401
    USE_QISKIT = True
except Exception:
    pass

try:
    import pennylane as qml  # noqa: F401
    USE_PENNYLANE = True
except Exception:
    pass
