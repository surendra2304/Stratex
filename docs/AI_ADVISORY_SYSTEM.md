# AI-Universe Advisory Intelligence Subsystem

## 1. Architectural Overview & Intent

The **AI-Universe Advisory Subsystem** provides deliberative multi-agent quantitative advisory for the Algorithmic Trading Bot. It allows external intelligence from **AI-Universe** (a multi-agent deliberative reasoning platform with specialized roles such as `Trading Analyst`, `Strategist`, and `Critic`) to evaluate live trading telemetry and recommend strategy parameter calibrations.

```
┌────────────────────────────────────────────────────────────────────────────┐
│                             AI-UNIVERSE SERVICE                            │
│           (Deliberative Multi-Agent Debates: Analyst, Strategist, Critic)  │
└─────────────────────────────────────┬──────────────────────────────────────┘
                                      │ POST /v1/trading/consult
                                      │ (Schedule: 4h / Trigger: Loss Streak)
                                      ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                    ALGORITHMIC TRADING BOT: ADVISORY INGRESS               │
├────────────────────────────────────────────────────────────────────────────┤
│ 1. Telemetry Collector (advisory_telemetry.py)                             │
│    └─ Gathers Equity, PnL, Win Rate, Profit Factor, Regimes, Active Params │
│                                                                            │
│ 2. HTTP Client (ai_universe_client.py)                                     │
│    └─ 120s Timeout, 2 Retries, Latency Measurement, Soft-Failure Isolation │
│                                                                            │
│ 3. Advisory Gate (advisory_gate.py)                                        │
│    ├─ Hardcoded ±20% Parameter Delta Bound                                 │
│    ├─ Position Size [0.5x, 1.5x] Clamping                                  │
│    ├─ Leverage Non-Increasing Invariant                                    │
│    ├─ Max 2 Parameter Modifications per Batch                              │
│    ├─ 4.0-Hour Live Cooldown Invariant                                     │
│    └─ FORBIDDEN_PARAMS (Risk limits, Drawdown limits, Keys, Trading mode)  │
│                                                                            │
│ 4. Evaluation Branch:                                                      │
│    ├─ IF ADVISORY_SHADOW_MODE=True:                                        │
│    │    ├─ Verdict = SHADOW_LOG_ONLY                                       │
│    │    ├─ Persist Audit Record to advisory_log.jsonl                      │
│    │    └─ Overlay remains EMPTY (Zero parameter mutation)                 │
│    │                                                                       │
│    └─ IF ADVISORY_SHADOW_MODE=False (Requires Double-Key):                 │
│         ├─ Verdict = APPLY                                                 │
│         ├─ Persist Audit Record to advisory_log.jsonl                      │
│         └─ Apply Overrides to AdvisoryParameterOverlay (advisory_params.py)│
└────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Command Precedence & Safety Invariants

The system maintains a strict hierarchy of authority:

```
[ LEVEL 1: HIGHEST ]  SAFETY GATES (RiskGate, ProfitabilityGate, ExecutionPolicy, Kill-Switch)
                                  ▼
[ LEVEL 2: DIRECT  ]  OPERATOR / HUMAN / FRIDAY COMMANDS
                                  ▼
