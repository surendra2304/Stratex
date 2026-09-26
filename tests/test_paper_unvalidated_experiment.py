import json

import config
import strategy_swing
from paper_engine.portfolio import PaperPortfolio
from paper_forward_runner import (
    FROZEN_STRATEGY,
    evaluate_forward_paper_candidate,
    load_or_create_experiment,
    log_signal_record,
    smooth_paper_signal,
)
import paper_forward_runner
from paper_engine.experiment_config import FrozenExperimentConfig
from paper_engine.signal_logger import SignalLogger
from testnet_engine.profitability_gate import ProfitabilityGate
from stratex_upgrade.decay import SignalDecaySmoother


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


def test_missing_prior_is_allowed_by_paper_runner_when_exchange_mode_is_futures(monkeypatch):
    result = strategy_swing.SignalResult("BUY", 98.0, 104.0, "RULE_BASED", None, 2.0)
    monkeypatch.setattr(config, "TRADING_MODE", "FUTURES")

    accepted, metrics = evaluate_forward_paper_candidate(
        FROZEN_STRATEGY, result, "BTCUSDT", "BUY", 100.0, 98.0, 104.0
    )

    assert accepted is True
    assert metrics["decision"] == "PAPER_SAMPLE_ONLY"
    assert metrics["evidence_status"] == "UNVALIDATED_PAPER_EXPERIMENT"


def test_exchange_profitability_gate_still_rejects_missing_prior():
    result = strategy_swing.SignalResult("BUY", 98.0, 104.0, "RULE_BASED", None, 2.0)

    accepted, metrics = ProfitabilityGate().evaluate_signal(
        "BTCUSDT", "BUY", 100.0, 98.0, 104.0, result
    )

    assert accepted is False
    assert metrics["reason"] == "UNVERIFIED_RULE_BASED_PRIOR"


def test_paper_runner_uses_existing_smoother_api_with_unknown_confidence(monkeypatch):
    smoother = SignalDecaySmoother(decay_steps=4, confirmation_threshold=0.50)
    monkeypatch.setattr(paper_forward_runner, "_decay_smoother", smoother)

    side, confidence_status = smooth_paper_signal("BTCUSDT", "1h", "BUY", None)

    assert side == "BUY"
    assert confidence_status == "UNKNOWN_NOT_ESTIMATED"


def test_unknown_strategy_confidence_is_logged_as_unknown(tmp_path):
    signal_log = tmp_path / "signals.jsonl"
    logger = SignalLogger(str(signal_log))

    log_signal_record(
        logger, 1_790_000_000.0, FROZEN_STRATEGY, "BTCUSDT", "BUY", None,
        100.0, 98.0, 104.0, decision="TRADED",
    )

    record = json.loads(signal_log.read_text(encoding="utf-8").strip())
    assert record["confidence"] is None
    assert record["confidence_status"] == "UNKNOWN_NOT_ESTIMATED"


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


def test_futures_mode_simulated_fill_has_no_exchange_order_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TRADING_MODE", "FUTURES")

    class ForbiddenOrderClient:
        def __getattr__(self, name):
            if "order" in name.lower():
                raise AssertionError("paper simulator must never call exchange order APIs")
            raise AttributeError(name)

    # The paper fill path must not construct or use the exchange client.
    monkeypatch.setattr(paper_forward_runner, "Client", ForbiddenOrderClient, raising=False)
    monkeypatch.setattr(paper_forward_runner, "MarketDataClient", ForbiddenOrderClient)
    portfolio = PaperPortfolio(
        filename=str(tmp_path / "portfolio.json"),
        ledger_file=str(tmp_path / "paper_trade_ledger.jsonl"),
    )
    result = strategy_swing.SignalResult("BUY", 98.0, 104.0, "RULE_BASED", None, 2.0)
    accepted, metrics = evaluate_forward_paper_candidate(
        FROZEN_STRATEGY, result, "BTCUSDT", "BUY", 100.0, 98.0, 104.0
    )
    assert accepted is True
    assert metrics["evidence_status"] == "UNVALIDATED_PAPER_EXPERIMENT"

    fill = paper_forward_runner.paper_execute(
        portfolio, "BUY", "BTCUSDT", 100.0, 98.0, 104.0,
        {"BTCUSDT": 100.0}, str(tmp_path / "paper_trade_ledger.jsonl"),
        signal_id="simulated-signal-1",
        evidence_status=metrics["evidence_status"],
    )
    assert fill["status"] == "FILLED"
    assert portfolio.positions[fill["pos_id"]]["evidence_status"] == "UNVALIDATED_PAPER_EXPERIMENT"


def test_code_revision_change_starts_a_separate_forward_experiment(tmp_path, monkeypatch):
    experiments = tmp_path / "experiments"
    experiments.mkdir()
    old = FrozenExperimentConfig(
        experiment_name="forward_exp_001",
        strategy_name=FROZEN_STRATEGY,
        git_sha="old-revision",
        status="RUNNING",
        started_at=1_790_000_000.0,
    )
    old.save(str(experiments))
    (experiments / "active_forward_experiment_id.txt").write_text(old.experiment_id)
    (experiments / "registry.json").write_text(json.dumps({"experiments": [{
        "experiment_id": old.experiment_id,
        "status": "RUNNING",
    }]}))

    monkeypatch.setattr(paper_forward_runner, "EXPERIMENT_DIR", str(experiments))
    monkeypatch.setattr(
        paper_forward_runner,
        "EXPERIMENT_ID_FILE",
        str(experiments / "active_forward_experiment_id.txt"),
    )
    monkeypatch.setattr(paper_forward_runner, "_get_git_sha", lambda: "new-revision")

    fresh = load_or_create_experiment()

    old_after = FrozenExperimentConfig.load(old.experiment_id, str(experiments))
    registry = json.loads((experiments / "registry.json").read_text())
    assert fresh.experiment_id != old.experiment_id
    assert fresh.git_sha == "new-revision"
    assert old_after.status == "ABORTED"
    assert "CODE_REVISION_CHANGED" in old_after.abort_reason
    assert registry["experiments"][0]["status"] == "ABORTED"
