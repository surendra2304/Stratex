# STRATEX — Repository Comprehension & Hardening Report

Repo: `surendra2304/Stratex` · branch `arena/bb27a2eb-stratex` · analysis date 2026-10-07
Evidence rule: every claim carries a `path:line` citation and a certainty label —
**[FACT]** directly observed in code/tests, **[INFERENCE]** derived from observed behavior,
**[HYPOTHESIS]** plausible but unverified.

---

## 1. What this system is

A self-contained crypto trading framework: market intelligence ingestion → regime/ML
screening → profitability + risk gating → paper/testnet execution (live orders are
blocked by design) → Flask dashboard/control plane, plus a research/backtesting
subsystem. ~10.8k lines in the five core files alone
(dashboard.py 6,197 · testnet_engine/service.py 3,206 · execution.py 1,085 · config.py 289) [FACT].

**[FACT]** Entry points: `bot.py` → `TestnetService` singleton daemon
(testnet_engine/service.py:202); `dashboard.py` Flask app (0.0.0.0:PORT);
`supervise_services.py` process supervisor for container restarts.

**[FACT]** Deployment target: Render `singapore` region, docker runtime, `/health`
probe (render.yaml; tests/test_render_deployment_hardening.py).

## 2. Architecture map

- **Engine pipeline** — `TestnetService.run()`: reconcile open orders → symbol
  discovery → governance gate → optional ML layer → MarketScanner → monitor /
  trailing / heartbeat threads (testnet_engine/service.py:3017-3145) [FACT].
- **Candidate ranking** — score = (expected_net_return × confidence) / max(0.001,
  risk_pct) × intelx/futuris/adx/rr multipliers
  (testnet_engine/service.py:1226-1310) [FACT].
- **Execution policy (the safety contract)** — `_resolve_execution_flags`
  (execution.py:76-98) resolves mode priority LIVE > PAPER > TESTNET > FUTURES;
  `can_place_order` order: PAPER/paper_safe → RESEARCH → TESTNET/FUTURES allow →
  LIVE forbid; truth table pinned by tests/test_safety_gates.py:12-25 [FACT].
- **Ledger & idempotency** — append-only ledger with O(new-lines) dedup cache
  `ledger_contains_id` (execution.py:292); `place_market_order` state machine
  (execution.py:265-470): panic → policy → idempotency → dedup → market order →
  fill parse → OCO protection → emergency close [FACT].
- **Control plane** — `POST /api/panic` (admin key), `/api/v1/control/pause|resume`
  (control key), fail-closed auth when no key is configured (dashboard.py) [FACT].
- **Paper/shadow layer** — `paper_shadow_scheduler` + `paper_runner_supervisor`
  run exchange-free shadow accounting; the scheduler contains no exchange-execution
  imports (tests/test_paper_shadow_scheduler.py:214-224) [FACT].

## 3. Security ledger (S1–S7, P1–P5) — status after hardening pass

| ID | Finding | Status | Evidence |
|----|---------|--------|----------|
| S1 | Hardcoded API/control secrets in config defaults | **FIXED** | config.py now derives ephemeral random keys when none are provided; webhook auth fails closed without a secret [FACT] |
| S2 | Pause endpoint was decorative (in-memory only, not enforced) | **FIXED end-to-end** | trading_pause.py (75 lines, durable flag) + control API persistence + `TestnetService._reject_paused_candidates` (testnet_engine/service.py:1231) + submission-boundary re-check (service.py:1543) + risk-limits display. Verified live: pause → `trading_pause_state.json` written with actor attribution; `/api/v1/control/risk-limits` reports `trading_paused:true` [FACT] |
| S3 | Control endpoints must reject unauthenticated callers | **VERIFIED** | no-key deployment returns 503 `AUTH_NOT_CONFIGURED`; wrong key → 401 `INVALID_API_KEY`; control rate limiter returns 429 after 10/min budget (observed: 200×7 then 429×5) [FACT] |
| S4 | Panic must survive restarts | **VERIFIED** | `/api/panic` writes `panic_state.json`; release via `{"release": true}` [FACT] |
| S5 | Audit trail integrity | **VERIFIED** | control_audit.jsonl HMAC chain; `/api/v1/security/status` reports `HMAC_CHAIN_VERIFIED` with ADMIN:1/CONTROL:1 key distribution [FACT] |
| S6 | Secrets in git | none committed beyond placeholder env names; `.gitignore` now also excludes `trading_pause_state.json` [FACT] |
| S7 | Live-order leakage | LIVE mode can never place orders: policy layer forbids and permanently disables LIVE on violation attempt (execution.py) [FACT] |
| P1–P5 | Procedural: daemon import-time side effects, test hermeticity, CWD-dependence | **FIXED** | dashboard autostart gated on `_AUTOSTART_SERVICES` (dashboard.py:6173); supervisor/shadow daemons no longer spawn under pytest; tests made CWD-independent (commit d8a4268) [FACT] |