[ LEVEL 3: LOWEST  ]  AI-UNIVERSE DELIBERATIVE ADVISORY (Parameters ONLY, Shadow Mode by Default)
```

### Invariants:
1. **Advisory Only**: AI-Universe recommendations modify **strategy parameters** (e.g. indicator periods, threshold bounds, SL/TP multipliers), **never risk limits** (e.g. daily loss cap, portfolio exposure limit, max drawdown threshold).
2. **Safety Gate Precedence**: RiskGate, ProfitabilityGate, ExecutionPolicy, and the manual kill-switch take absolute precedence over all AI recommendations.
3. **Execution Path Isolation**: AI consultations **never** run in the low-latency per-trade execution loop. They run exclusively in an asynchronous background thread on a schedule (every 4 hours) or event triggers (loss streak / drawdown breach).
4. **Zero-Downtime Resilience**: If AI-Universe is unreachable, slow, or returning errors, the trading engine continues executing with the last validated parameters without interruption.
5. **Shadow Mode by Default**: All decisions are recorded to `advisory_log.jsonl` in shadow mode (`ADVISORY_SHADOW_MODE=True`). Parameter changes are only applied to the live overlay if shadow mode is explicitly turned off.
6. **Double-Key Autonomy Safety**: Setting `ADVISORY_SHADOW_MODE=False` in environment will **refuse to start** unless `ADVISORY_AUTONOMY_CONFIRMED=True` is also provided.

---

## 3. AdvisoryGate Validation Bounds

The `AdvisoryGate` enforces hard-coded quantitative boundaries that cannot be bypassed via environment variables or model prompts:

| Safety Rule | Bound / Constraint | Behavior on Violation |
| :--- | :--- | :--- |
| **Max Parameter Delta** | Maximum **±20.0%** deviation from current value | Rejected with explicit delta violation reason |
| **Position Sizing Multiplier** | Clamped strictly between **0.5x and 1.5x** of current size | Scaled changes outside `[0.5x, 1.5x]` rejected |
| **Leverage Invariant** | Leverage may only **decrease or stay the same**, never increase | Leverage increase auto-rejected |
| **Forbidden Parameters** | `{"max_daily_loss", "max_drawdown", "live_trading_enabled", "api_key", "secret_key", "risk_limits"}` | Auto-rejected |
| **Batch Size Cap** | Maximum **2 parameter changes** per consultation | Batch exceeding 2 changes auto-rejected |
| **Cooldown Period** | Minimum **4.0 hours** between live applied changes | Premature modifications rejected |

---

## 4. How to Run Validation Tools

### Run Full Advisory Test Suite:
```bash
python -m pytest tests/test_advisory.py tests/test_shadow_mode.py tests/test_advisory_bounds.py tests/test_advisory_failures.py -v
```

### Run Advisory Log Quality Analyzer CLI:
```bash
python scripts/analyze_advisory_log.py --log advisory_log.jsonl --output advisory_quality_report.json
```

---

## 5. Interpreting Quality Reports

The analyzer produces an ASCII report and a structured `advisory_quality_report.json` document containing:
- **Consultations Summary**: Total lifetime, 24h, and 7-day consultation frequency.
- **Verdict Distribution**: Proportions of `SHADOW_LOG_ONLY`, `APPLY`, and `REJECT`.
- **Rejection Breakdown**: Categorization of rejected proposals (bounds violations, forbidden parameters, leverage increases, cooldown breaches).
- **Parameter Distributions**: Average and maximum percentage deltas per strategy parameter.
- **Confidence Histogram**: AI confidence distribution across five buckets (`0.00-0.20`, `0.21-0.40`, `0.41-0.60`, `0.61-0.80`, `0.81-1.00`).
- **Latency Percentiles**: p50, p95, and p99 round-trip consultation latency in milliseconds.
- **Contested Advisories**: Highlights any decision where the AI had high confidence ($> 0.70$) but was blocked by safety bounds.

---

## 6. How to Disable Shadow Mode (Double-Key Requirement)

> [!CAUTION]
> Disabling shadow mode allows AI-Universe to modify live strategy parameters during runtime execution (bounded by `AdvisoryGate`).
> This requires **both** environment keys to be set. Setting only one will result in a fatal startup rejection:

In `.env`:
```ini
# Primary Switch
ADVISORY_SHADOW_MODE="False"

