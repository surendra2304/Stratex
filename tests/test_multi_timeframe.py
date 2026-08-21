import datetime

from testnet_engine.market_scanner import MarketScanner
from testnet_engine.service import TestnetService


def test_market_scanner_multi_tf_isolation():
    # Setup test scanner with callbacks disabled for isolation
    scanner = MarketScanner(symbols=["BTCUSDT"])
    scanner.callbacks = []
    
    symbol = "BTCUSDT"
    
    # Simulate 1m candle update
    scanner._handle_socket_message({"data": {
        "e": "kline",
        "E": int(datetime.datetime.utcnow().timestamp() * 1000),
        "s": symbol,
        "k": {
            "t": 1000,
            "o": "100",
            "h": "110",
            "l": "90",
            "c": "105",
            "v": "10",
            "x": True,
            "q": "1000",
            "V": "5",
            "Q": "500",
            "i": "1m"
        }
    }})
    
    # Simulate 5m candle update
    scanner._handle_socket_message({"data": {
        "e": "kline",
        "E": int(datetime.datetime.utcnow().timestamp() * 1000),
        "s": symbol,
        "k": {
            "t": 1000,
            "o": "100",
            "h": "120",
            "l": "80",
            "c": "115",
            "v": "20",
            "x": True,
            "q": "2000",
            "V": "10",
            "Q": "1000",
            "i": "5m"
        }
    }})
    
    assert (symbol, "1m") in scanner.candle_cache
    assert (symbol, "5m") in scanner.candle_cache
    
    df_1m = scanner.candle_cache[(symbol, "1m")]
    df_5m = scanner.candle_cache[(symbol, "5m")]
    
    assert df_1m.iloc[-1]['close'] == 105.0
    assert df_5m.iloc[-1]['close'] == 115.0
    
    # Validate no fallback or legacy symbol keys remain
    assert symbol not in scanner.candle_cache

def test_service_strategy_loading(monkeypatch, mocker):
    # Mock environment
    monkeypatch.setattr("testnet_engine.service.TRADING_MODE", "TESTNET")
    monkeypatch.setenv("API_KEY", "dummy")
    monkeypatch.setenv("SECRET_KEY", "dummy")
    
    mock_client = mocker.MagicMock()
    mock_client.get_account.return_value = {"balances": [{"asset": "USDT", "free": "10000.0", "locked": "0.0"}]}
    mocker.patch("testnet_engine.service.get_exchange_client", return_value=mock_client)
    mocker.patch("execution._load_active_trades", return_value=[])
    mocker.patch("testnet_engine.discovery.SymbolDiscoveryService")

    # Mock config to load multiple strategies across different timeframes
    monkeypatch.setattr("testnet_engine.service.ACTIVE_STRATEGIES", {
        "adx_ema": ["4h", "2h"],
        "aggressor": "1m"
    })
    
    service = TestnetService()
    
    assert "4h" in service.strategies
    assert "2h" in service.strategies
    assert "1m" in service.strategies
    
    assert len(service.strategies["4h"]) == 1
    assert service.strategies["4h"][0][0] == "adx_ema"
    
    assert len(service.strategies["2h"]) == 1
    assert service.strategies["2h"][0][0] == "adx_ema"
    
    assert len(service.strategies["1m"]) == 1
    assert service.strategies["1m"][0][0] == "aggressor"
    
    # Validate timeframe_metrics initialization
    assert "4h" in service.stats["timeframe_metrics"]
    assert "2h" in service.stats["timeframe_metrics"]
    assert "1m" in service.stats["timeframe_metrics"]
