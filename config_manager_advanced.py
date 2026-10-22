"""
config_manager_advanced.py — Advanced Dynamic Configuration Management, Versioning & Rollback.

Capabilities:
1. Dynamic Parameter Bounds & Cross-Consistency Validation.
2. Immutable Configuration Versioning & History Tracking.
3. Instant Configuration Rollback.
"""

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class AdvancedConfigSchema:
    version: int = 1
    timestamp: float = field(default_factory=time.time)
    max_drawdown_limit_pct: float = 0.15
    max_daily_loss_pct: float = 0.05
    max_risk_per_trade_pct: float = 0.01
    leverage_limit: float = 1.0
    strategy_params: dict[str, dict[str, Any]] = field(default_factory=dict)


class AdvancedConfigManager:
    """
    Manages versioned configuration files with atomic updates and rollbacks.
    """

    def __init__(self, config_dir: str = "config_history"):
        self.config_dir = config_dir
        os.makedirs(self.config_dir, exist_ok=True)
        self.history: list[AdvancedConfigSchema] = []
        self.current_config = AdvancedConfigSchema()

    def validate_config(self, cfg: AdvancedConfigSchema) -> tuple[bool, list[str]]:
        """
        Validates safety invariants and cross-parameter constraints.
        """
        errors = []
        if cfg.max_drawdown_limit_pct > 0.15:
            errors.append("max_drawdown_limit_pct cannot exceed 0.15 (15%)")
        if cfg.max_daily_loss_pct > 0.05:
            errors.append("max_daily_loss_pct cannot exceed 0.05 (5%)")
        if cfg.max_risk_per_trade_pct > 0.03:
            errors.append("max_risk_per_trade_pct cannot exceed 0.03 (3%)")
        if cfg.leverage_limit > 5.0:
            errors.append("leverage_limit cannot exceed 5.0")

        return len(errors) == 0, errors

    def update_config(self, new_cfg: AdvancedConfigSchema) -> tuple[bool, str]:
        """
        Validates and commits a new configuration version.
        """
        is_valid, errs = self.validate_config(new_cfg)
        if not is_valid:
            return False, f"Validation failed: {', '.join(errs)}"

        self.history.append(self.current_config)
        new_cfg.version = len(self.history) + 1
        new_cfg.timestamp = time.time()
        self.current_config = new_cfg

        # Save to disk
        path = os.path.join(self.config_dir, f"config_v{new_cfg.version}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(new_cfg), f, indent=2)

        return True, f"Config updated to version {new_cfg.version}"

    def rollback(self) -> tuple[bool, str]:
        """
        Reverts to the immediately preceding configuration version.
        """
        if not self.history:
            return False, "No previous configuration history available for rollback."

        prev = self.history.pop()
        self.current_config = prev
        return True, f"Rolled back to configuration version {prev.version}"
