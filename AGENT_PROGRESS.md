# AGENT_PROGRESS — Stratex hardening session

## Overall goal
Make Stratex work in practice rather than merely pass its unit tests: exercise the real runtime
(HTTP routes, registry/promotion governance, autonomy state machine, execution/risk pipeline,
persistence, market-data handling) under stressful and adversarial edge cases, fix every defect
found with regression tests, keep all LIVE-trading blocks and fail-closed controls intact
(`LIVE_TRADING_ENABLED=False`, fail-closed auth), and keep `REPO_ANALYSIS.md` an evidence-based
report (path:line citations, `[FACT]`/`[INFERENCE]`/`[HYPOTHESIS]` labels, no fabricated metrics).
Work happens only on branch `arena/bb27a2eb-stratex`; every commit must pass the full suite.

## Checklist
1. [x] Reconcile local checkout with pushed tip `b5bffc5` (mixed reset; uncommitted work kept).
2. [x] Ecosystem state machine fail-closed semantics: UNKNOWN default, explicit health attestation
       for FULL_AUTONOMY, HALTED latched (only a named operator may exit), non-finite/invalid
       drawdown input fails closed; regression tests.
3. [x] Verify + commit pending uncommitted work (promotion evidence policy, registry integrity and
       locking, factory_winner_5 hash fix, ecosystem health truthfulness/route cleanup, seeded soak):
       full pytest, ruff, mypy; logical commits; push.
4. [x] Re-run runtime stress harnesses on current code (e2e, seeded soak, longsim); fix failures.
5. [x] Paper-trading lifecycle accounting (found by item 4 longsim/repro): forward runner never
       released margin (2 round trips starve all later entries) and did not persist SL/TP at entry;
       kill-switch flatten leaks margin; A/B engine reuses one event id for margin+PnL (fees/PnL
       never booked, drawdown guard blind) and counts CLOSED positions toward the open cap;
       close_position not idempotent; longsim harness skips PnL booking + writes into repo root.
6. [x] Re-run live HTTP endpoint census (all GET + POST routes) on current code; fix any 5xx.
       (commits ab4ec73 data honesty, 5b7186a http hardening; census now in-repo:
       `scripts/http_route_census.py`.)
7. [x] Stress pass — execution/risk numeric edge cases (NaN/inf/zero/negative price, size,
       balance, stop distance) through real sizing/protection code; fix fail-open paths + tests.
       (commit 8701a13; 228 regression cases in tests/test_numeric_edge_cases.py.)
8. [~] Stress pass — persistence robustness (corrupt/truncated/concurrent JSON + JSONL state
       stores: trades, ledger, baseline, advisory params); convert every shared-`.tmp` writer to
       `atomic_io`; fix crashes/data loss + tests.
9. [ ] Stress pass — malformed market data (NaN, duplicates, out-of-order, high<low, zero
       volume, short history) through indicator/strategy/signal path; fix + tests.
10. [ ] Census extension — GET query-parameter fuzz (wrong types, traversal, huge limits) on all
        167 GET routes; fix every 500 / unbounded response.
11. [ ] Type-safety pass — run mypy with every error code enabled + `check_untyped_defs`
        (602 findings at start: 171 attr-defined, 131 operator, 71 index, ...); fix real defects,
        tighten `mypy.ini` so they stay fixed.
12. [ ] Silent-failure pass — audit `try/except: pass` / blind `except Exception` (ruff S110/S112/
        BLE001) in trading-critical packages (risk, execution, paper_engine, testnet_engine,
        autonomy, api); log or fail closed; enforce in `ruff.toml` where feasible.
13. [ ] Timezone pass — naive `datetime.now()`/`utcnow()` (ruff DTZ, 257 findings) in
        persistence/trading/telemetry paths → aware UTC; enforce.
14. [ ] Library logging pass — `print()` in importable library modules (ruff T20) → logger;
        CLI entry points keep stdout; enforce per-file.
15. [ ] Lint-correctness pass — bugbear (B), RUF, SIM, RET, PERF, PT findings fixed; extend
        `ruff.toml` selection so regressions fail CI.
16. [ ] Modernization pass — pyupgrade (UP) typing/syntax modernization; enforce.
17. [ ] Coverage pass — measure coverage; add behavior tests for low-coverage critical modules
        (risk, execution, paper_engine, testnet_engine, autonomy).
18. [ ] Fabricated-output audit — remaining modules/routes that present random or hardcoded
        numbers as measurements; make them measured, labeled, or 503.
19. [ ] Refresh `REPO_ANALYSIS.md` with final verified counts and mechanically checked citations.
20. [ ] Final gate: full pytest + ruff + mypy green, census 0 failures, everything committed and
        pushed, this file at 100%, ≥30,000 changed lines since 5c66171 (user requirement).

## Current step
Item 8 — persistence robustness (atomic_io conversion of shared-`.tmp` writers, corrupt-state handling).

Line counter (user requirement, 2026-10-09: "only stop after genuinely modifying 30,000 lines"):
`git diff --shortstat 5c66171 HEAD` insertions+deletions, minus the 1,376 lines that were already
uncommitted when the requirement was given.

## Assumptions log
- 2026-10-08: Local HEAD was at base `edabb3c` after a workspace restore while the remote branch
  held `b5bffc5`; the working tree was a superset, so `git reset origin/arena/bb27a2eb-stratex`
  (mixed, no file changes) is the correct reconciliation.
