# quantum/__init__.py
"""Quantum Research Subsystem package.
Provides advisory‑only utilities without affecting trading execution.
"""

# Advisory flag – ensures other parts treat this as read‑only
QUANTUM_ADVISORY_ONLY = True

# Export primary service class for convenience
from .service import QuantumService
