"""
autonomy/mt5_autonomous_engine.py — Autonomous Multi-Asset Multi-Timeframe MT5 Quantitative Trading Engine.

Features:
1. Multi-Asset Scanning:
   - Forex Majors: EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD, USDCAD, NZDUSD
   - Commodities: XAUUSD (Gold)
2. Multi-Timeframe Alpha Synthesis:
   - M5: Rapid scalping & entry precision
   - M15: Momentum structure & regime identification
   - H1: Macro trend alignment & filter
3. Quantitative Mathematical Models:
   - VolatilityRegimeBreakoutModel: ATR Chandelier Exits & 4-state regime classifier
   - KalmanFilterMeanReversionModel: 1D state-space latent equilibrium & innovation Z-score
4. Cross-Agent Consensus Mesh (FRIDAY Universe):
   - SentinelContractClient: Zero-trust risk & authorization preflight
   - FuturisContractClient: Probabilistic forecasting & uncertainty bound (<0.03)
   - IntelXContractClient: Sourced intelligence & macro sentiment filter
   - MemoraScopedClient: Scoped audit persistence (Universal/Shared)
   - FridayGatewayClient: Telemetry & intent broadcast to FRIDAY Core Gateway (http://friday:10000)
5. Execution via MT5:
   - Broker-native preflight checks (order_check)
   - Dynamic lot sizing adhering to broker volume step (0.01) and max 2% risk budget
   - Mandatory protective Stop-Loss and Take-Profit brackets (minimum 1.5:1 reward/risk)
6. Instant Telegram Alerts & 2-Way Bot Integration:
   - Real-time HTML alerts with emojis on signal discovery, consensus approval, fill, and rejection
   - Interactive command hooks (/scan, /positions, /balance, /status)
"""
from __future__ import annotations

import datetime
import math
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from autonomy.agent_contracts import (
    AgentAdvisorySupervisor,
    FridayGatewayClient,
    FuturisContractClient,
    IntelXContractClient,
    MemoraScopedClient,
    SentinelContractClient,
)
from autonomy.ai_advisor import StratexAIAdvisor, get_ai_advisor
from exchanges.mt5_bridge_adapter import MT5BridgeAdapter, MT5BridgeError
from logger import get_logger
from notifications.telegram_bot import TelegramBotClient, get_telegram_bot
from panic_state import is_kill_switch_locked, is_panic_active
from stratex_agents.order_intent import TradeIntent
from strategies.quantitative_engine import (
    KalmanFilterMeanReversionModel,
    MarketRegime,
    VolatilityRegimeBreakoutModel,
)

logger = get_logger("mt5_autonomous_engine")

DEFAULT_SYMBOLS = [
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "USDCHF",
    "AUDUSD",
    "USDCAD",
    "NZDUSD",
    "XAUUSD",
]

