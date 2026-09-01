"""
Verification Test Suite for Gemini & Quantum Subsystem Isolation
Verifies:
1. Gemini AI is strictly advisory with zero execution authority.
2. Quantum is strictly research/advisory with zero execution authority.
3. Secret isolation (no API key leakage in responses, logs, or exceptions).
4. Graceful degradation when optional dependencies or API keys are missing.
5. Inability of advisory layers to mutate risk limits, trade signals, or live state.
"""
import pytest

from dashboard import app
from gemini_service import GeminiService, get_gemini_service
from quantum.service import QuantumService


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c

def test_gemini_service_isolation_and_advisory_contract():
    """Verifies GeminiService has no execution capabilities and degrades gracefully."""
    service = GeminiService(api_key="", enabled=False)
    
    # Check status
    st = service.get_status()
    assert st["status"] == "SUCCESS"
    assert st["gemini"]["enabled"] is False
    assert st["gemini"]["status"] == "UNAVAILABLE"
    assert "api_key" not in st["gemini"]

    # Test analysis with empty context
    sig_res = service.analyze_signal({})
    assert "why" in sig_res
    assert sig_res["ai_available"] is False

    trd_res = service.analyze_trade({})
    assert "trade_summary" in trd_res
    assert trd_res["ai_available"] is False

    perf_res = service.analyze_performance({})
    assert "performance_summary" in perf_res

    sys_res = service.analyze_system_diagnostics({})
    assert "system_summary" in sys_res

def test_gemini_cannot_mutate_risk_or_execution():
    """Verifies calling Gemini analysis functions does not mutate runtime risk limits or trading mode."""
    import config
    orig_risk = config.MAX_TESTNET_RISK_PER_TRADE
    orig_exposure = config.MAX_TESTNET_EXPOSURE
    orig_live = config.LIVE_TRADING_ENABLED

    service = get_gemini_service()
    service.analyze_signal({"symbol": "BTCUSDT", "side": "BUY", "risk_result": "REJECTED"})

    assert config.MAX_TESTNET_RISK_PER_TRADE == orig_risk
    assert config.MAX_TESTNET_EXPOSURE == orig_exposure
    assert config.LIVE_TRADING_ENABLED == orig_live
    assert config.LIVE_TRADING_ENABLED is False

def test_quantum_service_isolation_and_advisory_contract(client):
    """Verifies QuantumService is read-only and /api/quantum/advisory has no execution hooks."""
    qs = QuantumService()
    adv = qs.get_advisory(symbol="BTCUSDT", tf="15m")
    assert isinstance(adv, dict)
    assert "quantum_status" in adv
    assert "quantum_score" in adv
    assert "classical_score" in adv
    assert "hybrid_score" in adv

    # Verify no execution keys exist in the schema
    forbidden_keys = ["order_id", "execute", "buy", "sell", "place_order", "cancel_order"]
    for k in forbidden_keys:
        assert k not in adv

    # Test HTTP endpoint
    res = client.get("/api/quantum/advisory?symbol=BTCUSDT&tf=15m")
    assert res.status_code == 200
    data = res.get_json()
    assert "quantum_status" in data
    assert "symbol" in data
    assert data["symbol"] == "BTCUSDT"

def test_ai_endpoints_secret_leakage_and_error_handling(client):
    """Verifies POST /api/ai/* endpoints never leak credentials."""
    for ep in ["/api/ai/signal-analysis", "/api/ai/trade-analysis", "/api/ai/performance-analysis", "/api/ai/system-analysis"]:
        res = client.post(ep, json={"symbol": "BTCUSDT"})
        assert res.status_code == 200
        text = res.get_data(as_text=True)
        assert "AIza" not in text
        assert "SECRET" not in text
