import os
import json
import pytest
from dashboard import _get_trades_data

class TestProvenanceEventHardening:
    """
    Tests enforcing strict provenance and event ledger hardening:
    1. synthetic event rejection
    2. unverified recovery event rejection
    3. duplicate order deduplication
    4. duplicate fill deduplication
    5. duplicate close deduplication
    6. open position exclusion
    7. closed trade verification
    8. partial fill handling
    """

    def test_synthetic_events_completely_rejected(self, tmp_path, monkeypatch):
        """Events marked SYNTHETIC, SYNTHETIC_GENERATED, TEST, or MOCK must be excluded."""
        ledger = tmp_path / "testnet_trade_ledger.jsonl"
        fake_records = [
            {"symbol": "BTCUSDT", "source": "SYNTHETIC", "net_pnl": 500.0, "exit_order_id": "9001"},
            {"symbol": "ETHUSDT", "provenance": "SYNTHETIC_GENERATED", "net_pnl": 250.0, "exit_order_id": "9002"},
            {"symbol": "SOLUSDT", "source": "TEST", "net_pnl": -100.0, "exit_order_id": "9003"},
            {"symbol": "LINKUSDT", "source": "MOCK", "net_pnl": 50.0, "exit_order_id": "9004"},
            {"symbol": "BTCUSDT", "source": "BINANCE_EXECUTION", "provenance": "BINANCE_EXECUTION", "net_pnl": -5.0, "exit_order_id": "1001"}
        ]
        with open(ledger, "w") as f:
            for r in fake_records:
                f.write(json.dumps(r) + "\n")
                
        monkeypatch.setenv("TESTNET_LEDGER_FILE", str(ledger))
        data = _get_trades_data()
        assert data["total_trades"] == 1
        assert data["net_pnl"] == -5.0
        assert data["positions"][0]["order_id"] == "1001"

    def test_unverified_recovery_events_rejected(self, tmp_path, monkeypatch):
        """Recovery events lacking exchange fill/order proof must be classified UNVERIFIED and excluded."""
        ledger = tmp_path / "testnet_trade_ledger.jsonl"
        records = [
            {"symbol": "BTCUSDT", "source": "RECOVERED_WITHOUT_BINANCE_PROOF", "net_pnl": 100.0, "exit_order_id": "2001"},
            {"symbol": "BTCUSDT", "source": "RECOVERY_FROM_BINANCE", "provenance": "UNVERIFIED", "net_pnl": 50.0, "exit_order_id": "2002"},
            {"symbol": "BTCUSDT", "source": "BINANCE_EXECUTION", "provenance": "BINANCE_EXECUTION", "net_pnl": -10.0, "exit_order_id": "3001"}
        ]
        with open(ledger, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
                
        monkeypatch.setenv("TESTNET_LEDGER_FILE", str(ledger))
        data = _get_trades_data()
        assert data["total_trades"] == 1
        assert data["net_pnl"] == -10.0

    def test_duplicate_order_deduplication(self, tmp_path, monkeypatch):
        """Duplicate trade records referencing the same order ID must be deduplicated."""
        ledger = tmp_path / "testnet_trade_ledger.jsonl"
        records = [
            {"symbol": "BTCUSDT", "source": "BINANCE_EXECUTION", "net_pnl": 15.0, "exit_order_id": "4001", "timestamp": "2026-08-18T10:00:00Z"},
            {"symbol": "BTCUSDT", "source": "BINANCE_EXECUTION", "net_pnl": 15.0, "exit_order_id": "4001", "timestamp": "2026-08-18T10:00:00Z"}
        ]
        with open(ledger, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
                
        monkeypatch.setenv("TESTNET_LEDGER_FILE", str(ledger))
        data = _get_trades_data()
        assert data["total_trades"] == 1
        assert data["net_pnl"] == 15.0

    def test_duplicate_fill_deduplication(self, tmp_path, monkeypatch):
        """Duplicate entries referencing identical fill events must be counted exactly once."""
        ledger = tmp_path / "testnet_trade_ledger.jsonl"
        records = [
            {"symbol": "ETHUSDT", "source": "BINANCE_EXECUTION", "net_pnl": -2.5, "entry_order_id": "5001", "exit_order_id": "5002"},
            {"symbol": "ETHUSDT", "source": "BINANCE_EXECUTION", "net_pnl": -2.5, "entry_order_id": "5001", "exit_order_id": "5002"}
        ]
        with open(ledger, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
                
        monkeypatch.setenv("TESTNET_LEDGER_FILE", str(ledger))
        data = _get_trades_data()
        assert data["total_trades"] == 1
        assert data["net_pnl"] == -2.5

    def test_duplicate_close_events_deduplication(self, tmp_path, monkeypatch):
        """Multiple close messages on identical order ID must not inflate closed trade counts."""
        ledger = tmp_path / "testnet_trade_ledger.jsonl"
        records = [
            {"symbol": "LINKUSDT", "source": "BINANCE_EXECUTION", "net_pnl": 4.0, "exit_order_id": "6001", "action": "CLOSED_BUY"},
            {"symbol": "LINKUSDT", "source": "BINANCE_EXECUTION", "net_pnl": 4.0, "exit_order_id": "6001", "action": "CLOSED_BUY"}
        ]
        with open(ledger, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
                
        monkeypatch.setenv("TESTNET_LEDGER_FILE", str(ledger))
        data = _get_trades_data()
        assert data["total_trades"] == 1
        assert data["wins"] == 1

    def test_open_position_excluded_from_closed_metrics(self, tmp_path, monkeypatch):
        """Open positions must not be counted as closed trades or enter realized PnL."""
        ledger = tmp_path / "testnet_trade_ledger.jsonl"
        records = [
            {"symbol": "LINKUSDT", "source": "BINANCE_EXECUTION", "action": "BUY", "status": "OPEN", "entry_order_id": "436591"},
            {"symbol": "BTCUSDT", "source": "BINANCE_EXECUTION", "action": "CLOSED_BUY", "status": "CLOSED", "exit_order_id": "7001", "net_pnl": -1.0}
        ]
        with open(ledger, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
                
        monkeypatch.setenv("TESTNET_LEDGER_FILE", str(ledger))
        data = _get_trades_data()
        assert data["total_trades"] == 1
        assert data["net_pnl"] == -1.0

    def test_closed_trade_verification_with_canonical_event_ids(self, tmp_path, monkeypatch):
        """Canonical closed trades must contain event_id, trade_id, and verified flags."""
        ledger = tmp_path / "testnet_trade_ledger.jsonl"
        records = [
            {
                "event_id": "00000000-0000-0000-0000-000000000001",
                "trade_id": "TRD_BTCUSDT_1_2",
                "symbol": "BTCUSDT",
                "source": "BINANCE_EXECUTION",
                "provenance": "BINANCE_EXECUTION",
                "verified": True,
                "exit_order_id": "8001",
                "net_pnl": 12.0
            }
        ]
        with open(ledger, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
                
        monkeypatch.setenv("TESTNET_LEDGER_FILE", str(ledger))
        data = _get_trades_data()
        assert data["total_trades"] == 1
        assert data["wins"] == 1
        assert data["gross_profit"] == 12.0

    def test_partial_fill_handling(self, tmp_path, monkeypatch):
        """Multiple distinct partial exit orders must each record their own verified PnL."""
        ledger = tmp_path / "testnet_trade_ledger.jsonl"
        records = [
            {"symbol": "PORTALUSDT", "source": "BINANCE_EXECUTION", "exit_order_id": "9001", "net_pnl": -0.5, "quantity": 10.0},
            {"symbol": "PORTALUSDT", "source": "BINANCE_EXECUTION", "exit_order_id": "9002", "net_pnl": -0.5, "quantity": 10.0}
        ]
        with open(ledger, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
                
        monkeypatch.setenv("TESTNET_LEDGER_FILE", str(ledger))
        data = _get_trades_data()
        assert data["total_trades"] == 2
        assert data["net_pnl"] == -1.0
