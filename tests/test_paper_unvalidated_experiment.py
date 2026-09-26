import json

import config
import strategy_swing
from paper_engine.portfolio import PaperPortfolio
from paper_forward_runner import (
    FROZEN_STRATEGY,
    evaluate_forward_paper_candidate,
)
from testnet_engine.profitability_gate import ProfitabilityGate


def test_missing_prior_is_allowed_only_for_frozen_paper_experiment(monkeypatch):
    result = strategy_swing.SignalResult("BUY", 98.0, 104.0, "RULE_BASED", None, 2.0)
    monkeypatch.setattr(config, "TRADING_MODE", "PAPER")

    accepted, metrics = evaluate_forward_paper_candidate(
        FROZEN_STRATEGY, result, "BTCUSDT", "BUY", 100.0, 98.0, 104.0
    )

    assert accepted is True
    assert metrics["decision"] == "PAPER_SAMPLE_ONLY"
    assert metrics["reason"] == "UNVERIFIED_RULE_BASED_PRIOR"
    assert metrics["evidence_status"] == "UNVALIDATED_PAPER_EXPERIMENT"


def test_missing_prior_is_not_allowed_outside_paper_mode(monkeypatch):
    result = strategy_swing.SignalResult("BUY", 98.0, 104.0, "RULE_BASED", None, 2.0)
    monkeypatch.setattr(config, "TRADING_MODE", "FUTURES")

    accepted, metrics = evaluate_forward_paper_candidate(
        FROZEN_STRATEGY, result, "BTCUSDT", "BUY", 100.0, 98.0, 104.0
    )

    assert accepted is False
    assert metrics["reason"] == "PAPER_EXPERIMENT_REQUIRES_PAPER_MODE"


def test_exchange_profitability_gate_still_rejects_missing_prior():
    result = strategy_swing.SignalResult("BUY", 98.0, 104.0, "RULE_BASED", None, 2.0)

    accepted, metrics = ProfitabilityGate().evaluate_signal(
        "BTCUSDT", "BUY", 100.0, 98.0, 104.0, result
    )

    assert accepted is False
    assert metrics["reason"] == "UNVERIFIED_RULE_BASED_PRIOR"


def test_closed_paper_ledger_keeps_unvalidated_experiment_provenance(tmp_path):
    ledger = tmp_path / "paper_trade_ledger.jsonl"
    portfolio = PaperPortfolio(
        filename=str(tmp_path / "portfolio.json"), ledger_file=str(ledger)
    )
    portfolio.add_position(
        "trade-1", "BTCUSDT", "LONG", 100.0, 1.0,
        metadata={
            "strategy": FROZEN_STRATEGY,
            "strategy_version": "1.0.0",
            "signal_id": "signal-1",
            "evidence_status": "UNVALIDATED_PAPER_EXPERIMENT",
        },
    )
    portfolio.close_position("trade-1", 101.0)

    record = json.loads(ledger.read_text(encoding="utf-8").strip())
    assert record["strategy"] == FROZEN_STRATEGY
    assert record["signal_id"] == "signal-1"
    assert record["evidence_status"] == "UNVALIDATED_PAPER_EXPERIMENT"
