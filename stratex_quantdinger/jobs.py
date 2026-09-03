"""Durable finite-job store and asynchronous research job execution runner.

Decouples long-running backtests, Optuna hyperparameter optimization, and
walk-forward validation from synchronous HTTP request/response loops.
"""

from __future__ import annotations
import json
import threading
import traceback
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Callable

from .models import ExperimentJob, AuditEvent


class JobStore:
    def __init__(self, path: str = "experiment_jobs.json", audit_log_path: str = "quantdinger_audit.jsonl"):
        self.path = Path(path)
        self.audit_log_path = Path(audit_log_path)
        self._lock = threading.Lock()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    def _audit(self, event: AuditEvent) -> None:
        self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.audit_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event.__dict__) + "\n")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def create(self, job_id: str, job_type: str, metadata: dict[str, Any] | None = None) -> ExperimentJob:
        with self._lock:
            data = self._load()
            now = self._now()
            if job_id in data:
                return ExperimentJob(**data[job_id])
            job = ExperimentJob(
                job_id=job_id,
                job_type=job_type,
                status="QUEUED",
                progress=0.0,
                created_at=now,
                updated_at=now,
                metadata=metadata or {},
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

    def update(self, job_id: str, **changes) -> ExperimentJob:
        with self._lock:
            data = self._load()
            if job_id not in data:
                raise KeyError(f"Job not found: {job_id}")
            prev_status = data[job_id].get("status")
            data[job_id].update(changes)
            now = self._now()
            data[job_id]["updated_at"] = now
            self._save(data)

            new_status = data[job_id].get("status")
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
            return ExperimentJob(**data[job_id])

    def get(self, job_id: str) -> ExperimentJob:
        with self._lock:
            data = self._load()
            if job_id not in data:
                raise KeyError(f"Job not found: {job_id}")
            return ExperimentJob(**data[job_id])

    def list_jobs(self, job_type: str | None = None, status: str | None = None, limit: int = 50) -> list[ExperimentJob]:
        with self._lock:
            data = self._load()
            jobs = []
            for j_data in data.values():
                if job_type is not None and j_data.get("job_type") != job_type:
                    continue
                if status is not None and j_data.get("status") != status:
                    continue
                jobs.append(ExperimentJob(**j_data))
            jobs.sort(key=lambda x: x.created_at, reverse=True)
            return jobs[:limit]

    def cancel(self, job_id: str) -> ExperimentJob:
        return self.update(job_id, status="CANCELLED")


class ResearchJobRunner:
    """Asynchronous research job execution worker thread pool.
    Executes BACKTEST, OPTIMIZATION, WALK_FORWARD, and REPORT jobs durable in JobStore.
    """

    def __init__(self, store: JobStore | None = None, registry=None):
        self.store = store or JobStore()
        self.registry = registry

    def submit_and_execute_async(self, job_id: str, job_type: str, runner_fn: Callable[[JobStore, str], None], metadata: dict | None = None) -> ExperimentJob:
        """Enqueues and immediately launches execution on a background daemon worker thread."""
        job = self.store.create(job_id=job_id, job_type=job_type, metadata=metadata)
        t = threading.Thread(target=self._execute_wrapper, args=(job_id, runner_fn), daemon=True)
        t.start()
        return job

    def _execute_wrapper(self, job_id: str, runner_fn: Callable[[JobStore, str], None]):
        try:
            self.store.update(job_id, status="RUNNING", progress=0.05)
            runner_fn(self.store, job_id)
            # Ensure job is marked COMPLETED if runner_fn hasn't already
            current = self.store.get(job_id)
            if current.status == "RUNNING":
                self.store.update(job_id, status="COMPLETED", progress=1.0)
        except Exception as e:
            err_msg = f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
            self.store.update(job_id, status="FAILED", error=err_msg, progress=current.progress if 'current' in locals() else 0.0)
