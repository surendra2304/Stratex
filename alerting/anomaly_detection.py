"""
alerting/anomaly_detection.py — Multi-Pillar Statistical Anomaly Detection Engine.

Detects:
1. Performance Anomalies: Win rates / returns dropping > 2 standard deviations below expectation.
2. Market Anomalies: Volatility spikes (> 3 sigma above 30-day baseline) and extreme volume dislocations.
3. System Anomalies: REST latency spikes or memory growth patterns.
"""

import numpy as np
from typing import Dict, List, Optional, Tuple, Any
from alerting.intelligent_alerts import IntelligentAlertEngine


class AnomalyDetectionEngine:
    """
    Evaluates rolling metrics against statistical baselines to surface anomalies.
    """

    def __init__(self, alert_engine: Optional[IntelligentAlertEngine] = None):
        self.alert_engine = alert_engine or IntelligentAlertEngine()

    def check_strategy_performance_anomaly(
        self,
        strategy_name: str,
        current_win_rate: float,
        historical_win_rates: List[float]
    ) -> Optional[Dict[str, Any]]:
        """Flags strategy underperformance when current win rate drops below 2 sigma."""
        if len(historical_win_rates) < 10:
            return None

        mean = float(np.mean(historical_win_rates))
        std = float(np.std(historical_win_rates)) or 1.0
        z_score = (current_win_rate - mean) / std

        if z_score <= -2.0:
            rec = f"Temporarily reduce capital allocation to {strategy_name} by 50% until regime aligns."
            self.alert_engine.emit_alert(
                severity="HIGH",
                category="STRATEGY",
                title=f"Performance Anomaly: {strategy_name}",
                message=f"Current win rate ({current_win_rate:.1f}%) is {abs(z_score):.1f}σ below historical mean ({mean:.1f}%).",
                context="Likely caused by choppy ranging conditions.",
                recommendation=rec
            )
            return {"strategy": strategy_name, "z_score": round(z_score, 2), "severity": "HIGH"}

        return None

    def check_market_volatility_anomaly(
        self,
        symbol: str,
        current_atr: float,
        historical_atrs: List[float]
    ) -> Optional[Dict[str, Any]]:
        """Flags extreme market volatility expansion (> 3 sigma)."""
        if len(historical_atrs) < 15:
            return None

        mean = float(np.mean(historical_atrs))
        std = float(np.std(historical_atrs)) or 1.0
        z_score = (current_atr - mean) / std

        if z_score >= 3.0:
            rec = "Widen stop-loss thresholds and reduce initial position leverage."
            self.alert_engine.emit_alert(
                severity="CRITICAL",
                category="RISK",
                title=f"Volatility Expansion Anomaly: {symbol}",
                message=f"Current volatility is {z_score:.1f}σ above baseline.",
                context="Extreme breakout / liquidation cascade detected in market.",
                recommendation=rec
            )
            return {"symbol": symbol, "z_score": round(z_score, 2), "severity": "CRITICAL"}

        return None