## 4. Runtime verification (this pass) — not just unit tests

**[FACT]** Test suite: **1,146 passed / 6 skipped / 0 failed** across repeated full
runs (~84 s), green from inside and outside the repo root. ruff clean; mypy clean
on the changed core modules. All 6 skips are intentional: 2 opt-in live-network
peer tests and 4 artifact-absent validation guards that refuse to fabricate
evidence.

**[FACT]** Stress evidence (harnesses run from `/home/user/stress/`):
- `run_complete_e2e_system_test.py` → **13/13 stages PASS** (security invariants,
  market-data guard, registry governance, signal arbitration, advisory gate, risk
  manager, protection adapters, idempotency, execution state machine, timeout→UNKNOWN,
  emergency protection, walk-forward backtest, atomic persistence), 8.0 s.
- `battery_soak_runner.py` → 500 cycles / 4,000 signals, invariant
  “Cash + Positions + PnL Balanced” held every cycle, RAM 0.33→0.48 MB (no leak),
  8 trades executed, 3,992 risk-gate rejections (CONSECUTIVE_LOSS_LIMIT active).
- `scripts/run_long_simulation.py` → 100,000 events, 1,000 duplicates handled,
  667 gaps detected; no crash.

**[FACT]** Live dashboard boots (PAPER + FUTURES-misconfigured env both verified):
`/health` honest about capability (`engine_trading_capable=false`, reason:
governance gate has zero VALIDATED strategies — adx_ema retracted to OBSERVE_ONLY,
PF 0.8236). Full control-plane exercised over HTTP: panic trigger/release,
pause/resume, bad-key 401, no-key 503, rate-limit 429, security status.

**[FACT]** `bot.py` under unreachable-exchange conditions fails fast with a
classified error (auth vs network vs other) instead of a misleading credential
message (testnet_engine/service.py:201-223). [INFERENCE] In a deployment with
real testnet reachability, the same boot path proceeds to reconcile + discovery.

**[FACT]** Phase-2 runtime drills (all run live in this workspace):
- Control-plane concurrency race: 20 parallel pause/resume calls → state file
  stays valid JSON with actor attribution; HMAC audit chain still verifies;
  rate limiter caps at 10/min (429s after budget).
- Corruption drill: garbage writes into active-trades / pause / panic / audit
  files → all endpoints return 200 with graceful degradation; pause treats
  corruption as NOT-paused with loud logging (trading_pause.py:50-70).
- HTTP load: 330 requests across 11 endpoints, 20 concurrent → zero 5xx.
- Production supervisor drill (scripts/supervise_services.py): bot.py
  crash-looped 18× against unreachable exchange → every crash auto-recovered
  with backoff while the dashboard stayed up; SIGTERM → graceful cascade
  shutdown of all children.
- gunicorn (newly declared dependency) serves the app: /health 200, / 200.
- Production render.yaml env boot (FUTURES mode, no creds) → safe fallback
  to PAPER with fail-closed control endpoints.

