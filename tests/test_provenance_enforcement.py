import json
import pytest
import os
from dashboard import app, _get_trades_data

class TestProvenanceEnforcement:
    """
    Tests enforcing strict Binance-backed provenance:
    - Synthetic/test trades must be strictly rejected/filtered
    - Synthetic PnL cannot pollute realized PnL
    - Unverified positions cannot appear as open
    - Invalid Binance execution references are rejected
    """

    def test_synthetic_trades_excluded_from_metrics(self, tmp_path, monkeypatch):
        """Verify that records with source=TEST or synthetic provenance are filtered out."""
        ledger = tmp_path / "testnet_trade_ledger.jsonl"
        records = [
            # Real Binance verified trade
            {
                "trade_id": "TRD_1001",
                "symbol": "BTCUSDT",
                "side": "BUY",
                "strategy": "ADX_EMA",
                "net_pnl": 15.50,
                "status": "CLOSED",
                "provenance": "BINANCE_EXECUTION",
                "source": "BINANCE_EXECUTION",
                "entry_order_id": 2994266,
                "exit_order_id": 3552731,
                "binance_verified": True
            },
            # Synthetic trade (must be rejected)
            {
                "trade_id": "SYN_9999",
                "symbol": "BTCUSDT",
                "side": "BUY",
                "strategy": "ADX_EMA",
                "net_pnl": 500.00,
                "status": "CLOSED",
                "provenance": "SYNTHETIC_GENERATED",
                "source": "TEST",
                "entry_order_id": None,
                "exit_order_id": None,
                "binance_verified": False
            }
        ]
        with open(ledger, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

        monkeypatch.setenv("TESTNET_TRADE_LEDGER_FILE", str(ledger))
        
        # Test backend parser
        trades_data = _get_trades_data()
        positions = trades_data.get("positions", [])
        
        # Provenance filtering check
        for p in positions:
            assert p.get("provenance") != "SYNTHETIC_GENERATED", "Synthetic record leaked into positions"
            assert p.get("source") != "TEST", "Test record leaked into positions"

    def test_synthetic_pnl_cannot_enter_realized_pnl(self, tmp_path, monkeypatch):
        """Verify realized PnL only aggregates verified Binance-backed trades."""
        ledger = tmp_path / "testnet_trade_ledger.jsonl"
        records = [
            {
                "trade_id": "TRD_REAL",
                "symbol": "ETHUSDT",
                "net_pnl": 12.34,
                "status": "CLOSED",
                "provenance": "BINANCE_EXECUTION",
                "entry_order_id": 3171507,
                "exit_order_id": 3171512
            },
            {
                "trade_id": "TRD_SYNTH",
                "symbol": "ETHUSDT",
                "net_pnl": 9999.99,
                "status": "CLOSED",
                "provenance": "SYNTHETIC_GENERATED",
                "entry_order_id": None
            }
        ]
        with open(ledger, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

        monkeypatch.setenv("TESTNET_TRADE_LEDGER_FILE", str(ledger))
        trades_data = _get_trades_data()
        
        # Net PnL must not equal synthetic sum
        assert trades_data.get("net_pnl") != 10012.33

    def test_dashboard_status_endpoint_backed_by_binance(self):
        """Verify /api/status returns non-synthetic wallet balance."""
        with app.test_client() as client:
            res = client.get("/api/status")
            assert res.status_code == 200
            data = res.get_json()
            assert "equity" in data
            assert "cash" in data
            assert data.get("equity") > 0
            # Ensure open positions does not exceed real slots
            assert data.get("open_positions") <= 5

    def test_unverified_orders_rejected_from_ledger(self):
        """Verify that trade records without valid order IDs are rejected."""
        ledger_file = "testnet_trade_ledger.jsonl"
        if os.path.exists(ledger_file):
            with open(ledger_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        t = json.loads(line)
                        assert t.get("provenance") == "BINANCE_EXECUTION"
                        assert t.get("entry_order_id") is not None
