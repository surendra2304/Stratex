"""
tests/test_quant_agent.py — Comprehensive Unit Tests for QuantitativeStrategyAgent.

Verifies:
1. Health check & configuration reporting.
2. Statistical Arbitrage / Pairs Trading intent generation with correlated hedging.
3. Kalman Filter Mean Reversion intent generation with dynamic SL/TP levels.
4. Volatility Regime Breakout intent generation with ATR Chandelier Exits.
5. Dry-run execution mode when OMS is unattached.
6. Real execution forwarding and batch submission through StratexOrderManager.
"""
from unittest.mock import MagicMock

import numpy as np
import pytest

from stratex_agents.oms import OMSResult, StratexOrderManager
from stratex_agents.order_intent import TradeIntent
from stratex_agents.quant_agent import QuantitativeStrategyAgent


class TestQuantitativeStrategyAgent:
    @pytest.fixture
    def agent(self):
        return QuantitativeStrategyAgent(
            order_manager=None,
            default_venue="binance",
            default_environment="binance_testnet",
        )

    def test_health_check(self, agent):
        health = agent.health_check()
        assert health["status"] == "healthy"
        assert health["agent"] == "quant_agent"
        assert health["has_oms"] is False
        assert health["default_venue"] == "binance"
        assert health["default_environment"] == "binance_testnet"
        assert "statarb" in health["models_loaded"]
        assert "kalman_filter" in health["models_loaded"]
        assert "regime_breakout" in health["models_loaded"]

    # ==========================================================================
    # PAIRS TRADING PROPOSALS
    # ==========================================================================
    def test_propose_pairs_intents_insufficient_data(self, agent):
        intents = agent.propose_pairs_intents("ETHUSDT", "BTCUSDT", [100.0] * 10, [200.0] * 10, quantity_y=1.0)
        assert intents == []

    def test_propose_pairs_intents_underpriced_spread(self, agent):
        np.random.seed(42)
        base_x = np.linspace(100, 150, 50)
        base_y = 1.5 * base_x + np.random.normal(0, 0.5, 50)
        # Drop Y moderately to produce underpriced spread within entry bounds (BUY Y, SELL X)
        base_y[-1] -= 1.2

        intents = agent.propose_pairs_intents("ETHUSDT", "BTCUSDT", base_y, base_x, quantity_y=1.0)
        assert len(intents) == 2

        intent_y, intent_x = intents
        assert isinstance(intent_y, TradeIntent)
        assert isinstance(intent_x, TradeIntent)

        assert intent_y.symbol == "ETHUSDT"
        assert intent_y.side == "BUY"
        assert intent_y.stop_loss < base_y[-1]
        assert intent_y.take_profit > base_y[-1]
        assert intent_y.strategy == "stat_arb_pairs"

        assert intent_x.symbol == "BTCUSDT"
        assert intent_x.side == "SELL"
        assert intent_x.stop_loss > base_x[-1]
        assert intent_x.take_profit < base_x[-1]
        assert intent_x.strategy == "stat_arb_pairs"

        assert intent_y.metadata["pair_group_id"] == intent_x.metadata["pair_group_id"]
        assert intent_y.metadata["pair_role"] == "asset_y"
        assert intent_x.metadata["pair_role"] == "asset_x"

    def test_propose_pairs_intents_overpriced_spread(self, agent):
        np.random.seed(42)
        base_x = np.linspace(100, 150, 50)
        base_y = 1.5 * base_x + np.random.normal(0, 0.5, 50)
        # Spike Y moderately to produce overpriced spread within entry bounds (SELL Y, BUY X)
        base_y[-1] += 2.0

        intents = agent.propose_pairs_intents("ETHUSDT", "BTCUSDT", base_y, base_x, quantity_y=2.0)
        assert len(intents) == 2
        intent_y, intent_x = intents

        assert intent_y.side == "SELL"
        assert intent_x.side == "BUY"
        assert intent_y.stop_loss > base_y[-1]
        assert intent_y.take_profit < base_y[-1]
        assert intent_x.stop_loss < base_x[-1]
        assert intent_x.take_profit > base_x[-1]

    def test_propose_pairs_intents_neutral_spread(self, agent):
        np.random.seed(42)
        base_x = np.linspace(100, 150, 40)
        base_y = 1.5 * base_x + np.random.normal(0, 0.05, 40)
        # Spread is near 0 -> no action (HOLD or EXIT)
        intents = agent.propose_pairs_intents("ETHUSDT", "BTCUSDT", base_y, base_x, quantity_y=1.0)
        assert intents == []

    # ==========================================================================
    # KALMAN FILTER MEAN REVERSION PROPOSALS
    # ==========================================================================
    def test_propose_kalman_intent_insufficient_data(self, agent):
        intent = agent.propose_kalman_intent("BTCUSDT", [100.0, 101.0], quantity=0.1, current_price=101.0)
        assert intent is None

    def test_propose_kalman_intent_buy_on_drop(self, agent):
        # Steady price with sudden drop -> BUY
        prices = [100.0] * 30 + [85.0]
        intent = agent.propose_kalman_intent("BTCUSDT", prices, quantity=0.05, current_price=85.0)
        assert intent is not None
        assert intent.symbol == "BTCUSDT"
        assert intent.side == "BUY"
        assert intent.quantity == 0.05
        assert intent.stop_loss == pytest.approx(85.0 * 0.98)
        assert intent.take_profit == pytest.approx(85.0 * 1.04)
        assert intent.strategy == "kalman_mean_reversion"
        assert intent.metadata["z_score"] <= -2.0

    def test_propose_kalman_intent_sell_on_spike(self, agent):
        # Steady price with sudden spike -> SELL
        prices = [100.0] * 30 + [115.0]
        intent = agent.propose_kalman_intent("BTCUSDT", prices, quantity=0.05, current_price=115.0)
        assert intent is not None
        assert intent.symbol == "BTCUSDT"
        assert intent.side == "SELL"
        assert intent.quantity == 0.05
        assert intent.stop_loss == pytest.approx(115.0 * 1.02)
        assert intent.take_profit == pytest.approx(115.0 * 0.96)
        assert intent.strategy == "kalman_mean_reversion"
        assert intent.metadata["z_score"] >= 2.0

    def test_propose_kalman_intent_steady_hold(self, agent):
        prices = [100.0 + (i % 2) * 0.01 for i in range(35)]
        intent = agent.propose_kalman_intent("BTCUSDT", prices, quantity=0.05, current_price=100.0)
        assert intent is None

    # ==========================================================================
    # REGIME BREAKOUT PROPOSALS
    # ==========================================================================
    def test_propose_regime_breakout_insufficient_candles(self, agent):
        candles = [{"open": 100, "high": 105, "low": 95, "close": 100} for _ in range(10)]
        assert agent.propose_regime_breakout_intent("SOLUSDT", candles, quantity=1.0) is None

    def test_propose_regime_breakout_chaos_preservation(self, agent):
        # Extreme volatility regime -> HOLD
        candles = []
        for i in range(50):
            p = 100.0 if i % 2 == 0 else 180.0
            candles.append({"open": p, "high": p + 20, "low": p - 20, "close": p})
        intent = agent.propose_regime_breakout_intent("SOLUSDT", candles, quantity=1.0)
        assert intent is None

    def test_propose_regime_breakout_trending_buy(self, agent):
        candles = []
        for i in range(50):
            p = 100.0 + i * 2.0
            candles.append({"open": p - 1, "high": p + 1, "low": p - 1, "close": p})
        candles[-1]["close"] = candles[-1]["high"]

        intent = agent.propose_regime_breakout_intent("SOLUSDT", candles, quantity=2.0)
        assert intent is not None
        assert intent.symbol == "SOLUSDT"
        assert intent.side == "BUY"
        assert intent.quantity == 2.0
        assert intent.strategy == "volatility_regime_breakout"
        assert intent.stop_loss < intent.take_profit

    def test_propose_regime_breakout_trending_sell_and_fallbacks(self, agent):
        candles = []
        for i in range(50):
            p = 200.0 - i * 2.0
            candles.append({"open": p + 1, "high": p + 1, "low": p - 1, "close": p})
        candles[-1]["close"] = candles[-1]["low"]

        intent = agent.propose_regime_breakout_intent("SOLUSDT", candles, quantity=2.0)
        assert intent is not None
        assert intent.side == "SELL"
        assert intent.stop_loss > intent.take_profit

        # Test fallback bounds when model returns non-positive SL/TP
        mock_regime_model = MagicMock()
        mock_regime_model.evaluate.return_value = {
            "action": "BUY",
            "stop_loss": -10.0,  # Invalid non-positive
            "take_profit": 0.0,   # Invalid non-positive
            "current_price": 100.0,
            "regime": "LOW_VOL_TRENDING",
            "atr": 1.0,
            "confidence": 0.8,
        }
        agent_with_fallback = QuantitativeStrategyAgent(regime_model=mock_regime_model)
        fallback_intent = agent_with_fallback.propose_regime_breakout_intent("SOLUSDT", candles, quantity=1.0)
        assert fallback_intent is not None
        assert fallback_intent.stop_loss == 98.0
        assert fallback_intent.take_profit == 104.0

    # ==========================================================================
    # EXECUTION PIPELINE (DRY-RUN & OMS)
    # ==========================================================================
    def test_execute_dry_run_without_oms(self, agent):
        intent = TradeIntent(
            venue="binance",
            symbol="BTCUSDT",
            side="BUY",
            order_type="MARKET",
            quantity=0.01,
            environment="binance_testnet",
            stop_loss=90.0,
            take_profit=110.0,
            strategy="test_strategy",
        )
        res = agent.execute(intent)
        assert res["status"] == "PROPOSED_DRY_RUN"
        assert res["approved"] is False
        assert "no OMS attached" in res["reason"]

    def test_execute_with_attached_oms(self):
        mock_oms = MagicMock(spec=StratexOrderManager)
        mock_oms.submit.return_value = OMSResult(
            client_order_id="stx-test-123",
            venue="binance",
            status="ACCEPTED",
            result={"orderId": 9999},
        )

        agent = QuantitativeStrategyAgent(order_manager=mock_oms)
        intent = TradeIntent(
            venue="binance",
            symbol="BTCUSDT",
            side="BUY",
            order_type="MARKET",
            quantity=0.01,
            environment="binance_testnet",
            stop_loss=90.0,
            take_profit=110.0,
            strategy="test_strategy",
            client_order_id="stx-test-123",
        )

        res = agent.execute(intent)
        mock_oms.submit.assert_called_once_with(intent)
        assert isinstance(res, OMSResult)
        assert res.status == "ACCEPTED"
        assert res.client_order_id == "stx-test-123"

    def test_execute_many(self):
        mock_oms = MagicMock(spec=StratexOrderManager)
        mock_oms.submit.side_effect = lambda it: OMSResult(
            client_order_id=it.client_order_id,
            venue=it.venue,
            status="ACCEPTED",
        )

        agent = QuantitativeStrategyAgent(order_manager=mock_oms)
        intents = [
            TradeIntent(
                venue="binance",
                symbol="BTCUSDT",
                side="BUY",
                order_type="MARKET",
                quantity=0.01,
                environment="binance_testnet",
                stop_loss=90.0,
                take_profit=110.0,
                strategy="test_strategy",
                client_order_id=f"stx-multi-{i}",
            )
            for i in range(3)
        ]

        results = agent.execute_many(intents)
        assert len(results) == 3
        assert mock_oms.submit.call_count == 3
        for res, it in zip(results, intents):
            assert res.client_order_id == it.client_order_id
            assert res.status == "ACCEPTED"

    def test_build_quant_agent_factory(self):
        from stratex_agents.runtime import build_quant_agent
        agent = build_quant_agent(default_venue="mt5", default_environment="mt5_demo")
        assert isinstance(agent, QuantitativeStrategyAgent)
        assert agent.default_venue == "mt5"
        assert agent.default_environment == "mt5_demo"
        assert agent.order_manager is None