## 4b. Phase-2 defects found and fixed (integrity of reported truth)

Stress testing exposed a *reporting-integrity* defect class — endpoints that
claim verification without performing it:

1. **Audit integrity hardcoded** — `/api/v1/security/status` returned
   `"integrity": "HMAC_CHAIN_VERIFIED"` without ever running the verifier
   (security_hardening.py:514 pre-fix). A corrupted audit ledger was silently
   reported VERIFIED. Fixed: `audit_trail_integrity()` reads the ledger and
   re-verifies on every call; verdicts now VERIFIED / BROKEN / DEGRADED:N
   corrupt lines / NO_AUDIT_RECORDS. Unsigned records are tampering evidence,
   not skips (security_hardening.py:302+).
2. **Fabricated compliance verdicts** — the daily compliance dossier hardcoded
   `zero_live_order_policy_honored: True`, markdown always printed
   “Live Order Invariant: PASS / Drawdown Corridor: PASS”, and the voice
   summary named a hardcoded “strategy_supertrend” winner
   (autonomy/compliance_reporting.py pre-fix). Fixed: all three are computed
   (drawdown from measured data, live-order flag from caller evidence,
   signature self-verified by recomputation; markdown prints PASS/FAIL
   accordingly; best strategy derived from the ledger, voice summary admits
   when no winner is known).
3. **Fabricated operational metrics** — `/api/ecosystem/report` returned
   hardcoded `trades_count=24, daily_pnl=68.50, max_drawdown=1.8`
   (api/master_control_api.py:92 pre-fix). Fixed: metrics computed from the
   real ledger with honest provenance fields (`metrics_source`: LEDGER /
   NO_DATA / LEDGER_ERROR; `drawdown_source`). A shadowing duplicate route in
   dashboard.py that read a hardcoded 2026-08-28 report file was removed.
4. **Stale advisory overlays lived forever** — AI-suggested parameter
   overrides never expired. Fixed: `AdvisoryParameterOverlay` now enforces a
   staleness TTL (default 72 h, `STRATEX_ADVISORY_MAX_AGE_HOURS`); expired or
   provenance-less overlays fall back to strategy defaults and report
   `overlay_status: STALE_IGNORED` (advisory_params.py; 8 dedicated tests;
   verified live via /api/advisory/state with a 200 h-old overlay).
5. **Order-dependent test** — tests/test_prompt8_stratex.py patched only
   config attrs; execution-module import-time bindings leaked through the OR
   fallback in `_resolve_execution_flags`. Fixed by pinning both (documented
   in-test).

## 4c. Phase-3 findings — full endpoint census (all 205 routes exercised live)

**[FACT]** Every GET route (165) and every POST route (40) was exercised
against a running dashboard instance with real keys:
- GET census: 158 OK · 4 AUTH fail-closed · 3 benign 404s · **0 server errors**.
- POST census: **0 unhandled 500s**; every protected route fails closed;
  emergency routes require explicit confirmation; the FRIDAY task protocol
  returns 403 FORBIDDEN for any order/execute action from external agents
  (deterministic execution-authority invariant held).
- Control-plane fuzz: garbage bytes / wrong content-type / 2 MB body /
  300-level nested JSON → all degrade to a clean empty payload, never crash,
  never echo into the audit trail.
- Header security: duplicate same-name headers with different keys fail
  CLOSED (invalid-key rejected); single valid key works in any casing.

Two phase-3 defects found and fixed:
1. **Single-threaded server + SSE = total lockout** — `app.run()` lacked
   `threaded=True` while `/api/stream` (dashboard.py:6107) is an SSE endpoint
   that holds its connection open forever. ONE open dashboard tab blocked
   every other request, including the same page's API calls. Reproduced live
   (census froze permanently after hitting /api/stream), fixed
   (dashboard.py `app.run(..., threaded=True)`), re-verified: /health answers
   in 2 ms while an SSE stream is open.
