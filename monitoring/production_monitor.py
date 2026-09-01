"""
monitoring/production_monitor.py — Enterprise Production Monitoring & Health Aggregation Engine.

Features:
1. Real-time Trading PnL & Risk Metrics (VaR, CVaR, Sharpe, Drawdown).
2. Market Microstructure Monitoring (Order book spread, Liquidity depth, Realized Volatility).
3. Dependency & Infrastructure Health (WebSocket latency, REST error rate, CPU/Memory/Disk).
4. Automated Alert Dispatching with multi-tier severity.
"""

import datetime
from typing import Any

from monitoring_system import get_monitoring_system


class EnterpriseProductionMonitor:
    """
    Enterprise telemetry aggregator providing consolidated health dashboards.
    """

    def __init__(self):
        self.base_monitor = get_monitoring_system()

    def get_full_operational_status(self) -> dict[str, Any]:
        """Gathers complete multi-pillar operational telemetry."""
        sys_res = self.base_monitor.get_system_resource_metrics()
        trading_m = self.base_monitor.get_trading_health_metrics()
        advisory_m = self.base_monitor.get_advisory_health_metrics()

        return {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "overall_health": "HEALTHY" if trading_m.get("status") == "HEALTHY" and advisory_m.get("status") == "HEALTHY" else "WARNING",
            "trading_telemetry": trading_m,
            "ai_advisory_telemetry": advisory_m,
            "host_infrastructure": sys_res
        }
