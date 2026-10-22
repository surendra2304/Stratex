"""Tests for api/validation.py and the endpoints hardened with it.

The HTTP route census (scripts/http_route_census.py) found that wrong-typed
JSON fields crashed several POST handlers with 500s (``float({})``,
``pd.DataFrame("many")``, ``"x".upper()`` on non-strings) and that
``"NaN"``/``"Infinity"`` strings flowed into risk arithmetic. These tests pin
the strict parsing contract and the endpoint behaviour.
"""

from __future__ import annotations

import math

import pytest
from flask import Flask

from api.validation import (
    MISSING,
    RequestValidationError,
    coerce_finite_float,
    finite_float_item,
    get_bool,
    get_dict,
    get_float,
    get_int,
    get_list,
    get_str,
    get_symbol,
    install_validation_error_handler,
    json_body,
    object_item,
)


# ── unit: helpers ─────────────────────────────────────────────────────────────

class TestGetFloat:
    @pytest.mark.parametrize("value, expected", [(1, 1.0), (2.5, 2.5), ("3.25", 3.25), (" 4 ", 4.0), (0, 0.0)])
    def test_accepts_real_numbers(self, value, expected):
        assert get_float({"x": value}, "x") == expected

    @pytest.mark.parametrize("value", [True, False, "NaN", "nan", "Infinity", "-inf", 1e309, float("nan"),
                                       "abc", "", [], {}, [1.0], {"a": 1}])
    def test_rejects_non_finite_and_non_numbers(self, value):
        with pytest.raises(RequestValidationError) as err:
            get_float({"x": value}, "x")
        assert err.value.field == "x"

    def test_default_and_missing(self):
        assert get_float({}, "x", 1.5) == 1.5
        assert get_float({"x": None}, "x", 2.5) == 2.5
        with pytest.raises(RequestValidationError, match="required"):
            get_float({}, "x")

    def test_range_checks(self):
        assert get_float({"x": 5}, "x", min=5, max=5) == 5
        for kwargs, value in (({"min": 0}, -0.1), ({"max": 1}, 1.01), ({"gt": 0}, 0), ({"lt": 1}, 1)):
            with pytest.raises(RequestValidationError):
                get_float({"x": value}, "x", **kwargs)

    def test_numeric_strings_can_be_disallowed(self):
        with pytest.raises(RequestValidationError):
            get_float({"x": "1.0"}, "x", allow_numeric_string=False)


class TestGetInt:
    @pytest.mark.parametrize("value, expected", [(3, 3), (3.0, 3), ("42", 42), ("-7", -7)])
    def test_accepts_integers(self, value, expected):
        assert get_int({"n": value}, "n") == expected

    @pytest.mark.parametrize("value", [True, 3.5, "3.5", "1e3", float("inf"), "x" * 30, [], {}, "9" * 40])
    def test_rejects_non_integers(self, value):
        with pytest.raises(RequestValidationError):
            get_int({"n": value}, "n")

    def test_bounds(self):
        with pytest.raises(RequestValidationError):
            get_int({"n": 0}, "n", min=1)
        with pytest.raises(RequestValidationError):
            get_int({"n": 11}, "n", max=10)


class TestGetStrAndSymbol:
    def test_normalization(self):
        assert get_str({"s": "  buy "}, "s", upper=True) == "BUY"
        assert get_str({"s": "ABC"}, "s", lower=True) == "abc"
        assert get_symbol({"symbol": "btc/usdt"}) == "BTCUSDT"
        assert get_symbol({"symbol": "eth-usdt"}) == "ETHUSDT"

    @pytest.mark.parametrize("value", [1, 1.5, True, [], {}, None])
    def test_type_errors(self, value):
        body = {"s": value}
        if value is None:
            with pytest.raises(RequestValidationError, match="required"):
                get_str(body, "s")
        else:
            with pytest.raises(RequestValidationError):
                get_str(body, "s")

    def test_choices_pattern_and_length(self):
        with pytest.raises(RequestValidationError, match="one of"):
            get_str({"s": "HOLD"}, "s", choices=("BUY", "SELL"))
        import re
        with pytest.raises(RequestValidationError, match="format"):
            get_str({"s": "a b"}, "s", pattern=re.compile(r"^\w+$"))
        with pytest.raises(RequestValidationError, match="at most"):
            get_str({"s": "x" * 10}, "s", max_len=5)
        with pytest.raises(RequestValidationError, match="at least"):
            get_str({"s": ""}, "s", min_len=1)

    @pytest.mark.parametrize("symbol", ["", "B", "BTC USDT", "../../etc", "BTCUSDT;DROP", "X" * 30, "₿TC"])
    def test_bad_symbols(self, symbol):
        with pytest.raises(RequestValidationError):
            get_symbol({"symbol": symbol})


