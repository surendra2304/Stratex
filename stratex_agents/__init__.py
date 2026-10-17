"""Stratex's bounded multi-agent control plane.

Venue agents connect to exchanges; strategy, risk, portfolio, reconciliation and
intelligence roles remain separate, auditable components. No LLM agent is allowed
to bypass the deterministic execution policy.
"""
from .registry import get_agent_catalog

__all__ = ["get_agent_catalog"]