# Secondary Confirmation Key (Required)
ADVISORY_AUTONOMY_CONFIRMED="True"
```

---

## 7. Dashboard Endpoints & UI

- `GET /api/advisory/recent?limit=10`: Returns recent advisory decisions, verdicts, confidence levels, and applied/rejected changes.
- `GET /api/advisory/state`: Returns the current runtime parameter overlay state, active overrides, and AI-Universe health status.
- `GET /api/advisory/health`: Returns instantaneous connection status to AI-Universe.
- `GET /api/advisory/stats`: Returns the full `advisory_quality_report.json` payload for client-side visualization.
- **Live Alert Banner**: If `ADVISORY_SHADOW_MODE` is disabled, the dashboard renders an active red warning banner alerting operators that live parameter mutation is underway.

---

## 8. Parallel A/B Testing Infrastructure

The platform provides a dual-arm forward validation framework (`paper_ab_runner.py` and `config_ab.py`) to benchmark AI-advised strategy execution against an unmodulated baseline under identical live market feeds.

### Architecture Diagram

```
                        ┌─────────────────────────────────────┐
                        │      Real-Time Market Candles       │
                        │    (1H Feeds: BTC, ETH, SOL, ...)   │
                        └──────────────────┬──────────────────┘
                                           │ Identical Ingestion
                                           ▼
                 ┌─────────────────────────────────────────────────┐
                 │       Parallel Dual Engine (paper_ab_runner.py) │
                 └────────┬───────────────────────────────┬────────┘
                          │                               │
            ┌─────────────┴─────────────┐   ┌─────────────┴─────────────┐
            ▼                           ▼   ▼                           ▼