class TestGetBoolDictList:
    def test_strict_bool(self):
        assert get_bool({"b": True}, "b") is True
        assert get_bool({"b": False}, "b") is False
        for value in ("true", "false", 1, 0, "yes", [], {}):
            with pytest.raises(RequestValidationError):
                get_bool({"b": value}, "b")

    def test_dict(self):
        assert get_dict({"d": {"a": 1}}, "d") == {"a": 1}
        assert get_dict({}, "d", {}) == {}
        with pytest.raises(RequestValidationError):
            get_dict({"d": [1]}, "d")
        with pytest.raises(RequestValidationError, match="at most"):
            get_dict({"d": {str(i): i for i in range(5)}}, "d", max_keys=4)

    def test_list_with_item_validators(self):
        assert get_list({"l": [1, "2.5"]}, "l", item=finite_float_item) == [1.0, 2.5]
        with pytest.raises(RequestValidationError) as err:
            get_list({"l": [1, "NaN"]}, "l", item=finite_float_item)
        assert err.value.field == "l[1]"
        with pytest.raises(RequestValidationError):
            get_list({"l": [{}, 3]}, "l", item=object_item)
        with pytest.raises(RequestValidationError):
            get_list({"l": "abc"}, "l")
        with pytest.raises(RequestValidationError, match="at most"):
            get_list({"l": [1, 2, 3]}, "l", max_len=2)
        with pytest.raises(RequestValidationError, match="at least"):
            get_list({"l": []}, "l", min_len=1)

    def test_defaults_are_copied(self):
        default: list[int] = []
        result = get_list({}, "l", default)
        result.append(1)
        assert default == []
        assert MISSING is not None


def test_coerce_finite_float_messages():
    assert coerce_finite_float("p", "1e3") == 1000.0
    with pytest.raises(RequestValidationError, match="finite"):
        coerce_finite_float("p", math.inf)


def test_json_body_and_error_handler():
    app = Flask(__name__)
    install_validation_error_handler(app)

    @app.post("/echo")
    def echo():
        body = json_body()
        return {"qty": get_float(body, "qty", gt=0)}

    client = app.test_client()
    assert client.post("/echo", json={"qty": 2}).get_json() == {"qty": 2.0}
    res = client.post("/echo", json={"qty": "NaN"})
    assert res.status_code == 400
    assert res.get_json()["field"] == "qty"
    assert client.post("/echo", json=[1, 2]).status_code == 400
    assert client.post("/echo", data="").status_code == 400  # qty required


# ── endpoints ────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from dashboard import app

    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.mark.parametrize("path, body", [
    ("/api/v1/nautilus/orders/bracket", {"quantity": {"a": 1}}),
    ("/api/v1/nautilus/orders/bracket", {"entry_price": "NaN"}),
    ("/api/v1/nautilus/orders/bracket", {"side": 7}),
    ("/api/v1/nautilus/orders/bracket", {"entry_type": "MAGIC"}),
    ("/api/v1/nautilus/risk/check", {"price": [1]}),
    ("/api/v1/nautilus/risk/check", {"quantity": -1}),
    ("/api/v1/nautilus/risk/check", {"quantity": True}),
    ("/api/v1/nautilus/simulate", {"ticks": "many"}),
    ("/api/v1/nautilus/simulate", {"ticks": [1, 2]}),
    ("/api/v1/nautilus/simulate", {"ticks": [{"price": "Infinity"}]}),
    ("/api/v1/nautilus/simulate", {"ticks": [{"price": 1, "ts_event": "soon"}]}),
    ("/api/v1/nautilus/bars/aggregate", {"step": 0}),
    ("/api/v1/nautilus/bars/aggregate", {"step": 0.5, "bar_type": "TICK"}),
    ("/api/v1/nautilus/bars/aggregate", {"bar_type": 3}),
    ("/api/v1/backtrader/run", {"candles": "many"}),
    ("/api/v1/backtrader/run", {"candles": [{"open": 1}]}),
    ("/api/v1/backtrader/run", {"candles": [{"close": "NaN"}]}),
    ("/api/v1/backtrader/run", {"candles": [{"close": 1, "high": 1, "low": 2}]}),
    ("/api/v1/backtrader/run", {"candles": [{"close": 1}], "strategy": "Unknown"}),
    ("/api/v1/backtrader/run", {"candles": [{"close": 1}], "sizer": "__class__"}),
    ("/api/v1/backtrader/run", {"candles": [{"close": 1}], "params": {"broker": 1}}),
    ("/api/v1/backtrader/run", {"candles": [{"close": 1}], "params": {"fast_period": 50, "slow_period": 10}}),
    ("/api/v1/backtrader/run", {"candles": [{"close": 1}], "initial_cash": 0}),
    ("/api/v1/backtrader/analyze", {"trades": [{"pnl": "lots"}]}),
    ("/api/v1/backtrader/analyze", {"trades": ["x"]}),
])
def test_wrong_typed_compute_requests_are_400(client, path, body):
    res = client.post(path, json=body)
    assert res.status_code == 400, (path, body, res.get_data(as_text=True)[:300])
    assert res.get_json()["status"] == "ERROR"


