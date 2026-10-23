"""
tests/test_mt5_autonomous_engine.py — Comprehensive Unit & Integration Tests for MT5 Autonomous Engine.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from autonomy.ai_advisor import AITradeVerdict
from autonomy.agent_contracts import (
    FuturisForecast,
    IntelXResearch,
    SentinelPolicyResult,
)
from autonomy.mt5_autonomous_engine import (
    CloudPosition,
    ConsensusDecision,
    MT5AutonomousEngine,
    OpportunitySignal,
    get_autonomous_engine,
)
from exchanges.base_exchange import UnifiedBalance, UnifiedPosition
from exchanges.mt5_bridge_adapter import MT5BridgeError
from notifications.telegram_bot import TelegramBotClient
from strategies.quantitative_engine import MarketRegime


def _generate_synthetic_candles(
    n: int = 60,
    base_price: float = 1.1000,
    trend: float = 0.0001,
    volatility: float = 0.0005,
) -> list[dict[str, float]]:
    """Generate realistic OHLCV candles."""
    candles = []
    current = base_price
    start_ts = int(time.time()) - (n * 300)

    for i in range(n):
        open_p = current
        close_p = open_p + trend + (volatility if i % 2 == 0 else -volatility * 0.8)
        high_p = max(open_p, close_p) + abs(volatility * 0.5)
        low_p = min(open_p, close_p) - abs(volatility * 0.5)
        candles.append({
            "timestamp": start_ts + (i * 300),
            "open": open_p,
            "high": high_p,
            "low": low_p,
            "close": close_p,
            "volume": 100.0,
        })
        current = close_p
    return candles


@pytest.fixture
def mock_adapter():
    adapter = MagicMock()
    adapter.get_positions.return_value = []
    adapter.get_balance.return_value = {
        "USD": UnifiedBalance(currency="USD", free=999.82, used=0.0, total=999.82)
    }
    adapter.get_equity.return_value = {"USD": 999.82}
    adapter.calculate_position_size.return_value = {
        "approved": True,
        "recommended_volume": 0.02,
        "volume_step": 0.01,
        "volume_min": 0.01,
        "risk_amount": 15.0,
        "margin_required": 50.0,
        "account_currency": "USD",
        "risk_budget": 20.0,
        "margin_budget": 200.0,
    }
    adapter.order_check.return_value = {"retcode": 0, "comment": "Done"}
    adapter.submit_intent.return_value = {
        "status": "FILLED",
        "order_id": "mt5-deal-12345",
        "symbol": "EURUSD",
        "side": "BUY",
        "avg_price": 1.1050,
        "executed_qty": 0.02,
    }
    return adapter


@pytest.fixture
def mock_futuris():
    client = MagicMock()
    client.get_forecast.return_value = FuturisForecast(
        forecast_id="fc-1",
        symbol="EURUSD",
        horizon="15m",
        direction="BULLISH",
        expected_return=0.002,
        uncertainty=0.01,
        calibration_score=0.85,
        timestamp="2026-10-21T12:00:00Z",
    )
    return client


@pytest.fixture
def mock_intelx():
    client = MagicMock()
    client.get_market_intelligence.return_value = IntelXResearch(
        research_id="ix-1",
        symbol="EURUSD",
        sentiment_score=0.3,
        confidence=0.8,
        evidence_summary="Positive macro sentiment on EUR",
        citations=[],
        timestamp="2026-10-21T12:00:00Z",
    )
    return client


@pytest.fixture
def mock_sentinel():
    client = MagicMock()
    client.verify_action_authorization.return_value = SentinelPolicyResult(
        allowed=True,
        reason="policy_approved",
        policy_id="pol-1",
        audit_id="aud-1",
    )
    return client


@pytest.fixture
def mock_memora():
    client = MagicMock()
    client.record_scoped_event.return_value = True
    return client


@pytest.fixture
def mock_friday():
    client = MagicMock()
    client.broadcast_trade_event.return_value = True
    return client


@pytest.fixture
def mock_ai_advisor():
    advisor = MagicMock()
    advisor.evaluate_trade_candidate.return_value = AITradeVerdict(
        approved=True,
        verdict="APPROVED",
        confidence=0.9,
        thesis="Explicit test fixture approval.",
        risk_factors=[],
        model_used="test",
    )
    return advisor


@pytest.fixture
def mock_telegram():
    bot = MagicMock()
    bot.notify_trade_intent.return_value = True
    bot.notify_risk_rejection.return_value = True
    bot.send_message.return_value = True
    return bot


@pytest.fixture
def engine(
    mock_adapter,
    mock_futuris,
    mock_intelx,
    mock_sentinel,
    mock_memora,
    mock_friday,
    mock_telegram,
    mock_ai_advisor,
):
    return MT5AutonomousEngine(
        adapter=mock_adapter,
        symbols=["EURUSD", "GBPUSD", "XAUUSD"],
        timeframes=["5m", "15m"],
        max_active_positions=3,
        max_risk_per_trade_pct=0.02,
        min_confidence=0.60,
        dry_run=False,
        futuris_client=mock_futuris,
        intelx_client=mock_intelx,
        sentinel_client=mock_sentinel,
        memora_client=mock_memora,
        friday_client=mock_friday,
        telegram_bot=mock_telegram,
        ai_advisor=mock_ai_advisor,
    )


class TestMT5AutonomousEngine:
    def test_initialization(self, engine):
        assert "EURUSD" in engine.symbols
        assert "XAUUSD" in engine.symbols
        assert engine.max_active_positions == 3
        assert engine.max_risk_per_trade_pct == 0.02
        assert engine.dry_run is False

    def test_evaluate_symbol_timeframe_insufficient_candles(self, engine, mock_adapter):
        mock_adapter.get_historical_data.return_value = [{"close": 1.1000}]
        signal = engine.evaluate_symbol_timeframe("EURUSD", "15m")
        assert signal is None

    def test_evaluate_symbol_timeframe_breakout_buy(self, engine):
        candles = _generate_synthetic_candles(n=50, base_price=1.1000, trend=0.0003, volatility=0.0002)
        # Push last candle to break out above highs
        candles[-1]["close"] = 1.1200
        candles[-1]["high"] = 1.1210
        candles[-1]["low"] = 1.1190

        signal = engine.evaluate_symbol_timeframe("EURUSD", "15m", candles=candles)
        assert signal is not None
        assert signal.action == "BUY"
        assert signal.stop_loss < signal.current_price
        assert signal.take_profit > signal.current_price
        assert signal.risk_reward_ratio >= 1.4

    def test_evaluate_symbol_timeframe_breakout_sell(self, engine):
        candles = _generate_synthetic_candles(n=50, base_price=1.2000, trend=-0.0003, volatility=0.0002)
        # Push last candle to break out below lows
        candles[-1]["close"] = 1.1800
        candles[-1]["high"] = 1.1810
        candles[-1]["low"] = 1.1790

        signal = engine.evaluate_symbol_timeframe("GBPUSD", "15m", candles=candles)
        assert signal is not None
        assert signal.action == "SELL"
        assert signal.stop_loss > signal.current_price
        assert signal.take_profit < signal.current_price
        assert signal.risk_reward_ratio >= 1.4

    def test_peer_consensus_approved(self, engine):
        signal = OpportunitySignal(
            symbol="EURUSD",
            timeframe="15m",
            action="BUY",
            regime=MarketRegime.LOW_VOL_TRENDING,
            current_price=1.1050,
            stop_loss=1.1000,
            take_profit=1.1150,
            risk_reward_ratio=2.0,
            confidence=0.80,
            kalman_z=0.5,
            atr=0.002,
            strategy_source="volatility_regime_breakout",
        )
        decision = engine.consult_peer_mesh(signal)
        assert decision.approved is True
        assert decision.verdict == "APPROVED"
        assert decision.size_multiplier == 1.0
        assert decision.sentinel_approved is True

    def test_peer_consensus_ai_rejection_blocks_trade(self, engine, mock_ai_advisor):
        mock_ai_advisor.evaluate_trade_candidate.return_value = AITradeVerdict(
            approved=False,
            verdict="REJECTED",
            confidence=0.0,
            thesis="AI provider unavailable; approval withheld.",
            risk_factors=["provider unavailable"],
            model_used="unavailable",
        )
        signal = OpportunitySignal(
            symbol="EURUSD",
            timeframe="15m",
            action="BUY",
            regime=MarketRegime.LOW_VOL_TRENDING,
            current_price=1.1050,
            stop_loss=1.1000,
            take_profit=1.1150,
            risk_reward_ratio=2.0,
            confidence=0.80,
            kalman_z=0.5,
            atr=0.002,
            strategy_source="volatility_regime_breakout",
        )

        decision = engine.consult_peer_mesh(signal)

        assert decision.approved is False
        assert decision.verdict == "REJECTED"
        assert decision.size_multiplier == 0.0
        assert any("approval withheld" in reason.lower() for reason in decision.reasons)


    def test_peer_consensus_sentinel_rejection(self, engine, mock_sentinel):
        mock_sentinel.verify_action_authorization.return_value = SentinelPolicyResult(
            allowed=False,
            reason="daily_drawdown_limit_exceeded",
            policy_id="pol-risk",
            audit_id="aud-9",
        )
        signal = OpportunitySignal(
            symbol="EURUSD",
            timeframe="15m",
            action="BUY",
            regime=MarketRegime.LOW_VOL_TRENDING,
            current_price=1.1050,
            stop_loss=1.1000,
            take_profit=1.1150,
            risk_reward_ratio=2.0,
            confidence=0.80,
            kalman_z=0.5,
            atr=0.002,
            strategy_source="volatility_regime_breakout",
        )
        decision = engine.consult_peer_mesh(signal)
        assert decision.approved is False
        assert decision.verdict == "REJECTED"
        assert decision.size_multiplier == 0.0
        assert "Sentinel rejected" in decision.reasons[0]

    def test_peer_consensus_futuris_contradiction_skips(self, engine, mock_futuris):
        mock_futuris.get_forecast.return_value = FuturisForecast(
            forecast_id="fc-bear",
            symbol="EURUSD",
            horizon="15m",
            direction="BEARISH",
            expected_return=-0.005,
            uncertainty=0.01,
            calibration_score=0.90,  # High confidence contradiction
            timestamp="2026-10-21T12:00:00Z",
        )
        signal = OpportunitySignal(
            symbol="EURUSD",
            timeframe="15m",
            action="BUY",
            regime=MarketRegime.LOW_VOL_TRENDING,
            current_price=1.1050,
            stop_loss=1.1000,
            take_profit=1.1150,
            risk_reward_ratio=2.0,
            confidence=0.80,
            kalman_z=0.5,
            atr=0.002,
            strategy_source="volatility_regime_breakout",
        )
        decision = engine.consult_peer_mesh(signal)
        assert decision.approved is False
        assert decision.verdict == "SKIPPED"
        assert decision.size_multiplier == 0.0

    def test_calculate_lot_size_broker_and_fallback(self, engine, mock_adapter):
        # Broker-aware sizing
        lot = engine.calculate_lot_size("EURUSD", "BUY", 1.1050, 1.1000, 1.1150, multiplier=1.0)
        assert lot == 0.02

        # Fallback sizing when calculate_position_size fails
        mock_adapter.calculate_position_size.side_effect = RuntimeError("Bridge unavailable")
        lot_fallback = engine.calculate_lot_size("EURUSD", "BUY", 1.1050, 1.1000, 1.1150, multiplier=1.0)
        assert lot_fallback > 0.0
        assert lot_fallback <= 0.10

    def test_execute_signal_full_lifecycle(
        self,
        engine,
        mock_adapter,
        mock_telegram,
        mock_memora,
        mock_friday,
    ):
        signal = OpportunitySignal(
            symbol="EURUSD",
            timeframe="15m",
            action="BUY",
            regime=MarketRegime.LOW_VOL_TRENDING,
            current_price=1.1050,
            stop_loss=1.1000,
            take_profit=1.1150,
            risk_reward_ratio=2.0,
            confidence=0.85,
            kalman_z=0.3,
            atr=0.002,
            strategy_source="volatility_regime_breakout",
        )
        decision = ConsensusDecision(
            approved=True,
            verdict="APPROVED",
            size_multiplier=1.0,
            sentinel_approved=True,
            futuris_direction="BULLISH",
            intelx_sentiment=0.3,
            reasons=(),
        )

        result = engine.execute_signal(signal, decision)
        assert result["status"] == "FILLED"
        assert result["order_id"] == "mt5-deal-12345"
        assert result["symbol"] == "EURUSD"

        # Telegram verification
        assert mock_telegram.notify_trade_intent.call_count == 2  # PROPOSED and FILLED
        # Memora verification
        mock_memora.record_scoped_event.assert_called_once()
        args, kwargs = mock_memora.record_scoped_event.call_args
        assert kwargs["event_type"] == "MT5_TRADE_EXECUTED"
        assert kwargs["scope"] == "Universal"
        # Friday Gateway verification
        assert mock_friday.broadcast_trade_event.call_count == 2

    def test_execute_signal_dry_run(self, engine, mock_adapter):
        engine.dry_run = True
        signal = OpportunitySignal(
            symbol="XAUUSD",
            timeframe="5m",
            action="BUY",
            regime=MarketRegime.LOW_VOL_TRENDING,
            current_price=2650.0,
            stop_loss=2640.0,
            take_profit=2670.0,
            risk_reward_ratio=2.0,
            confidence=0.80,
            kalman_z=0.1,
            atr=5.0,
            strategy_source="volatility_regime_breakout",
        )
        decision = ConsensusDecision(
            approved=True,
            verdict="APPROVED",
            size_multiplier=1.0,
            sentinel_approved=True,
            futuris_direction="BULLISH",
            intelx_sentiment=0.4,
            reasons=(),
        )
        result = engine.execute_signal(signal, decision)
        assert result["status"] == "SIMULATED_FILLED"
        mock_adapter.submit_intent.assert_not_called()

    def test_scan_market_respects_max_active_positions(self, engine, mock_adapter):
        # Return 3 active positions (equal to max_active_positions)
        mock_adapter.get_positions.return_value = [
            UnifiedPosition("EURUSD", "LONG", 0.02, 1.1000, 1.1020, 4.0, 1.0, "mt5", "1"),
            UnifiedPosition("GBPUSD", "LONG", 0.02, 1.2500, 1.2520, 4.0, 1.0, "mt5", "2"),
            UnifiedPosition("USDJPY", "SHORT", 0.02, 150.0, 149.80, 4.0, 1.0, "mt5", "3"),
        ]
        executed = engine.scan_market()
        assert executed == []
        mock_adapter.get_historical_data.assert_not_called()

    @patch("autonomy.mt5_autonomous_engine.is_panic_active", return_value=True)
    def test_scan_market_aborts_on_panic(self, mock_panic, engine):
        executed = engine.scan_market()
        assert executed == []

    def test_singleton_accessor(self):
        engine1 = get_autonomous_engine()
        engine2 = get_autonomous_engine()
        assert engine1 is engine2

    def test_run_continuous_loop_bounded(self, engine):
        with patch.object(engine, "scan_market") as mock_scan:
            engine.run_continuous_loop(poll_interval_sec=0.01, max_iterations=2)
            assert mock_scan.call_count == 2
            engine.telegram.start_polling_in_background.assert_called_once()

    def test_evaluate_symbol_timeframe_chaos_regime_returns_none(self, engine):
        candles = _generate_synthetic_candles(n=50)
        with patch.object(engine.breakout_model, "evaluate", return_value={"regime": MarketRegime.EXTREME_VOL_CHAOS}):
            signal = engine.evaluate_symbol_timeframe("EURUSD", "15m", candles=candles)
            assert signal is None

    def test_evaluate_symbol_timeframe_kalman_overextension_filtered(self, engine):
        candles = _generate_synthetic_candles(n=50)
        with patch.object(
            engine.breakout_model,
            "evaluate",
            return_value={
                "regime": MarketRegime.LOW_VOL_TRENDING,
                "action": "BUY",
                "confidence": 0.8,
                "stop_loss": 1.09,
                "take_profit": 1.13,
                "atr": 0.002,
            },
        ):
            with patch.object(
                engine.kalman_model,
                "evaluate_price_series",
                return_value={"z_score": 3.0},  # Overextended BUY
            ):
                signal = engine.evaluate_symbol_timeframe("EURUSD", "15m", candles=candles)
                assert signal is None

    def test_evaluate_symbol_timeframe_choppy_mean_reversion_buy(self, engine):
        candles = _generate_synthetic_candles(n=50, base_price=1.1000)
        with patch.object(
            engine.breakout_model,
            "evaluate",
            return_value={
                "regime": MarketRegime.CHOP_MEAN_REVERTING,
                "action": "HOLD",
                "atr": 0.002,
            },
        ):
            with patch.object(
                engine.kalman_model,
                "evaluate_price_series",
                return_value={"action": "BUY", "confidence": 0.75, "z_score": -2.2},
            ):
                signal = engine.evaluate_symbol_timeframe("EURUSD", "15m", candles=candles)
                assert signal is not None
                assert signal.action == "BUY"
                assert signal.strategy_source == "kalman_mean_reversion"
                assert signal.stop_loss < signal.current_price < signal.take_profit

    def test_evaluate_symbol_timeframe_choppy_mean_reversion_sell(self, engine):
        candles = _generate_synthetic_candles(n=50, base_price=1.1000)
        with patch.object(
            engine.breakout_model,
            "evaluate",
            return_value={
                "regime": MarketRegime.CHOP_MEAN_REVERTING,
                "action": "HOLD",
                "atr": 0.002,
            },
        ):
            with patch.object(
                engine.kalman_model,
                "evaluate_price_series",
                return_value={"action": "SELL", "confidence": 0.75, "z_score": 2.2},
            ):
                signal = engine.evaluate_symbol_timeframe("EURUSD", "15m", candles=candles)
                assert signal is not None
                assert signal.action == "SELL"
                assert signal.strategy_source == "kalman_mean_reversion"
                assert signal.take_profit < signal.current_price < signal.stop_loss

    def test_evaluate_symbol_timeframe_invalid_geometry_returns_none(self, engine):
        candles = _generate_synthetic_candles(n=50)
        # Invalid geometry: stop_loss > current_price on a BUY
        with patch.object(
            engine.breakout_model,
            "evaluate",
            return_value={
                "regime": MarketRegime.LOW_VOL_TRENDING,
                "action": "BUY",
                "confidence": 0.8,
                "stop_loss": 1.25,  # higher than price
                "take_profit": 1.30,
                "atr": 0.002,
            },
        ):
            signal = engine.evaluate_symbol_timeframe("EURUSD", "15m", candles=candles)
            assert signal is None

    def test_execute_signal_failure_handling(self, engine, mock_adapter, mock_telegram):
        signal = OpportunitySignal(
            symbol="EURUSD",
            timeframe="15m",
            action="BUY",
            regime=MarketRegime.LOW_VOL_TRENDING,
            current_price=1.1050,
            stop_loss=1.1000,
            take_profit=1.1150,
            risk_reward_ratio=2.0,
            confidence=0.85,
            kalman_z=0.3,
            atr=0.002,
            strategy_source="volatility_regime_breakout",
        )
        decision = ConsensusDecision(
            approved=True,
            verdict="APPROVED",
            size_multiplier=1.0,
            sentinel_approved=True,
            futuris_direction="BULLISH",
            intelx_sentiment=0.3,
            reasons=(),
        )
        mock_adapter.submit_intent.side_effect = MT5BridgeError("Connection timed out")
        result = engine.execute_signal(signal, decision)
        assert result["status"] == "FAILED"
        assert "Connection timed out" in result["error"]
        mock_telegram.notify_risk_rejection.assert_called_once()

    def test_scan_market_success_flow(self, engine):
        signal = OpportunitySignal(
            symbol="EURUSD",
            timeframe="5m",
            action="BUY",
            regime=MarketRegime.LOW_VOL_TRENDING,
            current_price=1.1050,
            stop_loss=1.1000,
            take_profit=1.1150,
            risk_reward_ratio=2.0,
            confidence=0.85,
            kalman_z=0.3,
            atr=0.002,
            strategy_source="volatility_regime_breakout",
        )
        with patch.object(engine, "evaluate_symbol_timeframe", side_effect=[signal, None, None, None, None, None]):
            executed = engine.scan_market()
            assert len(executed) == 1
            assert executed[0]["status"] == "FILLED"
            assert executed[0]["symbol"] == "EURUSD"

    def test_scan_market_peer_rejection_flow(self, engine, mock_sentinel):
        signal = OpportunitySignal(
            symbol="EURUSD",
            timeframe="5m",
            action="BUY",
            regime=MarketRegime.LOW_VOL_TRENDING,
            current_price=1.1050,
            stop_loss=1.1000,
            take_profit=1.1150,
            risk_reward_ratio=2.0,
            confidence=0.85,
            kalman_z=0.3,
            atr=0.002,
            strategy_source="volatility_regime_breakout",
        )
        mock_sentinel.verify_action_authorization.return_value = SentinelPolicyResult(
            allowed=False, reason="leverage_too_high", policy_id="pol", audit_id="aud"
        )
        with patch.object(engine, "evaluate_symbol_timeframe", return_value=signal):
            executed = engine.scan_market()
            assert executed == []


class TestTelegramBotInteractiveCommands:
    def test_telegram_handle_command_positions_with_mock_positions(self):
        bot = TelegramBotClient("token", "123")
        mock_pos = [
            UnifiedPosition("EURUSD", "LONG", 0.05, 1.1000, 1.1050, 25.0, 1.0, "mt5", "101"),
            UnifiedPosition("XAUUSD", "SHORT", 0.02, 2650.0, 2640.0, 20.0, 1.0, "mt5", "102"),
        ]
        with patch("exchanges.mt5_bridge_adapter.MT5BridgeAdapter.get_positions", return_value=mock_pos):
            reply = bot.handle_command("/positions")
            assert "Active MT5 Positions" in reply
            assert "EURUSD" in reply
            assert "XAUUSD" in reply
            assert "Total Unrealized PnL" in reply

    def test_telegram_handle_command_balance_with_mock_balance(self):
        bot = TelegramBotClient("token", "123")
        mock_bals = {
            "USD": UnifiedBalance("USD", free=850.0, used=150.0, total=1000.0)
        }
        with patch("exchanges.mt5_bridge_adapter.MT5BridgeAdapter.get_balance", return_value=mock_bals):
            reply = bot.handle_command("/balance")
            assert "MT5 Wallet Balances" in reply
            assert "$1,000.00 USD" in reply
            assert "Free Margin" in reply

    def test_telegram_handle_command_scan(self):
        bot = TelegramBotClient("token", "123")
        with patch("autonomy.mt5_autonomous_engine.MT5AutonomousEngine.scan_market", return_value=[]):
            reply = bot.handle_command("/scan")
            assert "Market Scan Complete" in reply

    def test_telegram_handle_natural_language_query_with_asset(self):
        bot = TelegramBotClient("token", "123")
        signal = OpportunitySignal(
            symbol="EURUSD",
            timeframe="15m",
            action="BUY",
            regime=MarketRegime.LOW_VOL_TRENDING,
            current_price=1.1050,
            stop_loss=1.1000,
            take_profit=1.1150,
            risk_reward_ratio=2.0,
            confidence=0.85,
            kalman_z=0.2,
            atr=0.002,
            strategy_source="volatility_regime_breakout",
        )
        with patch("autonomy.mt5_autonomous_engine.MT5AutonomousEngine.evaluate_symbol_timeframe", return_value=signal):
            reply = bot.handle_command("What do you think about EURUSD?")
            assert "Stratex Quantitative Analysis: EURUSD" in reply
            assert "BUY" in reply
            assert "LOW_VOL_TRENDING" in reply
            assert "$1.1050" in reply


class TestMT5AutonomousCloudExecution:
    def test_execute_signal_rejects_when_mt5_bridge_is_disabled(self, engine):
        # Without a connected terminal, an approved intent must not fabricate
        # a fill or create a synthetic cloud position.
        engine.adapter.enabled = False
        signal = OpportunitySignal(
            symbol="XAUUSD",
            timeframe="15m",
            action="BUY",
            regime=MarketRegime.LOW_VOL_TRENDING,
            current_price=2650.0,
            stop_loss=2640.0,
            take_profit=2670.0,
            risk_reward_ratio=2.0,
            confidence=0.85,
            kalman_z=0.1,
            atr=5.0,
            strategy_source="volatility_regime_breakout",
        )
        decision = ConsensusDecision(
            approved=True,
            verdict="APPROVED",
            size_multiplier=1.0,
            sentinel_approved=True,
            futuris_direction="BULLISH",
            intelx_sentiment=0.3,
            reasons=(),
            ai_thesis="Breakout above key institutional liquidity zone",
        )

        result = engine.execute_signal(signal, decision)
        assert result["status"] == "REJECTED_NO_BRIDGE"
        assert result["symbol"] == "XAUUSD"
        assert result["error"] == "MT5 Bridge not connected to live terminal"
        assert "XAUUSD" not in engine.cloud_positions
        engine.adapter.submit_intent.assert_not_called()
        engine.memora.record_scoped_event.assert_not_called()
        engine.friday.broadcast_trade_event.assert_not_called()

    def test_manage_cloud_positions_take_profit_hit(self, engine):
        # Simulate active open BUY on XAUUSD
        engine.cloud_positions["XAUUSD"] = CloudPosition(
            position_id="test-tp-1",
            symbol="XAUUSD",
            side="BUY",
            quantity=0.02,
            entry_price=2650.0,
            stop_loss=2640.0,
            take_profit=2670.0,
            mark_price=2650.0,
            unrealized_pnl=0.0,
            opened_at="2026-10-21T12:00:00Z",
            strategy="volatility_regime_breakout",
        )
        engine.cloud_balance = 1000.0

        # Market price hits take-profit (2672.0 >= 2670.0)
        with patch.object(engine.cloud_feed, "get_current_price", return_value=2672.0):
            closed = engine.manage_cloud_positions()
            assert len(closed) == 1
            assert closed[0]["exit_type"] == "TAKE_PROFIT"
            assert closed[0]["symbol"] == "XAUUSD"
            assert "XAUUSD" not in engine.cloud_positions
            # Profit = (2672 - 2650) * 0.02 * 100 = $44.00
            assert engine.cloud_balance == 1044.0

    def test_manage_cloud_positions_stop_loss_hit(self, engine):
        # Simulate active open BUY on XAUUSD
        engine.cloud_positions["XAUUSD"] = CloudPosition(
            position_id="test-sl-1",
            symbol="XAUUSD",
            side="BUY",
            quantity=0.02,
            entry_price=2650.0,
            stop_loss=2640.0,
            take_profit=2670.0,
            mark_price=2650.0,
            unrealized_pnl=0.0,
            opened_at="2026-10-21T12:00:00Z",
            strategy="volatility_regime_breakout",
        )
        engine.cloud_balance = 1000.0

        # Market price hits stop loss (2638.0 <= 2640.0)
        with patch.object(engine.cloud_feed, "get_current_price", return_value=2638.0):
            closed = engine.manage_cloud_positions()
            assert len(closed) == 1
            assert closed[0]["exit_type"] == "STOP_LOSS"
            assert closed[0]["symbol"] == "XAUUSD"
            assert "XAUUSD" not in engine.cloud_positions
            # Loss = (2638 - 2650) * 0.02 * 100 = -$24.00
            assert engine.cloud_balance == 976.0

    def test_evaluate_symbol_timeframe_cloud_feed_fallback(self, engine):
        engine.adapter.enabled = False
        sample_candles = [
            {"timestamp": 1700000000 + i * 900, "open": 1.10 + i * 0.0001, "high": 1.101 + i * 0.0001, "low": 1.099 + i * 0.0001, "close": 1.1005 + i * 0.0001, "volume": 100.0}
            for i in range(50)
        ]
        with patch.object(engine.cloud_feed, "get_candles", return_value=sample_candles):
            with patch.object(engine.breakout_model, "evaluate", return_value={
                "regime": MarketRegime.LOW_VOL_TRENDING,
                "action": "BUY",
                "confidence": 0.82,
                "stop_loss": 1.095,
                "take_profit": 1.130,
                "atr": 0.002,
            }):
                with patch.object(engine.kalman_model, "evaluate_price_series", return_value={"z_score": 0.5}):
                    signal = engine.evaluate_symbol_timeframe("EURUSD", "15m")
                    assert signal is not None
                    assert signal.symbol == "EURUSD"
                    assert signal.action == "BUY"

    def test_get_active_positions_and_balance_cloud_mode(self, engine):
        engine.adapter.enabled = False
        engine.cloud_positions["EURUSD"] = CloudPosition(
            position_id="cld-1",
            symbol="EURUSD",
            side="BUY",
            quantity=0.05,
            entry_price=1.1000,
            stop_loss=1.0950,
            take_profit=1.1100,
            mark_price=1.1020,
            unrealized_pnl=10.0,
            opened_at="2026-10-21T12:00:00Z",
            strategy="volatility_regime_breakout",
        )
        engine.cloud_balance = 1000.0

        positions = engine.get_active_positions()
        assert len(positions) == 1
        assert positions[0].symbol == "EURUSD"

        balances = engine.get_account_balance()
        assert "USD" in balances
        assert balances["USD"]["total"] == 1000.0
        assert balances["USD"]["equity"] == 1010.0  # 1000 + 10 unrealized

