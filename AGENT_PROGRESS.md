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
6. [~] Re-run live HTTP endpoint census (all GET + POST routes) on current code; fix any 5xx.
7. [ ] Stress pass — execution/risk numeric edge cases (NaN/inf/zero/negative price, size,
       balance, stop distance) through real sizing/protection code; fix fail-open paths + tests.
8. [ ] Stress pass — persistence robustness (corrupt/truncated/concurrent JSON + JSONL state
       stores: trades, ledger, baseline, advisory params); fix crashes/data loss + tests.
9. [ ] Stress pass — malformed market data (NaN, duplicates, out-of-order, high<low, zero
       volume, short history) through indicator/strategy/signal path; fix + tests.
10. [ ] Refresh `REPO_ANALYSIS.md` with final verified counts and mechanically checked citations.
11. [ ] Final gate: full pytest + ruff + mypy green, everything committed and pushed,
        this file at 100%.

## Current step
Item 6 — rebuild endpoint census harness under /home/user/stress/census/ (prior one lost with workspace reset): start dashboard on a scratch copy with test auth, enumerate Flask url_map, hit every GET (SSE-aware) + POST with empty/malformed bodies, record 5xx; fix any server errors.

## Assumptions log
- 2026-10-08: Local HEAD was at base `edabb3c` after a workspace restore while the remote branch
  held `b5bffc5`; the working tree was a superset, so `git reset origin/arena/bb27a2eb-stratex`
  (mixed, no file changes) is the correct reconciliation.
- 2026-10-08: `run_high_frequency_cycle` has no production caller (tests only), so changing its
  return statuses cannot break a runtime loop; it is hardened for honesty/fail-closed behavior.
- 2026-10-08: Missing optimization artifacts are never regenerated or fabricated; dependent
  validation tests stay skipped with an explicit reason.

## Notes
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
