"""
config_production.py — Production-Hardened Configuration & Strict Safety Invariants.

Enforces maximal safety parameters, turns off debugging, locks core safety gates,
and sets conservative risk tolerances for production deployment.
"""

import os
from dataclasses import dataclass, field
from typing import Dict, List, Set

from config import *  # Inherit base settings and exchange definitions

# --- Production Environment Overrides ---
ENVIRONMENT = "PRODUCTION"
DEBUG = False
LOG_LEVEL = os.getenv("LOG_LEVEL", "WARNING").upper()

# --- Strict Safety Boundaries (Hard Limits) ---
MAX_DRAWDOWN_LIMIT_PCT = 0.15          # 15% absolute max account drawdown -> triggers circuit breaker
MAX_DAILY_LOSS_PCT = 0.05              # 5% max daily loss ceiling -> halts daily trading
WARNING_DRAWDOWN_PCT = 0.10            # 10% warning threshold
WARNING_DAILY_LOSS_PCT = 0.03          # 3% warning threshold

MAX_POSITION_SIZE_MULTIPLIER = 1.50     # AI recommendations cannot scale sizing > 1.5x
MIN_POSITION_SIZE_MULTIPLIER = 0.50     # AI recommendations cannot shrink sizing < 0.5x
MAX_PARAMETER_DELTA_PCT = 0.20          # Maximum ±20.0% parameter variance allowed per cycle
LEVERAGE_DECREASE_ONLY = True           # Leverage is forbidden from increasing

# --- Forbidden Parameters in Production ---
PRODUCTION_FORBIDDEN_PARAMS: Set[str] = {
    "max_daily_loss",
    "max_drawdown",
    "live_trading_enabled",
    "api_key",
    "secret_key",
    "binance_api_key",
    "binance_api_secret",
    "bot_api_key",
    "risk_limits",
    "leverage_ceiling"
}

# --- Safety Gate Permanent Enforcements ---
RISK_GATE_PERMANENTLY_ENABLED = True
PROFITABILITY_GATE_PERMANENTLY_ENABLED = True
EXECUTION_POLICY_STRICT_BLOCK_LIVE = True  # Hard-block direct real fund executions unless explicitly overridden

# --- AI Advisory Production Settings ---
ADVISORY_INTERVAL_HOURS = 4.0           # Optimal periodic consultation frequency
ADVISORY_TIMEOUT_SECONDS = 120          # 2 minute client timeout
ADVISORY_COOLDOWN_HOURS = 4.0           # Minimum cooldown between live applied changes
ADVISORY_MAX_CHANGES_PER_CYCLE = 2      # Maximum 2 parameter changes per consultation batch

# --- Resource & Monitoring Alert Thresholds ---
RESOURCE_CPU_WARN_PCT = 80.0
RESOURCE_MEMORY_WARN_PCT = 80.0
RESOURCE_DISK_WARN_PCT = 85.0
AI_UNREACHABLE_WARN_MINUTES = 5.0

# --- Production State & Audit File Namespaces ---
PROD_STATE_FILE = "production_bot_state.json"
PROD_AUDIT_LOG_FILE = "production_audit_log.jsonl"
PROD_ALERTS_LOG_FILE = "production_alerts.jsonl"
PROD_DEPLOYMENT_LOG_FILE = "deployment_audit_log.json"


def validate_production_security() -> Dict[str, bool]:
    """Validates that no production safety gates are bypassed or insecurely configured."""
    checks = {
        "debug_disabled": DEBUG is False,
        "drawdown_limit_safe": MAX_DRAWDOWN_LIMIT_PCT <= 0.15,
        "daily_loss_limit_safe": MAX_DAILY_LOSS_PCT <= 0.05,
        "param_delta_safe": MAX_PARAMETER_DELTA_PCT <= 0.20,
        "leverage_invariant_enforced": LEVERAGE_DECREASE_ONLY is True,
        "safety_gates_active": RISK_GATE_PERMANENTLY_ENABLED and PROFITABILITY_GATE_PERMANENTLY_ENABLED
    }
    return checks
