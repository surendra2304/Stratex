import pandas as pd

from testnet_engine.discovery import SymbolDiscoveryService
from testnet_engine.market_scanner import MarketScanner
from testnet_engine.risk_gate import RiskGate


class TestMultiAssetEngine:

    def test_risk_gate_multi_asset_exposure(self):
        """Test that RiskGate correctly rejects multi-asset trades exceeding max portfolio exposure."""
        import config
        config.MAX_NET_DIRECTIONAL_EXPOSURE = 0.10
        
        gate = RiskGate(starting_balance=10000.0)
        
        # Simulate having 4 open positions already
        # Current exposure = 4.5% (Very close to 5.0% max)
        active = {
            "POS1": {"quantity": 0.009, "entry_price": 50000.0, "side": "LONG"} # 0.045 * 10000 = $450
        }
        
        # We attempt to place a new trade that is fine on its own ($40 new risk)
        passed, reason, _ = gate.evaluate_risk("SOLUSDT", "LONG", 10000.0, active, proposed_qty=0.4, entry_price=100.0, data_health_status="OK")
        assert passed  # Can proceed
        
        # Test Cap
        # The trade wants 1.0 BTC at $50k. Risk is $100.
        filters = {"stepSize": 0.01, "minNotional": 10.0}
        gate.calculate_position_size(10000.0, 50000.0, 49900.0, filters)
        
        # Let's test the rejection
        active_high = {
            "POS1": {"quantity": 0.01, "entry_price": 51000.0, "side": "LONG"} # $510 exposure
        }
        passed, reason, _ = gate.evaluate_risk("ETHUSDT", "LONG", 10000.0, active_high, proposed_qty=0.4, entry_price=100.0, data_health_status="OK")
        assert not passed
        assert reason == "MAX_EXPOSURE_REACHED"

    def test_discovery_fallback(self, mocker):
        """Test that SymbolDiscoveryService falls back safely if Binance API fails."""
        mocker.patch('binance.client.Client.ping')
        service = SymbolDiscoveryService(testnet=True)
        
        # Mock client to raise exception
        mocker.patch.object(service.client, 'get_exchange_info', side_effect=Exception("API limit"))
        
        symbols = service.discover_eligible_symbols()
        assert "BTCUSDT" in symbols
        assert len(symbols) == 2

    def test_market_scanner_cache_rotation(self, mocker):
        """Test that MarketScanner maintains a maximum cache size per symbol."""
        # Mock TWM to prevent real connection
        mocker.patch('testnet_engine.market_scanner.ThreadedWebsocketManager.start')
        mocker.patch('testnet_engine.market_scanner.ThreadedWebsocketManager.start_multiplex_socket')
        mocker.patch('binance.client.Client.ping')
        
        scanner = MarketScanner(["BTCUSDT"])
        scanner.candle_cache["BTCUSDT"] = pd.DataFrame(index=range(250)) # Fake 250 items
        
        # Inject new websocket message
        msg = {
            'data': {
                'e': 'kline',
                's': 'BTCUSDT',
                'k': {
                    'x': True,
                    't': 1600000000,
                    'o': 100, 'h': 105, 'l': 95, 'c': 102, 'v': 10
                }
            }
        }
        
        scanner._handle_socket_message(msg)
        
        # Cache must remain exactly 250
        assert len(scanner.candle_cache["BTCUSDT"]) == 250
