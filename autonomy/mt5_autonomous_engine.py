"""
autonomy/mt5_autonomous_engine.py — Autonomous Multi-Asset Multi-Timeframe IC Markets cTrader Quantitative Trading Engine.

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
5. Execution via IC Markets cTrader FIX 4.4:
   - Broker-native preflight checks (order_check)
   - Risk-based lot sizing that honours the broker volume step
   - Mandatory protective Stop-Loss and Take-Profit brackets (minimum reward/risk floor)
6. Instant Telegram Alerts & 2-Way Bot Integration:
   - Fill and exit alerts only (proposal/heartbeat noise suppressed)
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
from strategies.strategy_registry import strategy_registry
from strategies.ensemble_strategy import get_signal as ensemble_get_signal
from strategies.ml_ensemble import get_signal as ml_ensemble_get_signal

logger = get_logger("ctrader_autonomous_engine")

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
    # Scandinavian & SGD Crosses (liquid, broker-tight spreads)
    "USDNOK",
    "USDSEK",
    "EURNOK",
    "EURSEK",
    "NOKJPY",
    "NOKSEK",
    "SEKJPY",
    "USDSGD",
    "EURSGD",
    "GBPSGD",
    "AUDSGD",
    "NZDSGD",
    "SGDJPY",
    "CHFSGD",
    # Precious & Industrial Metals
    "XAUUSD",
    "XAGUSD",
    "XPTUSD",
    "XPDUSD",
    # Energy
    "XTIUSD",
    "XBRUSD",
    "XNGUSD",
    # Global Indices
    "US30",
    "US500",
    "USTEC",
    "DE40",
    "UK100",
    "JP225",
    "AUS200",
    "HK50",
    "US2000",
    "SWI20",
    # Crypto CFDs
    "BTCUSD",
    "ETHUSD",
    "SOLUSD",
    "XRPUSD",
    "LTCUSD",
    "BNBUSD",
    "ADAUSD",
    "DOGEUSD",
    "DOTUSD",
]

# Every timeframe the broker offers, from scalping to swing. The scan scores
# each symbol across all six and keeps only its single best timeframe.
DEFAULT_TIMEFRAMES = ["5m", "15m", "30m", "1h", "4h", "1d"]

CRYPTO_SYMBOLS = {
    "BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "LTCUSD", "BNBUSD",
    "ADAUSD", "DOGEUSD", "DOTUSD", "BCHUSD", "XLMUSD", "TRXUSD",
}

INDEX_SYMBOLS = {
    "US30", "US500", "USTEC", "DE40", "DE30", "GER40", "UK100", "JP225",
    "AUS200", "HK50", "US2000", "SWI20", "F40", "STOXX50", "ES35", "IT40",
}

# Currencies whose USD value is fetched directly as "<CCY>USD".
_USD_PER_MAJOR = {"EUR", "GBP", "AUD", "NZD"}
# Currencies quoted as "USD<CCY>"; USD value is the inverse.
_USD_PER_MINOR = {
    "JPY", "CHF", "CAD", "SGD", "NOK", "SEK", "DKK", "HKD",
    "MXN", "ZAR", "TRY", "PLN", "CZK", "HUF", "CNH", "THB",
}


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _contract_size(symbol: str) -> float:
    """Units of the underlying in 1.00 lot at IC Markets cTrader.

    1 lot = 100,000 FX units, 100 oz of gold/platinum/palladium, 5,000 oz of
    silver, 1,000 barrels of oil, 1 index contract, or 1 coin for crypto CFDs.
    """
    sym = symbol.upper().replace("/", "")
    if sym in CRYPTO_SYMBOLS or sym in INDEX_SYMBOLS:
        return 1.0
    if any(k in sym for k in ("XAU", "GOLD", "XPT", "XPD")):
        return 100.0
    if "XAG" in sym or "SILVER" in sym:
        return 5000.0
    if any(k in sym for k in ("XTI", "XBR", "WTI", "BRENT", "OIL", "XNG")):
        return 1000.0
    return 100000.0


def _quote_currency(symbol: str) -> str:
    """Return the FX quote currency, or an empty string for non-FX assets."""
    sym = symbol.upper().replace("/", "")
    if sym in CRYPTO_SYMBOLS or sym in INDEX_SYMBOLS:
        return ""
    if any(k in sym for k in ("XAU", "XAG", "XPT", "XPD", "XTI", "XBR", "XNG", "WTI", "OIL")):
        return ""
    if len(sym) == 6 and sym.isalpha():
        return sym[3:]
    return ""


def _min_stop_distance(symbol: str, price: float) -> float:
    """Minimum sensible stop distance in price terms.

    Stops tighter than the broker spread are noise, not risk management: they
    exit on the first tick regardless of signal quality. Thresholds are set at
    roughly 3-5x a typical IC Markets spread for each asset class.
    """
    sym = symbol.upper().replace("/", "")
    if sym in CRYPTO_SYMBOLS:
        # 0.4% of price; for sub-dollar coins keep at least a few ticks.
        return max(price * 0.004, 0.0002 if price >= 1 else 0.00002)
    if "XAU" in sym or "GOLD" in sym:
        return max(1.20, price * 0.0004)
    if any(k in sym for k in ("XPT", "XPD")):
        return max(1.50, price * 0.0015)
    if "XAG" in sym or "SILVER" in sym:
        return max(0.06, price * 0.0015)
    if sym == "XNGUSD" or "NG" in sym:
        return 0.03
    if any(k in sym for k in ("XTI", "XBR", "WTI", "BRENT", "OIL")):
        return 0.15
    if sym in INDEX_SYMBOLS:
        return price * 0.0012
    if "JPY" in sym:
        return 0.08  # 8 pips on 3-decimal JPY pairs
    return 0.00080  # 8 pips on 5-decimal FX pairs


def _fallback_price(symbol: str) -> float:
    """Rough notional price used only when no live quote is available."""
    sym = symbol.upper().replace("/", "")
    if sym in CRYPTO_SYMBOLS:
        return 95000.0
    if sym in INDEX_SYMBOLS:
        return 5000.0
    if "XAU" in sym or "GOLD" in sym:
        return 4000.0
    if "XAG" in sym or "SILVER" in sym:
        return 50.0
    if any(k in sym for k in ("XPT", "XPD")):
        return 1500.0
    if any(k in sym for k in ("XTI", "XBR", "WTI", "BRENT", "OIL", "XNG")):
        return 75.0
    return 1.0


def _calculate_position_margin(symbol: str, lots: float, price: float | None = None) -> float:
    """Approximate used margin in USD for ``lots`` at the venue's leverage."""
    sym = symbol.upper().replace("/", "")
    notional = abs(lots) * _contract_size(sym) * (price or _fallback_price(sym))
    if sym in CRYPTO_SYMBOLS:
        leverage = 5.0      # crypto CFDs are the most margin-hungry
    elif sym in INDEX_SYMBOLS:
        leverage = 200.0
    elif any(k in sym for k in ("XTI", "XBR", "WTI", "BRENT", "OIL", "XNG")):
        leverage = 100.0
    else:
        leverage = 500.0
    return notional / leverage


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
    # Risk-based lot size. ``quantity`` holds the venue's units (oz, barrels,
    # contracts, coins or FX units) so PnL never has to guess the asset class.
    lots: float = 0.0
    # Client ids of the broker-side protective orders (cTrader tags 721).
    sl_order_id: str = ""
    tp_order_id: str = ""


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
    """Autonomous trading engine orchestrating IC Markets cTrader execution and the FRIDAY peer mesh."""

    def __init__(
        self,
        adapter: MT5BridgeAdapter | None = None,
        symbols: list[str] | None = None,
        timeframes: list[str] | None = None,
        max_active_positions: int | None = None,
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
        if max_active_positions is not None:
            self.max_active_positions = max_active_positions
        else:
            self.max_active_positions = int(os.getenv("STRATEX_MAX_POSITIONS", "10"))
        self.max_risk_per_trade_pct = min(
            0.02,
            max(0.001, float(os.getenv("STRATEX_RISK_PER_TRADE_PCT", str(max_risk_per_trade_pct)))),
        )
        self.max_daily_drawdown_pct = min(0.05, max(0.01, max_daily_drawdown_pct))
        self.min_confidence = float(os.getenv("STRATEX_MIN_CONFIDENCE", str(min_confidence)))
        self.max_entries_per_cycle = int(os.getenv("STRATEX_MAX_ENTRIES_PER_CYCLE", "3"))
        self.min_risk_reward = float(os.getenv("STRATEX_MIN_RR", "1.2"))
        self.notify_proposed = os.getenv("STRATEX_NOTIFY_PROPOSED", "false").lower() in {"1", "true", "yes"}
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
        # A restart must not inherit a phantom book: positions that are no longer
        # open at IC Markets would otherwise hold the scan at the position cap.
        try:
            purged = self.reconcile_with_broker(notify=False)
            if purged:
                logger.info(f"[CTRADER_ENGINE] Startup reconciliation purged {purged} phantom position(s).")
        except Exception as exc:
            logger.warning(f"[CTRADER_ENGINE] Startup reconciliation skipped: {exc}")

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
                raw_qty = float(p_data.get("quantity", 0.01))
                saved_lots = float(p_data.get("lots", 0.0) or 0.0)
                contract = _contract_size(sym)
                if saved_lots <= 0:
                    # Legacy ledger rows stored the lot size (0.01) rather than
                    # the venue units (1000). Migrate them once on load so PnL
                    # is exact for every asset class.
                    if raw_qty < max(1.0, contract * 0.01):
                        saved_lots = raw_qty
                        raw_qty = round(raw_qty * contract, 8)
                    else:
                        saved_lots = round(raw_qty / contract, 4) if contract else raw_qty
                self.cloud_positions[sym] = CloudPosition(
                    position_id=p_data.get("position_id", ""),
                    symbol=p_data.get("symbol", sym),
                    side=p_data.get("side", "BUY"),
                    quantity=raw_qty,
                    entry_price=float(p_data.get("entry_price", 0.0)),
                    stop_loss=float(p_data.get("stop_loss", 0.0)),
                    take_profit=float(p_data.get("take_profit", 0.0)),
                    mark_price=float(p_data.get("mark_price", 0.0)),
                    unrealized_pnl=float(p_data.get("unrealized_pnl", 0.0)),
                    opened_at=p_data.get("opened_at", _utc_now_iso()),
                    strategy=p_data.get("strategy", ""),
                    ai_thesis=p_data.get("ai_thesis"),
                    lots=saved_lots,
                    sl_order_id=p_data.get("sl_order_id", "") or "",
                    tp_order_id=p_data.get("tp_order_id", "") or "",
                )
            logger.info(f"[CTRADER_ENGINE] Loaded {len(self.cloud_positions)} cloud positions from state file (Balance: ${self.cloud_balance:,.2f})")
        except Exception as exc:
            logger.warning(f"[CTRADER_ENGINE] Could not load state from {self.state_file}: {exc}")

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
            logger.warning(f"[CTRADER_ENGINE] Could not save state to {self.state_file}: {exc}")

    def close_all_positions(self) -> list[dict[str, Any]]:
        """Close all active positions on the broker via adapter and update settled balance."""
        closed_reports: list[dict[str, Any]] = []
        positions_to_close = list(self.cloud_positions.values())

        for pos in positions_to_close:
            try:
                # Cancel broker-side protective orders first.
                if hasattr(self.adapter, "cancel_pending_order"):
                    if pos.sl_order_id:
                        try:
                            self.adapter.cancel_pending_order(pos.sl_order_id)
                        except Exception:
                            pass
                    if pos.tp_order_id:
                        try:
                            self.adapter.cancel_pending_order(pos.tp_order_id)
                        except Exception:
                            pass

                if getattr(self.adapter, "enabled", False):
                    self.adapter.close_position(
                        position_id=pos.position_id,
                        symbol=pos.symbol,
                        side=pos.side,
                        quantity=pos.quantity,
                    )
                realized = pos.unrealized_pnl
                self.cloud_balance = round(self.cloud_balance + realized, 2)
                closed_reports.append({
                    "symbol": pos.symbol,
                    "side": pos.side,
                    "quantity": pos.quantity,
                    "realized_pnl": realized,
                    "position_id": pos.position_id,
                })
            except Exception as exc:
                logger.error(f"[CTRADER_ENGINE] Error closing position {pos.symbol}: {exc}")

        self.cloud_positions.clear()
        self._save_cloud_state()
        if hasattr(self.adapter, "set_settled_balance"):
            try:
                self.adapter.set_settled_balance(self.cloud_balance)
            except Exception:
                pass
        return closed_reports

    def reset_portfolio_and_balance(self, new_balance: float = 1000.0) -> None:
        """Reset the portfolio ledger and clear all positions (e.g. after user resets demo broker account)."""
        self.cloud_balance = float(new_balance)
        self.cloud_positions.clear()
        self._save_cloud_state()
        if hasattr(self.adapter, "set_settled_balance"):
            try:
                self.adapter.set_settled_balance(new_balance)
            except Exception:
                pass
        logger.info(f"[CTRADER_ENGINE] Portfolio reset to ${new_balance:,.2f} USD with 0 active positions.")

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
                    logger.debug(f"[CTRADER_ENGINE] Bridge failed fetching candles for {symbol} ({timeframe}): {exc}")
                    candles = None

            # 2. Resilient Zero-Laptop Cloud Market Feed
            if not candles:
                try:
                    candles = self.cloud_feed.get_candles(symbol, timeframe=timeframe, limit=80)
                except Exception as exc:
                    logger.debug(f"[CTRADER_ENGINE] Cloud feed failed fetching candles for {symbol} ({timeframe}): {exc}")
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
            logger.info(f"[CTRADER_ENGINE] Chaos regime detected for {symbol} ({timeframe}); skipping.")
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
                    logger.debug(f"[CTRADER_ENGINE] {symbol} {b_action} filtered by Kalman overextension (Z={kalman_z:.2f})")
                    return None
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
            # Fall back to ensemble strategy for additional signal sources
            import pandas as pd
            try:
                df = pd.DataFrame(candles)
                # Try ensemble strategy
                ens_result = ensemble_get_signal(df, strategy_registry=strategy_registry, regime=regime.value if hasattr(regime, 'value') else str(regime))
                if ens_result and ens_result.side:
                    action = ens_result.side
                    strategy_source = "ensemble_consensus"
                    confidence = float(ens_result.win_rate_prior or 0.72)
                    stop_loss = float(ens_result.sl)
                    take_profit = float(ens_result.tp)
                else:
                    # Try ML ensemble
                    ml_result = ml_ensemble_get_signal(df, regime=regime.value if hasattr(regime, 'value') else str(regime))
                    if ml_result and ml_result.side:
                        action = ml_result.side
                        strategy_source = "ml_ensemble"
                        confidence = float(ml_result.win_rate_prior or 0.65)
                        stop_loss = float(ml_result.sl)
                        take_profit = float(ml_result.tp)
            except Exception as exc:
                logger.debug(f"[CTRADER_ENGINE] Ensemble/ML fallback failed for {symbol}: {exc}")

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

        # Realistic stop floor: cloud/indicative candles can compress ATR to a
        # fraction of a pip, which produces stops inside the spread and gets
        # scalped out instantly. Re-anchor the bracket to a tradable distance
        # while preserving the intended reward:risk.
        min_stop = _min_stop_distance(symbol, current_price)
        if risk_dist < min_stop:
            intended_rr = max(reward_dist / risk_dist, self.min_risk_reward)
            if action == "BUY":
                stop_loss = current_price - min_stop
                take_profit = current_price + (min_stop * intended_rr)
            else:
                stop_loss = current_price + min_stop
                take_profit = current_price - (min_stop * intended_rr)
            logger.info(
                f"[CTRADER_ENGINE] {symbol} bracket widened to a tradable stop "
                f"({risk_dist:.5f} -> {min_stop:.5f}, R:R {intended_rr:.2f})"
            )
            risk_dist = abs(current_price - stop_loss)
            reward_dist = abs(take_profit - current_price)

        risk_reward_ratio = reward_dist / risk_dist
        if risk_reward_ratio < self.min_risk_reward:
            return None  # Enforce minimum favorable risk-reward ratio

        # REJECT templated/fake signals that indicate the strategy didn't actually work
        # These are the telltale signs of hardcoded fallbacks
        is_fake_signal = (
            abs(confidence - 0.95) < 0.01 and   # Fake confidence cap
            abs(risk_reward_ratio - 1.67) < 0.01 and  # Fake R:R template
            strategy_source in {"ensemble_consensus", "ml_ensemble", "hybrid_breakout_kalman"}
        )
        if is_fake_signal:
            logger.warning(
                f"[CTRADER_ENGINE] Rejected fake signal for {symbol} ({timeframe}): "
                f"conf={confidence:.2f}, R:R={risk_reward_ratio:.2f}, source={strategy_source}"
            )
            return None

        # Additional quality gate: require minimum signal quality
        if strategy_source in {"ensemble_consensus", "ml_ensemble"}:
            # These fallbacks need stronger validation
            if confidence < 0.65:
                logger.debug(f"[CTRADER_ENGINE] Low-confidence fallback signal for {symbol}: {confidence:.2f}")
                return None

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
            actor="ctrader_autonomous_engine",
            context={
                "symbol": signal.symbol,
                "venue": "ctrader",
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

    def _quote_to_usd(self, quote: str) -> float:
        """USD value of one unit of ``quote``, from the live feed when possible.

        Fixed constants (1/152 for JPY, 0.72 for CAD, ...) go stale and silently
        skew both sizing and PnL once the market moves. Quotes are cached by the
        feed, so this costs nothing inside a scan.
        """
        quote = (quote or "").upper()
        if not quote or quote == "USD":
            return 1.0
        fallback = {
            "EUR": 1.05, "GBP": 1.27, "AUD": 0.65, "NZD": 0.60,
            "JPY": 1 / 152.0, "CHF": 1.15, "CAD": 0.72, "SGD": 0.74,
            "NOK": 0.093, "SEK": 0.095, "DKK": 0.145, "HKD": 0.128,
            "MXN": 0.050, "ZAR": 0.055, "TRY": 0.028, "PLN": 0.25,
            "CZK": 0.043, "HUF": 0.0028, "CNH": 0.14, "THB": 0.028,
        }.get(quote, 1.0)
        try:
            if quote in _USD_PER_MAJOR:
                px = self.cloud_feed.get_current_price(f"{quote}USD")
                return float(px) if px and px > 0 else fallback
            if quote in _USD_PER_MINOR:
                px = self.cloud_feed.get_current_price(f"USD{quote}")
                return (1.0 / float(px)) if px and px > 0 else fallback
        except Exception:
            return fallback
        return fallback

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

        # Broker-reported sizing is only a floor on volume step / minimum size;
        # IC Markets' cTrader adapter returns a flat 0.01 recommendation which
        # would silently keep every trade at micro-lot size regardless of the
        # risk budget. Size deterministically from the actual stop distance.
        step = 0.01
        min_vol = 0.01
        try:
            sizing = self.adapter.calculate_position_size(symbol, side, stop_loss, take_profit)
            step = max(0.01, float(sizing.get("volume_step", 0.01) or 0.01))
            min_vol = max(0.01, float(sizing.get("volume_min", 0.01) or 0.01))
        except Exception:
            pass

        risk_budget = equity * self.max_risk_per_trade_pct * max(multiplier, 0.1)
        risk_per_unit = abs(current_price - stop_loss)
        if risk_per_unit <= 1e-6:
            return min_vol

        # Risk per lot = stop distance x contract size x quote-to-USD rate.
        # Using a flat 100,000 contract size for every asset massively oversizes
        # indices and crypto and undersizes gold, so derive it per instrument.
        contract_size = _contract_size(symbol)
        quote = _quote_currency(symbol)
        quote_conv = self._quote_to_usd(quote) if quote else 1.0
        raw_lots = risk_budget / max(risk_per_unit * contract_size * quote_conv, 1e-9)
        stepped_lots = max(min_vol, math.floor(raw_lots / step) * step)
        # Hard ceiling for a four-figure demo account; a data glitch in the
        # stop distance must never translate into an outsized position.
        stepped_lots = min(stepped_lots, 0.50)

        # Margin sanity: never commit more than 25% of equity as margin.
        margin_per_lot = _calculate_position_margin(symbol, 1.0, current_price)
        if margin_per_lot > 0:
            stepped_lots = min(stepped_lots, (equity * 0.25) / margin_per_lot)

        return round(max(min_vol, stepped_lots), 2)

    def execute_signal(self, signal: OpportunitySignal, decision: ConsensusDecision) -> dict[str, Any]:
        """Execute the approved trade on IC Markets cTrader demo and dispatch notifications."""
        volume = self.calculate_lot_size(
            symbol=signal.symbol,
            side=signal.action,
            current_price=signal.current_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            multiplier=decision.size_multiplier,
        )

        intent = TradeIntent(
            venue="ctrader",
            symbol=signal.symbol,
            side=signal.action,
            order_type="MARKET",
            quantity=volume,
            environment="ctrader_demo",
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

        # Optional proposed-intent notification (off by default: only fills and
        # exits are worth a push; proposal spam makes the channel unusable).
        if self.notify_proposed:
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
            logger.info(f"[CTRADER_ENGINE] DRY_RUN: Simulated execution of {intent.side} {intent.quantity} {intent.symbol}")
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
                    logger.error(f"[CTRADER_ENGINE] Execution failed for {signal.symbol}: {exc}")
                    self.telegram.notify_risk_rejection(intent, f"Execution failed: {exc}")
                    return {"status": "FAILED", "error": str(exc), "symbol": signal.symbol}

            if not executed_on_bridge:
                if not getattr(self.adapter, "enabled", False):
                    order_id = f"stx-cld-{int(time.time())}"
                    result = {
                        "status": "FILLED",
                        "mode": "cloud_autonomous",
                        "order_id": order_id,
                        "symbol": signal.symbol,
                        "side": signal.action,
                        "quantity": volume,
                        "price": signal.current_price,
                    }
                else:
                    logger.warning(
                        f"[CTRADER_ENGINE] Bridge offline or execution disabled. Rejecting {signal.action} {signal.symbol} "
                        f"to prevent phantom trades."
                    )
                    self.telegram.notify_risk_rejection(
                        intent, "Execution rejected: cTrader FIX session offline (orders must fill on IC Markets demo)"
                    )
                    return {
                        "status": "REJECTED_NO_BRIDGE",
                        "error": "cTrader FIX session not connected to IC Markets",
                        "symbol": signal.symbol,
                    }

        # Confirm the fill against the live broker so the ledger and the
        # Telegram alert carry the venue's ticket and execution price rather
        # than the FIX acknowledgement (which reports price 0 while PENDING).
        broker_units = 0.0
        if getattr(self.adapter, "enabled", False) and result.get("status") in {"FILLED", "PENDING"}:
            for _ in range(4):
                snapshot = self._broker_positions_snapshot()
                broker_pos = None
                if snapshot:
                    for bp in snapshot:
                        if bp.symbol == signal.symbol:
                            broker_pos = bp
                            break
                if broker_pos:
                    if broker_pos.position_id:
                        result["order_id"] = broker_pos.position_id
                    if broker_pos.entry_price and broker_pos.entry_price > 0:
                        result["price"] = broker_pos.entry_price
                    if float(getattr(broker_pos, "quantity", 0) or 0) > 0:
                        broker_units = float(broker_pos.quantity)
                    break
                time.sleep(0.6)

        # Register position into tracked cloud portfolio
        pos_id = str(result.get("order_id") or f"pos-{int(time.time())}")
        raw_price = result.get("price")
        fill_px = float(raw_price) if raw_price and float(raw_price) > 0 else float(signal.current_price)
        # Store the venue's units (oz / barrels / contracts / coins / FX units),
        # not the lot size, so PnL is exact for every asset class.
        position_units = broker_units or round(volume * _contract_size(signal.symbol), 8)

        # Attach broker-side stop-loss and take-profit. cTrader protects a
        # position with separate Stop/Limit orders linked by tag 721; without
        # them the position would be naked if this daemon stopped.
        sl_order_id = ""
        tp_order_id = ""
        if (
            broker_units > 0
            and pos_id
            and not pos_id.startswith("pos-")
            and hasattr(self.adapter, "place_protective_orders")
        ):
            try:
                prot = self.adapter.place_protective_orders(
                    position_id=pos_id,
                    symbol=signal.symbol,
                    side=signal.action,
                    units=broker_units,
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit,
                )
                sl_order_id = prot.get("stop_client_id", "") or ""
                tp_order_id = prot.get("limit_client_id", "") or ""
                if prot.get("errors"):
                    logger.warning(
                        f"[CTRADER_ENGINE] Broker protection incomplete for {signal.symbol}: {prot['errors']}"
                    )
                else:
                    logger.info(
                        f"[CTRADER_ENGINE] Broker SL/TP attached for {signal.symbol} "
                        f"(SL {signal.stop_loss:,.5f}, TP {signal.take_profit:,.5f})"
                    )
            except Exception as exc:
                logger.warning(
                    f"[CTRADER_ENGINE] Could not attach broker protection for {signal.symbol}: {exc}"
                )

        self.cloud_positions[signal.symbol] = CloudPosition(
            position_id=pos_id,
            symbol=signal.symbol,
            side=signal.action,
            quantity=position_units,
            entry_price=fill_px,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            mark_price=fill_px,
            unrealized_pnl=0.0,
            opened_at=_utc_now_iso(),
            strategy=signal.strategy_source,
            ai_thesis=decision.ai_thesis,
            lots=volume,
            sl_order_id=sl_order_id,
            tp_order_id=tp_order_id,
        )
        self._save_cloud_state()

        # Telegram Fill Alert
        self.telegram.notify_trade_intent(
            intent,
            status="FILLED",
            fill_price=fill_px,
            order_id=pos_id,
        )

        # Record in Memora Scoped Audit Memory
        self.memora.record_scoped_event(
            event_type="CTRADER_TRADE_EXECUTED",
            scope="Universal",
            summary=f"Stratex executed {signal.action} {volume} lots of {signal.symbol} on IC Markets cTrader at {fill_px:,.5f}",
            details={
                "intent": intent.to_dict(),
                "execution_result": result,
                "consensus": asdict(decision),
            },
            tags=["trading", "ctrader", "execution", signal.symbol.lower()],
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

    def _broker_positions_snapshot(self) -> dict[str, Any] | None:
        """Return live broker positions keyed by symbol, or None when the venue is unavailable.

        A ``None`` result must never be treated as "flat book": a timed-out FIX
        session or a logged-out bridge returns an empty list, and purging the
        local ledger on that signal would erase real positions and re-enter the
        same symbols. Only a positively healthy venue may authoritatively report
        zero positions.
        """
        if not getattr(self.adapter, "enabled", False):
            return None
        try:
            if not getattr(self.adapter, "is_connected", False) and hasattr(self.adapter, "_ensure_connection"):
                self.adapter._ensure_connection()
        except Exception as exc:
            logger.warning(f"[CTRADER_ENGINE] Broker connect failed ({exc}); skipping position reconciliation.")
            return None
        if not getattr(self.adapter, "is_connected", False):
            return None
        try:
            broker_positions = self.adapter.get_positions()
        except Exception as exc:
            logger.warning(f"[CTRADER_ENGINE] Broker position query failed ({exc}); skipping reconciliation.")
            return None
        # A transport failure flips the adapter offline mid-query; an empty list
        # while still logged on is a genuinely flat book.
        if not getattr(self.adapter, "is_connected", False) or not getattr(self.adapter, "_is_logged_on", True):
            logger.warning("[CTRADER_ENGINE] Broker session dropped during position query; skipping reconciliation.")
            return None
        if broker_positions is None:
            return None
        return broker_positions

    def reconcile_with_broker(self, notify: bool = True, broker_pos_map: list | None = None) -> int:
        """Drop tracked positions that no longer exist at the broker. Returns count purged.

        ``notify`` is disabled during startup so stale ledger rows left over
        from an old deployment do not produce phantom "closed" alerts.

        For positions closed at the broker (SL/TP hit), calculates realized PnL
        using the protective level that was triggered, updates settled balance,
        and emits a proper exit alert.
        """
        if broker_pos_map is None:
            broker_pos_map = self._broker_positions_snapshot()
        if broker_pos_map is None:
            return 0
        broker_ids = {str(getattr(bp, "position_id", "") or "") for bp in broker_pos_map}
        # Also map by symbol for quick lookup
        broker_by_symbol: dict[str, list] = {}
        for bp in broker_pos_map:
            broker_by_symbol.setdefault(bp.symbol, []).append(bp)
        purged = 0
        for sym, pos in list(self.cloud_positions.items()):
            pid = str(pos.position_id or "")
            if sym in broker_by_symbol or (pid and pid in broker_ids):
                continue

            # Position is gone at broker -> SL or TP was hit.
            # Infer which level was hit using a FRESH price from the feed (not the
            # stale mark_price which may have been updated after the close).
            entry = float(pos.entry_price)
            sl = float(pos.stop_loss)
            tp = float(pos.take_profit)
            side = pos.side

            # Get current market price to infer direction since entry.
            fresh_price = self.cloud_feed.get_current_price(sym)
            if fresh_price and fresh_price > 0:
                infer_price = fresh_price
            else:
                infer_price = float(pos.mark_price or pos.entry_price)

            if side == "BUY":
                exit_price = tp if infer_price >= entry else sl
                direction = 1.0
            else:
                exit_price = tp if infer_price <= entry else sl
                direction = -1.0

            # Calculate realized PnL using venue units and quote conversion.
            units = abs(float(pos.quantity or 0.0))
            quote_conv = self._quote_to_usd(_quote_currency(sym))
            realized_pnl = round((exit_price - entry) * direction * units * quote_conv, 2)

            # Update settled balance and record.
            self.cloud_balance = round(self.cloud_balance + realized_pnl, 2)
            if hasattr(self.adapter, "record_realized_pnl"):
                try:
                    self.adapter.record_realized_pnl(realized_pnl)
                except Exception:
                    pass

            # Cancel any remaining protective orders at broker.
            if hasattr(self.adapter, "cancel_pending_order"):
                if pos.sl_order_id:
                    try:
                        self.adapter.cancel_pending_order(pos.sl_order_id)
                    except Exception:
                        pass
                if pos.tp_order_id:
                    try:
                        self.adapter.cancel_pending_order(pos.tp_order_id)
                    except Exception:
                        pass

            exit_type = "TAKE_PROFIT" if (side == "BUY" and exit_price >= tp) or (side == "SELL" and exit_price <= tp) else "STOP_LOSS"
            status_emoji = "🎉 TAKE PROFIT HIT" if exit_type == "TAKE_PROFIT" else "🛡️ STOP LOSS HIT"

            logger.info(
                f"[CTRADER_ENGINE] Reconciled broker exit {sym} ({exit_type}) "
                f"entry={entry:,.5f} exit={exit_price:,.5f} realized={realized_pnl:+,.2f} "
                f"balance={self.cloud_balance:,.2f}"
            )

            self.cloud_positions.pop(sym, None)
            purged += 1

            if notify:
                try:
                    pnl_emoji = "💰" if realized_pnl >= 0 else "🔻"
                    disp_qty = pos.lots if pos.lots > 0 else pos.quantity / max(_contract_size(sym), 1e-9)
                    self.telegram.send_message(
                        f"<b>{status_emoji}</b>\n\n"
                        f"• <b>Symbol</b>: <code>{sym}</code>\n"
                        f"• <b>Side</b>: <code>{side}</code>\n"
                        f"• <b>Size</b>: <code>{disp_qty:g} lots</code>\n"
                        f"• <b>Entry</b>: <code>{entry:,.5f}</code>\n"
                        f"• <b>Exit</b>: <code>{exit_price:,.5f}</code>\n"
                        f"• <b>Realized PnL</b>: {pnl_emoji} <code>${realized_pnl:+,.2f} USD</code>\n"
                        f"• <b>Updated Balance</b>: <code>${self.cloud_balance:,.2f} USD</code>\n"
                        f"• <b>Ticket</b>: <code>{pid or 'n/a'}</code>"
                    )
                except Exception as exc:
                    logger.debug(f"[CTRADER_ENGINE] Exit notification failed for {sym}: {exc}")

            # Memora Audit
            self.memora.record_scoped_event(
                event_type=f"CTRADER_{exit_type}_TRIGGERED",
                scope="Universal",
                summary=f"Stratex closed {sym} {side} at ${exit_price:,.4f} ({exit_type} via broker reconciliation) | PnL: ${realized_pnl:+,.2f}",
                details={
                    "symbol": sym,
                    "side": side,
                    "ticket": pid,
                    "entry_price": entry,
                    "exit_price": exit_price,
                    "realized_pnl": realized_pnl,
                    "exit_type": exit_type,
                    "new_balance": self.cloud_balance,
                },
                tags=["trading", "exit", exit_type.lower(), sym.lower()],
            )

            # Broadcast to FRIDAY Core Gateway
            self.friday.broadcast_trade_event(
                event_type="STRATEX_TRADE_CLOSED",
                payload={
                    "ticket": pid,
                    "symbol": sym,
                    "exit_price": exit_price,
                    "realized_pnl": realized_pnl,
                    "exit_type": exit_type,
                    "balance": self.cloud_balance,
                },
            )

        if purged:
            self._save_cloud_state()
        return purged

    def _amend_broker_stop(self, pos: CloudPosition) -> None:
        """Move the broker-side stop order when the engine trails the stop."""
        if not pos.sl_order_id or not hasattr(self.adapter, "amend_protective_order"):
            return
        try:
            canonical_sym, symbol_id = self.adapter.resolve_symbol(pos.symbol)
            if not symbol_id:
                return
            ok = self.adapter.amend_protective_order(
                client_order_id=pos.sl_order_id,
                ord_type=3,  # Stop order
                units=pos.quantity,
                price=pos.stop_loss,
                symbol_id=symbol_id,
            )
            if ok:
                logger.debug(f"[CTRADER_ENGINE] Amended broker SL for {pos.symbol} to {pos.stop_loss:,.5f}")
            else:
                logger.warning(f"[CTRADER_ENGINE] Failed to amend broker SL for {pos.symbol}")
        except Exception as exc:
            logger.warning(f"[CTRADER_ENGINE] Amend broker SL error for {pos.symbol}: {exc}")

    def manage_cloud_positions(self) -> list[dict[str, Any]]:
        """Monitor active cloud positions, update unrealized PnL, and execute Take-Profit / Stop-Loss exits via broker."""
        closed_events: list[dict[str, Any]] = []

        # Synchronize with live broker positions when the venue is positively healthy
        broker_pos_list = self._broker_positions_snapshot()
        if broker_pos_list is not None:
            self.reconcile_with_broker(notify=True, broker_pos_map=broker_pos_list)
            # Re-attach protective orders for any position missing them.
            for bp in broker_pos_list:
                sym = bp.symbol
                cloud_pos = self.cloud_positions.get(sym)
                if cloud_pos and (cloud_pos.sl_order_id == "" or cloud_pos.tp_order_id == ""):
                    # Position exists but SL/TP order IDs are missing - re-attach
                    try:
                        prot = self.adapter.place_protective_orders(
                            position_id=bp.position_id,
                            symbol=sym,
                            side=bp.side,
                            units=bp.quantity,
                            stop_loss=cloud_pos.stop_loss,
                            take_profit=cloud_pos.take_profit,
                        )
                        if prot.get("stop_client_id"):
                            cloud_pos.sl_order_id = prot["stop_client_id"]
                        if prot.get("limit_client_id"):
                            cloud_pos.tp_order_id = prot["limit_client_id"]
                        self._save_cloud_state()
                    except Exception as exc:
                        logger.warning(f"[CTRADER_ENGINE] Could not re-attach broker protection for {sym}: {exc}")
            try:
                # Group broker positions by symbol to detect multiple positions per symbol
                broker_by_symbol: dict[str, list] = {}
                for bp in broker_pos_list:
                    broker_by_symbol.setdefault(bp.symbol, []).append(bp)

                for sym, bp_list in broker_by_symbol.items():
                    cloud_pos = self.cloud_positions.get(sym)

                    if not cloud_pos:
                        # No cloud position for this symbol - adopt the first broker position
                        bp = bp_list[0]
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
                        # Close extra positions on same symbol (enforce single position rule)
                        if len(bp_list) > 1:
                            for extra_bp in bp_list[1:]:
                                try:
                                    self.adapter.close_position(
                                        position_id=extra_bp.position_id,
                                        symbol=sym,
                                        side=extra_bp.side,
                                        quantity=extra_bp.quantity,
                                    )
                                    logger.warning(
                                        f"[CTRADER_ENGINE] Closed extra broker position for {sym} "
                                        f"(id={extra_bp.position_id}, side={extra_bp.side}) to enforce single-position rule"
                                    )
                                except Exception as exc:
                                    logger.warning(f"[CTRADER_ENGINE] Failed to close extra {sym} position: {exc}")
                    else:
                        # Update existing position with broker ticket ID if available
                        existing_pos = self.cloud_positions[sym]
                        # Find matching broker position (by side)
                        matching_bp = next((bp for bp in bp_list if bp.side.upper() == existing_pos.side.upper()), None)
                        if matching_bp and matching_bp.position_id and not existing_pos.position_id:
                            existing_pos.position_id = matching_bp.position_id
                        # Close any broker positions on same symbol with opposite side or extra
                        for bp in bp_list:
                            if bp is matching_bp:
                                continue
                            try:
                                self.adapter.close_position(
                                    position_id=bp.position_id,
                                    symbol=sym,
                                    side=bp.side,
                                    quantity=bp.quantity,
                                )
                                logger.warning(
                                    f"[CTRADER_ENGINE] Closed conflicting extra broker position for {sym} "
                                    f"(id={bp.position_id}, side={bp.side}) to enforce single-position rule"
                                )
                            except Exception as exc:
                                logger.warning(f"[CTRADER_ENGINE] Failed to close extra {sym} position: {exc}")
                self._save_cloud_state()
            except Exception as exc:
                logger.warning(f"[CTRADER_ENGINE] Broker position reconciliation: {exc}")

        if not self.cloud_positions:
            return closed_events

        symbols_to_remove: list[str] = []
        mult_map = {"BUY": 1.0, "SELL": -1.0}

        for symbol, pos in list(self.cloud_positions.items()):
            current_price = self.cloud_feed.get_current_price(symbol)
            if current_price is None or current_price <= 0:
                continue

            if pos.entry_price <= 0:
                pos.entry_price = current_price
            pos.mark_price = current_price
            direction = mult_map.get(pos.side, 1.0)

            # ``quantity`` is already in venue units (oz, barrels, contracts,
            # coins, FX units). Only FX pairs quoted in a non-USD currency need
            # converting to USD, and that rate is live rather than hard-coded.
            units = abs(float(pos.quantity or 0.0))
            if units <= 0:
                continue
            quote_conv = self._quote_to_usd(_quote_currency(symbol))

            pnl = (current_price - pos.entry_price) * direction * units * quote_conv
            pos.unrealized_pnl = round(pnl, 2)

            # Dynamic Profit Locking & Trailing Stop
            if pos.side == "BUY":
                profit_distance = current_price - pos.entry_price
                target_distance = pos.take_profit - pos.entry_price
                if target_distance > 0 and profit_distance >= (0.45 * target_distance):
                    trail_level = pos.entry_price + (profit_distance * 0.40)
                    if pos.stop_loss < trail_level:
                        pos.stop_loss = round(trail_level, 5)
                        logger.info(f"[CTRADER_ENGINE] Trailing stop advanced for {pos.symbol} BUY: New SL = {pos.stop_loss}")
                        self._amend_broker_stop(pos)
            elif pos.side == "SELL":
                profit_distance = pos.entry_price - current_price
                target_distance = pos.entry_price - pos.take_profit
                if target_distance > 0 and profit_distance >= (0.45 * target_distance):
                    trail_level = pos.entry_price - (profit_distance * 0.40)
                    if pos.stop_loss > trail_level:
                        pos.stop_loss = round(trail_level, 5)
                        logger.info(f"[CTRADER_ENGINE] Trailing stop advanced for {pos.symbol} SELL: New SL = {pos.stop_loss}")
                        self._amend_broker_stop(pos)

            # Check Take Profit
            tp_hit = (pos.side == "BUY" and current_price >= pos.take_profit) or (pos.side == "SELL" and current_price <= pos.take_profit)
            # Check Stop Loss
            sl_hit = (pos.side == "BUY" and current_price <= pos.stop_loss) or (pos.side == "SELL" and current_price >= pos.stop_loss)

            if tp_hit or sl_hit:
                if tp_hit:
                    exit_type = "TAKE_PROFIT"
                    status_emoji = "🎉 TAKE PROFIT HIT"
                elif pos.unrealized_pnl > 0:
                    exit_type = "TRAILING_STOP"
                    status_emoji = "🎯 TRAILING STOP (PROFIT LOCKED)"
                else:
                    exit_type = "STOP_LOSS"
                    status_emoji = "🛡️ STOP LOSS HIT"

                # Close on the broker first and require confirmation. Booking a
                # win/loss and alerting before the venue acknowledges the exit is
                # how phantom "closed" trades get reported while the position is
                # still open.
                close_confirmed = not getattr(self.adapter, "enabled", False)
                close_price = current_price
                if not close_confirmed:
                    try:
                        close_res = self.adapter.close_position(
                            position_id=pos.position_id,
                            symbol=pos.symbol,
                            side=pos.side,
                            quantity=pos.quantity,
                        )
                        for _ in range(3):
                            snap = self._broker_positions_snapshot()
                            if snap is not None and symbol not in snap:
                                close_confirmed = True
                                break
                            time.sleep(0.6)
                        if close_confirmed and float(getattr(close_res, "avg_price", 0) or 0) > 0:
                            close_price = float(close_res.avg_price)
                        if not close_confirmed:
                            logger.warning(
                                f"[CTRADER_ENGINE] Exit not confirmed for {pos.symbol} "
                                f"(status={getattr(close_res, 'status', 'unknown')}); position kept open for retry."
                            )
                    except Exception as exc:
                        logger.error(f"[CTRADER_ENGINE] Failed closing broker position for {pos.symbol}: {exc}")

                if not close_confirmed:
                    continue

                # Cancel broker-side protective orders so they don't linger as orphans.
                if hasattr(self.adapter, "cancel_pending_order"):
                    if pos.sl_order_id:
                        try:
                            self.adapter.cancel_pending_order(pos.sl_order_id)
                        except Exception:
                            pass
                    if pos.tp_order_id:
                        try:
                            self.adapter.cancel_pending_order(pos.tp_order_id)
                        except Exception:
                            pass

                realized_pnl = round(
                    (close_price - pos.entry_price) * direction * units * quote_conv, 2
                )
                pos.mark_price = close_price
                pos.unrealized_pnl = realized_pnl
                self.cloud_balance = round(self.cloud_balance + realized_pnl, 2)
                if hasattr(self.adapter, 'record_realized_pnl'):
                    try:
                        self.adapter.record_realized_pnl(realized_pnl)
                    except Exception as exc:
                        logger.warning(f'[CTRADER_ENGINE] Failed updating adapter ledger: {exc}')
                symbols_to_remove.append(symbol)

                # Telegram Notification
                pnl_emoji = "💰" if realized_pnl >= 0 else "🔻"
                disp_qty = pos.lots if pos.lots > 0 else pos.quantity / max(_contract_size(pos.symbol), 1e-9)
                msg = (
                    f"<b>{status_emoji}</b>\n\n"
                    f"• <b>Symbol</b>: <code>{pos.symbol}</code>\n"
                    f"• <b>Side</b>: <code>{pos.side}</code>\n"
                    f"• <b>Size</b>: <code>{disp_qty:g} lots</code>\n"
                    f"• <b>Entry</b>: <code>{pos.entry_price:,.5f}</code>\n"
                    f"• <b>Exit</b>: <code>{close_price:,.5f}</code>\n"
                    f"• <b>Realized PnL</b>: {pnl_emoji} <code>${realized_pnl:+,.2f} USD</code>\n"
                    f"• <b>Updated Balance</b>: <code>${self.cloud_balance:,.2f} USD</code>\n"
                    f"• <b>Ticket</b>: <code>{pos.position_id}</code>"
                )
                self.telegram.send_message(msg)

                # Memora Audit
                self.memora.record_scoped_event(
                    event_type=f"CTRADER_{exit_type}_TRIGGERED",
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
        """Return active open positions with live mark prices and real-time PnL."""
        if not self.cloud_positions:
            self._load_cloud_state()
        return list(self.cloud_positions.values())

    def get_account_balance(self) -> dict[str, Any]:
        """Return live account balances and margin metrics."""
        if getattr(self.adapter, "enabled", False):
            try:
                balances = self.adapter.get_balance()
                if balances and "USD" in balances:
                    bal_obj = balances["USD"]
                    self.cloud_balance = float(bal_obj.total or bal_obj.free or self.cloud_balance)
            except Exception:
                pass
        if not self.cloud_positions:
            self._load_cloud_state()
        used_margin = sum(
            _calculate_position_margin(
                p.symbol,
                (p.lots if p.lots > 0 else p.quantity / max(_contract_size(p.symbol), 1e-9)),
                p.mark_price or p.entry_price or None,
            )
            for p in self.cloud_positions.values()
        )
        unrealized_sum = sum(p.unrealized_pnl for p in self.cloud_positions.values())
        equity = self.cloud_balance + unrealized_sum
        free_margin = max(0.0, equity - used_margin)
        return {
            "USD": {
                "total": round(self.cloud_balance, 2),
                "equity": round(equity, 2),
                "free": round(free_margin, 2),
                "used": round(used_margin, 2),
                "unrealized_pnl": round(unrealized_sum, 2),
            }
        }

    def scan_market(self) -> list[dict[str, Any]]:
        """Run a full scan across the asset universe and multiple timeframes."""
        if is_panic_active() or is_kill_switch_locked():
            logger.warning("[CTRADER_ENGINE] Emergency halt or kill-switch is active. Scan aborted.")
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
                f"[CTRADER_ENGINE] Max active positions reached ({len(active_symbols)}/{self.max_active_positions}). Holding."
            )
            return []

        executed: list[dict[str, Any]] = []

        # Warm candles for the whole universe concurrently. On a cold cache the
        # scan touches 70 symbols x 6 timeframes; doing that serially inside the
        # trading loop would take minutes and starve position management.
        try:
            self.cloud_feed.prefetch(
                [
                    (symbol, tf)
                    for symbol in self.symbols
                    if symbol not in active_symbols
                    for tf in self.timeframes
                ],
                max_workers=int(os.getenv("STRATEX_PREFETCH_WORKERS", "6")),
            )
        except Exception as exc:
            logger.debug(f"[CTRADER_ENGINE] Candle prefetch failed: {exc}")

        # Score every symbol across every timeframe, then act only on the best
        # setups. The previous first-come loop broke out after the entry quota
        # and therefore always traded whichever symbols sat earliest in the
        # list, regardless of signal quality.
        candidates: list[OpportunitySignal] = []
        for symbol in self.symbols:
            if symbol in active_symbols:
                continue
            best_signal: OpportunitySignal | None = None
            best_score = -1.0
            for tf in self.timeframes:
                signal = self.evaluate_symbol_timeframe(symbol, tf)
                if not signal or signal.confidence < self.min_confidence:
                    continue
                score = signal.confidence * min(max(signal.risk_reward_ratio, 0.1), 3.0)
                if score > best_score:
                    best_score = score
                    best_signal = signal
            if best_signal is not None:
                candidates.append(best_signal)

        candidates.sort(key=lambda s: (s.confidence, s.risk_reward_ratio), reverse=True)

        for signal in candidates:
            if len(active_symbols) >= self.max_active_positions:
                logger.info(
                    f"[CTRADER_ENGINE] Max active positions cap reached ({len(active_symbols)}/{self.max_active_positions}). Stopping scan."
                )
                break
            if len(executed) >= self.max_entries_per_cycle:
                logger.info(
                    f"[CTRADER_ENGINE] Entry quota reached for this cycle ({len(executed)}/{self.max_entries_per_cycle})."
                )
                break

            logger.info(
                f"[CTRADER_ENGINE] Best setup {signal.symbol} ({signal.timeframe}): {signal.action} "
                f"@ {signal.current_price:.5f} (SL: {signal.stop_loss:.5f}, TP: {signal.take_profit:.5f}, "
                f"R:R {signal.risk_reward_ratio}, Conf: {signal.confidence})"
            )

            # Peer Consensus Gate
            decision = self.consult_peer_mesh(signal)
            if not decision.approved:
                logger.info(f"[CTRADER_ENGINE] Peer consensus rejected {signal.symbol}: {decision.reasons}")
                continue

            exec_result = self.execute_signal(signal, decision)
            executed.append(exec_result)

            if exec_result.get("status") in {"FILLED", "PENDING", "SIMULATED_FILLED"}:
                active_symbols.add(signal.symbol)
                logger.info(f"[CTRADER_ENGINE] Opened position for {signal.symbol}; continuing to next candidate.")

        return executed

    def run_continuous_loop(self, poll_interval_sec: float = 60.0, max_iterations: int | None = None) -> None:
        """Continuous execution loop suitable for headless Docker containers on Oracle Cloud."""
        logger.info(
            f"[CTRADER_ENGINE] Starting 24/7 autonomous loop for {len(self.symbols)} symbols across "
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
                logger.error(f"[CTRADER_ENGINE] Error in market scan iteration: {exc}", exc_info=True)
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
