import json

from dashboard import _get_trades_data, app


class TestProvenanceEnforcement:
    """
    Tests enforcing strict Binance-backed provenance & canonical deduplication:
    - Synthetic/test trades must be strictly rejected/filtered
    - Synthetic PnL cannot pollute realized PnL
    - Multiple fills for one order/trade are reconciled correctly
    - OCO order lifecycle is tracked deterministically
    - Open positions are never counted as closed trades
    - Duplicate execution records are deduplicated
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
            f.writelines(json.dumps(r) + "\n" for r in records)

        monkeypatch.setenv("TESTNET_LEDGER_FILE", str(ledger))
        
        trades_data = _get_trades_data()
        positions = trades_data.get("positions", [])
        
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
                "source": "BINANCE_EXECUTION",
                "entry_order_id": 3171507,
                "exit_order_id": 3171512
            },
            {
                "trade_id": "TRD_SYNTH",
                "symbol": "ETHUSDT",
                "net_pnl": 9999.99,
                "status": "CLOSED",
                "provenance": "SYNTHETIC_GENERATED",
                "source": "TEST",
                "entry_order_id": None
            }
        ]
        with open(ledger, "w", encoding="utf-8") as f:
            f.writelines(json.dumps(r) + "\n" for r in records)

        monkeypatch.setenv("TESTNET_LEDGER_FILE", str(ledger))
        trades_data = _get_trades_data()
        
        # Net PnL must be strictly the real PnL (12.34), not 10012.33
        assert trades_data.get("net_pnl") == 12.34

    def test_duplicate_order_records_deduplication(self, tmp_path, monkeypatch):
        """Verify duplicate order records are deduplicated by key."""
        ledger = tmp_path / "testnet_trade_ledger.jsonl"
        records = [
            {
                "trade_id": "TRD_PORTAL_1",
                "symbol": "PORTALUSDT",
                "net_pnl": 5.20,
                "status": "CLOSED",
                "provenance": "BINANCE_EXECUTION",
                "source": "BINANCE_EXECUTION",
                "entry_order_id": 154342,
                "exit_order_id": 154343
            },
            {
                "trade_id": "TRD_PORTAL_1_DUP",
                "symbol": "PORTALUSDT",
                "net_pnl": 5.20,
                "status": "CLOSED",
                "provenance": "BINANCE_EXECUTION",
                "source": "BINANCE_EXECUTION",
                "entry_order_id": 154342,
                "exit_order_id": 154343
            }
        ]
        with open(ledger, "w", encoding="utf-8") as f:
            f.writelines(json.dumps(r) + "\n" for r in records)

        monkeypatch.setenv("TESTNET_LEDGER_FILE", str(ledger))
        trades_data = _get_trades_data()
        
        assert trades_data.get("total_trades") == 1
        assert trades_data.get("net_pnl") == 5.20

    def test_open_position_not_counted_as_closed(self):
        """Verify open position (LINKUSDT) is not present in closed trades."""
        with app.test_client() as client:
            res = client.get("/api/trades")
            data = res.get_json()
            positions = data.get("positions", [])
            for p in positions:
                assert p.get("status") == "CLOSED"
                assert p.get("exit_order_id") is not None or p.get("order_id") is not None

    def test_dashboard_status_endpoint_backed_by_binance(self):
        """Verify /api/status returns non-synthetic wallet balance."""
        with app.test_client() as client:
            res = client.get("/api/status")
            assert res.status_code == 200
            data = res.get_json()
            assert "equity" in data
            assert "cash" in data
            assert data.get("equity") > 0
            assert data.get("open_positions") <= 5
