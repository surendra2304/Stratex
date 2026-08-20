# quantum/service.py
"""High‑level service exposing quantum advisory data.
All operations are read‑only and never touch the execution engine.
"""
import time
from .config import DEFAULT_QUBIT_COUNT, DEFAULT_CIRCUIT_DEPTH, DEFAULT_SHOTS, USE_PENNYLANE, USE_QISKIT
from .features import extract_feature_vector
from .models import QuantumModel
from .schemas import QuantumResultSchema
try:
    from data import get_candles  # existing market data fetcher
except ImportError:
    from ..data import get_candles

class QuantumService:
    def __init__(self):
        # Initialize model (deterministic params)
        self.model = QuantumModel()
        self.qubit_count = DEFAULT_QUBIT_COUNT
        self.circuit_depth = DEFAULT_CIRCUIT_DEPTH
        self.shots = DEFAULT_SHOTS
        self.backend = "PennyLane" if USE_PENNYLANE else ("Qiskit" if USE_QISKIT else None)

    def _compute_classical_score(self, df):
        """Very simple classical baseline: average absolute return over last 10 candles."""
        returns = df["close"].pct_change().abs()
        if len(returns) == 0:
            return None
        return float(returns.tail(10).mean())

    def get_advisory(self, symbol: str = "BTCUSDT", tf: str = "15m"):
        start_time = time.time()
        # Load recent market data (same as /api/candles but limited)
        try:
            df = get_candles(symbol, tf, limit=300)
        except Exception as e:
            return QuantumResultSchema(
                quantum_status="FAIL",
                backend=self.backend,
                model="HybridClassifier",
                symbol=symbol,
                timeframe=tf,
                feature_count=0,
                qubit_count=self.qubit_count,
                circuit_depth=self.circuit_depth,
                shots=self.shots,
                quantum_score=None,
                classical_score=None,
                hybrid_score=None,
                latency_ms=(time.time() - start_time) * 1000,
                simulation=False,
                hardware_used=None,
                error=str(e),
                timestamp=""
            ).to_dict()

        # Extract feature vector
        feature_vec = extract_feature_vector(df)
        feature_count = len(feature_vec)
        # Classical baseline score
        classical_score = self._compute_classical_score(df)
        # Quantum score (fallback if no backend)
        quantum_score = None
        error = None
        simulation = False
        hardware_used = None
        try:
            if self.backend:
                quantum_score = self.model.predict(feature_vec)
                simulation = True
                hardware_used = "simulator"
            else:
                error = "No quantum backend available"
        except Exception as exc:
            error = f"Quantum execution error: {exc}"
            quantum_score = None

        # Hybrid combination – simple average when both present
        hybrid_score = None
        if quantum_score is not None and classical_score is not None:
            hybrid_score = (quantum_score + classical_score) / 2.0
        elif quantum_score is not None:
            hybrid_score = quantum_score
        elif classical_score is not None:
            hybrid_score = classical_score

        return QuantumResultSchema(
            quantum_status="SUCCESS" if error is None else "FAIL",
            backend=self.backend,
            model="HybridClassifier",
            symbol=symbol,
            timeframe=tf,
            feature_count=feature_count,
            qubit_count=self.qubit_count,
            circuit_depth=self.circuit_depth,
            shots=self.shots,
            quantum_score=quantum_score,
            classical_score=classical_score,
            hybrid_score=hybrid_score,
            latency_ms=(time.time() - start_time) * 1000,
            simulation=simulation,
            hardware_used=hardware_used,
            error=error,
            timestamp=""
        ).to_dict()
