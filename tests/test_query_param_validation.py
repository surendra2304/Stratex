"""Item 10 regression tests — GET query-parameter validation.

Malformed query values used to reach ``int(...)`` directly (500s on "abc",
"1.5", "nan", ""), ``limit=0`` divided by zero, negative limits/pages sliced
from the wrong end, ``limit`` had no upper bound on several list endpoints,
a path segment became a file name, and exports loaded whole ledgers.
"""

from __future__ import annotations

import json
import os

import pytest
from flask import Flask

from api import validation as v


@pytest.fixture
def app():
    app = Flask(__name__)
    v.install_validation_error_handler(app)
    return app


def _call(app, query, fn):
    with app.test_request_context("/?" + query):
        return fn()


# ── helpers ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "query,expected",
    [("", 7), ("limit=", 7), ("limit=5", 5), ("limit=%2B5", 5), ("limit=0", 1), ("limit=-4", 1),
     ("limit=999999999999999", 50), ("limit=50", 50), ("limit=+12", 12)],
)
def test_query_int_defaults_and_clamps(app, query, expected):
    assert _call(app, query, lambda: v.query_int("limit", 7, min=1, max=50)) == expected


@pytest.mark.parametrize("raw", ["abc", "1.5", "nan", "inf", "1e3", "0x10", "9" * 19, "%00", "1%2C2"])
def test_query_int_rejects_garbage(app, raw):
    with pytest.raises(v.RequestValidationError):
        _call(app, f"limit={raw}", lambda: v.query_int("limit", 7, min=1, max=50))


def test_query_float_rejects_non_finite_and_clamps(app):
    assert _call(app, "x=2.5", lambda: v.query_float("x", 1.0, min=0.0, max=2.0)) == 2.0
    assert _call(app, "x=-1e3", lambda: v.query_float("x", 1.0, min=0.0, max=2.0)) == 0.0
    for raw in ("nan", "inf", "-inf", "1e309", "text"):
        with pytest.raises(v.RequestValidationError):
            _call(app, f"x={raw}", lambda: v.query_float("x", 1.0, min=0.0, max=2.0))


@pytest.mark.parametrize("raw,expected", [("true", True), ("1", True), ("YES", True), ("off", False), ("0", False)])
def test_query_bool(app, raw, expected):
    assert _call(app, f"b={raw}", lambda: v.query_bool("b", False)) is expected


def test_query_bool_rejects_other_words(app):
    with pytest.raises(v.RequestValidationError):
        _call(app, "b=maybe", lambda: v.query_bool("b", False))


def test_query_symbol_timeframe_text_choice(app):
    assert _call(app, "symbol=ethusdt", lambda: v.query_symbol()) == "ETHUSDT"
    assert _call(app, "", lambda: v.query_symbol("symbol", None)) is None
    for bad in ("../../etc/passwd", "BTC-USDT", "A" * 30, "%E2%80%AE"):
        with pytest.raises(v.RequestValidationError):
            _call(app, f"symbol={bad}", lambda: v.query_symbol())
    assert _call(app, "timeframe=4h", lambda: v.query_timeframe()) == "4h"
    with pytest.raises(v.RequestValidationError):
        _call(app, "timeframe=4H", lambda: v.query_timeframe())
    assert _call(app, "status=OPEN", lambda: v.query_text("status")) == "OPEN"
    with pytest.raises(v.RequestValidationError):
        _call(app, "status=%3Cscript%3E", lambda: v.query_text("status"))
    with pytest.raises(v.RequestValidationError):
        _call(app, "q=" + "x" * 300, lambda: v.query_text("q", pattern=None))
    assert _call(app, "format=CSV", lambda: v.query_choice("format", "json", ("json", "csv"))) == "csv"
    with pytest.raises(v.RequestValidationError):
        _call(app, "format=xml", lambda: v.query_choice("format", "json", ("json", "csv")))


def test_repeated_parameter_uses_the_first_value(app):
    assert _call(app, "limit=3&limit=abc", lambda: v.query_int("limit", 7, min=1, max=50)) == 3


# ── routes ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    import dashboard

    return dashboard.app.test_client()


@pytest.fixture
def READ(monkeypatch):
    # api.auth (v1 blueprints) reads TRADING_BOT_API_KEY_READ at request time.
    key = "read-key-for-query-tests-0123456789abcdef"
    monkeypatch.setenv("TRADING_BOT_API_KEY_READ", key)
    return {"X-API-KEY": key}


@pytest.mark.parametrize("query", ["limit=0", "limit=abc", "limit=1.5", "limit=nan", "page=-3", "page=x", "limit=-1"])
def test_public_trades_never_500(client, query, READ):
    res = client.get(f"/api/v1/trades?{query}", headers=READ)
    assert res.status_code in (200, 400, 401, 403), res.get_data(as_text=True)[:200]


def test_public_trades_clamps_paging(client, tmp_path, monkeypatch, READ):
    import api.public_status as public_status

    monkeypatch.setattr(public_status, "PROJECT_ROOT", tmp_path)
    with open(tmp_path / "paper_trade_ledger.jsonl", "w", encoding="utf-8") as handle:
        for index in range(250):
            handle.write(json.dumps({"trade_id": index, "status": "CLOSED"}) + "\n")
    res = client.get("/api/v1/trades?limit=100000&page=0", headers=READ)
    body = res.get_json()
    assert res.status_code == 200
    assert body["pagination"]["limit"] == 100 and body["pagination"]["page"] == 1
    assert len(body["data"]) == 100


