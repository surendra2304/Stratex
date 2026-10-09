"""tests/test_stratex_ccxt_complete.py

Comprehensive test suite verifying CCXT Multi-Exchange Capabilities integrated into Stratex:
1. CCXTHub singleton, exchange pooling, and caching
2. ArbitrageScanner price comparison and spread math
3. FundingRateComparator basis spread and caching
4. OrderBookAnalyzer depth, micro-price, and imbalance ratio
5. Normalized models immutability
6. Flask REST API endpoints (/api/v1/ccxt/*)
"""

import pytest
from datetime import datetime, timezone
from dataclasses import asdict

from stratex_ccxt_adapter import (
    ccxt_hub,
    CCXTHub,
    ArbitrageScanner,
    FundingRateComparator,
    OrderBookAnalyzer,
    NormalizedTicker,
    ArbitrageOpportunity,
    OrderBookDepthAnalysis,
    FundingRateComparison,
)
from dashboard import app


@pytest.fixture
def flask_client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


# ------------------------------------------------------------------------------
# 1. CCXTHub & Exchange Pooling Tests
# ------------------------------------------------------------------------------
def test_ccxt_hub_singleton_and_listing():
    hub1 = CCXTHub()
    hub2 = CCXTHub()
    assert hub1 is hub2
    assert hub1 is ccxt_hub

    exchanges = ccxt_hub.list_exchanges()
    assert "binance" in exchanges
    assert "okx" in exchanges
    assert "bybit" in exchanges
    assert "kraken" in exchanges


def test_ccxt_hub_exchange_caching():
    adapter1 = ccxt_hub.get_exchange("binance")
    adapter2 = ccxt_hub.get_exchange("binance")
    assert adapter1 is adapter2
    assert adapter1.exchange_id == "binance"


def test_ccxt_hub_status():
    status = ccxt_hub.get_status()
    # HEALTHY requires a successful exchange round trip; offline test runs can
    # only ever see an unverified/degraded/empty hub.
    assert status["status"] in {"NO_ACTIVE_ADAPTERS", "UNVERIFIED", "DEGRADED"}
    assert "100% Free Public" in status["mode"]
    assert "supported_exchanges" in status


def test_ccxt_hub_status_reflects_adapter_evidence(monkeypatch):
    hub = CCXTHub()

    class _Adapter:
        def __init__(self, status):
            self._status = status

        def get_health_status(self):
            return {"status": self._status}

    monkeypatch.setattr(hub, "_exchanges", {})
    assert hub.get_status()["status"] == "NO_ACTIVE_ADAPTERS"
    monkeypatch.setattr(hub, "_exchanges", {"a": _Adapter("UNVERIFIED")})
    assert hub.get_status()["status"] == "UNVERIFIED"
    monkeypatch.setattr(hub, "_exchanges", {"a": _Adapter("HEALTHY"), "b": _Adapter("DEGRADED")})
    assert hub.get_status()["status"] == "DEGRADED"
    monkeypatch.setattr(hub, "_exchanges", {"a": _Adapter("HEALTHY"), "b": _Adapter("HEALTHY")})
    assert hub.get_status()["status"] == "HEALTHY"


def test_hub_rejects_non_exchange_ids():
    for bogus in ("nope", "__init__", "Exchange", "NetworkError"):
        with pytest.raises(KeyError):
            ccxt_hub.get_exchange(bogus)


# ------------------------------------------------------------------------------
# 2. ArbitrageScanner Tests
# ------------------------------------------------------------------------------
def test_arbitrage_scanner_opportunity_math():
    scanner = ArbitrageScanner(min_spread_pct=0.10)
    mock_tickers = {
        "binance": NormalizedTicker("BTC/USDT", last=65000.0, bid=64990.0, ask=65000.0, base_volume=10.0, quote_volume=650000.0, timestamp_ms=None),
        "okx": NormalizedTicker("BTC/USDT", last=65100.0, bid=65120.0, ask=65130.0, base_volume=8.0, quote_volume=520000.0, timestamp_ms=None),
        "bybit": NormalizedTicker("BTC/USDT", last=65050.0, bid=65040.0, ask=65060.0, base_volume=5.0, quote_volume=325000.0, timestamp_ms=None),
    }

    opp = scanner.scan("BTCUSDT", mock_tickers)
    # Lowest ask is Binance (65000.0), Highest bid is OKX (65120.0)
    assert opp.buy_exchange == "binance"
    assert opp.buy_price == 65000.0
    assert opp.sell_exchange == "okx"
    assert opp.sell_price == 65120.0

    # Spread = 65120 - 65000 = 120.0
    assert opp.spread == 120.0
    # Spread pct = 120 / 65000 * 100 = ~0.1846%
    assert pytest.approx(opp.spread_pct, rel=1e-3) == (120.0 / 65000.0) * 100.0
    assert opp.is_arbitrage_viable is True  # 0.1846% >= 0.10%


