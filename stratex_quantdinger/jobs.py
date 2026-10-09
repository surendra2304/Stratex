"""Durable finite-job store and asynchronous research job execution runner.

Decouples long-running backtests, Optuna hyperparameter optimization, and
walk-forward validation from synchronous HTTP request/response loops.

Durability and concurrency guarantees (each one was a reproduced defect):

* **Serialized read-modify-write.** Every HTTP request built its own
  ``JobStore`` with its own ``threading.Lock``, so two concurrent requests (or
  a request and a worker thread) loaded the same file, mutated different jobs
  and the second ``_save`` erased the first job's update. All mutations now
  run under :func:`atomic_io.locked_path`, which is shared per path across
  every instance in the process and across processes.
* **Unique temporaries + fsync.** ``_save`` wrote a shared ``<file>.tmp``;
  concurrent saves raced on it. Writes now go through
  :func:`atomic_io.atomic_write_json`.
* **Corruption is preserved, not overwritten.** An unreadable store used to
  load as ``{}``; the next ``create`` then replaced the corrupt-but-recoverable
  file with a one-job store. The file is now quarantined first.
* **A job id is executed at most once.** ``create`` returned an existing job
  for a duplicate id and ``submit_and_execute_async`` then launched a *second*
  execution of it, overwriting a finished job's result (default dashboard ids
  were second-resolution timestamps, so two submissions in one second
  collided). Strict creation raises :class:`JobAlreadyExistsError` instead.
* **Terminal states are final.** A worker finishing after an operator
  cancelled its job overwrote ``CANCELLED`` with ``COMPLETED``; updates to a
  job in a terminal state are now ignored (and audited as such).
"""

from __future__ import annotations

import dataclasses
import json
import logging
import threading
import traceback
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from atomic_io import atomic_write_json, load_json_state, locked_path

from .models import AuditEvent, ExperimentJob

logger = logging.getLogger("stratex_quantdinger.jobs")

TERMINAL_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})
VALID_STATUSES = frozenset({"QUEUED", "RUNNING"}) | TERMINAL_STATUSES
_JOB_FIELDS = frozenset(f.name for f in dataclasses.fields(ExperimentJob))
_MUTABLE_FIELDS = frozenset({"status", "progress", "result", "error", "metadata"})


class JobAlreadyExistsError(ValueError):
    """Raised by strict creation when the job id is already present."""


