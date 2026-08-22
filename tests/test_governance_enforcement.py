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
from config_strategy import PRODUCTION_STRATEGY_REGISTRY
from testnet_engine.service import governance_filter_strategies, governance_validated_assets


class TestStrategyGovernance:
    def test_only_validated_strategies_pass(self):
        filtered = governance_filter_strategies(config.ACTIVE_STRATEGIES)
        for strat_name in filtered:
            entry = PRODUCTION_STRATEGY_REGISTRY.get(strat_name)
            assert entry is not None, f"{strat_name} passed gate but is unregistered"
            assert entry["status"] == "VALIDATED", f"{strat_name} passed gate but status={entry['status']}"

    def test_known_friction_losers_are_blocked(self):
        """aggressor/scalper cannot overcome taker friction — must never trade."""
        filtered = governance_filter_strategies(config.ACTIVE_STRATEGIES)
        assert "aggressor" not in filtered
        assert "scalper" not in filtered

    def test_validated_strategy_pinned_to_validated_timeframe(self):
        filtered = governance_filter_strategies(config.ACTIVE_STRATEGIES)
        assert "adx_ema" in filtered
        assert filtered["adx_ema"] == [PRODUCTION_STRATEGY_REGISTRY["adx_ema"]["timeframe"]]

    def test_at_least_one_strategy_survives(self):
        """Gate must not brick the engine: the VALIDATED strategy still loads."""
        assert len(governance_filter_strategies(config.ACTIVE_STRATEGIES)) >= 1

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
        assert "BTCUSDT" in assets
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
        assert hb["strategies"] == ["adx_ema"]
        assert hb["strategy"] == "adx_ema"
        assert hb["timeframes"] == ["4h"]