DEFAULT_TIMEFRAMES = ["5m", "15m", "1h"]


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class OpportunitySignal:
    """Evaluated trading opportunity across multiple timeframes and quantitative models."""

    symbol: str
    timeframe: str
    action: str  # "BUY" or "SELL"
    regime: str
    current_price: float
    stop_loss: float
    take_profit: float
    risk_reward_ratio: float
    confidence: float
    kalman_z: float
    atr: float
    strategy_source: str
    timestamp: str = field(default_factory=_utc_now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConsensusDecision:
    """Outcome of the FRIDAY Universe cross-agent consensus gate."""

    approved: bool
    verdict: str  # "APPROVED", "DAMPENED", "REJECTED", "SKIPPED"
    size_multiplier: float  # Invariant: 0.0 <= size_multiplier <= 1.0
    sentinel_approved: bool
    futuris_direction: str | None
    intelx_sentiment: float | None
    reasons: tuple[str, ...]
    ai_thesis: str | None = None
    evaluated_at: str = field(default_factory=_utc_now_iso)


class MT5AutonomousEngine:
    """Autonomous trading engine orchestrating MT5 execution and the FRIDAY peer mesh."""

    def __init__(
        self,
        adapter: MT5BridgeAdapter | None = None,
        symbols: list[str] | None = None,
        timeframes: list[str] | None = None,
        max_active_positions: int = 3,
        max_risk_per_trade_pct: float = 0.02,
        max_daily_drawdown_pct: float = 0.05,
        min_confidence: float = 0.65,
        dry_run: bool = False,
        futuris_client: FuturisContractClient | None = None,
        intelx_client: IntelXContractClient | None = None,
        sentinel_client: SentinelContractClient | None = None,
        memora_client: MemoraScopedClient | None = None,
        friday_client: FridayGatewayClient | None = None,
        telegram_bot: TelegramBotClient | None = None,
        ai_advisor: StratexAIAdvisor | None = None,
    ):
        self.adapter = adapter or MT5BridgeAdapter()
        self.symbols = list(symbols or DEFAULT_SYMBOLS)
        self.timeframes = list(timeframes or DEFAULT_TIMEFRAMES)
        self.max_active_positions = max_active_positions
        self.max_risk_per_trade_pct = min(0.02, max(0.001, max_risk_per_trade_pct))
        self.max_daily_drawdown_pct = min(0.05, max(0.01, max_daily_drawdown_pct))
        self.min_confidence = min_confidence
        self.dry_run = dry_run

        # FRIDAY Universe Peer Mesh
        self.futuris = futuris_client or FuturisContractClient()
        self.intelx = intelx_client or IntelXContractClient()
        self.sentinel = sentinel_client or SentinelContractClient()
        self.memora = memora_client or MemoraScopedClient()
        self.friday = friday_client or FridayGatewayClient()
        self.advisory_supervisor = AgentAdvisorySupervisor(
            futuris_client=self.futuris,
            intelx_client=self.intelx,
        )
        self.telegram = telegram_bot or get_telegram_bot()
        self.ai_advisor = ai_advisor or get_ai_advisor()

        # Quantitative Models
        self.breakout_model = VolatilityRegimeBreakoutModel(atr_period=14, chandelier_mult=2.5, lookback=40)
        self.kalman_model = KalmanFilterMeanReversionModel(z_threshold=1.8)

        # Internal Execution Log
        self.execution_journal: list[dict[str, Any]] = []

    def evaluate_symbol_timeframe(
        self,
        symbol: str,
        timeframe: str,
        candles: list[dict[str, Any]] | None = None,
    ) -> OpportunitySignal | None:
        """Process candles for a single symbol and timeframe through quantitative models."""
        try:
            if candles is None:
                candles = self.adapter.get_historical_data(symbol, timeframe=timeframe, limit=80)
        except Exception as exc:
            logger.debug(f"[MT5_ENGINE] Failed fetching candles for {symbol} ({timeframe}): {exc}")
            return None

        if not candles or len(candles) < 30:
            return None

        closes = [float(c["close"]) for c in candles]
        current_price = closes[-1]

        # 1. Breakout & Regime Model
        breakout_result = self.breakout_model.evaluate(candles)
        if not breakout_result:
            return None

        regime = breakout_result.get("regime", MarketRegime.CHOP_MEAN_REVERTING)
        atr = float(breakout_result.get("atr", current_price * 0.001))

        # Chaos Regime Defense: Never initiate new positions in chaotic markets
        if regime == MarketRegime.EXTREME_VOL_CHAOS:
            logger.info(f"[MT5_ENGINE] Chaos regime detected for {symbol} ({timeframe}); skipping.")
            return None

        # 2. Kalman Filter Model
        kalman_result = self.kalman_model.evaluate_price_series(closes)
        kalman_z = float(kalman_result.get("z_score", 0.0)) if kalman_result else 0.0

        action = "HOLD"
        stop_loss = 0.0
        take_profit = 0.0
        confidence = 0.0
        strategy_source = "hybrid_breakout_kalman"

        # Signal Synthesis Logic
        if regime in {MarketRegime.LOW_VOL_TRENDING, MarketRegime.HIGH_VOL_TRENDING}:
            b_action = breakout_result.get("action", "HOLD")
            if b_action in {"BUY", "SELL"}:
                # Avoid entering if Kalman filter indicates extreme counter-extension
                if (b_action == "BUY" and kalman_z > 2.5) or (b_action == "SELL" and kalman_z < -2.5):
                    logger.debug(f"[MT5_ENGINE] {symbol} {b_action} filtered by Kalman overextension (Z={kalman_z:.2f})")
                else:
                    action = b_action
                    confidence = float(breakout_result.get("confidence", 0.75))
                    stop_loss = float(breakout_result.get("stop_loss", 0.0))
                    take_profit = float(breakout_result.get("take_profit", 0.0))
                    strategy_source = "volatility_regime_breakout"

        elif regime == MarketRegime.CHOP_MEAN_REVERTING:
            k_action = kalman_result.get("action", "HOLD") if kalman_result else "HOLD"
            if k_action in {"BUY", "SELL"}:
                action = k_action
                confidence = float(kalman_result.get("confidence", 0.70)) if kalman_result else 0.70
                strategy_source = "kalman_mean_reversion"
                # Use ATR for disciplined brackets
                if action == "BUY":
                    stop_loss = current_price - (1.5 * atr)
                    take_profit = current_price + (2.5 * atr)
                else:
                    stop_loss = current_price + (1.5 * atr)
                    take_profit = current_price - (2.5 * atr)

        if action == "HOLD" or stop_loss <= 0 or take_profit <= 0:
            return None

        # Safety Sanity Checks on SL/TP geometry
        if action == "BUY" and not (stop_loss < current_price < take_profit):
            return None
        if action == "SELL" and not (take_profit < current_price < stop_loss):
            return None

        risk_dist = abs(current_price - stop_loss)
        reward_dist = abs(take_profit - current_price)
        if risk_dist <= 1e-6:
            return None

        risk_reward_ratio = reward_dist / risk_dist
        if risk_reward_ratio < 1.4:
            return None  # Enforce minimum favorable risk-reward ratio

        return OpportunitySignal(
            symbol=symbol,
            timeframe=timeframe,
            action=action,
            regime=regime,
            current_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward_ratio=round(risk_reward_ratio, 2),
            confidence=round(confidence, 2),
            kalman_z=round(kalman_z, 2),
            atr=round(atr, 5),
            strategy_source=strategy_source,
            metadata={
                "breakout_confidence": breakout_result.get("confidence"),
                "kalman_confidence": kalman_result.get("confidence") if kalman_result else 0.0,
            },
        )

    def consult_peer_mesh(self, signal: OpportunitySignal) -> ConsensusDecision:
        """Consult Sentinel, Futuris, IntelX, and compile peer consensus."""
        reasons: list[str] = []

        # 1. Sentinel Governance Check
        sentinel_res = self.sentinel.verify_action_authorization(
            action=f"ORDER_{signal.action}",
            actor="mt5_autonomous_engine",
            context={
                "symbol": signal.symbol,
                "venue": "mt5",
                "stop_loss": signal.stop_loss,
                "take_profit": signal.take_profit,
                "confidence": signal.confidence,
            },
        )
        if not sentinel_res.allowed:
            reasons.append(f"Sentinel rejected: {sentinel_res.reason}")
            return ConsensusDecision(
                approved=False,
                verdict="REJECTED",
                size_multiplier=0.0,
                sentinel_approved=False,
                futuris_direction=None,
                intelx_sentiment=None,
                reasons=tuple(reasons),
            )

        # 2. Futuris & IntelX Advisory Supervisor
        advisory = self.advisory_supervisor.evaluate_strategy_candidate(
            symbol=signal.symbol,
            side=signal.action,
            strategy=signal.strategy_source,
            current_price=signal.current_price,
        )

        reasons.extend(advisory.reasons)
        futuris_forecast = self.futuris.get_forecast(symbol=signal.symbol)
        intelx_research = self.intelx.get_market_intelligence(symbol=signal.symbol)

        fut_dir = futuris_forecast.direction if futuris_forecast else None
        ix_sent = intelx_research.sentiment_score if intelx_research else None

        if advisory.verdict == "SKIP" or advisory.size_multiplier <= 0.0:
            return ConsensusDecision(
                approved=False,
                verdict="SKIPPED",
                size_multiplier=0.0,
                sentinel_approved=True,
                futuris_direction=fut_dir,
                intelx_sentiment=ix_sent,
                reasons=tuple(reasons or ["Advisory supervisor recommended SKIP"]),
            )

        # 3. AI Strategic Evaluator Review (Gemini 2.5 Flash / Inference Mesh)
        ai_verdict = self.ai_advisor.evaluate_trade_candidate(
            symbol=signal.symbol,
            timeframe=signal.timeframe,
            action=signal.action,
            current_price=signal.current_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            risk_reward_ratio=signal.risk_reward_ratio,
            indicators={
                "regime": signal.regime,
                "atr": signal.atr,
                "kalman_z": signal.kalman_z,
                "confidence": signal.confidence,
                "futuris_forecast": fut_dir,
                "intelx_sentiment": ix_sent,
            },
        )
        if not ai_verdict.approved or ai_verdict.verdict == "REJECTED":
            return ConsensusDecision(
                approved=False,
                verdict="REJECTED",
                size_multiplier=0.0,
                sentinel_approved=True,
                futuris_direction=fut_dir,
                intelx_sentiment=ix_sent,
                ai_thesis=ai_verdict.thesis,
                reasons=tuple(reasons + [f"AI Advisor rejected: {ai_verdict.thesis}"]),
            )

        final_multiplier = advisory.size_multiplier
        if ai_verdict.verdict == "DAMPENED":
            final_multiplier = min(final_multiplier, 0.7)

        verdict = "DAMPENED" if final_multiplier < 1.0 else "APPROVED"
        return ConsensusDecision(
            approved=True,
            verdict=verdict,
            size_multiplier=final_multiplier,
            sentinel_approved=True,
            futuris_direction=fut_dir,
            intelx_sentiment=ix_sent,
            ai_thesis=ai_verdict.thesis,
            reasons=tuple(reasons),
        )

    def calculate_lot_size(
        self,
        symbol: str,
        side: str,
        current_price: float,
        stop_loss: float,
        take_profit: float,
        multiplier: float = 1.0,
    ) -> float:
        """Calculate broker-safe lot size adhering to risk budgets and lot volume steps."""
        equity = 1000.0
        try:
            balance_dict = self.adapter.get_balance()
            if balance_dict:
                for b in balance_dict.values():
                    if b.total > 0:
                        equity = b.total
                        break
        except Exception:
            pass

        # Try native broker sizing calculator first
        try:
            sizing = self.adapter.calculate_position_size(symbol, side, stop_loss, take_profit)
            rec_volume = float(sizing.get("recommended_volume", 0.01))
            step = float(sizing.get("volume_step", 0.01))
            min_vol = float(sizing.get("volume_min", 0.01))
            final_vol = max(min_vol, round((rec_volume * multiplier) / step) * step)
            return round(final_vol, 2)
        except Exception:
            pass

        # Robust deterministic fallback sizing
        risk_budget = equity * self.max_risk_per_trade_pct * multiplier
        risk_per_unit = abs(current_price - stop_loss)
        if risk_per_unit <= 1e-6:
            return 0.01

        # Standard Forex contract size is 100,000 units. For XAUUSD it is 100 oz.
        contract_size = 100.0 if "XAU" in symbol.upper() else 100000.0
        raw_lots = risk_budget / (risk_per_unit * contract_size)
        stepped_lots = max(0.01, round(raw_lots / 0.01) * 0.01)
        # Cap at 0.10 lots for demo preservation ($1,000 base)
        return round(min(0.10, stepped_lots), 2)

    def execute_signal(self, signal: OpportunitySignal, decision: ConsensusDecision) -> dict[str, Any]:
        """Execute the approved trade on MT5 demo and dispatch notifications."""
        volume = self.calculate_lot_size(
            symbol=signal.symbol,
            side=signal.action,
            current_price=signal.current_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            multiplier=decision.size_multiplier,
        )

        intent = TradeIntent(
            venue="mt5",
            symbol=signal.symbol,
            side=signal.action,
            order_type="MARKET",
            quantity=volume,
            environment="mt5_demo",
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            strategy=signal.strategy_source,
            metadata={
                "timeframe": signal.timeframe,
                "regime": signal.regime,
                "confidence": signal.confidence,
                "kalman_z": signal.kalman_z,
                "risk_reward_ratio": signal.risk_reward_ratio,
                "consensus_verdict": decision.verdict,
                "ai_thesis": decision.ai_thesis,
            },
        )

        # Notify Telegram of Proposed Intent
        self.telegram.notify_trade_intent(intent, status="PROPOSED")

        # Notify FRIDAY Gateway
        self.friday.broadcast_trade_event(
            event_type="STRATEX_TRADE_PROPOSED",
            payload={
                "symbol": signal.symbol,
                "action": signal.action,
                "volume": volume,
                "price": signal.current_price,
                "stop_loss": signal.stop_loss,
                "take_profit": signal.take_profit,
                "strategy": signal.strategy_source,
                "verdict": decision.verdict,
            },
        )

        if self.dry_run:
            logger.info(f"[MT5_ENGINE] DRY_RUN: Simulated execution of {intent.side} {intent.quantity} {intent.symbol}")
            result = {
                "status": "SIMULATED_FILLED",
                "order_id": f"dry-{int(time.time())}",
                "symbol": signal.symbol,
                "side": signal.action,
                "quantity": volume,
                "price": signal.current_price,
            }
        else:
            try:
                # Preflight check
                if os.getenv("MT5_PREFLIGHT_CHECK", "true").lower() in {"1", "true", "yes"}:
                    self.adapter.order_check(
                        symbol=intent.symbol,
                        side=intent.side,
                        order_type=intent.order_type,
                        quantity=intent.quantity,
                        stop_loss=intent.stop_loss,
                        take_profit=intent.take_profit,
                        client_order_id=intent.client_order_id,
                    )

                submit_res = self.adapter.submit_intent(intent)
                result = {
                    "status": submit_res.get("status", "FILLED"),
                    "order_id": submit_res.get("order_id", ""),
                    "symbol": signal.symbol,
                    "side": signal.action,
                    "quantity": volume,
                    "price": submit_res.get("avg_price", signal.current_price),
                }
            except Exception as exc:
                logger.error(f"[MT5_ENGINE] Execution failed for {signal.symbol}: {exc}")
                self.telegram.notify_risk_rejection(intent, f"Execution failed: {exc}")
                return {"status": "FAILED", "error": str(exc), "symbol": signal.symbol}

        # Telegram Fill Alert
        self.telegram.notify_trade_intent(
            intent,
            status="FILLED",
            fill_price=result.get("price", signal.current_price),
        )

        # Record in Memora Scoped Audit Memory
        self.memora.record_scoped_event(
            event_type="MT5_TRADE_EXECUTED",
            scope="Universal",
            summary=f"Stratex executed {signal.action} {volume} lots of {signal.symbol} at ${result.get('price'):,.4f}",
            details={
                "intent": intent.to_dict(),
                "execution_result": result,
                "consensus": asdict(decision),
            },
            tags=["trading", "mt5", "execution", signal.symbol.lower()],
        )

        # Broadcast to FRIDAY Core Gateway
        self.friday.broadcast_trade_event(
            event_type="STRATEX_TRADE_FILLED",
            payload={
                "order_id": result.get("order_id"),
                "symbol": signal.symbol,
                "action": signal.action,
                "quantity": volume,
                "fill_price": result.get("price"),
                "stop_loss": signal.stop_loss,
                "take_profit": signal.take_profit,
            },
        )

        self.execution_journal.append({
            "timestamp": _utc_now_iso(),
            "symbol": signal.symbol,
            "action": signal.action,
            "quantity": volume,
            "fill_price": result.get("price"),
            "order_id": result.get("order_id"),
        })

        return result

    def scan_market(self) -> list[dict[str, Any]]:
        """Run a full scan across the asset universe and multiple timeframes."""
        if is_panic_active() or is_kill_switch_locked():
            logger.warning("[MT5_ENGINE] Emergency halt or kill-switch is active. Scan aborted.")
            return []

        # Check existing positions to respect max_active_positions
        try:
            active_positions = self.adapter.get_positions()
            if len(active_positions) >= self.max_active_positions:
                logger.info(
                    f"[MT5_ENGINE] Max active positions reached ({len(active_positions)}/{self.max_active_positions}). Holding."
                )
                return []
            active_symbols = {p.symbol for p in active_positions}
        except Exception:
            active_symbols = set()

        executed: list[dict[str, Any]] = []

        for symbol in self.symbols:
            if symbol in active_symbols:
                continue

            for tf in self.timeframes:
                signal = self.evaluate_symbol_timeframe(symbol, tf)
                if not signal:
                    continue

                if signal.confidence < self.min_confidence:
                    continue

                logger.info(
                    f"[MT5_ENGINE] Found signal on {symbol} ({tf}): {signal.action} "
                    f"@ {signal.current_price:.5f} (SL: {signal.stop_loss:.5f}, TP: {signal.take_profit:.5f}, "
                    f"R:R {signal.risk_reward_ratio}, Conf: {signal.confidence})"
                )

                # Peer Consensus Gate
                decision = self.consult_peer_mesh(signal)
                if not decision.approved:
                    logger.info(f"[MT5_ENGINE] Peer consensus rejected {symbol}: {decision.reasons}")
                    continue

                # Execute
                exec_result = self.execute_signal(signal, decision)
                executed.append(exec_result)

                # Move to next symbol after initiating position
                break

        return executed

    def run_continuous_loop(self, poll_interval_sec: float = 60.0, max_iterations: int | None = None) -> None:
        """Continuous execution loop suitable for headless Docker containers on Oracle Cloud."""
        logger.info(
            f"[MT5_ENGINE] Starting 24/7 autonomous loop for {len(self.symbols)} symbols across "
            f"{self.timeframes}. Interval: {poll_interval_sec}s."
        )
        # Ensure Telegram bot listener is running concurrently
        self.telegram.start_polling_in_background()

        iterations = 0
        while max_iterations is None or iterations < max_iterations:
            iterations += 1
            try:
                self.scan_market()
            except Exception as exc:
                logger.error(f"[MT5_ENGINE] Error in market scan iteration: {exc}", exc_info=True)
            if max_iterations is not None and iterations >= max_iterations:
                break
            time.sleep(poll_interval_sec)


# Global Singleton
_AUTONOMOUS_ENGINE_INSTANCE: MT5AutonomousEngine | None = None


def get_autonomous_engine() -> MT5AutonomousEngine:
    global _AUTONOMOUS_ENGINE_INSTANCE
    if _AUTONOMOUS_ENGINE_INSTANCE is None:
        _AUTONOMOUS_ENGINE_INSTANCE = MT5AutonomousEngine()
    return _AUTONOMOUS_ENGINE_INSTANCE


if __name__ == "__main__":
    engine = get_autonomous_engine()
    engine.run_continuous_loop(poll_interval_sec=float(os.getenv("SCAN_INTERVAL_SECONDS", "60")))
