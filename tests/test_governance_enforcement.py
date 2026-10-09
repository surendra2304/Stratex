"""
tests/test_governance_enforcement.py
Regression suite for the strategy/symbol governance gates.

Historical incident: config.ACTIVE_STRATEGIES enabled all 9 strategies while
PRODUCTION_STRATEGY_REGISTRY marked only adx_ema VALIDATED. DISABLED strategies
(aggressor, scalper) executed real testnet orders and produced the majority of
realized losses (see testnet_trade_ledger.jsonl 2026-08). These tests pin the
enforcement so it cannot silently regress.
"""

import config
import config_strategy
from config_strategy import PRODUCTION_STRATEGY_REGISTRY
from stratex_quantdinger.registry import StrategyRegistry
from testnet_engine.service import (
    TestnetService,
    governance_filter_strategies,
    governance_validated_assets,
)


class TestStrategyGovernance:
    def test_only_validated_strategies_pass(self):
        filtered = governance_filter_strategies(config.ACTIVE_STRATEGIES)
        for strat_name in filtered:
            entry = PRODUCTION_STRATEGY_REGISTRY.get(strat_name)
            assert entry is not None, f"{strat_name} passed gate but is unregistered"
            assert entry["status"] == "VALIDATED", f"{strat_name} passed gate but status={entry['status']}"

    def test_service_autoregistration_never_claims_unverified_active_status(self, monkeypatch, tmp_path):
        """Config admission is not evidence for QuantDinger ACTIVE status."""
        monkeypatch.setattr(config_strategy, "CENSUS_CANDIDATE_STRATEGY", {"p": 1}, raising=False)
        service = TestnetService.__new__(TestnetService)
        service.strategies = {"1h": [("census_candidate", object())]}
        service.registry = StrategyRegistry(path=str(tmp_path / "registry.json"))

        service._register_active_strategies_in_registry()

        version = service.registry.get("census_candidate", "v1.0.0")
        assert version.status == "RESEARCH"
        assert service.registry.get_active("census_candidate") is None

    def test_known_friction_losers_are_blocked(self):
        """aggressor/scalper cannot overcome taker friction — must never trade."""
        filtered = governance_filter_strategies(config.ACTIVE_STRATEGIES)
        assert "aggressor" not in filtered
        assert "scalper" not in filtered

    def test_observe_only_strategy_cannot_be_loaded_for_execution(self):
        filtered = governance_filter_strategies({"adx_ema": ["4h", "1h"]})
        assert "adx_ema" not in filtered

    def test_no_strategy_loads_without_reproducible_oos_evidence(self):
        """An empty executable set is safer than promoting an unverified prior."""
        assert governance_filter_strategies(config.ACTIVE_STRATEGIES) == {}

    def test_empty_input_is_safe(self):
        assert governance_filter_strategies({}) == {}


class TestSymbolGovernance:
    def test_validated_assets_from_loaded_strategies(self):
        filtered = governance_filter_strategies(config.ACTIVE_STRATEGIES)
        strategies_by_tf = {}
        for strat_name, tfs in filtered.items():
            for tf in tfs:
                strategies_by_tf.setdefault(tf, []).append((strat_name, None))
        assets = governance_validated_assets(strategies_by_tf)
        assert assets == set()
        # Assets implicated in historical unvalidated losses are excluded
        assert "PORTALUSDT" not in assets
        assert "SPCXBUSDT" not in assets

    def test_no_assets_when_nothing_loaded(self):
        assert governance_validated_assets({}) == set()
        assert governance_validated_assets(None) == set()


class TestHeartbeatReportsLoadedTruth:
    def test_heartbeat_shows_governance_filtered_strategies(self, monkeypatch, mocker, tmp_path):
        """The dashboard must mirror the engine: heartbeat reports actually-loaded
        (VALIDATED) strategies, not the raw config that includes DISABLED ones."""
        import json as _json

        import testnet_engine.service as svc

        monkeypatch.setattr(svc, "TRADING_MODE", "TESTNET")
        monkeypatch.setenv("API_KEY", "dummy")
        monkeypatch.setenv("SECRET_KEY", "dummy")
        mock_client = mocker.MagicMock()
        mock_client.get_account.return_value = {"balances": [{"asset": "USDT", "free": "10000.0", "locked": "0.0"}]}
        mocker.patch("testnet_engine.service.get_exchange_client", return_value=mock_client)
        mocker.patch("execution._load_active_trades", return_value=[])
        monkeypatch.setattr(svc, "ACTIVE_STRATEGIES", {"adx_ema": ["4h"], "aggressor": "1m"})

        hb_file = tmp_path / "hb.json"
        monkeypatch.setattr(svc, "TESTNET_HEARTBEAT_FILE", str(hb_file))

        service = svc.TestnetService()
        service._write_heartbeat()
        hb = _json.loads(hb_file.read_text())
        assert hb["strategies"] == []
        assert hb["strategy"] == "none"
        assert hb["timeframes"] == []