@pytest.mark.parametrize("query", ["limit=-1", "limit=abc", "limit=99999999999"])
def test_reports_alerts_bounded(client, query, READ):
    res = client.get(f"/api/v1/reports/alerts?{query}", headers=READ)
    assert res.status_code in (200, 400, 401, 403)


@pytest.mark.parametrize("date_str", ["2026-13-01", "not-a-date", "2026-1-1", "%3Cb%3E2026", "..", "2026-02-30"])
def test_daily_report_date_must_be_a_calendar_date(client, date_str, tmp_path, monkeypatch, READ):
    monkeypatch.chdir(tmp_path)
    res = client.get(f"/api/v1/reports/daily/{date_str}", headers=READ)
    assert res.status_code in (400, 401, 403, 404)
    assert not any(name.startswith("report_") for _, _, files in os.walk(tmp_path) for name in files)


def test_daily_report_generator_refuses_bad_dates():
    from reporting.daily_report import DailyReportGenerator

    generator = DailyReportGenerator.__new__(DailyReportGenerator)
    for bad in ("../../x", "2026-1-1", "2026-02-30", 20260101):
        with pytest.raises((ValueError, TypeError)):
            generator.generate_daily_report(date_str=bad)


def test_export_pages_skips_torn_lines_and_neutralizes_formulas(client, tmp_path, monkeypatch, READ):
    monkeypatch.chdir(tmp_path)
    with open("advisory_log.jsonl", "w", encoding="utf-8") as handle:
        for index in range(30):
            handle.write(json.dumps({"id": index, "note": "=HYPERLINK(\"http://x\")" if index == 0 else "ok"}) + "\n")
        handle.write('{"id": 30, "extra": 1}\n')
        handle.write('{"id": 31, "no')  # torn tail
    res = client.get("/api/v1/export/advisory-log?limit=10&offset=25", headers=READ)
    body = res.get_json()
    assert body["total"] == 31 and body["count"] == 6 and body["skipped_lines"] == 1
    assert body["truncated"] is False
    csv_res = client.get("/api/v1/export/advisory-log?format=csv&limit=40", headers=READ)
    text = csv_res.get_data(as_text=True)
    assert csv_res.status_code == 200 and "extra" in text.splitlines()[0]
    assert "'=HYPERLINK" in text
    assert csv_res.headers["X-Export-Total"] == "31"
    assert client.get("/api/v1/export/advisory-log?format=xml", headers=READ).status_code == 400


@pytest.mark.parametrize("query", ["symbol=../../x", "timeframe=7x", "limit=nan"])
def test_openbb_metrics_reject_bad_query(client, query, READ):
    res = client.get(f"/api/v1/openbb/quantitative/metrics?{query}", headers=READ)
    assert res.status_code == 400


def test_openbb_refresh_flag_is_strict(client, READ):
    assert client.get("/api/v1/openbb/crypto/global?refresh=maybe", headers=READ).status_code == 400


def test_candles_symbol_is_validated(client, READ):
    res = client.get("/api/candles?symbol=..%2F..%2Fetc&tf=15m")
    assert res.status_code in (400, 401, 403)


# ── bounded series / lists (found by the census --stress-records run) ───────

def test_downsample_series_keeps_real_points_first_and_last():
    from dashboard import downsample_series

    points = list(range(10_001))
    thinned, downsampled = downsample_series(points, 100)
    assert downsampled and len(thinned) <= 101
    assert thinned[0] == 0 and thinned[-1] == 10_000
    assert set(thinned) <= set(points) and thinned == sorted(thinned)
    assert downsample_series(points[:50], 100) == (points[:50], False)
    assert downsample_series(points, 1) == ([10_000], True)


def test_equity_endpoints_are_bounded(client, tmp_path, monkeypatch):
    import testnet_engine.telemetry_manager as tmm

    history = tmp_path / "equity.jsonl"
    with open(history, "w", encoding="utf-8") as handle:
        for index in range(5_000):
            handle.write(json.dumps({"timestamp": f"2026-01-01T00:{index // 60 % 60:02d}:{index % 60:02d}+00:00",
                                     "total_equity": 10_000 + index, "cash_usdt": 10_000}) + "\n")
    manager = tmm.TelemetryManager(base_dir=str(tmp_path))
    manager.equity_history_file = str(history)
    monkeypatch.setattr(tmm, "get_telemetry_manager", lambda: manager)

    res = client.get("/api/equity?max_points=300")
    assert res.status_code == 200
    assert len(res.get_json()) <= 301
    assert res.headers["X-Series-Downsampled"] == "true"
    assert int(res.headers["X-Series-Total-Points"]) == 5_000

    body = client.get("/api/equity-history?max_points=200").get_json()
    assert body["source_count"] == 5_000 and body["downsampled"] is True
    assert len(body["snapshots"]) <= 201


def test_public_equity_history_is_bounded(client, tmp_path, monkeypatch, READ):
    import api.public_status as public_status

    monkeypatch.setattr(public_status, "PROJECT_ROOT", tmp_path)
    with open(tmp_path / "paper_equity_curve.jsonl", "w", encoding="utf-8") as handle:
        for index in range(3_000):
            handle.write(json.dumps({"timestamp": index, "equity": 1000 + index}) + "\n")
    body = client.get("/api/v1/history/equity?max_points=100", headers=READ).get_json()
    assert body["series"] == {"source_count": 3_000, "returned": 100, "downsampled": True}
    assert body["data"][0]["timestamp"] == 0 and body["data"][-1]["timestamp"] == 2_999
