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
import json
import math
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from autonomy.agent_contracts import (
    AgentAdvisorySupervisor,
    FridayGatewayClient,
    FuturisContractClient,
    IntelXContractClient,
    MemoraScopedClient,
    SentinelContractClient,
)
from autonomy.ai_advisor import StratexAIAdvisor, get_ai_advisor
from autonomy.cloud_data_feed import CloudMarketDataFeed, get_cloud_data_feed
from exchanges.ctrader_fix_adapter import CTraderFixAdapter
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
    # Forex Majors
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "USDCHF",
    "AUDUSD",
    "USDCAD",
    "NZDUSD",
    # Euro Crosses
    "EURGBP",
    "EURJPY",
    "EURAUD",
    "EURCAD",
    "EURCHF",
    "EURNZD",
    # Pound Crosses
    "GBPJPY",
    "GBPAUD",
    "GBPCAD",
    "GBPCHF",
    "GBPNZD",
    # Yen Crosses
    "AUDJPY",
    "CADJPY",
    "CHFJPY",
    "NZDJPY",
    # Commodity Pairs
    "AUDCAD",
    "AUDCHF",
    "AUDNZD",
    "CADCHF",
    "NZDCAD",
    "NZDCHF",
    # Commodities & Precious Metals (Micro-lots)
    "XAUUSD",
    "XAGUSD",
    # Global Indices (0.1 contract micro units)
    "US500",
    "USTEC",
    "GER40",
    "US30",
    # Crypto CFDs (0.01 micro units)
    "BTCUSD",
    "ETHUSD",
    "SOLUSD",
]

DEFAULT_TIMEFRAMES = ["5m", "15m", "1h"]


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _get_contract_multiplier(symbol: str) -> float:
    """Returns pip / point contract valuation multiplier for risk and PnL calculation."""
    sym = symbol.upper().replace("/", "")
    if "XAU" in sym or "GOLD" in sym:
        return 100.0  # 100 oz per standard lot
    if "XAG" in sym or "SILVER" in sym:
        return 5000.0  # 5,000 oz
    if "WTI" in sym or "BRENT" in sym or "OIL" in sym:
        return 1000.0  # 1,000 barrels
    if any(k in sym for k in ["BTC", "ETH", "SOL", "US30", "US500", "USTEC", "GER40"]):
        return 1.0
    if "JPY" in sym:
        return 1000.0  # Scaled for 2-decimal JPY currency
    return 100000.0  # Standard Forex 100,000 units