class JobStore:
    def __init__(self, path: str = "experiment_jobs.json", audit_log_path: str | None = None):
        self.path = Path(path)
        # The audit trail lives beside the store unless placed explicitly, so a
        # store opened on a scratch path never appends to the working
        # directory's production audit log.
        self.audit_log_path = Path(audit_log_path) if audit_log_path else self.path.with_name("quantdinger_audit.jsonl")
        # Kept for backward compatibility; real serialization is per path.
        self._lock = threading.RLock()

    # ── persistence ────────────────────────────────────────────────────────

    def _load(self) -> dict[str, Any]:
        loaded = load_json_state(self.path, expected_type=dict)
        if loaded.status == "corrupt":
            logger.error(
                "Research job store %s was corrupt (%s); quarantined to %s and starting empty",
                self.path, loaded.error, loaded.quarantined_to,
            )
        return loaded.data

    def _load_for_read(self) -> dict[str, Any]:
        """Lock-free read. A corrupt file is reported but only quarantined by
        writers (under the lock), so a reader never races a writer's rename."""
        loaded = load_json_state(self.path, expected_type=dict, quarantine=False)
        if loaded.status == "corrupt":
            logger.error("Research job store %s is unreadable (%s)", self.path, loaded.error)
        return loaded.data

    def _save(self, data: dict[str, Any]) -> None:
        atomic_write_json(self.path, data, indent=2, sort_keys=True)

    def _audit(self, event: AuditEvent) -> None:
        self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event.__dict__, default=str) + "\n"
        with locked_path(self.audit_log_path):
            with open(self.audit_log_path, "a", encoding="utf-8") as f:
                f.write(line)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _to_job(record: Any) -> ExperimentJob | None:
        """Rebuild a job from a persisted record; ``None`` if it is unusable."""
        if not isinstance(record, dict):
            return None
        known = {k: v for k, v in record.items() if k in _JOB_FIELDS}
        if not isinstance(known.get("job_id"), str) or not isinstance(known.get("job_type"), str):
            return None
        if not isinstance(known.get("metadata", {}), dict):
            known["metadata"] = {}
        try:
            return ExperimentJob(**known)
        except TypeError:
            return None

    # ── public API ─────────────────────────────────────────────────────────

    def create(
        self,
        job_id: str,
        job_type: str,
        metadata: dict[str, Any] | None = None,
        *,
        exist_ok: bool = True,
    ) -> ExperimentJob:
        """Create a QUEUED job.

        With ``exist_ok=True`` (legacy behaviour) an existing job with the same
        id is returned unchanged. With ``exist_ok=False`` a duplicate raises
        :class:`JobAlreadyExistsError` — use this whenever creation is followed
        by execution, so one id can never run twice.
        """
        if not isinstance(job_id, str) or not job_id.strip():
            raise ValueError("job_id must be a non-empty string")
        if not isinstance(job_type, str) or not job_type.strip():
            raise ValueError("job_type must be a non-empty string")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata must be a dict")
        with locked_path(self.path):
            data = self._load()
            now = self._now()
            if job_id in data:
                existing = self._to_job(data[job_id])
                if not exist_ok:
                    raise JobAlreadyExistsError(f"Job already exists: {job_id}")
                if existing is not None:
                    return existing
                logger.error("Replacing unreadable job record %s", job_id)
            job = ExperimentJob(
                job_id=job_id,
                job_type=job_type,
                status="QUEUED",
                progress=0.0,
                created_at=now,
                updated_at=now,
                metadata=dict(metadata or {}),
            )
            data[job_id] = job.__dict__
            self._save(data)

            self._audit(AuditEvent(
                timestamp=now,
                actor="system",
                resource=f"job:{job_id}",
                action="CREATE_JOB",
                previous_state=None,
                new_state="QUEUED",
                reason=f"Created {job_type} research job",
                correlation_id=f"job={job_id} type={job_type}",
            ))
            return job

    def update(self, job_id: str, **changes: Any) -> ExperimentJob:
        unknown = set(changes) - _MUTABLE_FIELDS
        if unknown:
            raise ValueError(f"Cannot update job field(s): {sorted(unknown)}")
        if "status" in changes and changes["status"] not in VALID_STATUSES:
            raise ValueError(f"Invalid job status: {changes['status']!r}")
        with locked_path(self.path):
            data = self._load()
            if job_id not in data:
                raise KeyError(f"Job not found: {job_id}")
            current = self._to_job(data[job_id])
            if current is None:
                raise KeyError(f"Job record is unreadable: {job_id}")
            prev_status = current.status
            if prev_status in TERMINAL_STATUSES:
                if changes.get("status", prev_status) != prev_status or set(changes) - {"status"}:
                    logger.warning(
                        "Ignoring update %s to job %s: it is already %s",
                        sorted(changes), job_id, prev_status,
                    )
                    self._audit(AuditEvent(
                        timestamp=self._now(),
                        actor="worker",
                        resource=f"job:{job_id}",
                        action="UPDATE_REJECTED",
                        previous_state=prev_status,
                        new_state=prev_status,
                        reason=f"Job is terminal ({prev_status}); update {sorted(changes)} ignored",
                        correlation_id=f"job={job_id}",
                    ))
                return current

            record = dict(current.__dict__)
            record.update(changes)
            now = self._now()
            record["updated_at"] = now
            data[job_id] = record
            self._save(data)

            new_status = record.get("status")
            if new_status != prev_status:
                self._audit(AuditEvent(
                    timestamp=now,
                    actor="worker",
                    resource=f"job:{job_id}",
                    action="STATUS_CHANGE",
                    previous_state=prev_status,
                    new_state=new_status,
                    reason=f"Job transition: {changes.get('error') or 'progress update'}",
                    correlation_id=f"job={job_id}",
                ))
            return ExperimentJob(**record)

    # Reads take no lock: every write is an atomic rename, so a reader always
    # sees either the previous or the next complete file.

    def get(self, job_id: str) -> ExperimentJob:
        data = self._load_for_read()
        if job_id not in data:
            raise KeyError(f"Job not found: {job_id}")
        job = self._to_job(data[job_id])
        if job is None:
            raise KeyError(f"Job record is unreadable: {job_id}")
        return job

    def list_jobs(self, job_type: str | None = None, status: str | None = None, limit: int = 50) -> list[ExperimentJob]:
        data = self._load_for_read()
        jobs = []
        for record in data.values():
            job = self._to_job(record)
            if job is None:
                continue
            if job_type is not None and job.job_type != job_type:
                continue
            if status is not None and job.status != status:
                continue
            jobs.append(job)
        jobs.sort(key=lambda x: str(x.created_at), reverse=True)
        return jobs[:max(0, int(limit))]

    def cancel(self, job_id: str) -> ExperimentJob:
        return self.update(job_id, status="CANCELLED")


class ResearchJobRunner:
    """Asynchronous research job execution worker thread pool.
    Executes BACKTEST, OPTIMIZATION, WALK_FORWARD, and REPORT jobs durable in JobStore.
    """

    def __init__(self, store: JobStore | None = None, registry=None):
        self.store = store or JobStore()
        self.registry = registry

    def submit_and_execute_async(
        self,
        job_id: str,
        job_type: str,
        runner_fn: Callable[[JobStore, str], None],
        metadata: dict | None = None,
    ) -> ExperimentJob:
        """Create the job (strictly — a duplicate id raises
        :class:`JobAlreadyExistsError`) and run it on a daemon worker thread."""
        job = self.store.create(job_id=job_id, job_type=job_type, metadata=metadata, exist_ok=False)
        t = threading.Thread(
            target=self._execute_wrapper, args=(job_id, runner_fn), daemon=True,
            name=f"research-job-{job_id}",
        )
        t.start()
        return job

    def _execute_wrapper(self, job_id: str, runner_fn: Callable[[JobStore, str], None]) -> None:
        progress = 0.0
        try:
            self.store.update(job_id, status="RUNNING", progress=0.05)
            progress = 0.05
            runner_fn(self.store, job_id)
            # Ensure job is marked COMPLETED if runner_fn hasn't already
            current = self.store.get(job_id)
            progress = current.progress
            if current.status == "RUNNING":
                self.store.update(job_id, status="COMPLETED", progress=1.0)
        except Exception as e:
            err_msg = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
            try:
                progress = self.store.get(job_id).progress
            except Exception:  # store unreadable: keep last known progress
                pass
            try:
                self.store.update(job_id, status="FAILED", error=err_msg, progress=progress)
            except Exception:
                logger.exception("Could not record failure of research job %s", job_id)