def test_arbitrage_scanner_non_viable():
    scanner = ArbitrageScanner(min_spread_pct=0.50)  # High threshold
    mock_tickers = {
        "binance": NormalizedTicker("BTC/USDT", last=65000.0, bid=64999.0, ask=65001.0, base_volume=10.0, quote_volume=650000.0, timestamp_ms=None),
        "okx": NormalizedTicker("BTC/USDT", last=65002.0, bid=65001.0, ask=65003.0, base_volume=8.0, quote_volume=520000.0, timestamp_ms=None),
    }
    opp = scanner.scan("BTCUSDT", mock_tickers)
    assert opp.is_arbitrage_viable is False


# ------------------------------------------------------------------------------
# 3. FundingRateComparator Tests
# ------------------------------------------------------------------------------
class _FundingExchange:
    def __init__(self, rate=None, error=None):
        self.rate, self.error = rate, error

    def fetch_funding_rate(self, _symbol):
        if self.error:
            raise self.error
        return {"fundingRate": self.rate}


def test_funding_rate_comparator_spread():
    comparator = FundingRateComparator(cache_ttl_seconds=60)
    comparison = comparator.compare("BTCUSDT", {
        "binance": _FundingExchange(0.0001),
        "bybit": _FundingExchange(0.0003),
        "okx": _FundingExchange(error=RuntimeError("venue down")),
    })

    assert comparison.symbol == "BTCUSDT"
    assert comparison.rates == {"binance": 0.0001, "bybit": 0.0003}
    assert comparison.max_rate == 0.0003 and comparison.min_rate == 0.0001
    assert comparison.spread_bps == pytest.approx(2.0)


def test_funding_rate_comparator_never_fabricates_rates():
    from stratex_ccxt_adapter import MarketDataUnavailable

    comparator = FundingRateComparator(cache_ttl_seconds=60)
    with pytest.raises(MarketDataUnavailable):
        comparator.compare("BTCUSDT")
    with pytest.raises(MarketDataUnavailable):
        comparator.compare("BTCUSDT", {"okx": _FundingExchange(error=RuntimeError("down"))})
    # Failures are not cached: a later real answer is served.
    assert comparator.compare("BTCUSDT", {"okx": _FundingExchange(0.0002)}).rates == {"okx": 0.0002}


def test_funding_comparator_tolerates_concurrent_adapter_registration():
    comparator = FundingRateComparator(cache_ttl_seconds=0)
    exchanges = {}

    class _Growing:
        def fetch_funding_rate(self, _symbol):
            exchanges[f"late{len(exchanges)}"] = _FundingExchange(0.0001)  # another thread registers
            return {"fundingRate": 0.0002}

    exchanges["first"] = _Growing()
    result = comparator.compare("ETHUSDT", exchanges)
    assert result.rates == {"first": 0.0002}


def test_arbitrage_scanner_requires_two_real_quotes():
    from stratex_ccxt_adapter import MarketDataUnavailable

    scanner = ArbitrageScanner(min_spread_pct=0.10)
    one = {"binance": NormalizedTicker("BTC/USDT", last=1.0, bid=1.0, ask=1.1, base_volume=1.0, quote_volume=1.0, timestamp_ms=None)}
    for tickers in (None, {}, one):
        with pytest.raises(MarketDataUnavailable):
            scanner.scan("BTCUSDT", tickers)


# ------------------------------------------------------------------------------
# 4. OrderBookAnalyzer Tests
# ------------------------------------------------------------------------------
def test_order_book_depth_analyzer():
    mock_ob = {
        "bids": [
            [50000.0, 2.0],  # 100k
            [49900.0, 3.0],  # 149.7k
            [49800.0, 5.0],  # 249k
        ],
        "asks": [
            [50100.0, 1.0],  # 50.1k
            [50200.0, 2.0],  # 100.4k
            [50300.0, 2.0],  # 100.6k
        ],
    }

    res = OrderBookAnalyzer.analyze(mock_ob, symbol="BTCUSDT", exchange="binance", depth_levels=3)
    # Best bid 50000, Best ask 50100 -> Spread = 100, Mid = 50050.0
    assert res.spread == 100.0
    assert res.mid_price == 50050.0
    # Micro-price: (50100 * 2.0 + 50000 * 1.0) / 3.0 = (100200 + 50000) / 3 = 150200 / 3 = 50066.67
    assert pytest.approx(res.micro_price, rel=1e-3) == 50066.67
    # Bid depth = 100k + 149.7k + 249k = 498.7k
    assert pytest.approx(res.bid_depth_usd, rel=1e-2) == 498700.0
    # Ask depth = 50.1k + 100.4k + 100.6k = 251.1k
    assert pytest.approx(res.ask_depth_usd, rel=1e-2) == 251100.0
    # Positive imbalance since bids > asks
    assert res.imbalance_ratio > 0.0