def test_bracket_leg_ordering_is_enforced(client):
    bad = {"side": "BUY", "entry_price": 100, "take_profit_price": 90, "stop_loss_price": 95, "quantity": 0.01}
    res = client.post("/api/v1/nautilus/orders/bracket", json=bad)
    assert res.status_code == 400
    assert "stop_loss_price < entry_price < take_profit_price" in res.get_json()["message"]
    bad_sell = {"side": "SELL", "entry_price": 100, "take_profit_price": 110, "stop_loss_price": 105}
    assert client.post("/api/v1/nautilus/orders/bracket", json=bad_sell).status_code == 400


def test_aggregate_rejects_mixed_symbols_and_unordered_ticks(client):
    mixed = {"ticks": [{"symbol": "BTCUSDT", "price": 1, "ts_event": 1},
                       {"symbol": "ETHUSDT", "price": 1, "ts_event": 2}]}
    res = client.post("/api/v1/nautilus/bars/aggregate", json=mixed)
    assert res.status_code == 400 and "one symbol" in res.get_json()["message"]
    unordered = {"ticks": [{"price": 1, "ts_event": 5}, {"price": 1, "ts_event": 4}]}
    res = client.post("/api/v1/nautilus/bars/aggregate", json=unordered)
    assert res.status_code == 400 and "ordered" in res.get_json()["message"]


def test_valid_backtrader_run_still_works(client):
    candles = [{"close": 100 + (i % 7) - (i % 3), "volume": 10} for i in range(80)]
    res = client.post("/api/v1/backtrader/run", json={
        "candles": candles, "strategy": "SMACross", "params": {"fast_period": 3, "slow_period": 8},
    })
    assert res.status_code == 200, res.get_data(as_text=True)[:300]
    data = res.get_json()["data"]
    assert data["evidence_class"] == "caller_supplied_data_backtest"
    assert math.isfinite(data["ending_cash"])


def test_agent_gateway_validates_and_reports_no_execution(client, control_auth, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for body in ({"action": 5}, {"action": "DEPLOY"}, {"strategy_id": ["x"]}, {"n_trials": "many", "action": "OPTIMIZATION"},
                 {"windows": 1, "action": "WALK_FORWARD"}, {"parameters": "str"}, {"job_id": "../x"}):
        res = client.post("/api/agent-gateway/jobs", json=body, headers=control_auth)
        assert res.status_code == 400, (body, res.get_data(as_text=True)[:200])

    ok = client.post("/api/agent-gateway/jobs", json={"job_id": "agent_dup"}, headers=control_auth)
    assert ok.status_code == 202
    assert ok.get_json()["execution"] == "NOT_SCHEDULED"
    dup = client.post("/api/agent-gateway/jobs", json={"job_id": "agent_dup"}, headers=control_auth)
    assert dup.status_code == 409
    first = client.post("/api/agent-gateway/jobs", json={}, headers=control_auth).get_json()["job"]["job_id"]
    second = client.post("/api/agent-gateway/jobs", json={}, headers=control_auth).get_json()["job"]["job_id"]
    assert first != second
