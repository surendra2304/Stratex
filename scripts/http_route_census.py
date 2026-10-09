#!/usr/bin/env python3
"""HTTP route census — exercise every registered dashboard route over real HTTP.

Unit tests call handlers through Flask's test client with well-formed input.
This harness instead boots the real dashboard (threaded werkzeug server) on a
scratch copy of the repository and sends every GET and POST route a battery of
requests — anonymous and authenticated, empty / non-object / invalid /
wrong-typed / oversized bodies, per-field body mutations, path-traversal path
parameters, and per-parameter query-string mutations (wrong types, negative /
huge / non-finite numbers, traversal, oversized, repeated parameters) — and
reports every response class. Any 5xx other than an honest ``503``
(dependency unavailable), any transport error, any timeout and any response
body larger than ``--max-response-mb`` (an unbounded ``limit``) fails the run.

The scratch copy keeps the census from touching the checkout's state files
(panic flag, ledgers, registries); the server has no live-trading credentials
and LIVE trading stays blocked by configuration, so POSTing to emergency and
order endpoints is safe. Exchange calls fail without network access and must
surface as 503 — never as fabricated data or a 500.

Usage::

    python scripts/http_route_census.py                # full census, exit 1 on failure
    python scripts/http_route_census.py --methods GET  # GET routes only
    python scripts/http_route_census.py --json report.json --keep-scratch

The report (``--json``) lists every request with its status, latency and, for
failures, the first bytes of the body.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
ADMIN_KEY = "census-admin-key-0123456789abcdef0123456789"
READ_KEY = "census-read-key-0123456789abcdef0123456789ab"

_IGNORE_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules", "venv", ".venv"}

# Body variants sent to every POST route.
WRONG_TYPES_BODY = {
    "symbol": 123, "symbols": "BTCUSDT", "strategy_id": ["adx_ema"], "strategy": 7,
    "amount": "lots", "quantity": {"a": 1}, "qty": [1], "size": -1, "price": "NaN",
    "side": 7, "enabled": "yes", "confirm": "true", "release": "false", "reason": ["x"],
    "job_id": {"x": 1}, "job_type": 42, "metadata": "str", "params": "str", "parameters": [1],
    "candles": "many", "limit": "-5", "timeframe": 5, "exchange": ["binance"], "mode": 1,
    "capital": "infinite", "leverage": "1e309", "days": "seven", "trials": -3, "task": 5,
}

# Per-parameter query-string mutations sent (one parameter at a time) to every
# GET route, using parameter names extracted from the view function's source
# plus the paging names every list endpoint is expected to bound.
QUERY_FUZZ_VALUES: list[tuple[str, str]] = [
    ("negative", "-1"), ("zero", "0"), ("huge", "999999999999999999999"), ("float", "1.5"),
    ("nan", "nan"), ("inf", "inf"), ("exp_overflow", "1e309"), ("text", "census"), ("empty", ""),
    ("traversal", "../../../../etc/passwd"), ("long", "A" * 8192), ("unicode", "\u202e\U0001f4a5\u0000"),
    ("sql", "' OR 1=1 --"), ("script", "<script>alert(1)</script>"),
]
QUERY_FALLBACK_PARAMS = ("limit", "offset", "days", "n")

# Per-field mutations sent (one field at a time) to every POST route, using
# field names extracted from the view function's source.
FIELD_FUZZ_VALUES: list[tuple[str, Any]] = [
    ("null", None), ("true", True), ("zero", 0), ("negative", -1), ("huge", 1e308),
    ("nan_str", "NaN"), ("inf_str", "Infinity"), ("text", "census"), ("empty_str", ""),
    ("list", [1, "a", None]), ("object", {"nested": {"deep": [1]}}), ("list_of_objects", [{"x": 1}, {}]),
    ("long_str", "x" * 5000),
]

SERVER_BOOTSTRAP = r'''
import json, os, sys
sys.path.insert(0, os.getcwd())
os.environ.setdefault("STRATEX_CENSUS", "1")
import dashboard
import security_hardening as sh

# Census traffic deliberately includes hundreds of unauthenticated requests from
# one address; without this, the abuse block and rate limiters would mask the
# authenticated phase with 401/429s instead of measuring handlers.
sh._security_monitor.is_ip_blocked = lambda ip: False
for _name in ("SecurityRateLimiter",):
    _cls = getattr(sh, _name, None)
    if _cls is not None and hasattr(_cls, "is_allowed"):
        _cls.is_allowed = lambda self, *a, **k: (True, 999, 0)
try:
    import api.auth as _auth
    for _lim in (getattr(_auth, "api_rate_limiter", None), getattr(_auth, "control_rate_limiter", None)):
        if _lim is not None:
            for _meth in ("is_allowed", "check"):
                if hasattr(_lim, _meth):
                    setattr(_lim, _meth, lambda *a, **k: (True, 999, 0))
except Exception:
    pass

import inspect, re
_FIELD_RE = re.compile(r"""(?:\.get\(|get_[a-z_]+\(\s*[A-Za-z_]+\s*,|\[)\s*["']([A-Za-z_][A-Za-z0-9_]{0,40})["']""")
_NOT_BODY = {"X-API-KEY", "Content-Type", "Authorization"}
_QUERY_RE = re.compile(r"""(?:request\.args\s*(?:\.get(?:list)?\(|\[)|get_[a-z_]+\(\s*request\.args\s*,)\s*["']([A-Za-z_][A-Za-z0-9_]{0,40})["']""")


def _query_params(view):
    try:
        source = inspect.getsource(inspect.unwrap(view))
    except (OSError, TypeError):
        return []
    return sorted(set(_QUERY_RE.findall(source)))


def _body_fields(view):
    try:
        source = inspect.getsource(inspect.unwrap(view))
    except (OSError, TypeError):
        return []
    return sorted({m for m in _FIELD_RE.findall(source) if m not in _NOT_BODY})


app = dashboard.app
rules = []
for rule in app.url_map.iter_rules():
    methods = sorted(m for m in rule.methods if m not in ("HEAD", "OPTIONS"))
    view = app.view_functions.get(rule.endpoint)
    rules.append({
        "rule": rule.rule,
        "endpoint": rule.endpoint,
        "methods": methods,
        "arguments": sorted(rule.arguments),
        "converters": {k: type(v).__name__ for k, v in rule._converters.items()},
        "body_fields": _body_fields(view) if view is not None and "POST" in methods else [],
        "query_params": _query_params(view) if view is not None and "GET" in methods else [],
    })
with open(os.environ["CENSUS_RULES_OUT"], "w") as fh:
    json.dump(rules, fh, indent=1)

from werkzeug.serving import make_server
server = make_server("127.0.0.1", int(os.environ["CENSUS_PORT"]), app, threaded=True)
print("CENSUS_SERVER_READY", flush=True)
server.serve_forever()
'''


@dataclass
class Probe:
    phase: str
    method: str
    rule: str
    url: str
    variant: str
    status: int | None = None
    elapsed_ms: float = 0.0
    content_type: str = ""
    error: str | None = None
    body_excerpt: str = ""
    response_bytes: int = 0


@dataclass
class CensusReport:
    rules_total: int = 0
    get_rules: int = 0
    post_rules: int = 0
    probes: list[Probe] = field(default_factory=list)
    failures: list[Probe] = field(default_factory=list)
    status_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    server_log_tail: str = ""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _copy_scratch(dest: Path) -> None:
    def ignore(directory: str, names: list[str]) -> set[str]:
        skipped = {n for n in names if n in _IGNORE_DIRS}
        skipped |= {n for n in names if n.endswith((".log", ".pyc"))}
        return skipped

    shutil.copytree(REPO_ROOT, dest, ignore=ignore, dirs_exist_ok=True)


# Append-only datasets the dashboard reads by default (relative to its CWD).
STRESS_JSONL_FILES = (
    "testnet_trade_ledger.jsonl", "paper_trade_ledger.jsonl", "trade_ledger.jsonl", "advisory_log.jsonl",
    "production_alerts.jsonl", "live_equity_curve.jsonl", "testnet_equity_history.jsonl",
    "testnet_signals_log.jsonl", "testnet_trade_events.jsonl", "testnet_opportunity_log.jsonl",
    "testnet_execution_events.jsonl", "testnet_position_history.jsonl", "paper_equity_curve.jsonl",
    "risk_orchestration_log.jsonl", "control_audit.jsonl", "signals.jsonl",
)


def _seed_stress_data(app_dir: Path, records: int) -> None:
    """Fill the scratch copy's ledgers with ``records`` synthetic rows each.

    Scratch-only test fixtures (flagged ``census_synthetic``) used to prove
    that list/export endpoints bound their responses; they never touch the
    checkout.
    """
    stamp = "2026-01-01T00:00:00+00:00"
    for name in STRESS_JSONL_FILES:
        with open(app_dir / name, "w", encoding="utf-8") as handle:
            for index in range(records):
                handle.write(json.dumps({
                    "census_synthetic": True, "trade_id": f"syn-{index}", "signal_id": f"syn-{index}",
                    "symbol": "BTCUSDT", "strategy": "adx_ema", "side": "BUY", "status": "CLOSED",
                    "source": "BINANCE_EXECUTION", "entry_order_id": index, "exit_order_id": 10**9 + index,
                    "entry_price": 100.0, "exit_price": 101.0, "quantity": 0.01, "net_pnl": 0.01, "pnl": 0.01,
                    "equity": 10000.0, "balance": 10000.0, "timestamp": stamp, "entry_timestamp": stamp,
                    "exit_timestamp": stamp, "closed_at": stamp, "pad": "x" * 120,
                }) + "\n")


def _server_env(scratch: Path, port: int, rules_out: Path) -> dict[str, str]:
    env = dict(os.environ)
    for var in list(env):
        if var.endswith(("_API_KEY", "_API_SECRET", "_SECRET")) or var.startswith(("BINANCE_", "BYBIT_", "OKX_")):
            env.pop(var)
    env.update({
        "CENSUS_PORT": str(port),
        "CENSUS_RULES_OUT": str(rules_out),
        "BOT_API_KEY": ADMIN_KEY,
        "API_KEY_READONLY": READ_KEY,
        "TRADING_BOT_API_KEY_CONTROL": ADMIN_KEY,
        "TRADING_BOT_API_KEY_READ": READ_KEY,
        "LIVE_TRADING_ENABLED": "false",
        "PANIC_STATE_FILE": str(scratch / "panic_state.json"),
        "KILL_SWITCH_LOCK_FILE": str(scratch / "KILL_SWITCH_ACTIVE.lock"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
    })
    return env


def _sample_value(name: str, converter: str) -> str:
    lname = name.lower()
    if converter in ("IntegerConverter",):
        return "1"
    if converter in ("FloatConverter",):
        return "1.0"
    if converter == "PathConverter":
        return "..%2F..%2Fetc%2Fpasswd"
    if "symbol" in lname:
        return "BTCUSDT"
    if "exchange" in lname:
        return "binance"
    if "strategy" in lname:
        return "adx_ema"
    if "date" in lname:
        return "2026-01-01"
    if "file" in lname or "name" in lname or "path" in lname:
        return "..%2F..%2Fetc%2Fpasswd"
    return "census-nonexistent"


def _build_url(base: str, rule: dict[str, Any]) -> str:
    path = rule["rule"]
    for arg in rule["arguments"]:
        value = _sample_value(arg, rule["converters"].get(arg, ""))
        path = re.sub(r"<(?:[^:<>]+:)?" + re.escape(arg) + r">", value, path)
    return base + path


def _send(probe: Probe, data: bytes | None, headers: dict[str, str], timeout: float) -> Probe:
    request = urllib.request.Request(probe.url, data=data, method=probe.method, headers=headers)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            probe.status = response.status
            probe.content_type = response.headers.get("Content-Type", "")
            if probe.content_type.startswith("text/event-stream"):
                response.readline()  # first event only; streams never end
            else:
                # Count the whole body (up to the cap + 1 chunk) so an
                # unbounded "limit" shows up as an oversized response.
                while probe.response_bytes <= _MAX_RESPONSE_BYTES:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    probe.response_bytes += len(chunk)
    except urllib.error.HTTPError as exc:
        probe.status = exc.code
        probe.content_type = exc.headers.get("Content-Type", "") if exc.headers else ""
        try:
            body = exc.read(4096)
        except Exception:  # noqa: BLE001 - body is diagnostic only
            body = b""
        if exc.code >= 500:
            probe.body_excerpt = body[:600].decode("utf-8", "replace")
    except (TimeoutError, socket.timeout):
        probe.error = f"TIMEOUT after {timeout:.0f}s"
    except (urllib.error.URLError, ConnectionError, OSError) as exc:
        probe.error = f"TRANSPORT: {exc}"
    probe.elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    return probe


def _post_variants(include_oversized: bool) -> list[tuple[str, bytes | None, str | None]]:
    variants: list[tuple[str, bytes | None, str | None]] = [
        ("empty", None, None),
        ("object", b"{}", "application/json"),
        ("array", b"[1, 2, 3]", "application/json"),
        ("invalid_json", b"{bad json", "application/json"),
        ("wrong_types", json.dumps(WRONG_TYPES_BODY).encode(), "application/json"),
        ("form_encoded", b"a=1&b=2", "application/x-www-form-urlencoded"),
    ]
    if include_oversized:
        variants.append(("oversized", json.dumps({"blob": "x" * (1536 * 1024)}).encode(), "application/json"))
    return variants


_MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def _is_failure(probe: Probe, allow_timeouts: bool) -> bool:
    if probe.error:
        return not (allow_timeouts and probe.error.startswith("TIMEOUT"))
    if probe.response_bytes > _MAX_RESPONSE_BYTES:
        probe.error = f"UNBOUNDED_RESPONSE: more than {_MAX_RESPONSE_BYTES // (1024 * 1024)} MiB"
        return True
    return probe.status is not None and probe.status >= 500 and probe.status != 503


def _query_variants(rule: dict[str, Any]) -> list[tuple[str, str]]:
    """(variant label, query string) pairs: one mutated parameter per request."""
    names = list(dict.fromkeys([*rule.get("query_params", []), *QUERY_FALLBACK_PARAMS]))
    variants: list[tuple[str, str]] = []
    for name in names:
        for label, value in QUERY_FUZZ_VALUES:
            variants.append((f"query:{name}={label}", urllib.parse.urlencode({name: value})))
        variants.append((f"query:{name}=repeated", urllib.parse.urlencode([(name, "1"), (name, "x")])))
    variants.append(("query:unknown_param", "census_unknown=" + "z" * 64))
    return variants


def run_census(args: argparse.Namespace) -> CensusReport:
    scratch = Path(tempfile.mkdtemp(prefix="stratex-census-"))
    app_dir = scratch / "app"
    _copy_scratch(app_dir)
    port = args.port or _free_port()
    rules_out = scratch / "rules.json"
    (app_dir / "_census_server.py").write_text(SERVER_BOOTSTRAP)
    if args.stress_records:
        _seed_stress_data(app_dir, args.stress_records)
    log_path = scratch / "server.log"
    report = CensusReport()
    with open(log_path, "w") as log_handle:
        server = subprocess.Popen(
            [sys.executable, "_census_server.py"], cwd=app_dir, env=_server_env(app_dir, port, rules_out),
            stdout=log_handle, stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + args.startup_timeout
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    raise RuntimeError(f"census server exited with {server.returncode}; see {log_path}")
                if "CENSUS_SERVER_READY" in log_path.read_text(errors="replace"):
                    break
                time.sleep(0.2)
            else:
                raise RuntimeError(f"census server did not start within {args.startup_timeout}s")
            rules = json.loads(rules_out.read_text())
            _exercise(rules, f"http://127.0.0.1:{port}", args, report)
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
    report.server_log_tail = "\n".join(log_path.read_text(errors="replace").splitlines()[-40:])
    if args.keep_scratch:
        print(f"scratch kept at {scratch}")
    else:
        shutil.rmtree(scratch, ignore_errors=True)
    return report


def _exercise(rules: list[dict[str, Any]], base: str, args: argparse.Namespace, report: CensusReport) -> None:
    wanted = {m.strip().upper() for m in args.methods.split(",") if m.strip()}
    phases = [p.strip() for p in args.phases.split(",") if p.strip()]
    report.rules_total = len(rules)
    report.get_rules = sum("GET" in r["methods"] for r in rules)
    report.post_rules = sum("POST" in r["methods"] for r in rules)
    skip = re.compile(args.skip) if args.skip else None

    jobs: list[tuple[Probe, bytes | None, dict[str, str]]] = []
    for phase in phases:
        auth = {"X-API-KEY": ADMIN_KEY} if phase == "admin" else {}
        for rule in rules:
            if rule["rule"].startswith("/static") or (skip and skip.search(rule["rule"])):
                continue
            url = _build_url(base, rule)
            if "GET" in wanted and "GET" in rule["methods"]:
                jobs.append((Probe(phase, "GET", rule["rule"], url, "plain"), None, dict(auth)))
                if phase == "admin" and args.query_fuzz and not rule["rule"].endswith("/stream"):
                    for label, query in _query_variants(rule):
                        jobs.append((Probe(phase, "GET", rule["rule"], f"{url}?{query}", label), None, dict(auth)))
                if rule["arguments"]:
                    continue
            if "POST" in wanted and "POST" in rule["methods"]:
                for variant, body, ctype in _post_variants(include_oversized=phase == "admin"):
                    headers = dict(auth)
                    if ctype:
                        headers["Content-Type"] = ctype
                    jobs.append((Probe(phase, "POST", rule["rule"], url, variant), body, headers))
                if phase == "admin" and args.field_fuzz:
                    for name in rule.get("body_fields", []):
                        for label, bad in FIELD_FUZZ_VALUES:
                            headers = dict(auth, **{"Content-Type": "application/json"})
                            payload = json.dumps({name: bad}).encode()
                            jobs.append((Probe(phase, "POST", rule["rule"], url, f"field:{name}={label}"),
                                         payload, headers))

    # GETs in parallel (read-only), POSTs serially in rule order so a request's
    # side effects (panic engaged, job created) are deterministic run-to-run.
    gets = [j for j in jobs if j[0].method == "GET"]
    posts = [j for j in jobs if j[0].method != "GET"]
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        report.probes.extend(pool.map(lambda j: _send(j[0], j[1], j[2], args.timeout), gets))
    for probe, body, headers in posts:
        report.probes.append(_send(probe, body, headers, args.timeout))

    for probe in report.probes:
        key = f"{probe.phase}:{probe.method}"
        bucket = report.status_counts.setdefault(key, {})
        label = probe.error.split(":")[0] if probe.error else str(probe.status)
        bucket[label] = bucket.get(label, 0) + 1
        if _is_failure(probe, args.allow_timeouts):
            report.failures.append(probe)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--methods", default="GET,POST")
    parser.add_argument("--phases", default="anonymous,admin")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=25.0, help="per-request timeout (s)")
    parser.add_argument("--startup-timeout", type=float, default=120.0)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--skip", default="", help="regex of rules to skip")
    parser.add_argument("--allow-timeouts", action="store_true", help="do not fail on request timeouts")
    parser.add_argument("--no-field-fuzz", dest="field_fuzz", action="store_false",
                        help="skip per-field type mutations on POST routes")
    parser.add_argument("--no-query-fuzz", dest="query_fuzz", action="store_false",
                        help="skip per-parameter query-string mutations on GET routes")
    parser.add_argument("--stress-records", type=int, default=0,
                        help="seed this many synthetic rows into each scratch ledger before probing")
    parser.add_argument("--max-response-mb", type=float, default=8.0,
                        help="fail any response body larger than this (unbounded limits)")
    parser.add_argument("--json", dest="json_out", default="", help="write the full report here")
    parser.add_argument("--keep-scratch", action="store_true")
    args = parser.parse_args(argv)
    global _MAX_RESPONSE_BYTES
    _MAX_RESPONSE_BYTES = int(args.max_response_mb * 1024 * 1024)

    report = run_census(args)
    print(f"rules: {report.rules_total} ({report.get_rules} GET, {report.post_rules} POST); "
          f"probes: {len(report.probes)}")
    for key in sorted(report.status_counts):
        counts = Counter(report.status_counts[key])
        print(f"  {key:<16} " + ", ".join(f"{k}x{v}" for k, v in sorted(counts.items())))
    if report.failures:
        print(f"FAILURES: {len(report.failures)}")
        for probe in report.failures[:25]:
            detail = probe.error or probe.body_excerpt.replace("\n", " ")[:160]
            print(f"  {probe.phase} {probe.method} {probe.rule} [{probe.variant}] -> {probe.status} {detail}")
    else:
        print("FAILURES: 0")
    if args.json_out:
        payload = asdict(report)
        Path(args.json_out).write_text(json.dumps(payload, indent=1))
    return 1 if report.failures else 0


if __name__ == "__main__":
    sys.exit(main())