┌───────────────────────────────┐       ┌───────────────────────────────┐
│     ARM A: CONTROL (BASELINE) │       │   ARM B: TREATMENT (AI-ADVISED│
├───────────────────────────────┤       ├───────────────────────────────┤
│ • Static Strategy Parameters  │       │ • Dynamic Strategy Overlay    │
│ • Zero AI Consultations       │       │ • AI-Universe Consultations   │
│ • State: paper_state_ctrl.json│       │ • State: paper_state_treat.json│
│ • Ledger: ledger_ctrl.jsonl   │       │ • Ledger: ledger_treat.jsonl  │
│ • Equity: equity_ctrl.jsonl   │       │ • Equity: equity_treat.jsonl  │
│ • 10% Max Drawdown Hard Stop  │       │ • 10% Max Drawdown Hard Stop  │
└──────────────┬────────────────┘       └──────────────┬────────────────┘
               │                                       │
               └───────────────────┬───────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│       Statistical Comparison Engine (scripts/compare_ab_performance) │
├──────────────────────────────────────────────────────────────────────┤
│ • Welch's Two-Sample t-test (p < 0.05)                               │
│ • Mann-Whitney U Distribution Test (Rank Sum)                        │
│ • Bootstrap 95% Confidence Intervals                                 │
│ • Comparative Metrics: PnL, Profit Factor, Sharpe, Max Drawdown      │
│ • Output: ab_test_report.md & /api/ab/results                        │
└──────────────────────────────────────────────────────────────────────┘
```

### How to Run A/B Testing

1. **Launch the Dual Paper Runner**:
   ```bash
   python paper_ab_runner.py
   ```
2. **Execute Performance & Statistical Evaluation**:
   ```bash
   python scripts/compare_ab_performance.py --plot
   ```

### Decision Matrix for Promoting AI Advisory to Testnet

To promote AI advisory parameter modulations from Paper A/B testing into Binance Futures Testnet execution, all four criteria in the decision matrix must be satisfied simultaneously:

| Criterion | Pre-Registered Target | Required Verification |
| :--- | :--- | :--- |
| **Minimum Duration** | $\ge 30$ Calendar Days | Pre-registered experiment duration |
| **Trade Sample Size** | $\ge 30$ Trades per Arm | Minimum sample for central limit statistical validity |
| **Statistical Significance** | $p < 0.05$ (Welch's t-test or Mann-Whitney U) | Confirms performance delta is not random noise |
| **Economic Advantage** | Treatment Profit Factor $\ge 1.20$ AND Treatment Return > Control Return | Confirms genuine positive edge after friction |

---

## 9. Binance Testnet Advisory Integration

The AI Advisory subsystem integrates seamlessly into the live Testnet execution daemon (`bot.py` / `testnet_engine/service.py`) via `testnet_advisory_scheduler.py`.

### Architecture & Control Flow

```
                               ┌────────────────────────────────┐
                               │   bot.py (TestnetService)      │
                               └───────────────┬────────────────┘
                                               │
                        ┌──────────────────────▼──────────────────────┐
                        │   TESTNET_ADVISORY_ENABLED == True ?        │
                        └──────────────┬──────────────────────────────┘
                                       │
                      ┌────────────────┴────────────────┐
                      │ YES                             │ NO
                      ▼                                 ▼
       ┌──────────────────────────────┐   ┌──────────────────────────────┐
       │ TestnetAdvisoryScheduler     │   │ Standard Testnet Operation   │
       │ (testnet_advisory_scheduler) │   │ (No Advisory Calls)          │
       └──────────────┬───────────────┘   └──────────────────────────────┘
                      │
                      ├───────────────────────────────────────────────┐
                      ▼                                               ▼
       ┌──────────────────────────────┐                ┌──────────────────────────────┐
       │ Periodic Cycle (Every 4.0h)  │                │ Event/API Trigger:           │
       │ • Gathers Testnet Telemetry  │                │ • POST /api/testnet/advisory │
       │ • Calls AI-Universe Consult  │                │   /trigger                   │
       └──────────────┬───────────────┘                └──────────────┬───────────────┘
                      │                                               │
                      └───────────────────────┬───────────────────────┘
                                              ▼
                               ┌──────────────────────────────┐
                               │     AdvisoryGate Validation  │
                               │  (±20% Delta, Forbidden List)│
                               └──────────────┬───────────────┘
                                              │
                      ┌───────────────────────┴───────────────────────┐
                      │                                               │
                      ▼                                               ▼
       ┌──────────────────────────────┐                ┌──────────────────────────────┐
       │ If SHADOW Mode (Default):    │                │ If APPLY Mode:               │
       │ • Append to advisory_log     │                │ • Apply to runtime overlay   │
       │ • verdict=SHADOW_LOG_ONLY    │                │ • Dynamic parameter mutate   │
       │ • Zero parameter mutation    │                │ • Circuit breaker active     │
       └──────────────────────────────┘                └──────────────┬───────────────┘
                                                                      │
                                                       ┌──────────────▼───────────────┐
                                                       │ Max Drawdown >= 15.0%?       │
                                                       │ 🚨 Trip Circuit Breaker      │
                                                       │ 🚨 Revert all overrides      │
                                                       │ 🚨 Force back to SHADOW mode │
                                                       └──────────────────────────────┘
```

### Configuration Variables (`.env`)

```ini
# Enable AI advisory background service in Testnet engine
TESTNET_ADVISORY_ENABLED="False"

# Shadow mode toggle (True = Log-only audit, False = Live parameter modulation)
TESTNET_ADVISORY_SHADOW_MODE="True"

# Hard drawdown ceiling for AI advisory on Testnet (15% default)
TESTNET_ADVISORY_MAX_DRAWDOWN_PCT="0.15"
```

### Safety Invariants & Limits
1. **Circuit Breaker**: If Testnet account drawdown reaches or exceeds **15.0%**, the circuit breaker trips immediately, disabling live AI advisory, forcing `shadow_mode=True`, and resetting all runtime parameters to clean baseline defaults (`overlay.reset_to_defaults()`).
2. **Precedence Hierarchy**: Existing `RiskGate`, `ProfitabilityGate`, and daily loss limits take absolute priority and hard-block orders before parameter modulations take effect.
3. **Sizing & Leverage**: Position sizing changes are clamped to $[0.5\times, 1.5\times]$, leverage increases are strictly blocked, and max delta is capped at $\pm 20.0\%$.
4. **Forbidden Variables**: Core risk thresholds (`max_daily_loss`, `max_drawdown`, `live_trading_enabled`, `api_key`, `secret_key`) are permanently forbidden.

### API Endpoints
- `GET /api/testnet/advisory/status`: Current scheduler state, mode (`DISABLED`, `SHADOW`, `APPLY`), circuit breaker status, and live testnet equity/drawdown metrics.
- `GET /api/testnet/advisory/log?limit=10`: Recent advisory decisions recorded for testnet.
- `POST /api/testnet/advisory/trigger`: Triggers an instantaneous consultation cycle.
- `POST /api/testnet/advisory/toggle`: Toggles between `SHADOW` and `APPLY` modes (requires `X-BOT-API-KEY`).

