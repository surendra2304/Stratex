import pytest
from unittest.mock import Mock, patch
from binance.client import Client
from binance.exceptions import BinanceAPIException

from testnet_engine.protection import (
    place_oco_protection,
    emergency_market_close,
    check_oco_status,
    compute_net_pnl,
    round_price,
    round_qty
)


@pytest.fixture
def mock_client():
    client = Mock(spec=Client)
    # Default filters
    client.get_symbol_info.return_value = {
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001"},
            {"filterType": "MIN_NOTIONAL", "minNotional": "10.0"}
        ]
    }
    return client


def test_round_price():
    assert round_price(123.456, 0.01, 2) == "123.45"
    assert round_price(123.459, 0.01, 2) == "123.45"
    assert round_price(0.12345, 0.0001, 4) == "0.1234"


def test_round_qty():
    assert round_qty(1.2345, 0.001, 3) == 1.234
    assert round_qty(1.2349, 0.001, 3) == 1.234


def test_place_oco_protection_buy(mock_client):
    # Setup
    mock_client.create_oco_order.return_value = {
        "orderListId": 999,
        "orderReports": [
            {"type": "LIMIT_MAKER", "orderId": 101, "clientOrderId": "tp1"},
            {"type": "STOP_LOSS_LIMIT", "orderId": 102, "clientOrderId": "sl1"}
        ]
    }

    # Execute
    res = place_oco_protection(
        client=mock_client,
        symbol="BTCUSDT",
        entry_side="BUY",
        executed_qty=0.5,
        actual_fill_price=50000.0,
        sl_price=49000.0,
        tp_price=52000.0
    )

    # Verify
    assert res["oco_order_list_id"] == 999
    assert res["tp_order_id"] == 101
    assert res["sl_order_id"] == 102
    assert res["tp_price_sent"] == "52000.00"
    assert res["sl_price_sent"] == "49000.00"

    mock_client.create_oco_order.assert_called_once_with(
        symbol="BTCUSDT",
        quantity="0.500",
        side="SELL",
        aboveType="LIMIT_MAKER",
        abovePrice="52000.00",
        belowType="STOP_LOSS_LIMIT",
        belowStopPrice="49000.00",
        belowPrice="49000.00",
        belowTimeInForce="GTC"
    )


def test_place_oco_protection_sell(mock_client):
    mock_client.create_oco_order.return_value = {
        "orderListId": 888,
        "orderReports": [
            {"type": "STOP_LOSS_LIMIT", "orderId": 201, "clientOrderId": "sl2"},
            {"type": "LIMIT_MAKER", "orderId": 202, "clientOrderId": "tp2"}
        ]
    }

    res = place_oco_protection(
        client=mock_client,
        symbol="BTCUSDT",
        entry_side="SELL",
        executed_qty=0.5,
        actual_fill_price=50000.0,
        sl_price=51000.0,
        tp_price=48000.0
    )

    assert res["oco_order_list_id"] == 888
    
    mock_client.create_oco_order.assert_called_once_with(
        symbol="BTCUSDT",
        quantity="0.500",
        side="BUY",
        aboveType="STOP_LOSS_LIMIT",
        aboveStopPrice="51000.00",
        abovePrice="51000.00",
        aboveTimeInForce="GTC",
        belowType="LIMIT_MAKER",
        belowPrice="48000.00"
    )


def test_place_oco_invalid_prices(mock_client):
    # BUY with SL above entry
    with pytest.raises(ValueError, match="must be below fill price"):
        place_oco_protection(
            client=mock_client, symbol="BTCUSDT", entry_side="BUY",
            executed_qty=0.5, actual_fill_price=50000.0, sl_price=51000.0, tp_price=52000.0
        )
    
    # BUY with TP below entry
    with pytest.raises(ValueError, match="must be above fill price"):
        place_oco_protection(
            client=mock_client, symbol="BTCUSDT", entry_side="BUY",
            executed_qty=0.5, actual_fill_price=50000.0, sl_price=49000.0, tp_price=49000.0
        )


def test_place_oco_invalid_qty(mock_client):
    # Negative qty
    with pytest.raises(ValueError):
        place_oco_protection(
            client=mock_client, symbol="BTCUSDT", entry_side="BUY",
            executed_qty=-0.5, actual_fill_price=50000.0, sl_price=49000.0, tp_price=52000.0
        )
        
    # Min notional failure (0.001 * 5000 = 5 < 10.0)
    with pytest.raises(ValueError, match="MIN_NOTIONAL"):
        place_oco_protection(
            client=mock_client, symbol="BTCUSDT", entry_side="BUY",
            executed_qty=0.001, actual_fill_price=5000.0, sl_price=4900.0, tp_price=5200.0
        )


def test_oco_rejection_propagates(mock_client):
    mock_client.create_oco_order.side_effect = BinanceAPIException(
        Mock(status_code=400, text='{"code":-1013,"msg":"Filter failure"}'),
        400, '{"code":-1013,"msg":"Filter failure"}'
    )
    
    with pytest.raises(BinanceAPIException):
        place_oco_protection(
            client=mock_client, symbol="BTCUSDT", entry_side="BUY",
            executed_qty=0.5, actual_fill_price=50000.0, sl_price=49000.0, tp_price=52000.0
        )


def test_emergency_close(mock_client):
    mock_client.create_order.return_value = {"executedQty": "0.5"}
    
    res = emergency_market_close(
        client=mock_client, symbol="BTCUSDT", entry_side="BUY", executed_qty=0.5
    )
    
    assert float(res["executedQty"]) == 0.5
    mock_client.create_order.assert_called_once_with(
        symbol="BTCUSDT",
        side="SELL",
        type="MARKET",
        quantity=0.5
    )


def test_check_oco_status_all_done(mock_client):
    mock_client.v3_get_order_list.return_value = {
        "listOrderStatus": "ALL_DONE",
        "orders": [
            {"orderId": 101},
            {"orderId": 102}
        ]
    }
    
    # Simulate SL being hit
    def mock_get_order(symbol, orderId):
        if orderId == 101:
            return {"type": "LIMIT_MAKER", "status": "CANCELED"}
        elif orderId == 102:
            return {
                "type": "STOP_LOSS_LIMIT", "status": "FILLED",
                "executedQty": "0.5", "cummulativeQuoteQty": "24500" # 49000 avg price
            }
            
    mock_client.get_order.side_effect = mock_get_order
    
    res = check_oco_status(mock_client, "BTCUSDT", 999)
    assert res["list_status"] == "ALL_DONE"
    assert res["sl_filled"] is True
    assert res["tp_filled"] is False
    assert res["close_avg_price"] == 49000.0
    assert res["close_qty"] == 0.5


def test_compute_net_pnl():
    # Long trade win
    gross, net = compute_net_pnl("BUY", 1.0, 50000, 50, 1.0, 52000, 52)
    assert gross == 2000
    assert net == 2000 - 50 - 52

    # Short trade win
    gross, net = compute_net_pnl("SELL", 1.0, 50000, 50, 1.0, 48000, 48)
    assert gross == 2000
    assert net == 2000 - 50 - 48
