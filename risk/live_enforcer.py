"""
risk/circuit_breakers.py — Multi-Pillar Systemic Risk Circuit Breakers.

Circuit Breakers:
1. Volatility Circuit Breaker: 24h realized volatility > 4σ from 30-day mean = halt 1 hour.
2. Correlation Breakdown Breaker: Cross-strategy correlation drops suddenly below 0.20 = reduce exposure (diversification failure).
3. Execution Quality Breaker: Realized slippage > 3x normal for 3 consecutive orders = halt and investigate.
4. API Latency Breaker: Exchange API response latency > 2.0s median = reduce order frequency / throttle.
"""

import time
import datetime
import numpy as np
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field, asdict
from logger import get_logger

logger = get_logger("circuit_breakers")


@dataclass
class CircuitBreakerStatus:
    name: str
    is_tripped: bool
    tripped_at: Optional[float] = None
    reset_at: Optional[float] = None
    reason: str = ""
    severity: str = "HIGH"  # "CRITICAL", "HIGH", "MEDIUM"


class CircuitBreakerEngine:
    """
    Evaluates market conditions, execution telemetry, and latency against systemic circuit breakers.
    """

    def __init__(self):
        self.breakers: Dict[str, CircuitBreakerStatus] = {
            "volatility": CircuitBreakerStatus(name="volatility", is_tripped=False),
            "correlation_breakdown": CircuitBreakerStatus(name="correlation_breakdown", is_tripped=False),
            "execution_quality": CircuitBreakerStatus(name="execution_quality", is_tripped=False),
            "api_latency": CircuitBreakerStatus(name="api_latency", is_tripped=False)
        }
        self.consecutive_slippage_breaches = 0
        self.recent_latencies: List[float] = []

    def check_volatility_circuit_breaker(
        self,
        current_24h_vol: float,
        historical_vols: List[float]
    ) -> bool:
        """Checks if realized volatility is > 4 sigma above baseline."""
        now = time.time()
        # Check if currently cooling down
        if self.breakers["volatility"].is_tripped:
            if now < (self.breakers["volatility"].reset_at or 0):
                return True
            else:
                self.breakers["volatility"].is_tripped = False
                logger.info("[CIRCUIT_BREAKER] 🟢 Volatility circuit breaker cooled down and reset.")

        if len(historical_vols) < 15:
            return False

        mean_vol = float(np.mean(historical_vols))
        std_vol = float(np.std(historical_vols)) or 0.01
        z_score = (current_24h_vol - mean_vol) / std_vol

        if z_score >= 4.0:
            self.breakers["volatility"].is_tripped = True
            self.breakers["volatility"].tripped_at = now
            self.breakers["volatility"].reset_at = now + 3600  # Halt for 1 hour
            self.breakers["volatility"].reason = f"24h realized vol is {z_score:.1f}σ above mean (halted 1h)"
            logger.warning(f"[CIRCUIT_BREAKER] 🚨 VOLATILITY BREAKER TRIPPED: {self.breakers['volatility'].reason}")
            return True

        return False

    def check_correlation_breakdown(self, avg_strategy_corr: float) -> bool:
        """Checks if portfolio diversification broke down (avg cross-strategy correlation < 0.20 suddenly)."""
        if avg_strategy_corr < 0.20:
            self.breakers["correlation_breakdown"].is_tripped = True
            self.breakers["correlation_breakdown"].reason = f"Cross-strategy correlation dropped to {avg_strategy_corr:.2f} (< 0.20)"
            return True
        else:
            self.breakers["correlation_breakdown"].is_tripped = False
            return False

    def record_order_execution_slippage(self, realized_slippage_bps: float, normal_slippage_bps: float = 5.0) -> bool:
        """Checks if slippage > 3x normal for 3 consecutive orders."""
        if realized_slippage_bps > (3.0 * normal_slippage_bps):
            self.consecutive_slippage_breaches += 1
            if self.consecutive_slippage_breaches >= 3:
                self.breakers["execution_quality"].is_tripped = True
                self.breakers["execution_quality"].reason = f"Excessive slippage (> {3*normal_slippage_bps} bps) for 3 consecutive orders"
                logger.critical(f"[CIRCUIT_BREAKER] 🚨 EXECUTION QUALITY BREAKER TRIPPED: {self.breakers['execution_quality'].reason}")
                return True
        else:
            self.consecutive_slippage_breaches = 0
            self.breakers["execution_quality"].is_tripped = False
        return self.breakers["execution_quality"].is_tripped

    def record_api_latency(self, latency_seconds: float) -> bool:
        """Checks if median API response time > 2.0s."""
        self.recent_latencies.append(latency_seconds)
        if len(self.recent_latencies) > 20:
            self.recent_latencies.pop(0)

        median_lat = float(np.median(self.recent_latencies)) if self.recent_latencies else 0.0
        if median_lat > 2.0:
            self.breakers["api_latency"].is_tripped = True
            self.breakers["api_latency"].reason = f"Median API latency {median_lat:.2f}s > 2.0s (reducing order frequency)"
            return True
        else:
            self.breakers["api_latency"].is_tripped = False
            return False

    def get_status_summary(self) -> Dict[str, Any]:
        """Returns snapshot of all circuit breakers."""
        any_tripped = any(b.is_tripped for b in self.breakers.values())
        return {
            "any_circuit_breaker_active": any_tripped,
            "breakers": {k: asdict(v) for k, v in self.breakers.items()}
        }


