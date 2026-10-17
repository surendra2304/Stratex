import pytest

from stratex_agents.order_intent import TradeIntent
from stratex_agents.order_store import IntentConflictError, IntentInFlightError, OrderIntentStore
from stratex_agents.oms import StratexOrderManager


class FakeVenue:
    def __init__(self, result=None, error=None):
        self.calls = []
        self.result = result or {"status": "FILLED", "order_id": "demo-1"}
        self.error = error

    def submit_intent(self, intent):
        self.calls.append(intent)
        if self.error:
            raise self.error
        return self.result


def intent(client_id="stx-test-001", venue="binance", environment="binance_demo"):
    return TradeIntent(
        venue=venue, symbol="BTCUSDT" if venue == "binance" else "EURUSD",
        side="BUY", order_type="MARKET", quantity=0.001 if venue == "binance" else 0.01,
        environment=environment, stop_loss=90.0 if venue == "binance" else 1.05,
        take_profit=120.0 if venue == "binance" else 1.15,
        strategy="test_strategy", client_order_id=client_id,
    )


def setup_binance_env(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "FUTURES")
    monkeypatch.setenv("TESTNET_ENABLED", "true")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("BINANCE_DEMO_TRADING", "true")
    monkeypatch.setenv("STRATEX_ALLOWED_EXECUTION_VENUES", "binance")


def test_intent_requires_valid_venue_environment_and_protective_levels():
    with pytest.raises(ValueError, match="environment"):
        TradeIntent(
            venue="mt5", symbol="EURUSD", side="BUY", order_type="MARKET",
            quantity=0.1, environment="mt5_live", stop_loss=1.0,
            take_profit=1.2, strategy="test",
        )
    with pytest.raises(ValueError, match="stop_loss"):
        TradeIntent(
            venue="binance", symbol="BTCUSDT", side="BUY", order_type="MARKET",
            quantity=0.001, environment="binance_demo", stop_loss=0,
            take_profit=120, strategy="test",
        )


def test_order_store_duplicate_and_conflict_are_atomic(tmp_path):
    store = OrderIntentStore(str(tmp_path / "orders.sqlite3"))
    first = intent()
    reserved = store.reserve(first.client_order_id, first.payload_hash(), first.to_dict())
    assert reserved["kind"] == "new"
    with pytest.raises(IntentInFlightError):
        store.reserve(first.client_order_id, first.payload_hash(), first.to_dict())

    store.transition(first.client_order_id, "FILLED", result={"status": "FILLED", "order_id": "demo-1"})
    reopened = OrderIntentStore(str(tmp_path / "orders.sqlite3"))
    duplicate = reopened.reserve(first.client_order_id, first.payload_hash(), first.to_dict())
    assert duplicate["kind"] == "duplicate"
    assert duplicate["status"] == "FILLED"
    assert duplicate["result"]["order_id"] == "demo-1"

    different = intent(client_id=first.client_order_id)
    object.__setattr__(different, "quantity", 0.002)
    with pytest.raises(IntentConflictError):
        reopened.reserve(different.client_order_id, different.payload_hash(), different.to_dict())


def test_oms_submits_once_and_returns_durable_duplicate(monkeypatch, tmp_path):
    setup_binance_env(monkeypatch)
    venue = FakeVenue()
    store = OrderIntentStore(str(tmp_path / "oms.sqlite3"))
    oms = StratexOrderManager(
        {"binance": venue}, authorize_intent=lambda _intent: (True, "approved"),
        store=store, kill_switch_check=lambda: (False, ""),
    )
    trade = intent()
    result = oms.submit(trade)
    assert result.status == "FILLED"
    assert len(venue.calls) == 1

    duplicate = oms.submit(trade)
    assert duplicate.duplicate is True
    assert duplicate.status == "FILLED"
    assert len(venue.calls) == 1


def test_oms_never_blindly_retries_ambiguous_submission(monkeypatch, tmp_path):
    setup_binance_env(monkeypatch)
    venue = FakeVenue(error=TimeoutError("request timed out"))
    store = OrderIntentStore(str(tmp_path / "oms.sqlite3"))
    oms = StratexOrderManager(
        {"binance": venue}, authorize_intent=lambda _intent: (True, "approved"),
        store=store, kill_switch_check=lambda: (False, ""),
    )
    trade = intent("stx-ambiguous-001")
    result = oms.submit(trade)
    assert result.status == "UNKNOWN"
    assert len(venue.calls) == 1
    with pytest.raises(IntentInFlightError):
        oms.submit(trade)
    assert len(venue.calls) == 1


def test_oms_requires_authorization_and_respects_venue_allowlist(monkeypatch, tmp_path):
    setup_binance_env(monkeypatch)
    venue = FakeVenue()
    store = OrderIntentStore(str(tmp_path / "oms.sqlite3"))
    oms = StratexOrderManager(
        {"binance": venue}, authorize_intent=lambda _intent: (False, "risk cap exceeded"),
        store=store, kill_switch_check=lambda: (False, ""),
    )
    result = oms.submit(intent("stx-risk-denied-1"))
    assert result.status == "REJECTED"
    assert "risk cap" in result.reason
    assert venue.calls == []

    monkeypatch.setenv("STRATEX_ALLOWED_EXECUTION_VENUES", "binance")
    with pytest.raises(ValueError, match="MT5"):
        # A production/live environment is never valid even if the caller tries to construct it.
        TradeIntent(
            venue="mt5", symbol="EURUSD", side="BUY", order_type="MARKET",
            quantity=0.1, environment="mt5_live", stop_loss=1.0,
            take_profit=1.2, strategy="test",
        )


def test_oms_blocked_kill_switch_never_calls_venue(monkeypatch, tmp_path):
    setup_binance_env(monkeypatch)
    venue = FakeVenue()
    oms = StratexOrderManager(
        {"binance": venue}, authorize_intent=lambda _intent: (True, "approved"),
        store=OrderIntentStore(str(tmp_path / "oms.sqlite3")),
        kill_switch_check=lambda: (True, "emergency stop is active"),
    )
    result = oms.submit(intent("stx-kill-switch-001"))
    assert result.status == "REJECTED"
    assert "emergency stop" in result.reason
    assert venue.calls == []