- 2026-10-08: `run_high_frequency_cycle` has no production caller (tests only), so changing its
  return statuses cannot break a runtime loop; it is hardened for honesty/fail-closed behavior.
- 2026-10-08: Missing optimization artifacts are never regenerated or fabricated; dependent
  validation tests stay skipped with an explicit reason.

- 2026-10-09: Workspace was reset again (local HEAD back at `edabb3c`, venv/stress dirs gone);
  re-applied the mixed reset to `origin/arena/bb27a2eb-stratex` (5c66171), rebuilt the venv and
  `verify_commit.sh`. The census harness now lives in the repo (`scripts/http_route_census.py`)
  so it survives resets.
- 2026-10-09: "Modify 30,000 lines" is measured as git insertions+deletions after 5c66171,
  excluding the 1,376 pre-existing uncommitted lines; mechanical lint/modernization churn is
  reported separately from substantive fixes in the final summary. No repo-wide reformatting
  (`ruff format`) is used to inflate the count.
- 2026-10-09: Emergency endpoints accept a VALID key from an IP under the auth-failure block
  (shared proxy/NAT addresses would otherwise let anyone lock the operator out of the kill
  switch); invalid keys and all other endpoints stay blocked.
- 2026-10-09: Research-job HTTP endpoint only accepts BACKTEST (the worker always runs a
  BacktestEngine pass); agent-gateway jobs are reported as NOT_SCHEDULED because no worker
  consumes them.

- 2026-10-09: Profitability gate no longer invents a 0.5 win probability for invalid model
  confidence or unknown strategy types (consistent with the existing RULE_BASED rule); the
  test that pinned the old fallback was rewritten to pin the rejection.
- 2026-10-09: A trade result with non-finite PnL latches `RiskGate.accounting_fault` (entries
  refused until a named operator clears it) rather than being dropped.
- 2026-10-09: Spot entries require both SL and TP (`UNPROTECTED_ENTRY_REFUSED`); the only
  caller (testnet service) always passes both.
- 2026-10-09: `risk/live_enforcer.py` was a byte-for-byte copy of `risk/circuit_breakers.py`;
  `LiveRiskEnforcer` now lives only in live_enforcer.py and `CircuitBreakerEngine` only in
  circuit_breakers.py (re-export kept for compatibility).

## Notes
- Item 7 (8701a13): 1,606 passed / 6 skipped on a clean worktree; ruff + mypy clean. Every test
  file also passes when run standalone (isolation sweep over 160 files found 7 order-dependent
  files; fixed via the `pinned_testnet_mode` fixture and a per-test idempotency store).
- Item 6 census (5b7186a, in-repo harness, field-level fuzz from handler source): 201 rules
  (167 GET / 40 POST), 3,075 probes, 0 failures. admin GET 156×200/2×404/8×503; admin POST
  917×200, 15×201, 138×202, 1,232×400, 48×404, 33×405, 3×409, 117×503 (freqtrade data unavailable,
  testnet exchange unavailable); anonymous POST 102×401. Before fixes (same harness): 7 failures
  (nautilus bracket/risk float(dict), backtrader DataFrame("many"), agent-gateway job_id) then 6
  (FRIDAY idempotency_key unhashable). Suite: clean worktree 1,186 (ab4ec73) / 1,368 (5b7186a)
  passed, 6 skipped.
- venv lives at `/home/user/venv` (rebuilt from requirements.lock + ruff/mypy). `/home/user/verify_commit.sh <rev>` runs the full suite on a clean worktree.
- Use `/home/user/venv/bin/python -m pytest -q --tb=short -rs` from repo root (~85 s full suite); avoid
  piping pytest through grep.
- Item 5 (commit 3fed1b0, clean worktree 1,177 passed / 6 skipped): repro scripts in /home/user/stress/final4/margin/. Before fix: forward runner 2 fills / 38 'Insufficient cash' rejects with 0 open positions (used_margin 10,066.88); A/B 3 opened / 7 refused, realized_pnl 0.00 vs 140.40 in trade records. After: 40/40 fills, used_margin 0; A/B 10/10, booked == records. Longsim now asserts 5 invariants (all PASS: 100/100 trades, realized -40.00, equity 9,960.00). Note: `-p no:logging` disables caplog -> 1 spurious fixture error; don't use it for full runs.
- Item 4 results (HEAD a49c4bc): e2e 26 PASS markers / 0 FAIL, exit 0, 9.7 s; soak seed 20261007 -> 4,000 signals, 18 trades, 3,982 risk rejects, 3 open, peak RAM 0.47 MB (identical to prior runs); longsim 100,000 events, 1,000 duplicates, 667 gaps, exit 0 — but printed `Closed Trades: False`, `Final Equity: 10000.0` (harness never booked PnL) -> item 5.
- Item 3 commits (each verified on a clean worktree, 1,164–1,168 passed / 6 skipped): afdb59f governance, d9beb69 health, a49c4bc soak seed.
- Item 2 verified: full tree 1,168 passed / 6 skipped; clean worktree of commit 4f1802c 1,148 passed / 6 skipped.
- Previous verified baseline (before item 2 edits): 1,165 passed / 6 skipped / 0 failed.
  Skips: 2 opt-in live-network tests + 4 guards for absent `optimization_results/adx_ema_optimization.json`.
- Stress artifacts live outside the repo in `/home/user/stress/` (not committed).
- Never `pgrep -f dashboard.py` broadly (kills wrapper shell); match full command lines.
