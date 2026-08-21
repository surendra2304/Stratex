# quantum/schemas.py
"""Result schema for quantum advisory output.
Uses a simple dataclass to enforce required fields.
"""
import datetime
from dataclasses import asdict, dataclass


@dataclass
class QuantumResultSchema:
    quantum_status: str
    backend: str | None
    model: str | None
    symbol: str
    timeframe: str
    feature_count: int
    qubit_count: int
    circuit_depth: int
    shots: int
    quantum_score: float | None
    classical_score: float | None
    hybrid_score: float | None
    latency_ms: float
    simulation: bool
    hardware_used: str | None
    error: str | None
    timestamp: str

    def to_dict(self) -> dict:
        d = asdict(self)
        # Ensure ISO timestamp
        d["timestamp"] = datetime.datetime.utcnow().isoformat() + "Z"
        return d
