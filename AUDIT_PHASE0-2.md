# STRATEX — Phase 0/1/2 Audit Report

Branch: `arena/01a10cbe-stratex` @ `cddc8ba`
Date: 2026-10-05
Every claim below is backed by a command actually run in this sandbox. Output is quoted verbatim.

---

## PHASE 0 — TOTAL COMPREHENSION

### (a) What this project IS, and its dream state

STRATEX is a **quantitative trading research + forward-validation platform**, not a money printer. Its
own documents are unusually honest about this:

- `README.md:29` — *"Live real-money trading is strictly BLOCKED. Passing software tests proves
  implementation properties only; it does **not** prove an economic edge."*
- `PROJECT_HANDOFF.md:19` — *"NOT a guaranteed profitable money-making system... **insufficient
  evidence of statistically significant out-of-sample edge** to warrant live capital allocation."*

**Inferred dream state** (the `[DESCRIBE YOUR VISION]` placeholder was left unfilled, so I inferred it
from the docs, as instructed):

> A truthful, reproducible, safety-gated trading laboratory that runs two isolated tracks
> (pure-simulation Paper and real-order Binance Testnet), refuses to promote any strategy to
> executable status without a *stored, reproducible out-of-sample artifact*, keeps AI/quantum
> strictly advisory with zero execution authority, leaks zero credentials, and reports only what it
> actually measured — never what it hopes is true.

The codebase is organised around a **pre-registered promotion gate**: `strategy_registry.json`
records `adx_ema` at `OOS_PROFIT_FACTOR: 0.8236`, `OOS_TRADE_COUNT: 2`, `evidence_grade: "GRADE D
(under 30 trades)"`, `status: RESEARCH`. The gate wants ≥30 OOS trades, PF ≥ 1.0, 0 failed
walk-forward windows. Nothing has cleared it. That is the intended state, and it is being respected.

### (b) Execution flow, naming real code paths

**Track 2 — Testnet (entry: `bot.py`)**

1. `bot.py:main()` → `acquire_singleton_lock(port=48888)` (binds a local socket so two daemons can't
   both submit orders) → `testnet_engine.service.TestnetService()`.
2. `TestnetService.__init__` (`service.py:103`) hard-refuses to start unless
   `TRADING_MODE in ["TESTNET","FUTURES"]` (`service.py:106`), and requires `TESTNET_ONLY` or
   `TESTNET_ENABLED` (`service.py:109`). It spawns `self._execution_thread` (`service.py:357,361`)
   and `self._target_monitor_thread` (`service.py:359`).
3. `TestnetService.run()` (`service.py:2954`):
   - `reconcile_state()` (`service.py:2790`) repairs naked positions/orphan orders first.
   - `SymbolDiscoveryService.discover_eligible_symbols(min_quote_volume=1_000_000)` (`service.py:2965`).
   - `governance_validated_assets()` (`service.py:63`) restricts the universe to registry-VALIDATED
     assets. Since none are VALIDATED, this is currently empty.
   - Starts `MarketScanner` (`service.py:3031`), `position_monitor_loop`, `_trailing_loop`
     (`testnet_engine/trailing.py:trailing_cycle`), `_heartbeat_loop`, `_progress_report_loop`, and
     optionally `start_testnet_advisory_if_enabled()`.
4. **Signal funnel** — `TestnetService.on_candle_closed()` (`service.py:860`), in strict order:
   `safety_halt` → `data_health_status != "OK"` → `len(df) < 20` → candle-age vs `_TF_SECONDS` →
   `data.add_indicators()` → `governance_filter_strategies()` (`service.py:33`, drops anything not
   registry-`VALIDATED`) → `is_strategy_demoted()` (`service.py:2666`) → `strat_mod.get_signal(df)` →
   BTC macro regime (`compute_btc_regime`, `service.py:73`) → Qanat decay smoother →
   `testnet_engine/signal_quality.py:evaluate_signal_quality` → `ProfitabilityGate.evaluate_signal`
   (`testnet_engine/profitability_gate.py:41`) → IntelX gate → Futuris gate → `opportunity_pool.put()`.
5. **Ranking + execution** — `TestnetService.execution_loop()` (`service.py:1207`): scores candidates
   (`base_score = exp_net * conf / max(0.001, risk_pct)`, boosted by ADX/RR/IntelX/Futuris
   multipliers), sorts, then re-validates at the latest price and runs cooldown →
   duplicate-position guard → `ProfitabilityGate` again → `ProtectionManager` →
   `RiskGate.calculate_position_size` → `RiskGate.evaluate_risk` → `observe_only` check →
   `ExecutionIntent` + `IdempotencyGuard` → `execution.place_market_order`.
6. **Hard block** — `execution.ExecutionPolicy.can_place_order()` (`execution.py:103`) and
   `execution.get_exchange_client()` (`execution.py:125`). `config.LIVE_TRADING_ENABLED = False` is a
   literal, not env-driven (`config.py:87`), and `config.validate_environment_safety()`
   (`config.py:222`) raises `SecurityConfigurationError` if any env var matching
   `LIVE_TRADING|ENABLE_LIVE|REAL_MONEY|...` is truthy or if a production Binance URL appears.

