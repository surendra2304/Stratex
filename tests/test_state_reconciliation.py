"""
tests/test_state_reconciliation.py
Upgrade 2 (Bug #52 class): boot-time state reconciliation.

Scenarios:
  1. Naked position (LINK balance held, no resting orders) -> OCO protection
     placed at 3xATR SL / 3xATR TP.
  2. Orphan order (resting order, zero base balance) -> cancelled.
  3. Healthy state (balance + protective orders) -> untouched.
"""

import pandas as pd
import pytest

import testnet_engine.service as svc
from testnet_engine.service import TestnetService


def _make_service(monkeypatch, mocker, balances, open_orders):
    monkeypatch.setattr(svc, "TRADING_MODE", "TESTNET")
    monkeypatch.setenv("API_KEY", "dummy")
    monkeypatch.setenv("SECRET_KEY", "dummy")
    mock_client = mocker.MagicMock()
    mock_client.get_account.return_value = {
        "balances": [{"asset": a, "free": str(f), "locked": "0.0"} for a, f in balances]
    }
    mock_client.get_open_orders.return_value = open_orders
    mocker.patch("testnet_engine.service.get_exchange_client", return_value=mock_client)
    mocker.patch("execution._load_active_trades", return_value=[])
    monkeypatch.setattr(svc, "ACTIVE_STRATEGIES", {"adx_ema": ["4h"]})
    return TestnetService(), mock_client


def _fake_candles(n=60, base=10.0):
    idx = pd.date_range("2026-08-01", periods=n, freq="4h")
    close = pd.Series([base + (i % 5) * 0.05 for i in range(n)], index=idx)
    return pd.DataFrame({
        "open": close, "high": close + 0.05, "low": close - 0.05,
        "close": close, "volume": 1000.0,
    }, index=idx)


class TestReconcileState:
    def test_naked_position_gets_oco_protection(self, monkeypatch, mocker):
        svc_, client = _make_service(
            monkeypatch, mocker,
            balances=[("USDT", 11000.0), ("LINK", 23.24)],
            open_orders=[],
        )
        monkeypatch.setattr("data.get_candles", lambda sym, tf, limit=60: _fake_candles(), raising=False)
        placed = mocker.patch("testnet_engine.protection.place_oco_protection", return_value={"ok": True})
        summary = svc_.reconcile_state()
        assert summary["naked_positions_protected"] == ["LINKUSDT"]
        assert placed.call_count == 1
        kwargs = placed.call_args.kwargs
        assert kwargs["symbol"] == "LINKUSDT"
        assert kwargs["entry_side"] == "BUY"
        assert kwargs["sl_price"] < kwargs["actual_fill_price"] < kwargs["tp_price"]
        # 3xATR geometry: TP and SL equidistant from last close
        mid = kwargs["actual_fill_price"]
        assert abs((mid - kwargs["sl_price"]) - (kwargs["tp_price"] - mid)) < 1e-6

    def test_orphan_order_cancelled_when_no_position(self, monkeypatch, mocker):
        svc_, client = _make_service(
            monkeypatch, mocker,
            balances=[("USDT", 11000.0)],
            open_orders=[{"symbol": "SOLUSDT", "orderId": 111, "type": "STOP_LOSS_LIMIT"}],
        )
        summary = svc_.reconcile_state()
        assert summary["orphan_orders_cancelled"] == ["SOLUSDT:111"]
        client.cancel_order.assert_called_once_with(symbol="SOLUSDT", orderId=111)

    def test_healthy_state_untouched(self, monkeypatch, mocker):
        svc_, client = _make_service(
            monkeypatch, mocker,
            balances=[("USDT", 11000.0), ("BTC", 0.5)],
            open_orders=[{"symbol": "BTCUSDT", "orderId": 222, "type": "STOP_LOSS_LIMIT"}],
        )
        placed = mocker.patch("testnet_engine.protection.place_oco_protection")
        summary = svc_.reconcile_state()
        assert summary["naked_positions_protected"] == []
        assert summary["orphan_orders_cancelled"] == []
        assert summary["errors"] == []
        placed.assert_not_called()
        client.cancel_order.assert_not_called()

    def test_exchange_failure_is_non_fatal(self, monkeypatch, mocker):
        svc_, client = _make_service(monkeypatch, mocker, balances=[("USDT", 11000.0)], open_orders=[])
        client.get_open_orders.side_effect = RuntimeError("api down")
        summary = svc_.reconcile_state()
        assert summary["checked_symbols"] == 0
        assert any("exchange_query_failed" in e for e in summary["errors"])
