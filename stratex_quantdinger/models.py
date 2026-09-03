"""Stratex-native contracts inspired by QuantDinger's explicit runtime boundaries."""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class StrategyVersion:
    """Immutable record of a strategy version, its source code hash, and frozen parameters."""
    strategy_id: str
    version: str
    source_hash: str
    created_at: str
    parameters: dict[str, Any]
    status: str = "RESEARCH"  # RESEARCH, OOS_VALIDATED, APPROVED, ACTIVE, RETIRED


@dataclass
class ExperimentJob:
    """Durable finite research job (backtest, optimization, walk-forward, report)."""
    job_id: str
    job_type: str  # BACKTEST, OPTIMIZATION, WALK_FORWARD, ANALYTICS, REPORT
    status: str = "QUEUED"  # QUEUED, RUNNING, COMPLETED, FAILED, CANCELLED
    progress: float = 0.0  # 0.0 to 1.0
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeHeartbeat:
    """Telemetry heartbeat emitted periodically by active strategy execution runtimes."""
    runtime_id: str
    strategy_id: str
    status: str  # RUNNING, PAUSED, STOPPED, FAILED
    timestamp: str
    lease_expires_at: str | None = None


@dataclass(frozen=True)
class ExecutionIntent:
    """Explicit auditable intent created between strategy signal generation and exchange execution."""
    intent_id: str
    strategy_id: str
    strategy_version: str
    symbol: str
    side: str
    quantity: float
    order_type: str = "MARKET"
    price: float | None = None
    paper_only: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AuditEvent:
    """Audit log record capturing lifecycle and execution state transitions."""
    timestamp: str
    actor: str
    resource: str
    action: str
    previous_state: str | None
    new_state: str
    reason: str = ""
    correlation_id: str = ""
