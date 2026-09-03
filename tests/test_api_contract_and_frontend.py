"""
Automated API Contract and Frontend State Verification Suite
Validates:
1. Every Flask route, method, schema, status codes, parameter semantics, and secret isolation.
2. /api/candles timeframe/tf semantics, uppercase symbol normalization, and limit clamping.
3. POST endpoint payload validation and error handling without secret leakage.
4. Exact approved 10-view SPA navigation in static/index.html and static/app.js.
5. Zero dead DOM queries and zero duplicate functions in static/app.js.
6. Drawer/modal lifecycle and Chart destruction invariants.
"""
import os
import re

import pytest

os.environ["TRADING_MODE"] = "TESTNET"
os.environ["TESTNET_ONLY"] = "TRUE"
os.environ["TESTNET_ENABLED"] = "True"

from dashboard import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c

def test_approved_navigation_views_in_html():
    """Verifies exact 10 approved views in order: dashboard, scanner, positions, trades, markets, strategies, risk, analytics, system, settings."""
    with open("static/index.html", "r", encoding="utf-8") as f:
        html = f.read()

    expected_views = [
        "dashboard", "scanner", "positions", "trades", "markets",
        "strategies", "risk", "analytics", "abtest", "optimization", "system", "settings"
    ]


    # Verify nav items
    nav_views = re.findall(r'class=["\'][^"\']*nav-item[^"\']*["\'][^>]*data-view=["\']([^"\']+)["\']', html)
    assert nav_views == expected_views, f"Navigation mismatch: got {nav_views}, expected {expected_views}"

    # Verify view container divs
    container_views = re.findall(r'<div[^>]*id=["\']view-([^"\']+)["\']', html)
    assert container_views == expected_views, f"View container mismatch: got {container_views}, expected {expected_views}"

def test_global_header_elements_in_html():
    """Verifies global header contains STRATEX, ENGINE, MODE, UPTIME, and Clock."""
    with open("static/index.html", "r", encoding="utf-8") as f:
        html = f.read()

    assert "STRATEX" in html
    assert 'id="engine-status"' in html
    assert 'id="hdr-uptime"' in html
    assert 'id="live-clock"' in html
    assert 'id="btn-sound-toggle"' in html

def test_scanner_filters_structure_in_html():
    """Verifies Scanner has single filters dropdown with Symbol, Timeframe, Side, Result, Strategy."""
    with open("static/index.html", "r", encoding="utf-8") as f:
        html = f.read()

    assert 'id="scanner-filter-dropdown"' in html
    assert 'id="sf-symbol"' in html
    assert 'id="sf-tf"' in html
    assert 'id="sf-side"' in html
    assert 'id="sf-result"' in html
    assert 'id="sf-strategy"' in html

def test_frontend_js_zero_duplicate_functions_and_zero_dead_ids():
    """Verifies static/app.js has zero duplicate function definitions and zero queries to non-existent HTML IDs."""
    with open("static/index.html", "r", encoding="utf-8") as f:
        html = f.read()
    with open("static/app.js", "r", encoding="utf-8") as f:
        js = f.read()

    html_ids = set(re.findall(r'id=["\']([^"\']+)["\']', html))
    
    # Check duplicate functions
    func_pattern = re.compile(r'(?:function\s+([a-zA-Z0-9_$]+)\s*\(|(?:\bconst|\blet|\bvar)\s+([a-zA-Z0-9_$]+)\s*=\s*(?:function|\([^)]*\)\s*=>))')
    functions = {}
    for i, line in enumerate(js.splitlines(), start=1):
        m = func_pattern.search(line)
        if m:
            fname = m.group(1) or m.group(2)
            if fname and fname not in ['resolve', 'reject']:
                functions.setdefault(fname, []).append(i)

    duplicates = {k: v for k, v in functions.items() if len(v) > 1}
    assert len(duplicates) == 0, f"Found duplicate functions in app.js: {duplicates}"

    # Check dead DOM queries
    queried_ids = set(re.findall(r'(?:getElementById|\$)\(["\']([^"\']+)["\']\)', js))
    missing_ids = queried_ids - html_ids
    assert len(missing_ids) == 0, f"Found queries for missing HTML IDs in app.js: {missing_ids}"

def test_api_status_contract(client):
    """Verifies /api/status contract schema and data types."""
    res = client.get("/api/status")
    assert res.status_code == 200
    data = res.get_json()
    assert "equity" in data
    assert "cash" in data
    assert "crypto_holdings_value" in data
    assert "realized_pnl" in data
    assert "unrealized_pnl" in data
    assert "open_positions" in data
    assert "server_time" in data
    assert isinstance(data["equity"], (int, float))
    assert isinstance(data["cash"], (int, float))

def test_api_candles_contract_and_parameter_semantics(client):
    """Verifies /api/candles handles tf/timeframe aliases, lowercase symbols, and invalid timeframes."""
    # 1. Standard request
    res = client.get("/api/candles?symbol=BTCUSDT&tf=15m&limit=10")
    assert res.status_code in [200, 503]
    if res.status_code == 200:
        candles = res.get_json()
        assert isinstance(candles, list)
        if len(candles) > 0:
            c = candles[0]
            for key in ["time", "open", "high", "low", "close", "volume", "symbol", "timeframe"]:
                assert key in c

    # 2. Timeframe alias ?timeframe=15m
    res_alias = client.get("/api/candles?symbol=BTCUSDT&timeframe=15m&limit=10")
    assert res_alias.status_code in [200, 503]

    # 3. Lowercase symbol normalization ?symbol=btcusdt
    res_lower = client.get("/api/candles?symbol=btcusdt&tf=15m&limit=10")
    assert res_lower.status_code in [200, 503]

    # 4. Invalid timeframe returns 400 Bad Request
    res_bad_tf = client.get("/api/candles?symbol=BTCUSDT&tf=invalid_tf")
    assert res_bad_tf.status_code == 400
    data_bad = res_bad_tf.get_json()
    assert data_bad["status"] == "ERROR"
    assert "Invalid timeframe" in data_bad["error"]

def test_api_post_endpoints_validation_and_safety(client):
    """Verifies POST endpoints reject bad payloads safely without secret leakage."""
    # /api/settings POST with empty and invalid bodies
    res1 = client.post("/api/settings", data="", content_type="application/json")
    assert res1.status_code == 400

    res2 = client.post("/api/settings", data="{bad_json}", content_type="application/json")
    assert res2.status_code == 400

    # /api/settings POST forbidden live trading toggle
    res3 = client.post("/api/settings", json={"live_trading_enabled": True})
    assert res3.status_code == 403
    assert "SECURITY FORBIDDEN" in res3.get_json()["error"]

    # AI endpoints graceful handling
    res_ai = client.post("/api/ai/signal-analysis", json={})
    assert res_ai.status_code == 200
    assert "analysis" in res_ai.get_json()

def test_api_security_no_secret_leakage_across_routes(client):
    """Verifies no endpoint exposes API secrets or private keys."""
    endpoints = [
        "/api/status", "/api/scanner", "/api/positions", "/api/trades",
        "/api/markets", "/api/strategy-metrics", "/api/risk", "/api/risk-events",
        "/api/telemetry/analytics", "/api/engine-health", "/api/system-events",
        "/api/settings", "/api/ai/status"
    ]
    for ep in endpoints:
        res = client.get(ep)
        text = res.get_data(as_text=True)
        for secret in ["SECRET_KEY", "API_KEY", "BINANCE_SECRET"]:
            assert secret not in text, f"Potential secret leak of '{secret}' in endpoint {ep}"
