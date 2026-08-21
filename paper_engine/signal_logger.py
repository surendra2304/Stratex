import json
import os
import time
from dataclasses import dataclass


@dataclass
class Signal:
    """
    Standardized Signal interface for all strategies.
    No Lookahead Bias: This must be generated strictly from data <= signal_time.
    """
    symbol: str
    timeframe: str
    direction: str
    confidence: float
    signal_time: float
    reference_price: float
    strategy_name: str
    action: str  # "ENTRY", "EXIT", "REVERSE"
    reason: str
    features: dict | None = None


class PaperSignalLogger:
    """
    Append-only Forward Validation Dataset Logger.
    Every signal is recorded here, even if rejected by Risk Limits or Margin.
    This creates the next out-of-sample dataset for future model training.
    """

    def __init__(self, filename="paper_signals.json"):
        self.filename = filename
        self.signals = []
        os.makedirs(os.path.dirname(self.filename) or '.', exist_ok=True)
        self._load()

    def log_signal(self, signal: Signal, execution_result: str = "PENDING"):
        record = {
            "signal_id": f"sig_{int(time.time() * 1000)}",
            "timestamp": signal.signal_time,
            "symbol": signal.symbol,
            "timeframe": signal.timeframe,
            "strategy": signal.strategy_name,
            "direction": signal.direction,
            "action": signal.action,
            "confidence": getattr(signal, 'confidence', getattr(signal, 'win_rate_prior', 0.5)),
            "reference_price": signal.reference_price,
            "reason": signal.reason,
            "features": signal.features or {},
            "execution_result": execution_result,
            "eventual_outcome": None  # To be labeled later
        }
        self.signals.append(record)
        self._save()

    def update_outcome(self, signal_id: str, outcome: str):
        for sig in self.signals:
            if sig.get("signal_id") == signal_id:
                sig["eventual_outcome"] = outcome
                break
        self._save()

    def _save(self):
        tmp = self.filename + ".tmp"
        with open(tmp, 'w') as f:
            json.dump(self.signals, f, indent=4)
        os.replace(tmp, self.filename)

    def _load(self):
        if not os.path.exists(self.filename):
            return
        try:
            with open(self.filename, 'r') as f:
                self.signals = json.load(f)
        except Exception:
            self.signals = []


class SignalLogger:
    """
    Append-only JSONL signal logger with deduplication.
    Rejects duplicate signal_ids to prevent double-recording.
    Used by adversarial tests and forward validator.
    """

    def __init__(self, filename: str = "signals.jsonl"):
        self.filename = filename
        self._seen_ids: set[str] = set()
        self._load_seen_ids()

    def _load_seen_ids(self):
        if not os.path.exists(self.filename):
            return
        try:
            with open(self.filename, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        sid = record.get("signal_id")
                        if sid:
                            self._seen_ids.add(sid)
                    except json.JSONDecodeError:
                        pass
        except Exception:
            pass

    def log_signal(self, signal: dict) -> bool:
        """
        Write a signal to the JSONL log.
        Returns True if written, False if duplicate (idempotent).
        """
        sig_id = signal.get("signal_id")
        if sig_id and sig_id in self._seen_ids:
            return False  # idempotent — duplicate, not written again

        record = dict(signal)
        if "logged_at" not in record:
            record["logged_at"] = time.time()

        with open(self.filename, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record) + '\n')

        if sig_id:
            self._seen_ids.add(sig_id)
        return True
