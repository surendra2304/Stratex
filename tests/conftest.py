import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Create a global temporary directory for tests
test_dir = tempfile.mkdtemp(prefix="mt5_test_")

# Set all file paths to this temporary directory BEFORE any modules are imported
os.environ["TESTNET_LEDGER_FILE"] = os.path.join(test_dir, "testnet_trade_ledger.jsonl")
os.environ["TESTNET_TRADE_EVENTS_FILE"] = os.path.join(test_dir, "testnet_trade_events.jsonl")
os.environ["TESTNET_EXECUTION_EVENTS_FILE"] = os.path.join(test_dir, "testnet_execution_events.jsonl")
os.environ["TESTNET_SIGNALS_LOG_FILE"] = os.path.join(test_dir, "testnet_signals_log.jsonl")
os.environ["TESTNET_BALANCE_EVENTS_FILE"] = os.path.join(test_dir, "testnet_balance_events.jsonl")
os.environ["TESTNET_OPPORTUNITY_LOG"] = os.path.join(test_dir, "testnet_opportunity_log.jsonl")
os.environ["TESTNET_PORTFOLIO_FILE"] = os.path.join(test_dir, "testnet_portfolio.json")
os.environ["TESTNET_EQUITY_HISTORY_FILE"] = os.path.join(test_dir, "testnet_equity_history.jsonl")
os.environ["ACTIVE_TRADES_FILE"] = os.path.join(test_dir, "active_trades.json")
os.environ["FORWARD_RECONCILIATION_FILE"] = os.path.join(test_dir, "forward_reconciliation.jsonl")
os.environ["TRADING_MODE"] = "TESTNET"
os.environ["TESTNET_ONLY"] = "TRUE"
# Emergency-control state files (panic flag, kill-switch lock, pause flag) live
# in the temp dir too, so tests can never leave a real kill switch engaged in
# the working tree (or inherit one from it).
os.environ["PANIC_STATE_FILE"] = os.path.join(test_dir, "panic_state.json")
os.environ["KILL_SWITCH_LOCK_FILE"] = os.path.join(test_dir, "KILL_SWITCH_ACTIVE.lock")
os.environ["TRADING_PAUSE_STATE_FILE"] = os.path.join(test_dir, "trading_pause_state.json")

# Clear any secret keys for unauthenticated local test client assertions
for k in ["BOT_API_KEY", "API_KEY_CONTROL", "API_KEY_READONLY", "API_KEY_FRIDAY"]:
    os.environ[k] = ""


# Deliberately hyphenated so it contains no long alphanumeric run: a real
# Binance-style key is one long run, and tests/test_credentials.py flags those.
# This value is monkeypatched into the environment for a single test and is
# never written to disk or sent to an exchange.
CONTROL_API_KEY = "pytest-control-scope-key-not-a-real-credential"


FRIDAY_API_KEY = "pytest-friday-scope-key-not-a-real-credential"


@pytest.fixture
def friday_auth(monkeypatch):
    """Configures a FRIDAY-scope API key (read+control+friday) for one test."""
    monkeypatch.setenv("API_KEY_FRIDAY", FRIDAY_API_KEY)
    return {"X-API-KEY": FRIDAY_API_KEY}


@pytest.fixture(autouse=True)
def _reset_security_throttles():
    """Rate-limit windows and auth-failure IP blocks are process-global; reset
    them per test so one test's expected 401s/requests can't throttle another."""
    def _clear():
        import importlib

        for module_name, attrs in (
            ("security_hardening", ("_read_rate_limiter", "_control_rate_limiter", "_ip_rate_limiter")),
            ("api.auth", ("api_rate_limiter", "control_rate_limiter")),
        ):
            try:
                module = importlib.import_module(module_name)
            except Exception:
                continue
            for attr in attrs:
                limiter = getattr(module, attr, None)
                if limiter is not None and hasattr(limiter, "records"):
                    with limiter._lock:
                        limiter.records.clear()
        try:
            import security_hardening

            monitor = security_hardening._security_monitor
            with monitor._lock:
                monitor.failed_auth_attempts.clear()
        except Exception:
            pass

    _clear()
    yield
    _clear()


@pytest.fixture
def control_auth(monkeypatch):
    """Configures a control-scope API key for one test and returns its headers.

    Mutating endpoints now fail CLOSED (503 AUTH_NOT_CONFIGURED) when no API key
    is configured, so any test that needs to reach a mutating handler's business
    logic must present a key. Returning the header keeps every existing assertion
    about validation bounds, live-trading rejection and enforcer actions intact,
    and additionally proves the handler is reachable only to an authenticated
    control-scope caller.
    """
    monkeypatch.setenv("API_KEY_CONTROL", CONTROL_API_KEY)
    monkeypatch.delenv("TRADING_BOT_API_KEY", raising=False)
    return {"X-API-KEY": CONTROL_API_KEY}


@pytest.fixture(autouse=True)
def _reset_emergency_control_state():
    """Each test starts with no panic, no kill-switch lock and no pause flag."""
    paths = [os.environ.get(k) for k in ("PANIC_STATE_FILE", "KILL_SWITCH_LOCK_FILE", "TRADING_PAUSE_STATE_FILE")]
    for path in paths:
        if path and os.path.exists(path):
            os.remove(path)
    yield
    for path in paths:
        if path and os.path.exists(path):
            os.remove(path)