@dataclass
class LiveEnforcerStatus:
    is_halted: bool = False
    halt_reason: str = ""
    halt_until: Optional[float] = None
    requires_reauthorization: bool = False
    daily_realized_loss: float = 0.0
    current_drawdown_pct: float = 0.0
    active_positions_count: int = 0
    volatility_24h_pct: float = 0.0
    kill_switch_active: bool = False


class LiveRiskEnforcer:
    """
    Autonomous guardian strictly policing live capital allocations.
    """

    def __init__(
        self,
        level: int = 1,
        initial_capital: float = 1000.0,
        trading_window_hours: Optional[Tuple[int, int]] = None,
        max_volatility_threshold_pct: float = 8.5
    ):
        from deployment.capital_levels import get_level_spec, CapitalLevelSpec
        self.level = level
        self.spec: CapitalLevelSpec = get_level_spec(level)
        self.initial_capital = initial_capital
        self.trading_window_hours = trading_window_hours
        self.max_volatility_threshold_pct = max_volatility_threshold_pct
        self.status = LiveEnforcerStatus()
        self.correlated_pairs_map = {
            ("BTC/USDT", "ETH/USDT"): 0.88,
            ("BTC/USDT", "SOL/USDT"): 0.82,
            ("ETH/USDT", "SOL/USDT"): 0.85
        }

    def trigger_kill_switch(self, source: str = "OPERATOR_API", rationale: str = "Emergency halt requested") -> Dict[str, Any]:
        """Immediately activates kill switch and halts all live operations."""
        self.status.is_halted = True
        self.status.kill_switch_active = True
        self.status.halt_reason = f"KILL_SWITCH triggered by {source}: {rationale}"
        self.status.requires_reauthorization = True
        logger.critical(f"[LIVE_ENFORCER] 🚨 EMERGENCY KILL SWITCH TRIGGERED: {self.status.halt_reason}")
        return {
            "action": "FLATTEN_ALL",
            "halted": True,
            "reason": self.status.halt_reason,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
        }

    def evaluate_daily_loss(self, today_realized_pnl: float, current_equity: float) -> Tuple[bool, str]:
        """
        Checks if today's net loss exceeds the level max daily loss limit.
        If breached, halts trading for 24 hours and flags FLATTEN_ALL.
        """
        max_loss_dollars = self.initial_capital * self.spec.max_daily_loss_pct
        if today_realized_pnl <= -max_loss_dollars:
            self.status.is_halted = True
            self.status.halt_until = time.time() + 86400  # 24 hours
            self.status.halt_reason = f"Daily loss limit breached (${abs(today_realized_pnl):.2f} >= ${max_loss_dollars:.2f} limit: {self.spec.max_daily_loss_pct*100}%). Halted for 24h."
            logger.critical(f"[LIVE_ENFORCER] 🚨 {self.status.halt_reason}")
            return False, self.status.halt_reason
        return True, "Daily loss within tolerance"

    def evaluate_drawdown(self, peak_equity: float, current_equity: float) -> Tuple[bool, str]:
        """
        Checks if total drawdown exceeds level threshold.
        If breached, flattens all positions and requires physical re-authorization.
        """
        if peak_equity <= 0:
            return True, "Peak equity zero"
        dd_pct = max(0.0, (peak_equity - current_equity) / peak_equity) * 100.0
        self.status.current_drawdown_pct = round(dd_pct, 2)

        max_dd_pct = self.spec.max_drawdown_limit_pct * 100.0
        if dd_pct >= max_dd_pct:
            self.status.is_halted = True
            self.status.requires_reauthorization = True
            self.status.halt_reason = f"Drawdown limit breached ({dd_pct:.2f}% >= {max_dd_pct}% limit for Level {self.level}). Requires physical re-authorization."
            logger.critical(f"[LIVE_ENFORCER] 🚨 {self.status.halt_reason}")
            return False, self.status.halt_reason
        return True, "Drawdown within tolerance"

    def check_correlation_limit(self, proposed_symbol: str, current_open_symbols: List[str]) -> Tuple[bool, str]:
        """
        Ensures no more than 2 highly-correlated (> 0.85) assets are held simultaneously.
        """
        correlated_count = 0
        for sym in current_open_symbols:
            pair = (proposed_symbol, sym) if (proposed_symbol, sym) in self.correlated_pairs_map else (sym, proposed_symbol)
            corr = self.correlated_pairs_map.get(pair, 0.0)
            if corr >= 0.85:
                correlated_count += 1

        if correlated_count >= 2:
            return False, f"Correlation limit reached: {proposed_symbol} is highly correlated with {correlated_count} active positions (Max 2 allowed)."
        return True, "Correlation nominal"

    def check_volatility_circuit_breaker(self, rolling_vol_pct: float) -> Tuple[bool, str]:
        """Halts new entry orders if rolling market volatility spikes above threshold."""
        self.status.volatility_24h_pct = rolling_vol_pct
        if rolling_vol_pct > self.max_volatility_threshold_pct:
            return False, f"Volatility circuit breaker active: Realized 24h vol {rolling_vol_pct:.2f}% > {self.max_volatility_threshold_pct}% threshold."
        return True, "Volatility nominal"

    def check_time_window_restrictions(self) -> Tuple[bool, str]:
        """Checks if current UTC hour is within permitted trading window."""
        if not self.trading_window_hours:
            return True, "24/7 trading window"
        start_hr, end_hr = self.trading_window_hours
        curr_hr = datetime.datetime.utcnow().hour
        if not (start_hr <= curr_hr < end_hr):
            return False, f"Outside permitted trading window ({start_hr}:00 - {end_hr}:00 UTC. Current: {curr_hr}:00 UTC)."
        return True, "Inside permitted trading window"

    def validate_new_entry(
        self,
        symbol: str,
        notional: float,
        current_open_positions: List[Dict[str, Any]],
        current_equity: float,
        today_realized_loss: float = 0.0,
        rolling_vol_pct: float = 3.5
    ) -> Tuple[bool, str]:
        """
        Comprehensive pre-trade gate validating all live risk invariants.
        """
        # Check active halt or kill switch
        if self.status.kill_switch_active:
            return False, f"BLOCKED: Kill switch is active ({self.status.halt_reason})"

        if self.status.is_halted:
            if self.status.halt_until and time.time() >= self.status.halt_until and not self.status.requires_reauthorization:
                self.status.is_halted = False
                self.status.halt_until = None
                self.status.halt_reason = ""
            else:
                return False, f"BLOCKED: Live engine halted ({self.status.halt_reason})"

        # 1. Trading window check
        win_ok, win_msg = self.check_time_window_restrictions()
        if not win_ok:
            return False, f"BLOCKED: {win_msg}"

        # 2. Max Strategy / Position count check for this level
        max_pos_allowed = max(1, self.spec.max_strategies * 2)
        if len(current_open_positions) >= max_pos_allowed:
            return False, f"BLOCKED: Max position count ({len(current_open_positions)} >= {max_pos_allowed}) for Level {self.level} reached."

        # 3. Position Size Limit (% of capital)
        max_notional_allowed = current_equity * self.spec.max_position_size_pct
        if notional > (max_notional_allowed * 1.05):  # 5% buffer
            return False, f"BLOCKED: Order notional ${notional:.2f} exceeds Level {self.level} max position cap ${max_notional_allowed:.2f} ({self.spec.max_position_size_pct*100}%)."

        # 4. Correlation check
        open_syms = [p.get("symbol", "") for p in current_open_positions]
        corr_ok, corr_msg = self.check_correlation_limit(symbol, open_syms)
        if not corr_ok:
            return False, f"BLOCKED: {corr_msg}"

        # 5. Volatility check
        vol_ok, vol_msg = self.check_volatility_circuit_breaker(rolling_vol_pct)
        if not vol_ok:
            return False, f"BLOCKED: {vol_msg}"

        return True, "All live risk gates passed"

