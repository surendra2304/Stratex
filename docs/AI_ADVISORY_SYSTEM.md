# AI-Universe Advisory Intelligence Subsystem

## 1. Architectural Overview & Intent

The **AI-Universe Advisory Subsystem** provides deliberative multi-agent quantitative advisory for the Algorithmic Trading Bot. It allows external intelligence from **AI-Universe** (a multi-agent deliberative reasoning platform with specialized roles such as `Trading Analyst`, `Strategist`, and `Critic`) to evaluate live trading telemetry and recommend strategy parameter calibrations.

---

## 2. Command Precedence & Safety Invariants

The system maintains a strict hierarchy of authority:

```
[ LEVEL 1: HIGHEST ]  SAFETY GATES (RiskGate, ProfitabilityGate, ExecutionPolicy, Kill-Switch)
                                  ▼
[ LEVEL 2: DIRECT  ]  OPERATOR / HUMAN / FRIDAY COMMANDS
                                  ▼
[ LEVEL 3: LOWEST  ]  AI-UNIVERSE DELIBERATIVE ADVISORY (Parameters ONLY, Non-Executable by default)
```

### Invariants:
1. **Advisory Only**: AI-Universe recommendations modify **strategy parameters** (e.g. indicator periods, threshold bounds, SL/TP multipliers), **never risk limits** (e.g. daily loss cap, portfolio exposure limit, max drawdown threshold).
2. **Safety Gate Precedence**: RiskGate, ProfitabilityGate, ExecutionPolicy, and the manual kill-switch take absolute precedence over all AI recommendations.
3. **Execution Path Isolation**: AI consultations **never** run in the low-latency per-trade execution loop. They run exclusively in an asynchronous background thread on a schedule (every 4 hours) or event triggers (loss streak / drawdown breach).
4. **Zero-Downtime Resilience**: If AI-Universe is unreachable, slow, or returning errors, the trading engine continues executing with the last validated parameters without interruption.
5. **Shadow Mode by Default**: All decisions are recorded to `advisory_log.jsonl` in shadow mode (`ADVISORY_SHADOW_MODE=True`). Parameter changes are only applied to the live overlay if shadow mode is explicitly turned off.

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

## 4. Components & File Layout

- **`ai_universe_client.py`**: Resilient HTTP client querying `POST /v1/trading/consult`. Fails soft (returns `None`) on timeouts or malformed JSON.
- **`advisory_gate.py`**: Evaluates `AIUniverseDecision` against immutable bounds and returns `AdvisoryResult` (`APPLY`, `REJECT`, or `SHADOW_LOG_ONLY`).
- **`advisory_ledger.py`**: Durable append-only audit log (`advisory_log.jsonl`) using atomic writes.
- **`advisory_telemetry.py`**: Assembles portfolio equity, PnL, win rate, profit factor, loss streaks, recent closed trades, active parameters, and market regime data.
- **`advisory_scheduler.py`**: Background supervisor thread triggering periodic (every 4 hours) and event-driven consultations.
- **`advisory_params.py`**: Dynamic runtime overlay (`AdvisoryParameterOverlay`) maintaining parameter overrides with full rollback capability and persistence (`advisory_params_state.json`).

---

## 5. Dashboard Endpoints

- `GET /api/advisory/recent?limit=10`: Returns recent advisory decisions, verdicts, confidence levels, and applied/rejected changes.
- `GET /api/advisory/state`: Returns the current runtime parameter overlay state, active overrides, and AI-Universe health status.