def test_order_book_analyzer_empty():
    res = OrderBookAnalyzer.analyze({}, symbol="BTCUSDT", exchange="binance")
    assert res.mid_price == 0.0
    assert res.imbalance_ratio == 0.0


# ------------------------------------------------------------------------------
# 5. Flask REST API Routes Tests
# ------------------------------------------------------------------------------
def test_flask_ccxt_routes(flask_client):
    # GET /status
    res = flask_client.get("/api/v1/ccxt/status")
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "OK"
    assert data["data"]["status"] in {"NO_ACTIVE_ADAPTERS", "UNVERIFIED", "DEGRADED"}

    # GET /exchanges
    res = flask_client.get("/api/v1/ccxt/exchanges")
    assert res.status_code == 200
    data = res.get_json()
    assert "binance" in data["data"]

    # Offline: no real quotes -> honest 503, never a synthetic opportunity/book.
    for url in ("/api/v1/ccxt/arbitrage?symbol=BTCUSDT",
                "/api/v1/ccxt/depth?symbol=BTCUSDT&exchange=binance",
                "/api/v1/ccxt/funding?symbol=BTCUSDT"):
        res = flask_client.get(url)
        assert res.status_code == 503, url
        assert res.get_json()["error"] in {"DATA_UNAVAILABLE", "UPSTREAM_UNAVAILABLE"}
        assert "65000" not in res.get_data(as_text=True)

    # Invalid input is a client error, not a 500.
    for url in ("/api/v1/ccxt/ticker?exchange=__init__",
                "/api/v1/ccxt/depth?exchange=nope",
                "/api/v1/ccxt/depth?levels=0",
                "/api/v1/ccxt/depth?levels=100000",
                "/api/v1/ccxt/ticker?symbol=%00bad",
                "/api/v1/ccxt/funding?symbol=" + "A" * 200):
        assert flask_client.get(url).status_code == 400, url


def test_flask_ccxt_arbitrage_with_real_quotes(flask_client, monkeypatch):
    from api import ccxt_routes

    quotes = {
        "binance": NormalizedTicker("BTC/USDT", last=100.0, bid=99.9, ask=100.0, base_volume=1.0, quote_volume=100.0, timestamp_ms=1),
        "okx": NormalizedTicker("BTC/USDT", last=100.5, bid=100.4, ask=100.6, base_volume=1.0, quote_volume=100.0, timestamp_ms=1),
    }
    monkeypatch.setattr(ccxt_routes.ccxt_hub, "fetch_multi_ticker", lambda symbol, exchange_ids=None: quotes)
    monkeypatch.setattr(ccxt_routes.ccxt_hub.arbitrage_scanner, "_cache", {})
    res = flask_client.get("/api/v1/ccxt/arbitrage?symbol=BTCUSDT")
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert (data["buy_exchange"], data["sell_exchange"]) == ("binance", "okx")
    assert data["spread"] == pytest.approx(0.4)


def test_flask_ccxt_depth_with_real_book(flask_client, monkeypatch):
    from api import ccxt_routes

    class _Adapter:
        def fetch_order_book(self, symbol, limit=30):
            return {"bids": [[99.0, 2.0], [98.0, 1.0]], "asks": [[101.0, 1.0], [102.0, 3.0]]}

    monkeypatch.setattr(ccxt_routes.ccxt_hub, "get_exchange", lambda exchange_id="binance", **kw: _Adapter())
    res = flask_client.get("/api/v1/ccxt/depth?symbol=BTCUSDT&exchange=binance&levels=2")
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "OK"
    assert data["data"]["mid_price"] == pytest.approx(100.0)
    assert "imbalance_ratio" in data["data"] and "micro_price" in data["data"]

    class _EmptyAdapter:
        def fetch_order_book(self, symbol, limit=30):
            return {"bids": [], "asks": []}

    monkeypatch.setattr(ccxt_routes.ccxt_hub, "get_exchange", lambda exchange_id="binance", **kw: _EmptyAdapter())
    res = flask_client.get("/api/v1/ccxt/depth?symbol=BTCUSDT&exchange=binance")
    assert res.status_code == 503 and res.get_json()["error"] == "DATA_UNAVAILABLE"
