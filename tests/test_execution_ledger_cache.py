"""Regression tests: incremental ledger dedup index (execution.ledger_contains_id).

The old duplicate-signal check re-read the ENTIRE trade ledger on every order
(O(history) per order over an unbounded file). The index must stay
semantically identical to a full scan while only parsing newly-appended bytes.
"""
import json

import pytest

import execution


@pytest.fixture(autouse=True)
def fresh_cache():
    execution.reset_ledger_id_cache()
    yield
    execution.reset_ledger_id_cache()


def _append(path, records):
    with open(path, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


class TestLedgerIdIndex:
    def test_missing_file_never_matches(self, tmp_path):
        assert execution.ledger_contains_id(str(tmp_path / "nope.jsonl"), "sig-1") is False

    def test_matches_every_dedup_field(self, tmp_path):
        ledger = tmp_path / "ledger.jsonl"
        _append(ledger, [
            {"signal_id": "sig-1"},
            {"entry_client_id": "cli-2"},
            {"trade_id": "trd-3"},
            {"entry_order_id": 9004},
            {"unrelated": "x"},
        ])
        assert execution.ledger_contains_id(str(ledger), "sig-1") is True
        assert execution.ledger_contains_id(str(ledger), "cli-2") is True
        assert execution.ledger_contains_id(str(ledger), "trd-3") is True
        assert execution.ledger_contains_id(str(ledger), "9004") is True  # str compare, like the old scan
        assert execution.ledger_contains_id(str(ledger), "sig-9") is False

    def test_incremental_append_is_seen_without_full_rescan(self, tmp_path):
        ledger = tmp_path / "ledger.jsonl"
        _append(ledger, [{"signal_id": "sig-1"}])
        assert execution.ledger_contains_id(str(ledger), "sig-1") is True
        assert execution.ledger_contains_id(str(ledger), "sig-2") is False

        _append(ledger, [{"signal_id": "sig-2"}])
        assert execution.ledger_contains_id(str(ledger), "sig-2") is True

    def test_truncation_resets_the_index(self, tmp_path):
        ledger = tmp_path / "ledger.jsonl"
        _append(ledger, [{"signal_id": "sig-1"}, {"signal_id": "sig-2"}])
        assert execution.ledger_contains_id(str(ledger), "sig-2") is True

        ledger.write_text(json.dumps({"signal_id": "sig-9"}) + "\n", encoding="utf-8")
        assert execution.ledger_contains_id(str(ledger), "sig-2") is False
        assert execution.ledger_contains_id(str(ledger), "sig-9") is True

    def test_rotation_to_new_file_rescans(self, tmp_path):
        ledger = tmp_path / "ledger.jsonl"
        _append(ledger, [{"signal_id": "sig-1"}])
        assert execution.ledger_contains_id(str(ledger), "sig-1") is True

        other = tmp_path / "rotated.jsonl"
        _append(other, [{"signal_id": "sig-77"}])
        assert execution.ledger_contains_id(str(other), "sig-1") is False
        assert execution.ledger_contains_id(str(other), "sig-77") is True

    def test_malformed_lines_do_not_break_dedup(self, tmp_path):
        ledger = tmp_path / "ledger.jsonl"
        with open(ledger, "w", encoding="utf-8") as f:
            f.write('{"signal_id": "sig-1"}\n')
            f.write("{corrupt line\n")
            f.write("\n")
            f.write('{"signal_id": "sig-2"}\n')
        assert execution.ledger_contains_id(str(ledger), "sig-1") is True
        assert execution.ledger_contains_id(str(ledger), "sig-2") is True
        assert execution.ledger_contains_id(str(ledger), "sig-3") is False

    def test_place_market_order_rejects_ledger_duplicate(self, tmp_path, monkeypatch):
        """End-to-end: a signal already present in the ledger must never reach
        the exchange client, regardless of mode (dedup runs before policy)."""
        import config
        ledger = tmp_path / "ledger.jsonl"
        _append(ledger, [{"signal_id": "dup-sig"}])
        monkeypatch.setenv("TESTNET_LEDGER_FILE", str(ledger))
        monkeypatch.setenv("ACTIVE_TRADES_FILE", str(tmp_path / "active_trades.json"))
        monkeypatch.setattr(execution, "TRADING_MODE", "TESTNET", raising=False)
        monkeypatch.setattr(config, "TRADING_MODE", "TESTNET", raising=False)
        monkeypatch.setattr(execution, "PAPER_SAFE_MODE", False, raising=False)
        monkeypatch.setattr(config, "PAPER_SAFE_MODE", False, raising=False)
        monkeypatch.setenv("RESEARCH_MODE", "0")

        def _explode(**kwargs):
            raise AssertionError("exchange client must never be called for a duplicate signal")

        monkeypatch.setattr(execution, "get_exchange_client", lambda: type("C", (), {"create_order": staticmethod(_explode)})())

        result = execution.place_market_order("adx_ema", "BUY", "BTCUSDT", 0.001,
                                              sl=100.0, tp=110.0, client_order_id="dup-sig")
        assert result is None