2. **ccxt routes leaked internals via 500** — `/api/v1/ccxt/ticker` returned
   500 with raw ccxt messages (internal URLs); `/arbitrage`, `/depth`,
   `/funding` had no error handling at all. Fixed: upstream/network failures
   → sanitized 503 UPSTREAM_UNAVAILABLE, bad input → 400, unexpected →
   generic 500 without leakage (api/ccxt_routes.py + 3 regression tests).

**[FACT]** All three stress harnesses re-run green on the final code:
e2e 13/13 stages · soak 500 cycles/4000 signals with balanced invariants and
flat RAM · 100,000-event simulation with 1,000 duplicates and 667 gaps
handled, no crash.

**[FACT]** Nautilus research endpoints are safe unauthenticated: the adapter
raises PermissionError on any live-routing attempt because
`LIVE_TRADING_ENABLED = False` is permanent
(stratex_nautilus_adapter/client.py:55-59).

## 5. Defects found and fixed this pass (code, not just reports)

1. pandas 3.x frequency API breakage → `to_pandas_freq` in
   stratex_freqtrade_adapter/data/downloader.py [FACT].
2. Non-hermetic/network tests → simulated Futuris peer mock + empty-df guard
   (quantum/service.py) [FACT].
3. Stale PAPER_SAFE_MODE test preconditions → both `execution.*` and `config.*`
   bindings pinned in-test (execution alone insufficient: fallback
   `or getattr(config, ...)`) [FACT].
4. validation_claims artifacts → loud skip + un-gitignored artifacts [FACT].
5. mass_backtester CSV round-trip: pandas 3 [ms] vs [us] index resolution →
   `as_unit("us")` on both paths [FACT].
6. Ledger dedup O(n²) → incremental index cache invalidated on rotation/truncation
   (execution.py:292+; 7 dedicated tests in tests/test_execution_ledger_cache.py) [FACT].
7. Pause decorative → durable `trading_pause.py` + engine enforcement
   (`_reject_paused_candidates` wiring test tests/test_trading_pause_wiring.py) [FACT].
8. Import-time daemon spawns racing tests → `__main__`/env-var autostart gate
   (dashboard.py:6173) + `pytest in sys.modules` guard (paper_runner_supervisor.py) [FACT].
9. Test-run pollution of repo root → logger.py writes `test_bot.log` under pytest;
   runtime state gitignored [FACT].
10. Misleading boot-failure diagnostics → auth/network/other classification [FACT].
11. CWD-dependent tests (static/index.html, app.js, shadow scheduler source,
    render.yaml/Dockerfile) → repo-root-relative reads [FACT].
12. Dependency hygiene → gunicorn declared, unused `ta` dropped,
    requirements.lock generated from the verified environment [FACT].

## 6. Remaining open items

- **[FIXED]** Stale advisory overlays: now TTL-enforced (see §4b item 4).
- **[HYPOTHESIS]** Full walk-forward regime pipeline at scale beyond the 13-stage
  e2e is not exercised in CI; recommend a scheduled long-horizon job on Render.
- **[FACT]** `engine_trading_capable=false` until a strategy passes governance as
  VALIDATED — this is the designed gate, not a defect; current best candidate
  adx_ema sits at OBSERVE_ONLY (PF 0.8236) and is correctly non-executable.
- **[INFERENCE]** Under extreme HTTP bursts the Flask dev server queues requests
  (a minority of 30-parallel requests time out client-side); production
  deployments should run gunicorn (now declared in requirements.txt) rather
  than the dev server.

## 7. How to run

```bash
python -m venv venv && venv/bin/pip install -r requirements.txt   # or -r requirements.lock
venv/bin/python dashboard.py            # dashboard + daemons on $PORT (default 5000)
venv/bin/python bot.py                  # engine daemon
venv/bin/python -m pytest tests/ -q     # 1129 passed / 6 skipped
```

Secrets hygiene: never commit real keys; unset keys ⇒ ephemeral random keys are
generated at startup and control endpoints fail closed for missing credentials.