@dataclass
class CloudPosition:
    """Active open position managed autonomously in the cloud without client dependencies."""

    position_id: str
    symbol: str
    side: str  # "BUY" or "SELL"
    quantity: float
    entry_price: float
    stop_loss: float
    take_profit: float
    mark_price: float
    unrealized_pnl: float
    opened_at: str
    strategy: str
    ai_thesis: str | None = None


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
        cloud_feed: CloudMarketDataFeed | None = None,
        initial_balance: float = 1000.0,
    ):
        if adapter is not None:
            self.adapter = adapter
        elif os.getenv("CTRADER_FIX_ENABLED", "true").lower() in {"1", "true", "yes"}:
            self.adapter = CTraderFixAdapter()
        else:
            self.adapter = MT5BridgeAdapter()
        self.symbols = list(symbols or DEFAULT_SYMBOLS)
        self.timeframes = list(timeframes or DEFAULT_TIMEFRAMES)
        self.max_active_positions = int(os.getenv("STRATEX_MAX_POSITIONS", "3"))
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
        self.cloud_feed = cloud_feed or get_cloud_data_feed()

        # Zero-Laptop Autonomous Cloud Portfolio Tracking
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.state_file = os.path.join(repo_root, "state", "cloud_portfolio_state.json")
        self.cloud_positions: dict[str, CloudPosition] = {}
        self.cloud_balance: float = float(os.getenv("STRATEX_INITIAL_BALANCE", str(initial_balance)))
        self._load_cloud_state()

        # Quantitative Models
        self.breakout_model = VolatilityRegimeBreakoutModel(atr_period=14, chandelier_mult=2.5, lookback=40)
        self.kalman_model = KalmanFilterMeanReversionModel(z_threshold=1.8)

        # Internal Execution Log
        self.execution_journal: list[dict[str, Any]] = []

    def _load_cloud_state(self) -> None:
        """Load persisted positions and balance from disk."""
        if not os.path.exists(self.state_file):
            return
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.cloud_balance = float(data.get("balance", self.cloud_balance))
            saved_positions = data.get("positions", {})
            for sym, p_data in saved_positions.items():
                self.cloud_positions[sym] = CloudPosition(
                    position_id=p_data.get("position_id", ""),
                    symbol=p_data.get("symbol", sym),
                    side=p_data.get("side", "BUY"),
                    quantity=float(p_data.get("quantity", 0.01)),
                    entry_price=float(p_data.get("entry_price", 0.0)),
                    stop_loss=float(p_data.get("stop_loss", 0.0)),
                    take_profit=float(p_data.get("take_profit", 0.0)),
                    mark_price=float(p_data.get("mark_price", 0.0)),
                    unrealized_pnl=float(p_data.get("unrealized_pnl", 0.0)),
                    opened_at=p_data.get("opened_at", _utc_now_iso()),
                    strategy=p_data.get("strategy", ""),
                    ai_thesis=p_data.get("ai_thesis"),
                )
            logger.info(f"[MT5_ENGINE] Loaded {len(self.cloud_positions)} cloud positions from state file (Balance: ${self.cloud_balance:,.2f})")
        except Exception as exc:
            logger.warning(f"[MT5_ENGINE] Could not load state from {self.state_file}: {exc}")

    def _save_cloud_state(self) -> None:
        """Persist positions and balance to disk for zero-laptop resilience and cross-process access."""
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            data = {
                "balance": self.cloud_balance,
                "updated_at": _utc_now_iso(),
                "positions": {
                    sym: asdict(pos) for sym, pos in self.cloud_positions.items()
                },
            }
            tmp_file = f"{self.state_file}.tmp"
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_file, self.state_file)
        except Exception as exc:
            logger.warning(f"[MT5_ENGINE] Could not save state to {self.state_file}: {exc}")

    def evaluate_symbol_timeframe(
        self,
        symbol: str,
        timeframe: str,
        candles: list[dict[str, Any]] | None = None,
    ) -> OpportunitySignal | None:
        """Process candles for a single symbol and timeframe through quantitative models."""
        if candles is None:
            # 1. Try adapter if enabled and connected
            if getattr(self.adapter, "enabled", False):
                try:
                    candles = self.adapter.get_historical_data(symbol, timeframe=timeframe, limit=80)
                except Exception as exc:
                    logger.debug(f"[MT5_ENGINE] Bridge failed fetching candles for {symbol} ({timeframe}): {exc}")
                    candles = None

            # 2. Resilient Zero-Laptop Cloud Market Feed
            if not candles:
                try:
                    candles = self.cloud_feed.get_candles(symbol, timeframe=timeframe, limit=80)
                except Exception as exc:
                    logger.debug(f"[MT5_ENGINE] Cloud feed failed fetching candles for {symbol} ({timeframe}): {exc}")
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

        # Robust deterministic fallback sizing (strictly micro-lots for $1,000 base)
        risk_budget = equity * self.max_risk_per_trade_pct * multiplier
        risk_per_unit = abs(current_price - stop_loss)
        if risk_per_unit <= 1e-6:
            return 0.01

        # Standard Forex contract size is 100,000 units. For XAUUSD it is 100 oz.
        contract_size = 100.0 if "XAU" in symbol.upper() else 100000.0
        raw_lots = risk_budget / (risk_per_unit * contract_size)
        stepped_lots = max(0.01, round(raw_lots / 0.01) * 0.01)
        # Cap strictly at 0.01 lots for capital preservation on $1,000 account
        return round(min(0.01, stepped_lots), 2)

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
            executed_on_bridge = False
            if getattr(self.adapter, "enabled", False):
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
                        "order_id": str(submit_res.get("order_id", f"mt5-{int(time.time())}")),
                        "symbol": signal.symbol,
                        "side": signal.action,
                        "quantity": volume,
                        "price": float(submit_res.get("avg_price", signal.current_price)),
                    }
                    executed_on_bridge = True
                except Exception as exc:
                    logger.error(f"[MT5_ENGINE] Execution failed for {signal.symbol}: {exc}")
                    self.telegram.notify_risk_rejection(intent, f"Execution failed: {exc}")
                    return {"status": "FAILED", "error": str(exc), "symbol": signal.symbol}

            if not executed_on_bridge:
                logger.warning(
                    f"[MT5_ENGINE] Bridge offline or execution disabled. Rejecting {signal.action} {signal.symbol} "
                    f"to prevent phantom trades."
                )
                self.telegram.notify_risk_rejection(
                    intent, "Execution rejected: MT5 Bridge offline (orders must fill on IC Markets demo)"
                )
                return {
                    "status": "REJECTED_NO_BRIDGE",
                    "error": "MT5 Bridge not connected to live terminal",
                    "symbol": signal.symbol,
                }

        # Register position into tracked cloud portfolio
        pos_id = str(result.get("order_id") or f"pos-{int(time.time())}")
        fill_px = float(result.get("price", signal.current_price))
        self.cloud_positions[signal.symbol] = CloudPosition(
            position_id=pos_id,
            symbol=signal.symbol,
            side=signal.action,
            quantity=volume,
            entry_price=fill_px,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            mark_price=fill_px,
            unrealized_pnl=0.0,
            opened_at=_utc_now_iso(),
            strategy=signal.strategy_source,
            ai_thesis=decision.ai_thesis,
        )
        self._save_cloud_state()

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

    def manage_cloud_positions(self) -> list[dict[str, Any]]:
        """Monitor active cloud positions, update unrealized PnL, and execute Take-Profit / Stop-Loss exits via broker."""
        closed_events: list[dict[str, Any]] = []

        # Synchronize with live broker positions if adapter is connected
        if getattr(self.adapter, "enabled", False):
            try:
                broker_positions = self.adapter.get_positions()
                broker_pos_map = {bp.symbol: bp for bp in broker_positions}
                # Add any broker position not yet tracked
                for sym, bp in broker_pos_map.items():
                    if sym not in self.cloud_positions:
                        direction = 1.0 if bp.side.upper() in {"BUY", "LONG"} else -1.0
                        self.cloud_positions[sym] = CloudPosition(
                            position_id=bp.position_id or f"brk-{int(time.time())}",
                            symbol=sym,
                            side="BUY" if direction > 0 else "SELL",
                            quantity=bp.quantity,
                            entry_price=bp.entry_price,
                            stop_loss=bp.entry_price * (0.985 if direction > 0 else 1.015),
                            take_profit=bp.entry_price * (1.025 if direction > 0 else 0.975),
                            mark_price=bp.entry_price,
                            unrealized_pnl=0.0,
                            opened_at=_utc_now_iso(),
                            strategy="broker_synced",
                        )
                # Purge cloud positions that were already closed on the broker
                for sym in list(self.cloud_positions.keys()):
                    if sym not in broker_pos_map:
                        self.cloud_positions.pop(sym, None)
                self._save_cloud_state()
            except Exception as exc:
                logger.warning(f"[MT5_ENGINE] Broker position reconciliation: {exc}")

        if not self.cloud_positions:
            return closed_events

        symbols_to_remove: list[str] = []
        mult_map = {"BUY": 1.0, "SELL": -1.0}

        for symbol, pos in list(self.cloud_positions.items()):
            current_price = self.cloud_feed.get_current_price(symbol)
            if current_price is None or current_price <= 0:
                continue

            pos.mark_price = current_price
            contract_multiplier = _get_contract_multiplier(symbol)
            direction = mult_map.get(pos.side, 1.0)
            pnl = (current_price - pos.entry_price) * direction * pos.quantity * contract_multiplier
            pos.unrealized_pnl = round(pnl, 2)

            # Check Take Profit
            tp_hit = (pos.side == "BUY" and current_price >= pos.take_profit) or (pos.side == "SELL" and current_price <= pos.take_profit)
            # Check Stop Loss
            sl_hit = (pos.side == "BUY" and current_price <= pos.stop_loss) or (pos.side == "SELL" and current_price >= pos.stop_loss)

            if tp_hit or sl_hit:
                exit_type = "TAKE_PROFIT" if tp_hit else "STOP_LOSS"
                realized_pnl = pos.unrealized_pnl
                self.cloud_balance = round(self.cloud_balance + realized_pnl, 2)
                symbols_to_remove.append(symbol)

                # Close position on broker via adapter
                if getattr(self.adapter, "enabled", False):
                    try:
                        self.adapter.close_position(
                            position_id=pos.position_id,
                            symbol=pos.symbol,
                            side=pos.side,
                            quantity=pos.quantity,
                        )
                        logger.info(f"[MT5_ENGINE] Successfully closed broker position for {pos.symbol} (ID: {pos.position_id}) via FIX")
                    except Exception as exc:
                        logger.error(f"[MT5_ENGINE] Failed closing broker position for {pos.symbol}: {exc}")

                # Telegram Notification
                status_emoji = "🎉 TAKE PROFIT HIT" if tp_hit else "🛡️ STOP LOSS HIT"
                pnl_emoji = "💰" if realized_pnl >= 0 else "🔻"
                msg = (
                    f"<b>{status_emoji}</b>\n\n"
                    f"• <b>Symbol</b>: <code>{pos.symbol}</code>\n"
                    f"• <b>Side</b>: <code>{pos.side}</code>\n"
                    f"• <b>Quantity</b>: <code>{pos.quantity} lots</code>\n"
                    f"• <b>Entry Price</b>: <code>${pos.entry_price:,.4f}</code>\n"
                    f"• <b>Exit Price</b>: <code>${current_price:,.4f}</code>\n"
                    f"• <b>Realized PnL</b>: {pnl_emoji} <code>${realized_pnl:+,.2f} USD</code>\n"
                    f"• <b>Updated Balance</b>: <code>${self.cloud_balance:,.2f} USD</code>\n"
                    f"• <b>Ticket</b>: <code>{pos.position_id}</code>"
                )
                self.telegram.send_message(msg)

                # Memora Audit
                self.memora.record_scoped_event(
                    event_type=f"MT5_{exit_type}_TRIGGERED",
                    scope="Universal",
                    summary=f"Stratex closed {pos.symbol} {pos.side} at ${current_price:,.4f} ({exit_type}) | PnL: ${realized_pnl:+,.2f}",
                    details={
                        "symbol": pos.symbol,
                        "side": pos.side,
                        "ticket": pos.position_id,
                        "entry_price": pos.entry_price,
                        "exit_price": current_price,
                        "realized_pnl": realized_pnl,
                        "exit_type": exit_type,
                        "new_balance": self.cloud_balance,
                    },
                    tags=["trading", "exit", exit_type.lower(), pos.symbol.lower()],
                )

                # Broadcast to FRIDAY Core Gateway
                self.friday.broadcast_trade_event(
                    event_type="STRATEX_TRADE_CLOSED",
                    payload={
                        "ticket": pos.position_id,
                        "symbol": pos.symbol,
                        "exit_price": current_price,
                        "realized_pnl": realized_pnl,
                        "exit_type": exit_type,
                        "balance": self.cloud_balance,
                    },
                )

                closed_events.append({
                    "ticket": pos.position_id,
                    "symbol": pos.symbol,
                    "exit_type": exit_type,
                    "exit_price": current_price,
                    "realized_pnl": realized_pnl,
                })

        for sym in symbols_to_remove:
            self.cloud_positions.pop(sym, None)

        self._save_cloud_state()
        return closed_events

    def get_active_positions(self) -> list[Any]:
        """Return active open positions from bridge adapter or cloud portfolio."""
        if getattr(self.adapter, "enabled", False):
            try:
                positions = self.adapter.get_positions()
                if positions:
                    return positions
            except Exception:
                pass
        if not self.cloud_positions:
            self._load_cloud_state()
        return list(self.cloud_positions.values())

    def get_account_balance(self) -> dict[str, Any]:
        """Return live account balances and margin metrics."""
        if getattr(self.adapter, "enabled", False):
            try:
                balances = self.adapter.get_balance()
                if balances:
                    return balances
            except Exception:
                pass
        if not self.cloud_positions and not os.path.exists(self.state_file):
            pass
        elif not self.cloud_positions:
            self._load_cloud_state()
        used_margin = sum(p.quantity * 100.0 for p in self.cloud_positions.values())
        unrealized_sum = sum(p.unrealized_pnl for p in self.cloud_positions.values())
        equity = self.cloud_balance + unrealized_sum
        free_margin = max(0.0, equity - used_margin)
        return {
            "USD": {
                "total": round(self.cloud_balance, 2),
                "equity": round(equity, 2),
                "free": round(free_margin, 2),
                "used": round(used_margin, 2),
            }
        }

    def scan_market(self) -> list[dict[str, Any]]:
        """Run a full scan across the asset universe and multiple timeframes."""
        if is_panic_active() or is_kill_switch_locked():
            logger.warning("[MT5_ENGINE] Emergency halt or kill-switch is active. Scan aborted.")
            return []

        # 1. Manage active cloud positions (bracket checks & realized PnL)
        self.manage_cloud_positions()

        # 2. Check existing positions to respect max_active_positions
        active_symbols = set()
        try:
            active_positions = self.adapter.get_positions()
            if active_positions:
                active_symbols.update(p.symbol for p in active_positions)
        except Exception:
            pass
        active_symbols.update(self.cloud_positions.keys())

        if len(active_symbols) >= self.max_active_positions:
            logger.info(
                f"[MT5_ENGINE] Max active positions reached ({len(active_symbols)}/{self.max_active_positions}). Holding."
            )
            return []

        executed: list[dict[str, Any]] = []

        for symbol in self.symbols:
            if len(active_symbols) >= self.max_active_positions:
                logger.info(
                    f"[MT5_ENGINE] Max active positions cap reached ({len(active_symbols)}/{self.max_active_positions}). Stopping scan."
                )
                break

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

                if exec_result.get("status") in {"FILLED", "PENDING", "SIMULATED_FILLED"}:
                    active_symbols.add(symbol)
                    logger.info(f"[MT5_ENGINE] Initiated 1 new position for {symbol}. Pausing entries for this cycle.")
                    return executed

                # Move to next symbol
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
