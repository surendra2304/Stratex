"""A validation claim must be reproducible from a stored artifact.

The research registry once recorded adx_ema as OOS_VALIDATED and ACTIVE, with an
out-of-sample expectancy of +30.8 and a profit factor of 1.26. No artifact contained
those figures, and the only reproducible run measured a profit factor of 0.82 with
two out-of-sample trades. These tests fail if that kind of claim is reintroduced.
"""

import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "strategy_registry.json"
OPTIMIZATION_ARTIFACT = ROOT / "optimization_results" / "adx_ema_optimization.json"

PROMOTION_POLICY = {
    "min_out_of_sample_trades": 30,
    "min_out_of_sample_profit_factor": 1.0,
    "max_failed_walk_forward_windows": 0,
}


@pytest.fixture(scope="module")
def research_registry():
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def optimization():
    return json.loads(OPTIMIZATION_ARTIFACT.read_text(encoding="utf-8"))


def _entries(registry):
    for strategy_id, versions in registry["strategies"].items():
        for version, record in versions.items():
            yield strategy_id, version, record


def test_every_entry_records_a_status():
    research_registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    for strategy_id, _version, record in _entries(research_registry):
        assert record.get("status"), f"{strategy_id} has no recorded status"


def test_no_strategy_claims_validated_without_an_evidence_artifact(research_registry):
    """OOS_VALIDATED and ACTIVE both assert a completed review. Demand the receipt."""
    for strategy_id, _version, record in _entries(research_registry):
        params = record.get("parameters") or {}
        oos_status = params.get("OOS_VALIDATION_STATUS")
        claimed = record.get("status") in {"OOS_VALIDATED", "APPROVED", "ACTIVE"} or oos_status == "VALIDATED"
        if not claimed:
            continue
        evidence = record.get("evidence") or {}
        artifact = evidence.get("artifact")
        assert artifact, f"{strategy_id} claims validation with no evidence artifact"
        assert (ROOT / artifact).exists(), f"{strategy_id} cites a missing artifact: {artifact}"


def test_recorded_metrics_match_the_cited_artifact(research_registry, optimization):
    """Numbers in the registry must be transcribed from the run, not asserted."""
    record = research_registry["strategies"]["adx_ema"]["v1.0.0"]
    params = record["parameters"]
    measured = optimization["optimized_oos"]
    assert params["OOS_PROFIT_FACTOR"] == pytest.approx(measured["profit_factor"])
    assert params["OOS_EXPECTANCY_PER_TRADE"] == pytest.approx(measured["expectancy"])
    assert params["OOS_MAX_DRAWDOWN_PCT"] == pytest.approx(measured["max_drawdown_pct"] * 100, rel=1e-3)
    assert params["OOS_WIN_RATE_PRIOR"] == pytest.approx(measured["win_rate"] / 100.0)
    assert params["OOS_SHARPE"] == pytest.approx(measured["sharpe"])


def test_validated_assets_are_empty_while_evidence_is_insufficient(research_registry, optimization):
    """Validated assets cannot be asserted for a strategy the run never proved."""
    record = research_registry["strategies"]["adx_ema"]["v1.0.0"]
    measured = optimization["optimized_oos"]
    evidence_is_sufficient = (
        measured["trade_count"] >= PROMOTION_POLICY["min_out_of_sample_trades"]
        and measured["profit_factor"] >= PROMOTION_POLICY["min_out_of_sample_profit_factor"]
    )
    assets = record["parameters"].get("OOS_VALIDATED_ASSETS") or []
    if not evidence_is_sufficient:
        assert assets == [], (
            "OOS_VALIDATED_ASSETS must stay empty while out-of-sample evidence is "
            f"{measured['trade_count']} trades at profit factor {measured['profit_factor']:.2f}"
        )


def test_adx_ema_is_not_recorded_as_executable(research_registry, optimization):
    """The reproducible run lost money. It must not be recorded as tradable."""
    record = research_registry["strategies"]["adx_ema"]["v1.0.0"]
    assert optimization["optimized_oos"]["profit_factor"] < 1.0
    assert record["status"] not in {"OOS_VALIDATED", "APPROVED", "ACTIVE"}
    assert record["parameters"]["OOS_VALIDATION_STATUS"] != "VALIDATED"


def test_the_two_registries_never_disagree_about_executability(research_registry):
    """config_strategy.py is what the engine loads. The two must not contradict."""
    from config_strategy import PRODUCTION_STRATEGY_REGISTRY

    for strategy_id, _version, record in _entries(research_registry):
        executable_in_research_registry = record["status"] == "ACTIVE"
        production_entry = PRODUCTION_STRATEGY_REGISTRY.get(strategy_id)
        executable_in_engine = bool(production_entry) and production_entry.get("status") == "VALIDATED"
        assert not (executable_in_research_registry and not executable_in_engine), (
            f"{strategy_id} is ACTIVE in strategy_registry.json but not VALIDATED in the engine "
            "registry, so the research registry claims an execution permission that does not exist"
        )


def test_production_registry_advertises_no_unbacked_runtime_priors():
    """Unvalidated entries must not carry stored metrics that callers could read."""
    from config_strategy import PRODUCTION_STRATEGY_REGISTRY

    for name, entry in PRODUCTION_STRATEGY_REGISTRY.items():
        if entry.get("status") == "VALIDATED":
            continue
        assert entry.get("oos_win_rate_prior") is None, f"{name} advertises a win-rate prior while unvalidated"
        assert entry.get("expected_net_edge_bps") is None, f"{name} advertises an edge prior while unvalidated"
        assert entry.get("validated_assets") == [], f"{name} advertises validated assets while unvalidated"


def test_artifact_reports_the_run_failed_out_of_sample(optimization):
    """Pins the fact this whole correction rests on, so it cannot be quietly retyped."""
    measured = optimization["optimized_oos"]
    assert measured["net_pnl"] < 0
    assert measured["expectancy"] < 0
    assert measured["sharpe"] < 0
    assert measured["trade_count"] < PROMOTION_POLICY["min_out_of_sample_trades"]
    assert optimization["promotion_status"] == "RESEARCH ONLY"
