# Stratex Master Upgrade Plan

**Owner:** Stratex engineering  
**Baseline branch:** `main`  
**Execution policy:** Small, reviewable feature branches; preserve existing work; no reset/force-push; never weaken fail-closed trade controls to make tests pass.

## Objective

Make Stratex auditable, reproducible, resilient, and safe to operate as the quantitative-trading component of FRIDAY Universe. Prioritize verified correctness and risk containment over arbitrary line-count targets or adding features before existing behavior is tested. No claim of production readiness is valid until the release gates below have evidence.

## Verified baseline (2026-10-22)

- Default branch: `main`, at `0322715e7e923dc7c61a422cadadf7d52f2c2553` when this plan was started.
- The most recent observed CI run, [run 39667425925](https://github.com/surendra2304/Stratex/actions/runs/39667425925), failed after lint succeeded. Pytest reported **2,211 passed, 6 skipped, 1 failed**.
- The specific failure was `tests/test_ai_advisor.py::TestStratexAIAdvisor::test_call_llm_direct_gemini_success`, which failed during patch setup with `ModuleNotFoundError: No module named 'google'`.
- `autonomy/ai_advisor.py` imports `google.genai` for its direct Gemini provider; `requirements.txt` did not declare the corresponding `google-genai` package. The failing test already exists and covers the intended SDK path.
- At the time of inspection, open PRs #32, #36, #37, #38, and #13 were still open. They must be reviewed on their actual current heads and CI state; they should not be assumed safe to merge just because their descriptions say tests passed.
- This baseline is a point-in-time snapshot, not a claim that every module, route, adapter, or deployment environment has already been audited.

## Workstreams and release gates

### 1. Dependency and CI recovery
- [x] Reproduce the first current CI failure from GitHub Actions logs.
- [x] Declare the direct Gemini SDK dependency in the runtime requirements and align the checked-in package inventory.
- [ ] Verify dependency resolution and the direct Gemini test through the PR CI run.
- [ ] Require the full pytest suite, PostgreSQL order-ledger integration test, and Docker build to complete successfully before merging.
- [ ] Reconcile the checked-in package inventory with the actual supported resolver/install workflow; avoid treating an incomplete package listing as a generated lock unless it is mechanically reproducible.

### 2. Repository inventory and executable architecture map
- [ ] Enumerate Python packages, entry points, HTTP routes, workflows, scripts, adapters, data stores, and test ownership.
- [ ] Map order-intent → risk checks → venue submission → acknowledgement/fill → reconciliation → durable ledger.
- [ ] Locate duplicate configuration sources and identify which files are canonical versus compatibility facades.
- [ ] Replace stale or unsupported documentation claims with source-backed descriptions and links to the tests that verify them.

### 3. Trading safety and order lifecycle
- [ ] Keep live trading disabled by default and ensure environment flags cannot bypass explicit execution policy.
- [ ] Verify fail-closed behavior on missing/stale market data, invalid/non-finite prices and quantities, venue errors, unknown submission outcomes, partial fills, and reconciliation drift.
- [ ] Prove order idempotency, close idempotency, exposure/margin accounting, stop-loss/take-profit persistence, and durable restart recovery with fault-injection tests.
- [ ] Test exchange adapters independently using deterministic mocks; no tests may place real-money orders.
- [ ] Verify that emergency halt is latched and that resumption requires the intended operator/policy path.

### 4. Market data and quantitative correctness
- [ ] Define and test timestamp/timezone policy, candle ordering and uniqueness, missing values, outlier handling, minimum history, and freshness thresholds.
- [ ] Add property/regression tests for indicators, sizing, PnL, costs, slippage, fees, drawdown, and portfolio exposure.
- [ ] Separate simulated, paper, testnet, and any real-venue evidence in data models and reporting.
- [ ] Ensure backtest and forward-validation outputs disclose their assumptions and do not report fabricated or unavailable measurements as observed performance.

### 5. Asynchronous pipelines and autonomy
- [ ] Bound task queues, worker counts, retries, payload sizes, and in-memory dead-letter storage.
- [ ] Implement explicit timeouts, interruptible shutdown, retry backoff, circuit-breaker recovery, and health signals with well-defined semantics.
- [ ] Test duplicate events, queue saturation, upstream outages, process restarts, stuck dependencies, and concurrent shutdown/enqueue.
- [ ] Ensure advisory-agent failure is observable and cannot be silently reinterpreted as a successful policy approval.

### 6. API, persistence, and security
- [ ] Audit authentication/authorization and every mutating route; validate query/body types and bound pagination and payload sizes.
- [ ] Check path traversal, unsafe deserialization, secret leakage, CORS behavior, webhook authentication/signing, and log redaction.
- [ ] Verify atomic writes, cross-thread/process coordination, corrupt-file recovery, schema migrations, and PostgreSQL transactions.
- [ ] Use bounded network timeouts and avoid swallowing exceptions in trading-critical paths without a structured observable outcome.

### 7. AI advisor and FRIDAY Universe integrations
- [ ] Validate Gemini, Inference, and OpenRouter provider routing with timeouts, retry policy, response-schema validation, provider attribution, and redaction tests.
- [ ] Treat model output as advisory only: deterministic execution/risk policy remains authoritative.
- [ ] Reject malformed JSON, non-finite/out-of-range confidence, unexpected verdicts, invalid types, and missing required fields safely.
- [ ] Contract-test Sentinel, Futuris, IntelX, Inference, Memora, and FRIDAY clients for unavailable, malformed, stale, and standby responses.

### 8. Static analysis, tests, and continuous delivery
- [ ] Preserve critical syntax/name linting and make it mandatory.
- [ ] Add and phase in ruff rule groups, type checking, and coverage thresholds only after baseline measurement and real findings are addressed.
- [ ] Test supported Python version(s), deterministic unit suites, PostgreSQL integration, runtime health/route census, and image build.
- [ ] Keep CI reports diagnostic: failures must point to a reproducible assertion/command, never silently skip a required verification.
- [ ] Avoid declaring 100% coverage/pass rate until a completed run records the actual counts.

### 9. Deployment, observability, and operational recovery
- [ ] Verify container user/permissions, signal handling, health/readiness distinctions, resource limits, image build and startup with missing upstream services.
- [ ] Define deployment, rollback, migration/restore, disaster recovery, and operator halt/resume procedures.
- [ ] Ensure monitoring reports measured state or explicit `UNAVAILABLE`, never invented health/performance numbers.
- [ ] Validate a clean deployment from documented configuration without laptop-local state or undeclared dependencies.

### 10. Final release audit
- [ ] Re-run all required checks on the exact PR head.
- [ ] Review the diff for secrets, unsafe live-trading paths, generated noise, unrelated changes, and untested claims.
- [ ] Merge only after required CI is green; then verify the merge commit and its main-branch CI.
- [ ] Record unresolved risks and evidence links; do not mark a workstream complete based on intent alone.

## Engineering rules

1. Never reset, force-push, or overwrite unrelated in-flight work.
2. Every behavior fix needs a regression test where practical; no placeholder implementations or success-shaped fallbacks.
3. An AI, market-data, or peer-agent response is untrusted input; execution policy must be deterministic and fail closed.
4. Do not turn off tests, reduce assertions, skip failing suites, or relax safety controls just to get green CI.
5. Every branch must identify its scope and base commit. Merge only after checking the exact head SHA, current diff, and validation evidence.
6. Maintain short execution notes below so a later session can resume from evidence rather than restart the audit.

## Execution log

### 2026-10-22 — dependency regression (active)
- **Finding:** CI test failure on missing `google` namespace during `@patch("google.genai.Client")`.
- **Change prepared:** Add `google-genai` as a declared runtime dependency and align its required dependencies in `requirements.lock`.
- **Branch:** `fix/ai-advisor-google-genai-dependency`.
- **Validation:** Pending pull-request CI. No claim is made yet that the new dependency installation, full suite, PostgreSQL integration, or Docker build has passed.
- **Next:** Inspect the run for the exact branch head; fix any further failures without weakening tests; only merge if every required gate succeeds.
