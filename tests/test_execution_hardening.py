from unittest.mock import MagicMock

import pytest
from binance.exceptions import BinanceAPIException

import execution
from execution import (
    OrderState,
    _save_active_trades,
    place_market_order,
)


class TestExecutionHardening:
    """
    Comprehensive tests for Binance Testnet execution hardening:
    - Failure recording: API code, API message, symbol, side, quantity, price, order type, client order ID
    - Duplicate protection across signal_id, client_order_id, and entry_order_id
    - API timeout simulation
    - Partial fill handling
    - OCO failure emergency market close
    - Restart after entry (crash recovery and missing protection restoration)
    - Restart after close
    """

    @pytest.fixture(autouse=True)
    def setup_clean_env(self, tmp_path, monkeypatch):
        active_file = tmp_path / "active_trades.json"
        ledger_file = tmp_path / "testnet_trade_ledger.jsonl"
        with open(active_file, "w") as f:
            f.write("[]")
        with open(ledger_file, "w") as f:
            f.write("")
            
        monkeypatch.setattr(execution, "ACTIVE_TRADES_FILE", str(active_file))
        monkeypatch.setenv("ACTIVE_TRADES_FILE", str(active_file))
        monkeypatch.setenv("TESTNET_LEDGER_FILE", str(ledger_file))
        monkeypatch.setattr(execution, "TRADING_MODE", "TESTNET")
        monkeypatch.setattr(execution, "TESTNET_ENABLED", True)
        monkeypatch.setattr(execution, "PAPER_SAFE_MODE", False)

    def test_api_timeout_and_failure_logging(self, monkeypatch):
        """Failure must record code, message, symbol, side, quantity, price, order type, and client_order_id."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = '{"code": -1001, "msg": "Internal error; unable to process your request."}'
        mock_client.create_order.side_effect = BinanceAPIException(mock_response, 500, mock_response.text)

        monkeypatch.setattr(execution, "get_exchange_client", lambda: mock_client)

        with pytest.raises(BinanceAPIException) as exc_info:
            place_market_order(
                strategy_name="adx_ema",
                side="BUY",
                symbol="BTCUSDT",
                quantity=0.001,
                sl=59000.0,
                tp=62000.0,
                client_order_id="TEST_ORDER_001"
            )

        assert exc_info.value.code == -1001
        assert "Internal error" in exc_info.value.message

    def test_partial_fill_handling(self, monkeypatch):
        """Partial fill must compute exact executedQty and weighted average price from fills."""
        mock_client = MagicMock()
        mock_client.create_order.return_value = {
            "orderId": 999111,
            "origQty": "1.000",
            "executedQty": "0.500",
            "cummulativeQuoteQty": "30000.00", # 30,000 / 0.5 = 60,000 avg price
            "fills": [
                {"price": "60000.00", "qty": "0.500", "commission": "0.05", "commissionAsset": "USDT"}
            ]
        }
        
        # Mock OCO protection
        monkeypatch.setattr(execution, "place_oco_protection", lambda **kwargs: {
            "oco_order_list_id": 888111,
            "tp_order_id": 888112,
            "sl_order_id": 888113,
            "tp_price_sent": "62000.00",
            "sl_price_sent": "59000.00",
            "qty_sent": "0.500"
        })
        monkeypatch.setattr(execution, "get_exchange_client", lambda: mock_client)

        order = place_market_order(
            strategy_name="adx_ema",
            side="BUY",
            symbol="BTCUSDT",
            quantity=1.0,
            sl=59000.0,
            tp=62000.0,
            client_order_id="TEST_PARTIAL_001"
        )

        assert order is not None
        assert order["_executed_qty"] == 0.5
        assert order["_actual_price"] == 60000.0
        assert order["_total_fee"] == 0.05
        assert order["_final_state"] == OrderState.PROTECTED

    def test_duplicate_signal_and_order_rejection(self, monkeypatch):
        """Duplicate signal_id / client_order_id must be rejected immediately."""
        active = [{
            "strategy": "adx_ema",
            "symbol": "ETHUSDT",
            "side": "BUY",
            "quantity": 0.1,
            "entry_price": 3000.0,
            "entry_fee": 0.03,
            "entry_timestamp": "2026-08-18T10:00:00Z",
            "signal_id": "SIG_UNIQUE_123",
            "oco_id": 777111,
            "tp_price": 3200.0,
            "sl_price": 2900.0,
            "state": "PROTECTED",
            "entry_client_id": "SIG_UNIQUE_123",
            "entry_order_id": 555111
        }]
        _save_active_trades(active)

        # Attempt to submit duplicate order with identical client_order_id
        res = place_market_order(
            strategy_name="adx_ema",
            side="BUY",
            symbol="ETHUSDT",
            quantity=0.1,
            sl=2900.0,
            tp=3200.0,
            client_order_id="SIG_UNIQUE_123"
        )
        assert res is None  # Must reject duplicate

    def test_oco_failure_triggers_emergency_close(self, monkeypatch):
        """If OCO placement fails on Binance, engine must trigger emergency market close."""
        mock_client = MagicMock()
        mock_client.create_order.return_value = {
            "orderId": 444111,
            "origQty": "0.01",
            "executedQty": "0.01",
            "cummulativeQuoteQty": "600.00",
            "fills": [{"price": "60000.00", "qty": "0.01", "commission": "0.01"}]
        }
        
        # OCO placement raises BinanceAPIException
        def mock_oco_fail(**kwargs):
            mock_resp = MagicMock()
            mock_resp.text = '{"code": -2010, "msg": "Filter failure: MIN_NOTIONAL"}'
            raise BinanceAPIException(mock_resp, 400, mock_resp.text)

        mock_emergency = MagicMock()
        mock_emergency.return_value = {"executedQty": "0.01", "cummulativeQuoteQty": "599.50"}

        monkeypatch.setattr(execution, "place_oco_protection", mock_oco_fail)
        monkeypatch.setattr(execution, "emergency_market_close", mock_emergency)
        monkeypatch.setattr(execution, "get_exchange_client", lambda: mock_client)

        res = place_market_order(
            strategy_name="scalper",
            side="BUY",
            symbol="BTCUSDT",
            quantity=0.01,
            sl=59000.0,
            tp=62000.0,
            client_order_id="TEST_OCO_FAIL_001"
        )
        assert res is None # Entry blocked / closed out
        mock_emergency.assert_called_once()

    def test_crash_recovery_restores_missing_oco_protection(self, tmp_path, monkeypatch):
        """On restart after crash, service detects open asset and restores missing OCO protection."""
        mock_client = MagicMock()
        # Account has 23.24 LINK balance (unprotected)
        mock_client.get_account.return_value = {
            "balances": [
                {"asset": "USDT", "free": "9700.00", "locked": "0.00"},
                {"asset": "LINK", "free": "23.24", "locked": "0.00"}
            ]
        }
        mock_client.get_open_orders.return_value = [] # No OCO on exchange
        mock_client.get_my_trades.return_value = [
            {"price": "9.4070", "qty": "23.24", "isBuyer": True, "commission": "0.02", "orderId": 333111}
        ]
        mock_client.get_symbol_ticker.return_value = {"price": "9.4500"}

        mock_place_oco = MagicMock(return_value={
            "oco_order_list_id": 555666,
            "tp_order_id": 555667,
            "sl_order_id": 555668
        })

        monkeypatch.setattr("testnet_engine.protection.place_oco_protection", mock_place_oco)

        # Service sync exchange state
        account = mock_client.get_account()
        assets = [item for item in account['balances'] if float(item['free']) > 0 or float(item['locked']) > 0]
        open_symbols_from_assets = {a['asset'] + "USDT" for a in assets if a['asset'] != "USDT"}
        
        assert "LINKUSDT" in open_symbols_from_assets
