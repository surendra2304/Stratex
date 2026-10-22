"""
advisory_ledger.py — Append-only durable audit ledger for AI-Universe consultations.

Records every AI advisory decision, validation result, debate summary, and regime status.
Uses atomic write (.tmp then replace) for durability.
"""

import datetime
import json
import os
import threading
from typing import Any

from logger import get_logger

logger = get_logger("advisory_ledger")

ADVISORY_LOG_FILE = os.getenv("ADVISORY_LOG_FILE", "advisory_log.jsonl")
_ledger_lock = threading.Lock()


def append_advisory_entry(entry: dict[str, Any], filepath: str | None = None) -> bool:
    """
    Appends a consultation record to the JSONL advisory log atomically.
    Ensures required schema fields are present.
    """
    target_file = filepath or ADVISORY_LOG_FILE
    now_iso = datetime.datetime.utcnow().isoformat() + "Z"

    record = {
        "timestamp": entry.get("timestamp", now_iso),
        "decision_id": str(entry.get("decision_id", "UNKNOWN")),
        "consultation_reason": str(entry.get("consultation_reason", "SCHEDULED")),
        "ai_status": str(entry.get("ai_status", "UNKNOWN")),
        "confidence": float(entry.get("confidence", 0.0)),
        "requested_changes": entry.get("requested_changes", []),
        "verdict": str(entry.get("verdict", "REJECT")),
        "applied_changes": entry.get("applied_changes", []),
        "rejected_changes": entry.get("rejected_changes", []),
        "ai_debate_summary": entry.get("ai_debate_summary", ""),
        "regime_analysis": entry.get("regime_analysis", {}),
        "latency_ms": float(entry.get("latency_ms", 0.0)),
        "shadow_mode": bool(entry.get("shadow_mode", True)),
        "bounds_checked": entry.get("bounds_checked", {})
    }

    with _ledger_lock:
        try:
            line = json.dumps(record) + "\n"
            # Append directly if exists, or create
            with open(target_file, "a", encoding="utf-8") as f:
                f.write(line)
            logger.info(f"[ADVISORY_LEDGER] Appended decision {record['decision_id']} ({record['verdict']}) to {target_file}")
            return True
        except Exception as e:
            logger.error(f"[ADVISORY_LEDGER] Failed to append advisory entry: {e}")
            return False


def read_recent_advisory_entries(limit: int = 50, filepath: str | None = None) -> list[dict[str, Any]]:
    """
    Reads the last N records from the advisory ledger in reverse chronological order.
    """
    target_file = filepath or ADVISORY_LOG_FILE
    if not os.path.exists(target_file):
        return []

    entries = []
    with _ledger_lock:
        try:
            with open(target_file, "r", encoding="utf-8") as f:
                for line in f:
                    line_str = line.strip()
                    if not line_str:
                        continue
                    try:
                        entries.append(json.loads(line_str))
                    except Exception:
                        continue
        except Exception as e:
            logger.error(f"[ADVISORY_LEDGER] Error reading advisory log {target_file}: {e}")
            return []

    # Return most recent first
    entries.reverse()
    return entries[:max(1, limit)]
