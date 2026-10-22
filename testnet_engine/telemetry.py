"""
Telemetry module bridge and exports.
"""

from testnet_engine.telemetry_manager import (
    TelemetryManager,
    get_telemetry_manager,
    validate_balance_event,
    validate_equity_snapshot,
    validate_execution_event,
    validate_position_event,
    validate_signal_event,
    validate_trade_event,
)

__all__ = [
    "TelemetryManager",
    "get_telemetry_manager",
    "validate_balance_event",
    "validate_equity_snapshot",
    "validate_execution_event",
    "validate_position_event",
    "validate_signal_event",
    "validate_trade_event"
]
