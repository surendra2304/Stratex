"""stratex_quantdinger — QuantDinger-inspired architecture for STRATEX."""

from .models import (
    StrategyVersion,
    ExperimentJob,
    RuntimeHeartbeat,
    ExecutionIntent,
    AuditEvent,
)
from .registry import StrategyRegistry
from .jobs import JobStore, ResearchJobRunner
from .runtime import RuntimeLease, RuntimeSupervisor
from .idempotency import IdempotencyGuard
from .agent_contract import ResearchAgentGateway

__all__ = [
    "StrategyVersion",
    "ExperimentJob",
    "RuntimeHeartbeat",
    "ExecutionIntent",
    "AuditEvent",
    "StrategyRegistry",
    "JobStore",
    "ResearchJobRunner",
    "RuntimeLease",
    "RuntimeSupervisor",
    "IdempotencyGuard",
    "ResearchAgentGateway",
]
