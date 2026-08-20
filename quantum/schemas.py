# quantum/schemas.py
"""Result schema for quantum advisory output.
Uses a simple dataclass to enforce required fields.
"""
from dataclasses import dataclass, asdict
from typing import Optional, Any
import datetime

@dataclass
class QuantumResultSchema:
    quantum_status: str
    backend: Optional[str]
    model: Optional[str]
    symbol: str
    timeframe: str
    feature_count: int
    qubit_count: int
    circuit_depth: int
    shots: int
    quantum_score: Optional[float]
    classical_score: Optional[float]
    hybrid_score: Optional[float]
    latency_ms: float
    simulation: bool
    hardware_used: Optional[str]
    error: Optional[str]
    timestamp: str

    def to_dict(self) -> dict:
        d = asdict(self)
        # Ensure ISO timestamp
        d["timestamp"] = datetime.datetime.utcnow().isoformat() + "Z"
        return d
