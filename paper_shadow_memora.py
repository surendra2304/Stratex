"""Durable, cloud-only checkpoints for the observe-only shadow paper processor.

This adapter stores append-only snapshots in Stratex's private Memora namespace.
It is deliberately not wired into a scheduler or exchange engine.
If Memora is missing, unavailable, or reports non-durable storage, calls fail
closed and the caller must not continue as though state had been persisted.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from typing import Any, Callable

import pandas as pd

from paper_shadow_processor import SHADOW_CANDIDATES

NAMESPACE = "memora://stratex/private/paper-shadow"
TASK_ID = "paper-shadow-checkpoint"
SCHEMA_VERSION = 1
MAX_SNAPSHOT_BYTES = 900_000


class ShadowPersistenceError(RuntimeError):
    """Memora persistence could not be confirmed; shadow processing must stop."""


class MemoraShadowStore:
    """Persist and restore bounded processor snapshots through Memora's v1 API.

    ``transport`` is injectable for tests and must return ``(status, body)``.
    Production calls use only the documented ``POST /v1/memories`` and
    ``POST /v1/memories/query`` endpoints; there is no local database fallback.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 5.0,
        transport: Callable[[str, str, dict[str, str], dict[str, Any] | None, float], tuple[int, Any]] | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("MEMORA_URL", "https://memora-cavc.onrender.com")).rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._transport = transport or _http_transport

    def save(self, processor: Any) -> dict[str, Any]:
        """Append an idempotent checkpoint; require a confirmed durable receipt."""
        snapshot = _snapshot_processor(processor)
        content = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(content.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
            raise ShadowPersistenceError("paper shadow checkpoint exceeds the Memora payload limit")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        payload = {
            "agent_id": "stratex",
            "task_id": TASK_ID,
            "idempotency_key": f"paper-shadow-v1-{digest}",
            "content_text": content,
            "target_namespace_path": NAMESPACE,
            "memory_type": "episodic",
            "source": "stratex:paper_shadow_processor",
            "source_type": "tool_output",
            "trust_level": "candidate",
            "provenance": {
                "subsystem": "paper_shadow_processor",
                "schema_version": SCHEMA_VERSION,
                "evidence_status": "UNVALIDATED_PAPER_SHADOW",
                "trust_level": "candidate",
            },
            "confidence": 1.0,
            "importance": 0.5,
            "allow_duplicates": False,
        }
        response = self._request("POST", "/v1/memories", payload)
        if not isinstance(response, dict) or not response.get("id"):
            raise ShadowPersistenceError("Memora did not return a checkpoint receipt")
        if response.get("storage_durable") is not True:
            raise ShadowPersistenceError("Memora did not confirm durable checkpoint storage")
        return {"memory_id": response["id"], "storage_durable": True, "namespace": NAMESPACE}

    def load_latest(self, processor: Any) -> bool:
        """Restore the latest matching snapshot; return False only if none exist."""
        query = {
            "agent_id": "stratex",
            "task_id": TASK_ID,
            "namespace_path": NAMESPACE,
            "owner_name": "stratex",
            "limit": 100,
            "offset": 0,
        }
        response = self._request("POST", "/v1/memories/query", query)
        if not isinstance(response, list):
            raise ShadowPersistenceError("Memora returned an invalid checkpoint query response")
        # Memora's query contract orders records newest first. Still validate
        # ownership markers in content before restoring any state.
        for record in response:
            content = record.get("content_text") if isinstance(record, dict) else None
            if not isinstance(content, str):
                continue
            try:
                snapshot = json.loads(content)
            except (TypeError, json.JSONDecodeError):
                continue
            if _is_our_snapshot(snapshot):
                _restore_processor(processor, snapshot)
                return True
        return False

    def _request(self, method: str, path: str, payload: dict[str, Any] | None) -> Any:
        key = self.api_key or os.getenv("STRATEX_API_KEY")
        if not key:
            raise ShadowPersistenceError("STRATEX_API_KEY is not configured for Memora")
        if not self.base_url.startswith("https://"):
            raise ShadowPersistenceError("Memora URL must use HTTPS")
        headers = {
            "X-Agent-Name": "stratex",
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        try:
            status, body = self._transport(method, self.base_url + path, headers, payload, self.timeout)
        except Exception as exc:
            raise ShadowPersistenceError(f"Memora request failed ({type(exc).__name__})") from exc
        if not 200 <= status < 300:
            raise ShadowPersistenceError(f"Memora returned HTTP {status}")
        return body


def _snapshot_processor(processor: Any) -> dict[str, Any]:
    """Serialize only the small state required to resume shadow simulation."""
    key = lambda parts: "|".join(parts)
    snapshot = {
        "kind": "stratex.paper_shadow.checkpoint",
        "schema_version": SCHEMA_VERSION,
        "evidence_status": "UNVALIDATED_PAPER_SHADOW",
        "market_data_source": getattr(processor, "market_data_source", None),
        "configuration": {
            "starting_capital": float(processor.starting_capital),
            "risk_fraction": float(processor.risk_fraction),
            "max_position_fraction": float(processor.max_position_fraction),
            "max_total_exposure_fraction": float(processor.max_total_exposure_fraction),
            "cost_engine": {
                name: float(getattr(processor.cost_engine, name))
                for name in ("entry_fee", "exit_fee", "entry_slip", "exit_slip", "spread")
            },
        },
        "realized_equity": float(processor.realized_equity),
        "positions": {key(k): v for k, v in processor.positions.items()},
        "pending_entries": {key(k): v for k, v in processor.pending_entries.items()},
        "last_timestamps": {key(k): v.isoformat() for k, v in processor.last_timestamps.items()},
    }
    # Ensure snapshots contain JSON-safe finite data before any network call.
    json.dumps(snapshot, allow_nan=False)
    return snapshot


def _is_our_snapshot(snapshot: Any) -> bool:
    return (
        isinstance(snapshot, dict)
        and snapshot.get("kind") == "stratex.paper_shadow.checkpoint"
        and snapshot.get("schema_version") == SCHEMA_VERSION
        and snapshot.get("evidence_status") == "UNVALIDATED_PAPER_SHADOW"
    )


def _restore_processor(processor: Any, snapshot: dict[str, Any]) -> None:
    if not _is_our_snapshot(snapshot):
        raise ShadowPersistenceError("Memora checkpoint schema or ownership marker is invalid")
    expected = _snapshot_processor(processor)["configuration"]
    if snapshot.get("configuration") != expected:
        raise ShadowPersistenceError("checkpoint risk or cost configuration differs from this processor")
    if snapshot.get("market_data_source") != getattr(processor, "market_data_source", None):
        raise ShadowPersistenceError("checkpoint market data source differs from this scheduler")
    try:
        equity = float(snapshot["realized_equity"])
        positions = {_parse_key(k): v for k, v in snapshot["positions"].items()}
        pending = {_parse_key(k): v for k, v in snapshot["pending_entries"].items()}
        timestamps = {
            _parse_key(k): pd.Timestamp(v)
            for k, v in snapshot["last_timestamps"].items()
        }
        if not (
            equity > 0
            and all(key[0] in SHADOW_CANDIDATES for key in positions)
            and all(key[0] in SHADOW_CANDIDATES for key in pending)
            and all(key[0] in SHADOW_CANDIDATES for key in timestamps)
        ):
            raise ValueError("invalid shadow state")
    except Exception as exc:
        raise ShadowPersistenceError("Memora checkpoint contains invalid shadow state") from exc
    processor.realized_equity = equity
    processor.positions = positions
    processor.pending_entries = pending
    processor.last_timestamps = timestamps
    # The per-stream timestamp cursor is authoritative for duplicate rejection.
    processor.seen_signal_ids = set()


def _parse_key(value: str) -> tuple[str, str, str]:
    parts = value.split("|", 2)
    if len(parts) != 3:
        raise ValueError("invalid stream key")
    return parts[0], parts[1], parts[2]


def _http_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any] | None,
    timeout: float,
) -> tuple[int, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            return response.status, json.loads(body.decode("utf-8")) if body else {}
    except urllib.error.HTTPError as exc:
        return exc.code, {"error": "Memora request rejected"}
