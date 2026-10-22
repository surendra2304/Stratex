"""
stratex_agents/quant_agent.py — Quantitative Strategy Execution Agent for Stratex.

Provides systematic evaluation and trade intent generation for:
- Statistical Arbitrage / Pairs Trading (`StatisticalArbitrageModel`)
- Kalman Filter Adaptive Mean Reversion (`KalmanFilterMeanReversionModel`)
- Volatility Regime Breakout with Chandelier Exits (`VolatilityRegimeBreakoutModel`)

Routes authorized trade proposals to `StratexOrderManager` with mandatory
protective brackets (Stop-Loss and Take-Profit) in strict demo/testnet mode.
"""
from __future__ import annotations

import math
import uuid
from typing import Any

import numpy as np

from logger import get_logger
from strategies.quantitative_engine import (
    KalmanFilterMeanReversionModel,
    MarketRegime,
    StatisticalArbitrageModel,
    VolatilityRegimeBreakoutModel,
)
from .oms import OMSResult, StratexOrderManager
from .order_intent import TradeIntent

logger = get_logger("quant_agent")


class QuantitativeStrategyAgent:
    """
    Autonomous Quantitative Strategy Agent.
    Evaluates market time-series against statistical/state-space models and
    submits validated TradeIntent objects to the shared order manager.
    """

    def __init__(
        self,
        order_manager: StratexOrderManager | None = None,
        *,
        statarb_model: StatisticalArbitrageModel | None = None,
        kalman_model: KalmanFilterMeanReversionModel | None = None,
        regime_model: VolatilityRegimeBreakoutModel | None = None,
        default_venue: str = "binance",
        default_environment: str = "binance_testnet",
    ):
        self.order_manager = order_manager
        self.statarb_model = statarb_model or StatisticalArbitrageModel(window=30)
        self.kalman_model = kalman_model or KalmanFilterMeanReversionModel()
        self.regime_model = regime_model or VolatilityRegimeBreakoutModel()
        self.default_venue = default_venue.strip().lower()
        self.default_environment = default_environment.strip().lower()

    def health_check(self) -> dict[str, Any]:
        """Return operational health of the quantitative agent."""
        return {
            "status": "healthy",
            "agent": "quant_agent",
            "has_oms": self.order_manager is not None,
            "default_venue": self.default_venue,
            "default_environment": self.default_environment,
            "models_loaded": ["statarb", "kalman_filter", "regime_breakout"],
        }

    # ==========================================================================
    # 1. STATISTICAL ARBITRAGE / PAIRS TRADING PROPOSALS
    # ==========================================================================
    def propose_pairs_intents(
        self,
        asset_y: str,
        asset_x: str,
        series_y: list[float] | np.ndarray,
        series_x: list[float] | np.ndarray,
        quantity_y: float,
        quantity_x: float | None = None,
        venue: str | None = None,
        environment: str | None = None,
        sl_pct: float = 0.02,
        tp_pct: float = 0.04,
    ) -> list[TradeIntent]:
        """
        Evaluate cointegration spread between Y and X.
        If spread is mispriced, returns a pair of market TradeIntents with SL/TP.
        """
        signal = self.statarb_model.evaluate_pair(asset_y, asset_x, series_y, series_x)
        if signal is None or signal.action_y == "HOLD" or signal.action_y == "EXIT":
            return []

        v = (venue or self.default_venue).strip().lower()
        env = (environment or self.default_environment).strip().lower()

        last_py = float(series_y[-1])
        last_px = float(series_x[-1])

        # Size asset X proportionally to hedge ratio beta if not specified
        beta = abs(signal.hedge_ratio)
        effective_qx = float(quantity_x) if quantity_x is not None else float(quantity_y * beta)
        effective_qx = max(0.001, effective_qx)

        # Asset Y Intent
        if signal.action_y == "BUY":
            sl_y = last_py * (1.0 - sl_pct)
            tp_y = last_py * (1.0 + tp_pct)
        else:
            sl_y = last_py * (1.0 + sl_pct)
            tp_y = last_py * (1.0 - tp_pct)

        # Asset X Intent (Inverse hedge)
        if signal.action_x == "BUY":
            sl_x = last_px * (1.0 - sl_pct)
            tp_x = last_px * (1.0 + tp_pct)
        else:
            sl_x = last_px * (1.0 + sl_pct)
            tp_x = last_px * (1.0 - tp_pct)

        pair_group_id = uuid.uuid4().hex[:12]
        intent_y = TradeIntent(
            venue=v,
            symbol=asset_y,
            side=signal.action_y,
            order_type="MARKET",
            quantity=float(quantity_y),
            environment=env,
            stop_loss=float(sl_y),
            take_profit=float(tp_y),
            strategy="stat_arb_pairs",
            client_order_id=f"stx-pairs-{pair_group_id}-y",
            metadata={
                "pair_role": "asset_y",
                "partner_symbol": asset_x,
                "z_score": signal.z_score,
                "hedge_ratio": signal.hedge_ratio,
                "confidence": signal.confidence,
                "pair_group_id": pair_group_id,
            },
        )

        intent_x = TradeIntent(
            venue=v,
            symbol=asset_x,
            side=signal.action_x,
            order_type="MARKET",
            quantity=float(effective_qx),
            environment=env,
            stop_loss=float(sl_x),
            take_profit=float(tp_x),
            strategy="stat_arb_pairs",
            client_order_id=f"stx-pairs-{pair_group_id}-x",
            metadata={
                "pair_role": "asset_x",
                "partner_symbol": asset_y,
                "z_score": signal.z_score,
                "hedge_ratio": signal.hedge_ratio,
                "confidence": signal.confidence,
                "pair_group_id": pair_group_id,
            },
        )

        return [intent_y, intent_x]

    # ==========================================================================
    # 2. KALMAN FILTER MEAN REVERSION PROPOSALS
    # ==========================================================================
    def propose_kalman_intent(
        self,
        symbol: str,
        prices: list[float] | np.ndarray,
        quantity: float,
        current_price: float,
        venue: str | None = None,
        environment: str | None = None,
        stop_pct: float = 0.02,
        tp_pct: float = 0.04,
    ) -> TradeIntent | None:
        """
        Evaluate price series with 1D state-space Kalman Filter.
        Emits mean-reversion TradeIntent with deterministic protective levels.
        """
        eval_result = self.kalman_model.evaluate_price_series(prices)
        if not eval_result or eval_result["action"] not in {"BUY", "SELL"}:
            return None

        v = (venue or self.default_venue).strip().lower()
        env = (environment or self.default_environment).strip().lower()
        action = eval_result["action"]

        if action == "BUY":
            stop_loss = current_price * (1.0 - stop_pct)
            take_profit = current_price * (1.0 + tp_pct)
        else:
            stop_loss = current_price * (1.0 + stop_pct)
            take_profit = current_price * (1.0 - tp_pct)

        return TradeIntent(
            venue=v,
            symbol=symbol,
            side=action,
            order_type="MARKET",
            quantity=float(quantity),
            environment=env,
            stop_loss=float(stop_loss),
            take_profit=float(take_profit),
            strategy="kalman_mean_reversion",
            client_order_id=f"stx-kalman-{uuid.uuid4().hex[:20]}",
            metadata={
                "latent_price": eval_result.get("latent_price"),
                "innovation_error": eval_result.get("innovation_error"),
                "z_score": eval_result.get("z_score"),
                "confidence": eval_result.get("confidence"),
            },
        )

    # ==========================================================================
    # 3. VOLATILITY REGIME BREAKOUT PROPOSALS
    # ==========================================================================
    def propose_regime_breakout_intent(
        self,
        symbol: str,
        candles: list[dict[str, Any]],
        quantity: float,
        venue: str | None = None,
        environment: str | None = None,
    ) -> TradeIntent | None:
        """
        Evaluate OHLCV candle series with dynamic volatility regime classifier.
        Emits breakout TradeIntent with ATR Chandelier Exits.
        """
        eval_result = self.regime_model.evaluate(candles)
        if not eval_result or eval_result["action"] not in {"BUY", "SELL"}:
            return None

        v = (venue or self.default_venue).strip().lower()
        env = (environment or self.default_environment).strip().lower()
        action = eval_result["action"]
        sl = eval_result.get("stop_loss")
        tp = eval_result.get("take_profit")
        cur_price = eval_result.get("current_price", 100.0)

        # Enforce positive finite bounds
        if sl is None or not math.isfinite(sl) or sl <= 0:
            sl = cur_price * (0.98 if action == "BUY" else 1.02)
        if tp is None or not math.isfinite(tp) or tp <= 0:
            tp = cur_price * (1.04 if action == "BUY" else 0.96)

        return TradeIntent(
            venue=v,
            symbol=symbol,
            side=action,
            order_type="MARKET",
            quantity=float(quantity),
            environment=env,
            stop_loss=float(sl),
            take_profit=float(tp),
            strategy="volatility_regime_breakout",
            client_order_id=f"stx-regime-{uuid.uuid4().hex[:20]}",
            metadata={
                "regime": eval_result.get("regime"),
                "atr": eval_result.get("atr"),
                "confidence": eval_result.get("confidence"),
            },
        )

    # ==========================================================================
    # 4. INTENT EXECUTION VIA ORDER MANAGER
    # ==========================================================================
    def execute(self, intent: TradeIntent) -> OMSResult | dict[str, Any]:
        """
        Submit a single TradeIntent to the StratexOrderManager.
        If order manager is not attached, returns dry-run proposal record.
        """
        if self.order_manager is None:
            logger.info("[QUANT_AGENT] Dry-run proposal generated for %s (no OMS attached)", intent.client_order_id)
            return {
                "status": "PROPOSED_DRY_RUN",
                "client_order_id": intent.client_order_id,
                "venue": intent.venue,
                "approved": False,
                "reason": "Dry run: no OMS attached",
                "intent": intent.to_dict(),
            }

        return self.order_manager.submit(intent)

    def execute_many(self, intents: list[TradeIntent]) -> list[OMSResult | dict[str, Any]]:
        """Submit a collection of TradeIntents sequentially."""
        return [self.execute(intent) for intent in intents]