**Track 1 — Paper (entry: `paper_forward_runner.py`)**
`fetch_candles()` (`paper_forward_runner.py:304`) → `filter_closed_candles()` (rejects the forming
bar so intrabar flicker can't be counted as independent observations) → `FrozenExperimentConfig`
(`paper_engine/experiment_config.py`, git-SHA stamped) → `research_phase9.cost_engine.CostEngine` →
`paper_engine/portfolio.py` → `paper_engine/reconciliation.py` → `paper_engine/statistical_report.py`.
`paper_engine/kill_switch.py` applies the same `CostEngine` on forced exits so kill-switch slippage
isn't free.

**Dashboard (`dashboard.py`, 6,115 lines)** — Flask app; registers `quantum_endpoint.quantum_bp` plus
12 blueprints via `importlib` (`dashboard.py:33-58`), each wrapped in try/except so a broken adapter
degrades instead of taking down the app.

### (c) The 5 most important files

| File | Why |
|---|---|
| `config.py` (17.3 KB) | Single source of truth for `TRADING_MODE`, every risk limit, and the permanent `LIVE_TRADING_ENABLED = False` invariant. `validate_config()` runs **at import time** (`config.py:281`) so a bad env can't even boot. Also the site of 3 silent duplicate-definition bugs (see HIGH-5). |
| `testnet_engine/service.py` (3,082 lines) | The whole Track-2 loop: `on_candle_closed` funnel, `execution_loop` ranking, `reconcile_state`, `_check_degradation`, heartbeats. Every ordering decision about *whether a signal may become an order* lives here. |
| `execution.py` (1,010 lines) | `ExecutionPolicy`, `OrderState` enum (`SIGNAL→…→PROTECTED→CLOSED`), OCO placement, atomic state I/O, `_validate_trade_schema` raising `StateCorruptionError`. The last line of defence before an order leaves the building. |
| `testnet_engine/profitability_gate.py` | The economic heart. Correctly refuses to invent a probability: `RULE_BASED` signals with a missing/invalid `win_rate_prior` are **rejected**, not defaulted (`UNVERIFIED_RULE_BASED_PRIOR`). `E[net] = P(win)·reward − P(loss)·risk − friction`. This file is the reason unpromoted strategies can't trade. |
| `config_strategy.py` (397 lines) | `PRODUCTION_STRATEGY_REGISTRY` — the governance ledger. Lines 392-397 scrub `oos_win_rate_prior`, `expected_net_edge_bps` and `validated_assets` to `None`/`[]` for anything not `VALIDATED`, so stale research numbers can't leak into live signals. Genuinely good design. |

### (d) What surprised me

1. **The honesty is real, and it's enforced by tests.** `config_strategy.py:392-397` nulls out
   unproven priors; `tests/test_oos_claims_are_not_runtime_priors.py`,
   `tests/test_validation_claims_are_reproducible.py` and `tests/test_engine_capability_truthfulness.py`
   exist specifically to stop fabricated performance claims reaching runtime. `strategy_registry.json`'s
   `_provenance.correction_reason` openly documents that a previous `PF 1.26 / expectancy +30.8` claim
   was withdrawn because *"No artifact contained those figures."* I have not seen many repos do this.
2. **`dashboard.py:739-763` contains the comment** *"Liveness is not capability. A live process that
   loaded zero strategies and has not evaluated a candle in 38 minutes is answering 'ONLINE' while
   being economically inert."* — and then separates `healthy` from `trading_capable` with
   `capability_reasons`. Proven live: `/api/ready` → **503** with
   `["NO_EXECUTABLE_STRATEGY: governance gate loaded zero validated strategies",
   "NO_STRATEGY_EVALUATION: engine has never evaluated a strategy"]`. The engine correctly reports
   itself inert rather than faking readiness.
3. **...and yet the same repo contains a function literally named `mock_backtest_runner`** that writes
   `profit_factor: 1.42, win_rate: 58.3` into a durable store as `COMPLETED`, served on a public
   unauthenticated endpoint (`dashboard.py:342`). The project's central discipline is contradicted by
   its own API. That tension is the theme of this audit.
4. **There are two auth systems with opposite failure modes.** `api/auth.py` fails **closed**
   (proved: `/api/v1/health/detailed` → 401 with no key). `security_hardening.py` fails **open** for
   CONTROL scope (proved: `/api/panic` → 200 with no key). Same app, same port.
5. **`execution.py` and `execution/` both exist.** The package wins, and its `__init__.py` `exec()`s
   the module's source into its own globals to fake equivalence. It works — until it doesn't, and
   then it fails silently (proved below).
6. **Scale vs. surface:** 199 registered URL rules; the shipped SPA calls **8** of them.

---

## PHASE 1 — TRUTH AUDIT

### 1.1 Fresh install, lint, typecheck, tests

System Python is PEP-668 externally managed, so I created `.venv` (gitignored) and installed
`requirements.txt` plus `pytest-asyncio ruff mypy`.

Installed versions (note the drift): **pandas 3.0.6**, **numpy 2.4.6**, **pytest 9.1.1**,
scikit-learn 1.9.1, scipy 1.17.1, xgboost 3.2.0, flask 3.1.3, ccxt 4.5.85, python-binance 1.0.37.
`requirements.txt` pins only `python-binance==1.0.37` and uses `>=` for everything else — so a fresh
install today resolves to pandas 3.x. That is the direct cause of two failures below.

```
$ .venv/bin/ruff check .
All checks passed!
```

```
$ .venv/bin/python -m mypy .
config_strategy.py:395: error: Unsupported target for indexed assignment ("object")  [index]
config_strategy.py:396: error: Unsupported target for indexed assignment ("object")  [index]
config_strategy.py:397: error: Unsupported target for indexed assignment ("object")  [index]
stratex_backtrader_adapter/cerebro.py:35: error: Function "builtins.any" is not valid as a type  [valid-type]
stratex_backtrader_adapter/cerebro.py:37: error: Function "builtins.any" is not valid as a type  [valid-type]
stratex_backtrader_adapter/cerebro.py:38: error: Function "builtins.any" is not valid as a type  [valid-type]
stratex_backtrader_adapter/cerebro.py:175: error: Function "builtins.any" is not valid as a type  [valid-type]
stratex_backtrader_adapter/client.py:83: error: Function "builtins.any" is not valid as a type  [valid-type]
stratex_backtrader_adapter/models.py:95: error: Function "builtins.any" is not valid as a type  [valid-type]
stratex_ccxt_adapter/arbitrage.py:59: error: Incompatible return value type (got "float | None", expected "SupportsDunderLT[Any] | SupportsDunderGT[Any]")  [return-value]
stratex_ccxt_adapter/arbitrage.py:63: error: Incompatible return value type (got "float | None", expected "SupportsDunderLT[Any] | SupportsDunderGT[Any]")  [return-value]
Found 11 errors in 5 files (checked 496 source files)
```

```
$ .venv/bin/python -m pytest -q
12 failed, 1027 passed, 2 skipped, 4 errors in 79.20s (0:01:19)
```

Collection is clean — **1045 tests collected in 5.62s**, zero import errors.

**All 16 non-passing items, verbatim:**

| # | Test | Verbatim failure |
|---|---|---|
| 1 | `tests/test_forensic_hardening.py::TestForensicHardening::test_live_trading_impossible_by_design` | `AssertionError: assert ('LIVE' in 'PAPER_BLOCKED' or 'FORBIDDEN' in 'PAPER_BLOCKED')` |
| 2 | `tests/test_freqtrade_integration.py::test_live_trading_permanently_forbidden` | `AssertionError: assert ('LIVE' in 'PAPER_BLOCKED' or 'FORBIDDEN' in 'PAPER_BLOCKED')` |
| 3 | `tests/test_freqtrade_integration.py::test_research_mode_blocks_execution_policy` | `AssertionError: assert 'PAPER_BLOCKED' == 'RESEARCH_BLOCKED'` |
| 4 | `tests/test_mesh_decision.py::test_live_mode_is_forbidden_by_design` | `AssertionError: assert 'PAPER_BLOCKED' == 'LIVE_FORBIDDEN_BY_DESIGN'` |
| 5 | `tests/test_ai_and_quantum_isolation.py::test_quantum_service_isolation_and_advisory_contract` | `features.py:14: in add_features  df['returns'] = df['close'].pct_change()` → `KeyError: 'close'` |
| 6 | `tests/test_security_audit_and_hardening.py::test_api_endpoints_do_not_leak_secrets` | same `KeyError: 'close'` via `/api/quantum/advisory` → HTTP 500 |
| 7 | `tests/test_market_data_integrity.py::TestMarketDataIntegrity::test_websocket_unavailable_uses_rest_fallback` | `assert not True` — `Empty DataFrame Columns: [] Index: []`; log: `[DATA] Exchange client disabled. Cannot fetch live candles for BTCUSDT. DATA_UNAVAILABLE.` |
| 8 | `tests/test_mass_backtester_data_integrity.py::test_downloaded_candles_are_cached_with_source_and_hash` | `Attribute "dtype" are different  [left]: datetime64[ms]  [right]: datetime64[us]` |
| 9 | `tests/test_stratex_freqtrade_complete.py::test_flask_freqtrade_routes` | `ValueError: 'm' is no longer supported for offsets. Please use 'ME' instead.` at `stratex_freqtrade_adapter/data/downloader.py:92: dates = pd.date_range(end=now, periods=limit, freq=timeframe)` |
| 10 | `tests/test_credentials.py::test_no_hardcoded_credentials_in_source` | `CREDENTIAL_FOUND in 1 file(s): .venv/lib/python3.11/site-packages/flask/config.py: lines [66]` |
| 11 | `tests/integration/test_forecast_context_flow.py::test_forecast_context_enrichment_flow` | `AssertionError: assert 'probability' in {}` — `status='UNAVAILABLE:CONNECTION_ERROR'` |
| 12 | `tests/integration/test_forecast_context_flow.py::test_forecast_accuracy_feedback_cycle` | `assert 'prediction_correct' in {'status': 'UNAVAILABLE', 'reason': 'No fresh live forecast exists for this symbol.'}` |
| E1-E4 | `tests/test_validation_claims_are_reproducible.py` × 4 | `FileNotFoundError: [Errno 2] No such file or directory: '/home/user/Stratex/optimization_results/adx_ema_optimization.json'` |

### 1.2 Boot + exercise main flows

Booted for real:

```
$ TRADING_MODE=PAPER HOST=0.0.0.0 PORT=5000 .venv/bin/python dashboard.py
 * Serving Flask app 'dashboard'
 * Running on all addresses (0.0.0.0)
listening_ports: [{"port":5000,"address":"0.0.0.0"}]
```

Endpoint sweep against the live server:

```
HTTP 200   365B  /health
HTTP 200   365B  /api/health
HTTP 503   372B  /api/ready          -> capability_reasons: NO_EXECUTABLE_STRATEGY, NO_STRATEGY_EVALUATION
HTTP 200  1913B  /api/status
HTTP 500   265B  /api/quantum/advisory     <<< CRASH
HTTP 503   192B  /api/candles        -> "status":"DATA_UNAVAILABLE","freshness":"STALE"
HTTP 200  2157B  /api/markets
HTTP 200  1366B  /api/scanner
HTTP 200   504B  /api/positions
HTTP 200   139B  /api/trades
HTTP 200  1374B  /api/funnel
HTTP 200   416B  /api/strategies
HTTP 200   561B  /api/config
HTTP 200   745B  /api/v1/security/status
HTTP 200  1307B  /api/paper/forward-status
HTTP 200   727B  /api/engine-health
```

Server-side traceback for the 500:

```
File "/home/user/Stratex/quantum_endpoint.py", line 29, in advisory
    result = _quantum_service.get_advisory(symbol=symbol, tf=tf)
File "/home/user/Stratex/quantum/service.py", line 69, in get_advisory
    feature_vec = extract_feature_vector(df)
File "/home/user/Stratex/quantum/features.py", line 61, in extract_feature_vector
    df = add_features(df)
File "/home/user/Stratex/features.py", line 14, in add_features
    df['returns'] = df['close'].pct_change()
KeyError: 'close'
127.0.0.1 - - [05/Oct/2026 17:17:00] "GET /api/quantum/advisory HTTP/1.1" 500 -
```

`node --check static/app.js` → **OK** (PROJECT_HANDOFF's step-3 verification passes).
UI route audit: UI references 8 `/api` paths, app registers 199 rules, **0** UI paths 404.

**Network egress in this sandbox is restricted** (PyPI reachable; render.com and binance.vision are
TLS-intercepted and fail). Proven:

```
https://futuris-th6f.onrender.com/health  -> ERR SSLError ... TLS/SSL connection has been closed (EOF)
https://testnet.binance.vision/api/v3/ping -> ERR SSLError ... TLS/SSL connection has been closed (EOF)
https://pypi.org/simple/                   -> 200
```

So failures #11/#12 are *not* a code defect I can prove or disprove here — they are integration tests
that require a live third-party service. I am labelling them **BLOCKED-BY-SANDBOX-EGRESS**, not fixed.

### 1.3 Docs vs. reality

**Documented but does not exist:**

| Claim | Where | Reality |
|---|---|---|
| "You **MUST** read `AGENTS.md`" | `README.md:15` | `ls: cannot access 'AGENTS.md': No such file or directory` |
| `status_check.py` — "CLI status report for account and market data" | `README.md` project structure | file absent |
| `experiments/` — "Immutable frozen experiment JSON registry" | `README.md` | absent (and `.gitignore:101`) |
| `backtest_results/` incl. `stage15/` "Final Quantitative Audit Report" | `README.md` | absent (`.gitignore:83`) |
| `research_stage6/ – stage10/` | `README.md` | absent |
| `data_cache/` — "Parquet candle data caches" | `PROJECT_HANDOFF.md` | absent (`.gitignore:90`) |
| "Run all **690** automated tests" / "tests/ (690 tests)" | `README.md` | **1045** collected |
| "Ensure **505/505** tests pass" / "505 unit, integration, chaos and security tests" | `PROJECT_HANDOFF.md` ×2 | **1027 passed, 12 failed, 4 errors** |
| `FINAL_HANDOFF_AUDIT.md` — "Read in order" step 2 | `PROJECT_HANDOFF.md:44` | file absent |
| "Single-page terminal UI (**10 views**)" | `PROJECT_HANDOFF.md` §8 | **5** `data-view` attributes: activity, health, markets, overview, portfolio |
| "55+ routes" | `PROJECT_HANDOFF.md` §8 | **199** registered rules |
| `optimization_results/adx_ema_optimization.json` — cited as the reproducible evidence artifact | `strategy_registry.json:_provenance` | absent; `.gitignore:82` excludes the whole directory, so it can **never** be present from a clone |

**Working but undocumented:** the entire `stratex_upgrade/` package, `stratex_quantdinger/`
(`ExecutionIntent`, `IdempotencyGuard`, `RuntimeSupervisor`, `JobStore`), `autonomy/mesh_decision.py`,
the Qanat decay smoother + allocator wired into `on_candle_closed`, `stratex_paper_lab.py`,
`paper_shadow_*` (has CI, no README mention), `battery_soak_runner.py`, `render_market_scanner.py`,
`alerting/`, `reporting/voice_summaries.py`.

**Config contradiction:** `Dockerfile:9` sets `TRADING_MODE=TESTNET`; `render.yaml:19` sets
`TRADING_MODE: "FUTURES"` **and** `PAPER_SAFE_MODE: "FALSE"`. Render's env wins, so the deployed
service runs a different mode than the image declares and than `README.md:118` documents.

### 1.4 Tests exist

153 test files, 1045 tests. No "write the safety net" work needed — the net is extensive. It is
currently **red** (12 failed / 4 errors), which is the problem.

---

## PHASE 2 — PRIORITIZED BUG REPORT

### CRITICAL

---

**C1 — Control-plane auth fails OPEN when no API keys are configured**
`security_hardening.py:312-315`

```python
if not configured_keys:
    if required_scope == SCOPE_READ:
        return True, "ANONYMOUS_DEV", {"role": "DEV", "scopes": [SCOPE_READ]}
    return True, "ANONYMOUS_DEV", {"role": "DEV", "scopes": [SCOPE_READ, SCOPE_CONTROL]}
```

**Root cause:** absence of credentials is treated as *permission*, and the fall-through branch grants
`SCOPE_CONTROL` — the most privileged scope — to an anonymous caller. This is not hypothetical:
`.env.example` ships `TRADING_BOT_API_KEY_READ=` and `TRADING_BOT_API_KEY_CONTROL=` **empty**, and
`render.yaml:33-36` marks both `sync: false` (unset). A default Render deploy therefore has
`configured_keys == {}` → **every control endpoint is publicly writable**.

**Evidence — real unauthenticated POSTs against the running server:**

```
POST /api/live/emergency/flatten -> HTTP 200 | {"enforcer_action":{"action":"FLATTEN_ALL","halted":true,
    "reason":"KILL_SWITCH triggered by DASHBOARD_UI_OPERATOR: Manual emergency kill switch activated",...
POST /api/live/emergency/halt    -> HTTP 200 | {"message":"Live order entries halted.","status":"OK",...}
POST /api/panic                  -> HTTP 200 | {"message":"PANIC ACTIVATED: new orders blocked engine-side;...
POST /api/research-jobs          -> HTTP 202 | {"job":{"job_id":"job_backtest_1791222801","job_type":"BACKTEST",...
POST /api/agent-gateway/jobs     -> HTTP 202 | {"job":{"job_id":"agent_job_1791222801",...
POST /api/config                 -> HTTP 200 | {"message":"Runtime configuration updated:  (applies until restart)","status":"success",...
POST /api/ecosystem/mode         -> HTTP 401 | {"error":"UNAUTHORIZED",...}   <-- the ONLY one that refused
```

Persistent state written by the anonymous caller:

```
$ cat panic_state.json
{"active": true, "activated_at": "2026-10-05T17:53:21.537592+00:00", "actor": "api:/api/panic"}

$ git status --short
?? experiment_jobs.json          <-- NOT gitignored; created by an unauthenticated POST
```

Note `/api/panic` *is* decorated `@require_bot_api_key` (`dashboard.py:4464`) and still returned 200 —
proving the decorator itself is the hole, not a missing decorator. Meanwhile `/api/ecosystem/mode`
(protected by `api/auth.py:require_permission`) correctly returned 401. **Two auth systems with
opposite failure modes coexist on the same port.**

**Impact:** anonymous caller can flatten all positions, halt trading, trip the panic kill switch,
mutate runtime config, and enqueue unbounded compute jobs (DoS). On testnet this destroys real
experiment state; the same code path guards any future live deployment.

**Proposed fix:** make `authenticate_request` fail **closed** — when no keys are configured, deny
`SCOPE_CONTROL` (and anything above read) with `503 AUTH_NOT_CONFIGURED`, mirroring what
`api/auth.py:88-97` already does correctly. Keep anonymous read only for an explicitly opt-in
`ENVIRONMENT != production` dev flag. Then converge the two systems on one.

---

**C2 — `/api/research-jobs` publishes fabricated backtest results as COMPLETED**
`dashboard.py:342-351`

```python
def mock_backtest_runner(s, j_id):
    time.sleep(0.5)
    s.update(j_id, progress=0.5)
    time.sleep(0.5)
    s.update(j_id, status="COMPLETED", progress=1.0,
        result={"total_trades": 12, "profit_factor": 1.42, "win_rate": 58.3, "net_pnl": 145.20})
```

**Root cause:** the endpoint's only job runner is a hardcoded stub. It sleeps 1s, then writes invented
performance numbers into the durable `JobStore`, marked `COMPLETED`.

**Evidence — this is what an anonymous caller actually retrieved and what persisted to disk:**

```json
"job_backtest_1791222801": {
  "job_type": "BACKTEST", "status": "COMPLETED", "progress": 1.0,
  "result": {"net_pnl": 145.2, "profit_factor": 1.42, "total_trades": 12, "win_rate": 58.3},
  "metadata": {"strategy_id": "adx_ema"}
}
```

**Impact:** directly violates the project's own constitution — `PROJECT_HANDOFF.md:47` *"Never
fabricate results: Never invent profit factors, win rates, backtest numbers."* A `PF 1.42` for
`adx_ema` contradicts the *only* reproducible measurement in the repo (`strategy_registry.json`:
`OOS_PROFIT_FACTOR: 0.8236`, `OOS_TRADE_COUNT: 2`). Any consumer — the FRIDAY mesh, an agent, a human
reading the dashboard — would treat 1.42 as a measured result for a strategy whose real evidence is
GRADE D. This is the single most dangerous line in the repo given what this project is *for*.

**Proposed fix:** wire the endpoint to the real backtester (`backtest_engine.py` /
`research/validation/walk_forward_engine.py`) and return its actual output; if no engine can run,
return `status: "UNSUPPORTED"` with an explicit `evidence_class`, never synthetic numbers. Add a
regression test asserting no `COMPLETED` job can exist without a real computation.

---

**C3 — `/api/v1/health/detailed` and `/integrations` return wholesale fabricated health**
`api/health.py:46,62,70-98,108-116`

**Evidence — live response from the running server, verbatim:**

```json
{"data": {
  "overall_status": "HEALTHY",
  "version": "2.4.0-quantum-hardened",
  "exchange_connectivity": {"binance":"HEALTHY","bybit":"HEALTHY","okx":"HEALTHY","coinbase":"HEALTHY"},
  "data_feed_freshness": {"BTC/USDT_last_tick_age_sec":0.4,"ETH/USDT_last_tick_age_sec":0.6,
                          "feed_status":"REAL_TIME_STREAMING"},
  "risk_system": {"status":"OPERATIONAL","circuit_breakers_tripped":0,"portfolio_heat_budget_pct":100.0},
  "evolution_engine": {"status":"ACTIVE","active_population":80,"current_generation":14},
  "storage": {"state_accessible":true,"ledgers_appendable":true,"disk_free_gb":50.0},
  "system_resources": {"cpu_percent":4.5,"rss_memory_mb":145.2,"disk_usage_pct":32.0,
                       "memory_total_mb":8192.0,"memory_used_mb":1024.0,"disk_free_gb":17.23},
  "ai_advisory": {"ai_universe_online":false,"status":"WARNING","rejected_count":4,
                  "unreachable_duration_sec":6.3},
  "uptime_seconds": -0.0
}}
```

Not one of those `HEALTHY` values was measured. Contradictions *inside the single response*:

- `"overall_status": "HEALTHY"` while its own `ai_advisory.status == "WARNING"` and
  `ai_universe_online == false`. Cause: `api/health.py:61` computes overall status from `trading` only.
- `"storage.disk_free_gb": 50.0` (hardcoded, `api/health.py:49`) vs
  `"system_resources.disk_free_gb": 17.23` (real, from `shutil.disk_usage`).
- `"feed_status": "REAL_TIME_STREAMING"` and `last_tick_age_sec: 0.4` while the same server was
  returning `/api/candles` → `503 {"status":"DATA_UNAVAILABLE","freshness":"STALE"}` and the paper
  runner was logging `DATA_UNAVAILABLE — skipping cycle` every cycle.
- `psutil` is **not installed and not in `requirements.txt`**
  (`ModuleNotFoundError: No module named 'psutil'`; `grep -n psutil requirements.txt` → no match), so
  the `except` fallbacks at `api/health.py:46-49` and `monitoring_system.py:124,126`
  (`cpu=12.5, mem=34.2, used=1024.0, total=8192.0`) are **always** what ships.
- `/api/v1/health/integrations` reports `"ai_universe": "CONNECTED_OR_FALLBACK_ACTIVE"` and four
  `HEALTHY` exchanges with no probe at all.
- `"uptime_seconds": -0.0` — negative zero, because `start_time` defaults to `time.time()`.

**Impact:** this is the endpoint an operator or an upstream orchestrator polls to decide whether the
system is well. It reports a healthy, streaming, fully-connected platform during a total data
blackout. It also undermines `overall_status` for `render.yaml:7 healthCheckPath: /health` triage.

**Proposed fix:** replace each fabricated block with a real probe or an explicit
`"status": "UNVERIFIED"` / `"evidence_class"` marker, matching the honest pattern already used in
`dashboard.py:_health_payload` and `/api/ready`. Add `psutil` to `requirements.txt`. Make
`overall_status` the worst of all pillars, not just `trading`.

---

**C4 — `execution` package silently becomes an empty shell on any import failure**
`execution/__init__.py:6-16`

```python
try:
    ...
    with open(_exec_file, "r", encoding="utf-8") as _f:
        _code = _f.read()
    exec(compile(_code, _exec_file, 'exec'), globals())
except Exception as _err:
    pass
```

**Root cause:** both `execution.py` (module) and `execution/` (package) exist. The package wins, so
`__init__.py` `exec()`s the module's source into its own globals to fake equivalence. The bare
`except Exception: pass` means **any** failure inside that exec — including a transient failure of any
of `execution.py`'s ~10 transitive imports — leaves the package empty and raises nothing.

**Evidence — I simulated one transient dependency failure and inspected the result:**

```
execution module loaded: /home/user/Stratex/execution/__init__.py
has place_market_order? -> False
has ExecutionPolicy?    -> False
RESULT: package silently became an EMPTY shell, no error raised
```

**Impact:** the module that owns `ExecutionPolicy` — the last gate before an order — can vanish with
zero diagnostics. Callers using `getattr(execution, "X", default)` silently take the default branch;
callers using `from execution import X` get a confusing `ImportError` pointing at the wrong cause. In
the worst case a safety check resolves to a permissive default.

**Proposed fix:** log-and-re-raise instead of `pass` (fail loudly at import), and longer-term remove
the duplication — make `execution/` re-export from `execution.py` via a normal import, or move the
module into the package.

---

**C5 — Degradation halt has no minimum duration; `OBSERVE_ONLY_COOLDOWN_SECONDS` is dead config**
`testnet_engine/service.py:2541`, `2571`, `2590-2600`

```python
cooldown_sec = float(getattr(config, "OBSERVE_ONLY_COOLDOWN_SECONDS", 7200))  # 2 hours max halt
...
# Self-healing: resume live trading if PnL is positive, win rate recovered, or cooldown elapsed
if self.observe_only:
    elapsed = time.time() - getattr(self, "observe_only_since", 0)
    logger.info(f"... Cooldown elapsed: {elapsed:.0f}s. Resuming live order execution.")
    self.observe_only = False
```

**Root cause:** `cooldown_sec` is assigned and **never read again**. `elapsed` is computed and logged
but never compared to anything. Repo-wide there are exactly two references to
`OBSERVE_ONLY_COOLDOWN_SECONDS`: the `config.py:170` definition and the dead assignment. So the
documented 2-hour minimum halt does not exist, and the docstring's "or cooldown expires" branch is
unimplemented.

**Evidence — ran the real `_check_degradation` against a synthetic ledger:**

```
CONFIG: OBSERVE_ONLY_COOLDOWN_SECONDS = 7200 (2h minimum halt intended)
CONFIG: MIN_WIN_RATE_THRESHOLD = 0.3

STEP 1  5 straight losers        -> observe_only = True (HALTED, correct)
STEP 2  win_rate=20% (STILL degraded), pnl=+496
        elapsed since halt = 0.0s of a 7200s cooldown
        -> observe_only = False   <<< RESUMED LIVE EXECUTION INSTANTLY

STEP 3  ledger truncated below DEGRADATION_WINDOW (rotation/loss):
        -> observe_only = False   <<< HALT CLEARED BY SHRINKING THE SAMPLE
```

**Second defect, same function (`service.py:2570-2573`):** when `len(closed_trades) < window`, the code
force-clears the halt — *"Insufficient closed trade sample... Resetting to ACTIVE trading."* That is
**fail-open**: shrinking or rotating the ledger disarms the safety gate (STEP 3 above).

**Impact:** a strategy in a genuine losing regime resumes live order submission the moment one large
winner flips recent PnL positive, even while its win rate is still below the degradation threshold
and zero cooldown has elapsed. Combined with HIGH-5 (the threshold itself is 0.30, not the documented
0.35), the guard is materially weaker than advertised. `PROJECT_HANDOFF.md:41` says *"Never weaken
risk gates"* — this one is weakened.

**Proposed fix:** enforce `elapsed < cooldown_sec → stay halted` in the self-healing branch; and in the
insufficient-sample branch, preserve an existing halt (only refuse to *newly* set one), so a shrinking
sample can never clear a trip.

---

### HIGH

---

**H1 — `ExecutionPolicy.can_place_order()` evaluates the LIVE prohibition last, making it unreachable**
`execution.py:103-120`

```python
mode, paper_safe, live_enabled, testnet_enabled = _resolve_execution_flags()
if mode == "PAPER" or paper_safe:          # line 106 — short-circuits first
    return False, "PAPER_BLOCKED"
if os.environ.get("RESEARCH_MODE") == "1": # line 109
    return False, "RESEARCH_BLOCKED"
if mode in ["TESTNET", "FUTURES"]:         # line 112
    ...
if mode == "LIVE" or live_enabled:         # line 117 — never reached when paper_safe
    return False, "LIVE_FORBIDDEN_BY_DESIGN"
```

**Root cause:** the most severe safety condition is checked after three less severe ones. Because
`config.validate_config()` (`config.py:262-273`) downgrades `TRADING_MODE` to `PAPER` and sets
`PAPER_SAFE_MODE = True` whenever credentials are missing, `paper_safe` is `True` in most real
environments — so a `mode == "LIVE"` request reports `PAPER_BLOCKED` and the LIVE-specific branch is
dead. Note `get_exchange_client()` (`execution.py:128`) *does* check LIVE first; the two functions
disagree.

**Evidence:** 4 separate tests across 3 files fail on this exact mismatch (Truth Audit #1-#4), all
expecting `LIVE_FORBIDDEN_BY_DESIGN` / `RESEARCH_BLOCKED` and receiving `PAPER_BLOCKED`.

**Honest scoping:** the *outcome* is still blocked in every case I could construct — `can_place_order`
returns `False` for `mode == "LIVE"` on all paths. So this is **not** a live-trading hole. It is a
wrong-reason-code bug that corrupts the audit trail (an operator investigating a blocked LIVE attempt
is told "paper mode", not "live is forbidden"), plus dead safety code that four tests assert must be
reachable.

**Proposed fix:** move the LIVE/`live_enabled` check to the top of `can_place_order`, matching
`get_exchange_client`. Surgical reordering, no behaviour change to the block itself.

---

**H2 — PAPER mode (the documented default) has a total market-data blackout**
`data_client.py:100-116`, `data.py:28-32`

```python
if TRADING_MODE == "PAPER":
    self.__client = None
    self.data_source = "DATA_UNAVAILABLE"
...
def is_available(self) -> bool:
    return self.__client is not None or getattr(self, "_MarketDataClient__prod_client", None) is not None
```

**Root cause:** in PAPER mode neither `__client` nor `__prod_client` is created, so `is_available()`
returns `False` — even though `__init__` *did* successfully build `self.__public_client`
(`data_client.py:93`, `public_data_source = "BINANCE_PUBLIC"`), an anonymous client for public klines
that needs no credentials. `is_available()` never consults it, and `get_klines` never falls back to
it. `data.get_candles` (`data.py:29-32`) then returns an empty DataFrame before trying anything.

Public Binance klines require **no authentication** — the file's own header says so:
*"Binance public kline/ticker endpoints do NOT require authentication."* Gating them on `TRADING_MODE`
is the bug.

**Evidence — live:**

```
HTTP 503 /api/candles -> {"candles":[],"status":"DATA_UNAVAILABLE","freshness":"STALE"}
[WARNING] [data] [DATA] Exchange client disabled. Cannot fetch live candles for BTCUSDT. DATA_UNAVAILABLE.
[WARNING] [paper_forward_runner] DATA_UNAVAILABLE — skipping cycle     <-- every cycle, indefinitely
```

`paper_forward_runner.py:315` calls `MarketDataClient().get_klines(...)`; `get_klines` returns `None`
when `is_available()` is False → `fetch_candles` returns `None` → `paper_forward_runner.py:925-930`
logs `DATA_UNAVAILABLE — skipping cycle` and loops forever.

**Impact:** `README.md:113` and `.env.example:6` both make `PAPER` the default mode, and
`README.md:57-64` describes Track 1 as the primary forward-validation path. In that default
configuration the primary validation track can never collect a single candle. The paper-shadow runner
sidesteps this by calling `get_public_futures_klines()` directly — proving the capability exists and
is simply not wired into the main path.

**Proposed fix:** include `__public_client` in `is_available()`, and add a public-client fallback in
`get_klines`/`get_historical_klines`/`futures_klines` for the credential-free read paths.

---

**H3 — No empty-DataFrame guard in the feature pipeline → HTTP 500**
`features.py:14`, `quantum/features.py:61`, `quantum/service.py:69`, `quantum_endpoint.py:29`

**Root cause:** `add_features(df)` immediately does `df['close'].pct_change()`. When upstream returns
an empty DataFrame (the normal, documented `DATA_UNAVAILABLE` path from `data.py:31`), this raises
`KeyError: 'close'`. `data.add_indicators` (`data.py:92-94`) *does* guard (`if df is None or df.empty
or len(df) < 20: return df`), but `quantum/features.py:61` calls `add_features` directly and bypasses
that guard. `quantum_endpoint.advisory` has no try/except, so it becomes a 500.

**Evidence:** live `GET /api/quantum/advisory` → **HTTP 500** with the full traceback quoted in §1.2.
Also causes Truth Audit failures #5 and #6.

**Impact:** any data outage turns an advisory endpoint into a 500. `/api/quantum/advisory` is one of
the endpoints `tests/test_security_audit_and_hardening.py` sweeps, so a data blip masks the
secret-leak assertion for *all* remaining endpoints in that test (it aborts at the first one).

**Proposed fix:** guard `add_features` on empty/missing-`close` input and return the frame unchanged;
have `QuantumService.get_advisory` emit its existing `QuantumAdvisoryResult` error shape (the
`except` branch at `quantum/service.py:50-67` already builds one) instead of propagating; return a
200 with `status: DATA_UNAVAILABLE` from the endpoint.

---

**H4 — Advisory scheduler retry storm: interval never honoured when upstream is down**
`advisory_scheduler.py:88-90`, `135`, `184-186`

```python
decision = self.client.consult(telemetry)
if not decision:
    logger.warning("...returned no decision...")
    return None                    # line ~90: early exit BEFORE line 135
...
with self._lock:
    self._last_consultation_time = datetime.datetime.utcnow()   # line 135: only on full success
```

and in `_worker_loop`:

```python
if self._last_consultation_time is None:
    should_run = True
    reason = "STARTUP_SCHEDULED"   # line 184-186
```

**Root cause:** `_last_consultation_time` is set only after a *successful* consultation. When the
upstream is unreachable, `consult()` returns falsy → early `return None` → the timestamp stays `None`
forever → `_worker_loop` re-fires every 60s poll (`check_interval_sec = 60`) with reason
`STARTUP_SCHEDULED`, permanently. There is no backoff, no failure counter, and no cap.

**Evidence — ~37 minutes of real runtime from the booted dashboard:**

```
[ADVISORY_SCHEDULER] Background worker thread started (interval=4.0h).
[ADVISORY_SCHEDULER] Starting consultation cycle (reason='STARTUP_SCHEDULED', shadow_mode=True)...
[AI_UNIVERSE_CLIENT] Network/Request exception during consultation: ...SSLError...
[ADVISORY_SCHEDULER] AI-Universe returned no decision. Retaining last validated parameters.
   ... repeats identically every ~60 seconds for the entire process lifetime ...
```

Counted in the captured log: ~37 `STARTUP_SCHEDULED` cycles in ~37 minutes. Intended: **1 per 4
hours** (`ADVISORY_INTERVAL_HOURS=4.0`). That is roughly a **148× amplification**, and each cycle also
runs `build_telemetry_payload()` (file I/O) *and* a Futuris network call
(`[FUTURIS_CLIENT] Futuris forecast unavailable: SSLError`) — so two dead endpoints are hammered
every minute, forever.

**Impact:** unbounded wasted I/O and log volume; the reason code lies (it is not a startup after the
first); masks real advisory failures in noise; on a metered/egress-limited host this is a cost leak.
It also makes `ai_advisory.unreachable_duration_sec` the only honest signal in a sea of retries.

**Proposed fix:** record the attempt timestamp (or a `_last_attempt_time`) regardless of outcome so
the interval is honoured; add exponential backoff with a cap on consecutive failures; distinguish
`STARTUP_SCHEDULED` (once) from `RETRY_BACKOFF` / `PERIODIC_SCHEDULED`.

---

**H5 — `config.py` defines three safety-relevant constants twice; the documented values are dead**
`config.py:89` vs `123`, `128` vs `168`, `129` vs `169`

```
$ grep -oP "^[A-Z_]+(?= =)" config.py | sort | uniq -d
DEGRADATION_WINDOW
MIN_WIN_RATE_THRESHOLD
TESTNET_BASELINE_RESET_ISO

config.py:89: TESTNET_BASELINE_RESET_ISO = os.getenv(..., "2026-09-01T00:00:00Z")
config.py:123:TESTNET_BASELINE_RESET_ISO = os.getenv(..., "2026-09-09T13:00:00Z")
config.py:128:DEGRADATION_WINDOW = 20
config.py:168:DEGRADATION_WINDOW = int(os.getenv("DEGRADATION_WINDOW", "20"))
config.py:129:MIN_WIN_RATE_THRESHOLD = 0.35   # "switch to OBSERVE-ONLY if < 35% win rate"
config.py:169:MIN_WIN_RATE_THRESHOLD = float(os.getenv("MIN_WIN_RATE_THRESHOLD", "0.30"))
```

**Effective values, verified at runtime:**

```
$ .venv/bin/python -c "import config; print(config.TESTNET_BASELINE_RESET_ISO, config.MIN_WIN_RATE_THRESHOLD)"
2026-09-09T13:00:00Z 0.3
```

**Root cause / impact, per constant:**

- **`MIN_WIN_RATE_THRESHOLD`** — the degradation gate's win-rate floor is **0.30**, but line 129's
  comment (the one a reader trusts) says **0.35**. The runtime consumer
  `service.py:2539` reads the effective 0.30. A risk gate is silently 5 percentage points looser than
  its own documented intent, in direct tension with `PROJECT_HANDOFF.md:41` (*"Never weaken risk
  gates"*).
- **`TESTNET_BASELINE_RESET_ISO`** — effective default is **2026-09-09**, but *all three consumers*
  hardcode the **other** value as their `getattr` fallback:
  `dashboard.py:1589`, `service.py:587`, `service.py:2253` all use
  `getattr(config, "TESTNET_BASELINE_RESET_ISO", "2026-09-01T00:00:00Z")`. This ISO date anchors the
  equity baseline for daily-loss and drawdown risk gating — two different baseline dates in one codebase
  means the risk window can be computed inconsistently if the attribute is ever missing or reloaded.
- **`DEGRADATION_WINDOW`** — benign today (both resolve to 20), but line 128 is not env-overridable
  and is dead, so a reader can't tell which one governs.

**Proposed fix:** delete the earlier duplicate of each and keep the env-overridable definition; align
the three consumers' `getattr` fallbacks with the surviving default. Add a test asserting each safety
constant is defined exactly once in `config.py` (an AST check), so this can't silently regress.

---

**H6 — Duplicate route registration silently shadows the richer handlers (dead code)**
`dashboard.py:821-822` vs `4996`; `dashboard.py:3328` vs `4389`

```
$ grep -oP "@app\.route\('\K[^']+" dashboard.py | sort | uniq -d
/api/health
/api/telemetry/analytics
```

Flask registers both rules; the **first** registered endpoint wins URL matching, so the second
handler is unreachable.

**Evidence — both rules present, and the live response is the first one's:**

```
rule: /api/health endpoint: health_liveness     -> dashboard.health_liveness
rule: /api/health endpoint: api_overall_health  -> dashboard.api_overall_health
LIVE /api/health -> 200 {'evidence_class': 'process_liveness', 'dashboard': 'online', 'engine': 'online', ...}
```

The response is `health_liveness`'s payload. `api_overall_health` (`dashboard.py:4997-5020`) — which
returns `trading`, `advisory` and `system_resources` pillars and computes a real
`overall_status` — is **never reachable**. Same for `api_telemetry_analytics` (`dashboard.py:4389`),
shadowed by `api_analytics` (`dashboard.py:3328`).

**Impact:** the more informative health handler is dead. A consumer polling `/api/health` gets process
liveness only and can never see trading/advisory/resource state — which is precisely the information
C3's endpoint fabricates instead. Two independent bugs pointing at the same missing capability.

**Proposed fix:** decide which handler should own each path, delete the loser, and add a test asserting
`app.url_map` has no duplicate rules (so this cannot recur).

---

**H7 — Crypto timeframe strings passed directly as pandas offset aliases**
`stratex_freqtrade_adapter/data/downloader.py:92`

```python
dates = pd.date_range(end=now, periods=limit, freq=timeframe)   # timeframe == "5m"
```

**Root cause:** `"5m"` is an *exchange* timeframe token. It is not a pandas offset alias, and the two
vocabularies are conflated with no mapping table anywhere in the adapter
(`grep -n freq stratex_freqtrade_adapter/data/downloader.py` → only line 92).

**Evidence:**

```
$ .venv/bin/python -c "import pandas as pd; pd.date_range(end=pd.Timestamp.now(), periods=3, freq='5m')"
5m   -> ERROR: ValueError Invalid frequency: 5m. Failed to parse with error message:
      ValueError("'m' is no longer supported for offsets. Please use 'ME' instead.")
5min -> [Timestamp(...19:27:53), Timestamp(...19:32:53), Timestamp(...19:37:53)]
```

and the failing test:

```
api/freqtrade_routes.py:94: in run_backtest
stratex_freqtrade_adapter/data/downloader.py:92: in fetch_ohlcv
E   ValueError: 'm' is no longer supported for offsets. Please use 'ME' instead.
```

**This was always wrong, not just on pandas 3.** In pandas 2.x `'m'` was the deprecated alias for
**month-end**, so `freq="5m"` meant *five month-ends* — the downloader would have silently generated
monthly bars for a request for 5-minute bars. pandas 3 converted a silent data-correctness bug into a
loud crash. `'1d'` is also drifting: `Pandas4Warning: 'd' is deprecated ... please use 'D' instead.`

**Impact:** `POST /api/v1/freqtrade/backtest` is broken end-to-end (500), and the pre-pandas-3
behaviour would have fed month-resolution data into a 5-minute backtest.

**Proposed fix:** add an explicit exchange-timeframe → pandas-offset map (`1m→1min, 5m→5min,
15m→15min, 1h→1h, 4h→4h, 1d→1D, 1w→1W`) with a hard error on unknown tokens. Also fix
`metrics.py:88` / `research/metrics/metrics_engine.py:88` `resample('1D')` → `'1D'` is fine, but audit
all freq strings for the `'d'` deprecation.

---

**H8 — The reproducibility test suite depends on a gitignored artifact it can never obtain**
`tests/test_validation_claims_are_reproducible.py:17`, `.gitignore:82`

```python
OPTIMIZATION_ARTIFACT = ROOT / "optimization_results" / "adx_ema_optimization.json"
@pytest.fixture(scope="module")
def optimization():
    return json.loads(OPTIMIZATION_ARTIFACT.read_text(encoding="utf-8"))
```

```
$ git check-ignore -v optimization_results/adx_ema_optimization.json
.gitignore:82:optimization_results/   optimization_results/adx_ema_optimization.json
```

**Root cause:** 4 module-scoped fixtures `read_text()` an artifact that `.gitignore` excludes. From a
fresh clone the file cannot exist, so all 4 tests **error at setup** — permanently, for everyone.

**Evidence:**
```
E  FileNotFoundError: [Errno 2] No such file or directory:
   '/home/user/Stratex/optimization_results/adx_ema_optimization.json'
```
× 4 (`test_recorded_metrics_match_the_cited_artifact`,
`test_validated_assets_are_empty_while_evidence_is_insufficient`,
`test_adx_ema_is_not_recorded_as_executable`, `test_artifact_reports_the_run_failed_out_of_sample`).

**The irony is the point:** this file's own docstring says *"A validation claim must be reproducible
from a stored artifact"*, and `strategy_registry.json`'s `_provenance.note` cites this exact path as
the evidence for its corrected `PF 0.8236` figures. But the artifact is untracked, so the claim is
**not** reproducible — the test suite cannot verify its own central premise. CI never catches this
because `.github/workflows/strategy-truth.yml` doesn't run this file, while the documented
`pytest` command always errors.

**Proposed fix:** either commit the artifact (un-ignore that one JSON, it's small and it *is* the
evidence), or make the fixture `pytest.skip` with an explicit `ARTIFACT_ABSENT` reason and add a
separate test asserting that if the registry cites an artifact path, that path is tracked in git.
Do **not** delete the assertions.

---

### MEDIUM

---

**M1 — Credential scanner doesn't exclude virtualenvs → false CREDENTIAL_FOUND**
`tests/test_credentials.py:26`

```python
_EXCLUDE_DIRS = {".git", "__pycache__", "data_cache", "backtest_results", ".env"}
```

`_collect_source_files` walks from `_REPO_ROOT` and prunes only those. Any developer or CI job that
installs deps into an in-repo venv scans all of `site-packages`.

**Evidence:**
```
E  AssertionError: CREDENTIAL_FOUND in 1 file(s):
E    .venv/lib/python3.11/site-packages/flask/config.py: lines [66]
```
That is Flask's own source, not a Stratex secret. **No real credential exists in this repo** — my
working-tree and full-git-history scans both came back clean (see §Security below).

**Fix:** add `.venv`, `venv`, `env`, `site-packages`, `node_modules`, `.tox`, `.nox`, `build`, `dist`
to `_EXCLUDE_DIRS`. This narrows the *scan scope*, not the assertion — the check stays equally strict
for all first-party code.

---

**M2 — `/api/status` reports `binance: OK` / `data: OK` during a total data blackout (fail-open defaults)**
`dashboard.py:774-775`, `1354-1355`

```python
"binance_connected": hb.get("binance_connected", True),
"websocket_connected": hb.get("websocket_connected", True),
```
```python
components["binance"] = "OK" if engine_data.get("binance_connected") else "ERROR"
components["data"]    = "OK" if engine_data.get("websocket_connected") else "ERROR"
```

**Root cause:** missing heartbeat fields default to `True`. Absence of evidence is reported as a
healthy connection — the opposite of the fail-closed posture the rest of the file takes (compare the
excellent `capability_reasons` logic 30 lines above).

**Evidence — live, same process, same minute:**
```
GET /api/status  -> "components": {"binance":"OK","data":"OK","engine":"OK","execution":"OK","strategy":"IDLE"}
                    "engine_data": {"binance_connected": true, "websocket_connected": true,
                                    "heartbeat_age_seconds": 999.0}
GET /api/candles -> 503 {"status":"DATA_UNAVAILABLE","freshness":"STALE"}
log              -> [paper_forward_runner] DATA_UNAVAILABLE — skipping cycle
```
`heartbeat_age_seconds: 999.0` is the sentinel for "no readable timestamp" (`dashboard.py:697`), yet
connectivity still reports `true`.

**Fix:** default both to `False`, or better, derive them from observed data freshness (last candle
age) rather than a heartbeat field nobody writes. This is what makes C3's fabrication *believable* —
the two must be fixed together.

---

**M3 — Integration tests require a live third-party service with no mock and no skip**
`tests/integration/test_forecast_context_flow.py:6-38`

```python
client = get_futuris_client()
forecast = client.fetch_forecast('BTCUSDT')
assert 'probability' in forecast.volatility_forecast
```

No fixture, no monkeypatch, no `skipif`. When the service is unreachable the client degrades
*correctly* (`status='UNAVAILABLE:CONNECTION_ERROR'`, `volatility_forecast={}`) and the assertion
fails — so the test measures the network, not the code.

**Evidence:** failures #11/#12, plus `SSLError` to `futuris-th6f.onrender.com` confirmed in §1.2.

**Status: BLOCKED-BY-SANDBOX-EGRESS.** I cannot make these pass here without either a network or
changing what they assert, and I will not weaken them unilaterally. **This needs your decision** —
see the question at the end.

---

**M4 — CSV cache round-trip silently changes timestamp resolution**
`tests/test_mass_backtester_data_integrity.py:52`

```
E  AssertionError: DataFrame.index are different
E  Attribute "dtype" are different
E  [left]:  datetime64[ms]      <- freshly downloaded
E  [right]: datetime64[us]      <- reloaded from data_cache/factory_data/BTCUSDT_5m.csv
```

**Root cause:** the in-memory frame carries millisecond resolution (Binance epoch-ms); writing to CSV
and reading back yields microseconds. The `sha256` provenance check *passes* (it hashes the CSV text),
so the metadata says "verified" while the reloaded frame is not identical to what was downloaded. For
a repo whose whole thesis is reproducibility, a cache that returns a different dtype than the source
is a real fidelity gap — and it can change merge/join/reindex behaviour downstream.

**Fix:** normalise the timestamp dtype explicitly on both write and read (`.astype('datetime64[ms]')`
or a documented canonical unit) in `research/strategy_factory/mass_backtester.py:load_market_data`,
so cache and source are bit-comparable.

---

**M5 — Webhook emitter: unbounded DLQ, thread-per-event, no backoff on HTTP errors**
`api/webhooks.py:32,47-49,58-67`

- `self.dead_letter_queue: list = []` — appended on every failure (`:67`), never drained, never
  capped, never persisted. A permanently-down webhook URL grows this list forever → memory leak.
- `emit_event` spawns a **new daemon thread per event** (`:47-49`). At trade/risk-event rates this is
  unbounded thread creation with no pool and no concurrency limit.
- The backoff `time.sleep(0.5 * (2 ** attempt))` lives **inside `except`** (`:63-64`). A non-2xx HTTP
  response (500, 503) sets `success = False` and retries **immediately with zero delay** — hammering a
  struggling receiver 3× in a row.

It *is* wired (`alerting/intelligent_alerts.py:25,60,107`), so this is live code, not dead code.

**Fix:** bound the DLQ (deque with maxlen) and persist it; use a small bounded worker pool or queue;
move the sleep so it applies to every failed attempt regardless of failure type.

---

**M6 — `psutil` absent from `requirements.txt`, so all resource metrics are permanently fake**
`api/health.py:46-52`, `monitoring_system.py:116-126`

```
$ .venv/bin/python -c "import psutil"
ModuleNotFoundError: No module named 'psutil'
$ grep -n psutil requirements.txt
(no match)
```

Both files guard with `try/except` or `_HAS_PSUTIL` and fall back to hardcoded constants
(`145.2 MB`, `4.5%`, `32.0%`, `50.0 GB`, and `monitoring_system`'s `12.5/34.2/1024.0/8192.0`). Since
the dependency is never declared, the fallback is the **only** code path that ever runs in a real
deployment. Compounding C3.

**Fix:** add `psutil` to `requirements.txt`, and make the fallback emit an explicit
`"source": "UNAVAILABLE"` marker rather than plausible-looking numbers.

---

**M7 — mypy: 11 errors in 5 files** (full list in §1.1)

- `config_strategy.py:395-397` `[index]` — the governance null-out loop assigns into
  `PRODUCTION_STRATEGY_REGISTRY`, which mypy infers as `dict[str, object]` from heterogeneous values.
  **This is the single most safety-critical loop in the repo** (it scrubs unproven priors) and it is
  untyped. Fix: annotate as `dict[str, dict[str, Any]]`.
- `stratex_backtrader_adapter/{cerebro.py:35,37,38,175, client.py:83, models.py:95}` `[valid-type]` —
  `any` (the builtin function) used as a type annotation instead of `typing.Any`. 6 occurrences. Real
  defect: the annotations are meaningless and the adapter's contracts are unchecked.
- `stratex_ccxt_adapter/arbitrage.py:59,63` `[return-value]` — a `max()`/`min()` key function can
  return `float | None`, so the sort key can be `None`. In an **arbitrage spread comparison** a `None`
  key is a latent `TypeError` at runtime.

---

**M8 — `execution/router.py` and `execution/exchange_router.py` are byte-identical duplicates**

```
$ diff execution/router.py execution/exchange_router.py && echo IDENTICAL
IDENTICAL FILES (byte-for-byte)          # 13,486 bytes each
```

Plus a third, different `exchange_router.py` (4,343 bytes) at the repo root. Two copies of the same
13 KB router will drift; whichever one a caller imports determines behaviour.

**Fix:** keep one, make the other a thin re-export (or delete it and fix importers), and decide what
the root-level `exchange_router.py` is for.

---

**M9 — 191 of 199 registered API routes are unconsumed by the shipped UI**

```
UI references 8 distinct /api paths; app registers 199 rules
UI paths with NO matching route (0)
```

The SPA (`static/app.js`) calls only `/api/engine-health`, `/api/equity-history`, `/api/markets`,
`/api/paper/forward-status`, `/api/positions`, `/api/status`, `/api/telemetry/signals`,
`/api/telemetry/trades`. The other ~191 routes — including every mutating one in C1 — are reachable
only by external callers. That's a large, unexercised, largely unauthenticated attack and
rot surface. `PROJECT_HANDOFF.md` claims "55+ routes"; the real number is 199.

**Fix:** not a single patch — an inventory exercise. Classify each route as (a) UI-consumed,
(b) mesh/agent-consumed (then it needs auth), (c) dead → remove. This is Phase 4 work.

---

### LOW

**L1 — Documentation drift** (full table in §1.3): missing `AGENTS.md` (README makes it mandatory
reading), missing `status_check.py`, `FINAL_HANDOFF_AUDIT.md`, `experiments/`, `backtest_results/`,
`research_stage6-10/`, `data_cache/`; test counts quoted as 690 and 505 vs **1045** actual;
"10 views" vs **5**; "55+ routes" vs **199**.

**L2 — `Dockerfile:9` vs `render.yaml:19-25` disagree on the deployed mode.**
`TRADING_MODE=TESTNET` + `PAPER_SAFE_MODE` unset in the image; `TRADING_MODE: "FUTURES"` +
`PAPER_SAFE_MODE: "FALSE"` in Render. Render wins, so production runs FUTURES with paper-safe off —
not what `README.md:118` tells a reader to expect. Needs an explicit decision and a single source.

**L3 — 221 `datetime.datetime.utcnow()` calls in non-test source.** Deprecated since Python 3.12 and
returns naive datetimes; the codebase mixes these with tz-aware `pd.Timestamp.now(tz="UTC")`
(`data.py:82`) and `datetime.now(timezone.utc)` (`api/health.py:26`). Naive/aware comparisons are a
latent `TypeError` class. `dashboard.py:694` already has to strip tzinfo to cope:
`.replace(tzinfo=None)`.

**L4 — 134 `except Exception:` blocks whose body is `pass`, plus 16 bare `except:`.**
Systematic suppression. `execution/__init__.py:15-16` (C4) is the worst instance; `service.py:885`,
`data_client.py:139,144` (which swallow the errors that would explain a data outage) are others.

**L5 — `strategy_factory_winners.py:8-14` docstring asserts unbacked performance figures:**
*"Net PF 1.481, Win Rate 43.1%"* … *"Net PF 1.361, Win Rate 30.5%"* for 5 winners, attributed to
`research/strategy_factory/mass_backtester.py` "across 204 variations" — with no artifact in the repo
(`backtest_results/` is gitignored). These are the same *category* of claim C2 fabricates, sitting in
a runtime-importable module. Currently harmless only because
`config_strategy.py:392-397` scrubs the registry copies and none are `VALIDATED` — but the docstring
is an unaudited claim a future reader will trust.

**L6 — `.dockerignore:16` excludes `tests`**, so the production image cannot run the suite. Probably
intentional, but it means the container can never self-verify; worth a deliberate decision.

**L7 — `_check_degradation` re-reads and re-parses the entire JSONL ledger on every call**
(`service.py:2549-2566`), with a per-line `try/except: pass`. `testnet_trade_ledger.jsonl` is already
27,904 bytes in this checkout and grows without bound. This is an O(ledger size) scan on a hot
monitoring path — a slow-burn performance trap, plus the silent per-line swallow hides corruption.

---

## SECURITY SUMMARY

**No secrets found. Nothing to rotate.**

```
# Full git history, all revisions, credential-shaped patterns:
$ git rev-list --all | while read c; do git grep -InE "(AIza[0-9A-Za-z_-]{30,}|sk-[A-Za-z0-9]{20,}|
   ghp_[A-Za-z0-9]{30,}|xox[baprs]-|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----)" $c; done | sort -u
SCAN_DONE                              # (no output — clean; history is 1 squashed commit)

# Working tree:
$ grep -rInE "(AIza...|sk-...|ghp_...|github_pat_|xox.-|AKIA...|BEGIN.*PRIVATE KEY)" --exclude-dir=.venv .
(no output)

$ grep -rInE "(API_KEY|SECRET|TOKEN|PASSWORD|PASSPHRASE)['\"]?\s*[:=]\s*['\"][A-Za-z0-9_\-]{24,}['\"]" ...
./README.md:199:API_KEY="your_binance_testnet_api_key"          # placeholder
./config_template.py:7:API_KEY = "YOUR_BINANCE_TESTNET_API_KEY_HERE"   # placeholder
```

Both hits are placeholders. `.env.example` is clean. `config.py:20-40` reads credentials exclusively
from env with a BOM-repair shim; `LIVE_TRADING_ENABLED = False` is a literal at `config.py:87` and is
not env-overridable. `data_client.py` correctly refuses credentials for public data and blocks
non-whitelisted Binance methods via `__getattr__` (`data_client.py:283-296`). `api/auth.py` uses
`hmac.compare_digest` throughout. This part of the codebase is genuinely well-built — **C1 is a
fail-open default, not a leaked secret.**

Other security-relevant constructs reviewed: `strategy_ml.py:48-55` uses `pickle.load` on local model
artifacts (acceptable for local files, but worth a provenance check if artifacts ever become
downloadable); `stratex_upgrade/audit.py:24` uses `subprocess.run(command, shell=True)` on a
caller-supplied string (currently only called with internal constants, and the module is never
imported — but it's a loaded gun); all other `subprocess` uses are fixed-argument `git rev-parse`
lists with timeouts.

---

## SUMMARY TABLE

| Sev | ID | File:line | One-line |
|---|---|---|---|
| CRIT | C1 | `security_hardening.py:312-315` | Control-plane auth fails OPEN with no keys → anonymous flatten/halt/panic/config |
| CRIT | C2 | `dashboard.py:342-351` | `mock_backtest_runner` publishes fabricated PF 1.42 / WR 58.3 as COMPLETED |
| CRIT | C3 | `api/health.py:46-98,108-116` | Health API fabricates exchanges, feed freshness, risk, evolution, storage |
| CRIT | C4 | `execution/__init__.py:14-16` | `exec()` + bare `except: pass` → execution package silently empties |
| CRIT | C5 | `service.py:2541,2571,2590-2600` | Cooldown is dead config; halt clears instantly and on ledger shrink |
| HIGH | H1 | `execution.py:106-120` | LIVE prohibition unreachable → wrong reason codes, 4 tests red |
| HIGH | H2 | `data_client.py:100-116` | PAPER mode = total market-data blackout; public client ignored |
| HIGH | H3 | `features.py:14` → `quantum_endpoint.py:29` | No empty-frame guard → HTTP 500 on /api/quantum/advisory |
| HIGH | H4 | `advisory_scheduler.py:90,135,184` | Retry storm: ~37 cycles/37min vs intended 1/4h, no backoff |
| HIGH | H5 | `config.py:89/123,128/168,129/169` | 3 duplicate safety constants; win-rate gate 0.30 not documented 0.35 |
| HIGH | H6 | `dashboard.py:822/4996, 3328/4389` | Duplicate routes shadow the richer handlers (dead code) |
| HIGH | H7 | `freqtrade_adapter/data/downloader.py:92` | Exchange timeframe used as pandas freq; '5m' meant *months* pre-pandas-3 |
| HIGH | H8 | `test_validation_claims...py:17` + `.gitignore:82` | Reproducibility tests depend on a gitignored artifact → 4 permanent errors |
| MED | M1 | `tests/test_credentials.py:26` | Scanner doesn't exclude venvs → false CREDENTIAL_FOUND |
| MED | M2 | `dashboard.py:774-775,1354-1355` | `binance_connected`/`websocket_connected` default True (fail-open) |
| MED | M3 | `tests/integration/test_forecast_context_flow.py` | Requires live 3rd-party service, no mock/skip — **BLOCKED-BY-EGRESS** |
| MED | M4 | `mass_backtester` CSV cache | Round-trip changes datetime64[ms]→[us] while sha256 says "verified" |
| MED | M5 | `api/webhooks.py:32,47-49,63` | Unbounded DLQ, thread-per-event, no backoff on HTTP errors |
| MED | M6 | `api/health.py:46`, `monitoring_system.py:124` | psutil undeclared → resource metrics permanently fake |
| MED | M7 | 5 files | 11 mypy errors incl. untyped governance loop and `None` sort key in arbitrage |
| MED | M8 | `execution/router.py` vs `exchange_router.py` | Byte-identical 13 KB duplicates |
| MED | M9 | `dashboard.py` | 191 of 199 routes unconsumed by the UI |
| LOW | L1-L7 | various | Docs drift; Dockerfile/render.yaml mode conflict; 221 `utcnow()`; 134 silent `except: pass`; unbacked docstring claims; `.dockerignore` excludes tests; O(n) ledger rescan |

**Counts: 5 CRITICAL, 8 HIGH, 9 MEDIUM, 7 LOW = 29 findings.**

---

## STATUS: REAL vs CONFIGURED-BUT-UNVERIFIED

| Capability | Status | Evidence |
|---|---|---|
| Test suite runs | **REAL** | 1045 collected, 1027 pass, 79.20s |
| Test suite green | **NOT REAL** | 12 failed / 4 errors |
| Lint clean | **REAL** | `ruff check .` → All checks passed |
| Typecheck clean | **NOT REAL** | 11 mypy errors / 5 files |
| Dashboard boots & serves | **REAL** | listening on 0.0.0.0:5000, 15/16 endpoints 2xx/503 |
| `/api/quantum/advisory` | **BROKEN** | HTTP 500, traceback captured |
| Frontend JS valid | **REAL** | `node --check static/app.js` → OK; 0 dangling API paths |
| Live-trading hard block | **REAL** | `config.LIVE_TRADING_ENABLED=False` literal; `validate_environment_safety()`; `can_place_order()` returns False on all LIVE paths |
| Governance gate (no strategy executable) | **REAL** | `/api/strategies` → `executable_count: 0`, `status: OBSERVE_ONLY`; `/api/ready` → 503 with honest reasons |
| Zero credential exposure | **REAL** | clean git-history + working-tree scans |
| Control-plane auth | **NOT REAL** | anonymous flatten/halt/panic/config all → 200 |
| Health/observability truth | **NOT REAL** | C3 + M2, fabricated values captured verbatim |
| Paper track collects data | **NOT REAL** | `DATA_UNAVAILABLE — skipping cycle` forever in PAPER mode |
| Testnet track places orders | **CONFIGURED-BUT-UNVERIFIED** | requires `API_KEY`/`SECRET_KEY` + egress to `testnet.binance.vision`; both unavailable here (SSLError proven). I did not place an order and cannot claim it works. |
| Futuris / IntelX / AI-Universe / Memora mesh | **CONFIGURED-BUT-UNVERIFIED** | all `*.onrender.com` hosts TLS-blocked in sandbox; clients degrade gracefully (verified), round-trip unverified |
| Quantum subsystem | **CONFIGURED-BUT-UNVERIFIED** | PennyLane/Qiskit not in `requirements.txt` and not installed; `QuantumService` runs the `backend=None` path. `PROJECT_HANDOFF.md` verdict "B — NO QUANTUM ADVANTAGE (p>0.63)" not reproduced here |
| Deployment (Render/Docker) | **CONFIGURED-BUT-UNVERIFIED** | never deployed; `Dockerfile`/`render.yaml` mode conflict noted (L2) |
| 24h soak | **CONFIGURED-BUT-UNVERIFIED** | `battery_soak_runner.py` exists; `PROJECT_HANDOFF.md` §11.4 already admits it hasn't run |

---

## ONE QUESTION (per Prime Directive 6)

I can proceed on everything else without input, but **M3** is a genuine fork where either choice
could violate a Prime Directive, so I need your call:

`tests/integration/test_forecast_context_flow.py` asserts on a **live response from
`futuris-th6f.onrender.com`**, which is unreachable from this sandbox (proven `SSLError`). The client
degrades correctly; only the test's assumption of a live upstream fails.

**How should I handle it?**

- **(A)** Convert it to contract-test the enrichment flow against an injected stub forecast, and add a
  separate `@pytest.mark.live` test (skipped by default, opt-in via env) that keeps the real
  end-to-end assertion. Nothing is deleted; the live check survives but stops gating CI on a
  third party's uptime.
- **(B)** Leave it exactly as-is and report it permanently red / `BLOCKED-BY-SANDBOX-EGRESS`, on the
  principle that touching it at all risks weakening a test.
- **(C)** Something else you have in mind.

I recommend **(A)** — it preserves the assertion's intent while removing an external uptime
dependency from your test gate, and it matches the graceful-degradation contract the client already
implements.

Everything else in Phase 2 I will fix in priority order (C1→C5, then H1→H8, then M/L) with a
regression test per fix and a full suite + lint + typecheck re-run after each batch, per Phase 3.

**Awaiting your go-ahead before making any code changes.**
