# ALGORITHMIC TRADING BOT — COMPLETE PROJECT BRIEF FOR AI AGENTS
> Version: 2026-08-22 (Day 9) · Maintained per `DIARY_SPEC.md` · Read `AGENTS.md` before modifying anything.

---

## 1. WHAT THIS PROJECT IS

A multi-strategy **quantitative trading and forward-validation platform** (Python 3.11) that trades on **Binance Spot TESTNET only**. Live real-money trading is **permanently blocked** in code and by API. It runs a Flask dark-mode dashboard on Render (Frankfurt) that mirrors the testnet state in real time.

- **Repository**: https://github.com/surendra2304/algorithmic-trading-bot (branch `master`, auto-deploys to Render)
- **Live dashboard**: https://algorithmic-trading-bot-fra.onrender.com
- **Local path (this machine)**: `D:\MT5\python_bot` · venv: `D:\MT5\.venv` (Python 3.11.9)
- **Key health URLs**: `/health`, `/api/status`, `/api/engine-health`, `/api/scanner`, `/api/trades`

### HARD SAFETY RULES (from AGENTS.md — never violate)
1. **TESTNET ONLY** — never enable live trading or remove safeguards (`LIVE_TRADING_ENABLED=False` is enforced; POST `/api/settings` with live-trading keys → 403).
2. **NO CREDENTIALS** in code/logs/commits (`.env` is git-ignored).
3. **NO FABRICATION** of market or trade data.
4. **NO RISK WEAKENING** to generate more trades. Tightening is allowed; loosening is not.
5. **NO FALSE CONFIDENCE** — verify production health against live endpoints, not just local tests.

---

## 2. ARCHITECTURE

Two execution tracks, selected by `TRADING_MODE` (`PAPER` | `TESTNET`):

| Layer | Files | Role |
|---|---|---|
| **Testnet engine** (ACTIVE) | `bot.py` → `testnet_engine/service.py` (`TestnetService`) | Scanner → signals → gates → real testnet orders |
| Market data | `testnet_engine/market_scanner.py`, `data.py`, `data_client.py` | Websocket kline streams + REST; 250-bar cache with **production warm-seeding** |
| Strategies | `strategy_*.py` | Only `strategy_adx_ema.py` (V2-spot rev3) is registry-VALIDATED |
| Gates | `testnet_engine/profitability_gate.py`, `risk_gate.py`, `protection.py` | EV≥5bps gate, 5-position/2%-daily-loss/exposure limits, OCO SL/TP |
| Governance | `config_strategy.PRODUCTION_STRATEGY_REGISTRY` + `governance_filter_strategies()` / `governance_validated_assets()` in service.py | Only VALIDATED strategies/assets can trade |
| Dashboard | `dashboard.py` + `static/` (app.js, ui-compat.js, style.css) | 10-view futuristic UI; mirrors testnet truth |
| Paper track | `paper_forward_runner.py`, `paper_engine/` | 30-day forward experiment framework (currently stalled) |
| Research | `research/upgrade_2026_08/` | Evidence engine for all strategy decisions |
| Tests | `tests/` — **529 passing** | incl. chaos, adversarial causality, accounting invariants |

**Signal path**: 4h candle closes → `get_signal()` → LONG_ONLY check → BTC-regime gate → profitability gate (EV math) → risk gate (positions/exposure/daily loss) → market order + OCO SL/TP on exchange → telemetry/ledger.

---

## 3. CURRENT PRODUCTION STATE (as of 2026-08-22)

- **Engine**: LIVE on Render, `adx_ema` @ 4h, 7 validated symbols: BTC, ETH, BNB, SOL, XRP, LINK, INJ (all USDT)
- **Balance baseline**: $11,609.29 USDT (exchange-authoritative; LINKUSDT position closed at profit, all stale orders cleared)
- **Ledger**: reset to clean on 2026-08-22 (pre-reset history archived in `backup/reset_2026-08-22_pre_v2spot/`, git-ignored)
- **Expected cadence**: ~4 signals/month; every trade goes through all gates
- **Historical realized performance** (pre-upgrade era, 30 trades): PF 0.40 — caused by now-fixed governance leak and V1 strategy defects

---

## 4. THE LIVE STRATEGY — ADX+EMA "V2-SPOT REV3" (frozen config: `ADX_EMA_STRATEGY_V2`)

Long-only (spot cannot short). Timeframe: 4h. Universe: 7 validated USDT pairs.

**Entry A — Crossover**: EMA(20) crosses above EMA(50) AND close > EMA(200) AND ADX(14) > 20.
**Entry B — Qualified retest**: within 10 bars after a qualified crossover (that did NOT already enter), first bar whose low touches EMA(20) (≤ EMA20×1.002) with bullish close (close>open, close≥EMA20), while trend still aligned (close>EMA200, EMA20>EMA50, ADX>20).
**Regime gate (service-level)**: BUY allowed only when BTCUSDT 4h close > its EMA200 (`compute_btc_regime()`; fails open with warning if BTC data unavailable).
**Exits**: SL = 3×ATR(14), TP = 3×ATR(14) via exchange OCO. rr = 1.0.
**Sizing**: 0.5% equity risk per trade (`MAX_TESTNET_RISK_PER_TRADE`), max 5 open positions, 2% daily loss halt, 5% max drawdown kill switch.
**REMOVED (do not re-enable)**: V1 "established trend pullback" entry (net-negative 2021-2026, PF 0.85 with it on); 1h timeframe (OOS PF 0.38-0.73 — friction destruction, Bug #45).

**Validated evidence** (`research/upgrade_2026_08/` — 256 grid variants, 2021-2026, 74k+ bars/asset, 31 bps round-trip friction, next-candle-open entries, SL-first intrabar):

| Config | IS PF | OOS PF | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|
| V1 as-coded (ADX20+pullback) | 0.99 | 0.85 | 1.19 | 1.04 | 0.58 |
| **V2-spot rev3 (live)** | **2.12** | **2.36** | **2.27** | **2.57** | **2.08** |

OOS 2024-01→2026-08: 136 trades, win 0.551, +216 bps/trade at 1% risk. Reproduce: `python research/upgrade_2026_08/param_study.py` and `expansion_study.py`.

---

## 5. GOVERNANCE SYSTEM (why the bot stopped losing)

`PRODUCTION_STRATEGY_REGISTRY` in `config_strategy.py` classifies every strategy. Only `adx_ema` = VALIDATED; aggressor/scalper/supertrend/swing/ml = DISABLED with documented reasons (1m scalps mathematically cannot beat 31 bps friction).

- `governance_filter_strategies()` — engine loads ONLY VALIDATED strategies, pinned to their validated timeframe.
- `governance_validated_assets()` — scanner universe restricted to validated assets, with discovery backfill (`discovery.get_symbol_filters()`) for validated assets missed by top-N volume ranking.
- Heartbeat + `/api/strategy-metrics` report the **actually loaded** (post-gate) strategies with a 5-minute freshness guard — never raw config.

---

## 6. ALL DEFECTS FIXED THIS SESSION (DIARY bugs #39–#51)

| # | Defect | Fix |
|---|---|---|
| 39 | `requirements.txt` missing `statsmodels`, `pyyaml` | added |
| 40 | `last_known_price` NameError wedged paper-runner daily rollover | None-guard |
| 41 | **Governance leak**: DISABLED strategies + unvalidated symbols traded live (~85% of losses) | runtime registry enforcement |
| 42 | V1 pullback entry net-negative 2021-2026 | removed in V2 |
| 43 | Validated params assumed both sides; engine is LONG_ONLY — long-only PF was 0.63 | dedicated long-only study → ADX20 + regime gate |
| 44 | Reset script reused stale equity as baseline | explicit target-balance arg |
| 45 | 1h timeframe structurally unprofitable | studied & permanently rejected |
| 46 | **17 UI handler functions didn't exist** — all markets/settings/risk/system buttons dead | `static/ui-compat.js` implements all |
| 47 | Strategy metrics showed raw-config status (all ACTIVE) | heartbeat-truthful + freshness guard |
| 48 | Strategy badge hardcoded ACTIVE | real status |
| 49 | Settings POST silently ignored unknown keys with "success" | 10-key validated whitelist, unknown→400 |
| 50 | **CRITICAL: testnet holds only ~101 bars of 4h history** — EMA200 ran under-warmed | pagination + production warm-seeding (cache 101→249 bars) |
| 51 | Stress remnants: MAX_OPEN_TRADES=30, 100 trades/3h | aligned to 5 / 30-trade-30-day gate |

---

## 7. HOW TO WORK ON THIS PROJECT

```bash
cd D:/MT5/python_bot
D:/MT5/.venv/Scripts/python.exe -m pytest tests/ -q   # MUST run TWICE, all 529 pass
node -c static/app.js && node -c static/ui-compat.js   # frontend syntax
D:/MT5/.venv/Scripts/python.exe -m pyflakes <files>    # static analysis
D:/MT5/.venv/Scripts/python.exe dashboard.py           # local dashboard (needs .env)
```
- **Deploy**: push to `master` → Render auto-builds (~6 min engine boot; brief "OFFLINE" is normal).
- **Diary**: `DIARY.md` is append-only, chronological DAY entries; sequential bug numbers (next: #52); verification section must cite evidence tiers (Tier 1 = live Binance testnet API). Follow `DIARY_SPEC.md`.
- **Commits**: conventional style (`fix(...)`, `feat(...)`, `docs(...)`).
- **Reset stats**: `python reset_all_statistics.py [target_balance]` (archives nothing — back up ledgers to `backup/` first).
- **Env**: `.env` needs `API_KEY`, `SECRET_KEY` (Binance **testnet** keys), `TRADING_MODE=TESTNET`, `TESTNET_ENABLED=true`.

### Querying the testnet directly (Tier-1 evidence)
```python
from dotenv import load_dotenv; load_dotenv(dotenv_path=".env")
import os
from binance.client import Client
c = Client(os.getenv("API_KEY"), os.getenv("SECRET_KEY"), testnet=True)
c.get_account(); c.get_open_orders(); c.get_my_trades(symbol="BTCUSDT", limit=20)
```

---

## 8. HONEST LIMITATIONS & CAVEATS

1. **PF 2.36 is backtest evidence, not proof.** Deployment gates remain: ≥30 genuine forward trades, PF ≥ 1.20 measured live, ≥30 days duration. Live trading stays blocked.
2. **Spot cannot short.** The strategy's short side was the strongest edge (OOS PF 3.14). Capturing it requires migrating execution to Binance Futures testnet — significant architecture change, not yet approved/attempted.
3. **2026 regime is choppy** (long-only PF 1.05 without the retest entry; 2.08 with it). Expect breakeven-to-modest near-term.
4. **Testnet data quirks**: only ~17 days of 4h kline history (hence warm-seeding from production public data for indicator seeding only); testnet volumes/prices are synthetic-ish.
5. **Paper forward runner is stalled** (silent since Aug 18) — restart it if the statistical track matters.
6. In-app browser automation could not attach in the last environment; frontend verification was done by auditing served files (51/51 handlers bound) — a human click-through is still worthwhile.

---

## 9. RECOMMENDED NEXT STEPS (in priority order)

1. Let the testnet ledger accumulate (~4 trades/month; check `/api/trades` weekly). Measure real PF after ≥30 trades.
2. Restart `paper_forward_runner.py` for the parallel statistical track.
3. Consider the Futures-testnet migration project to unlock short edge (needs explicit operator approval — large change).
4. Re-validate SL/TP ATR multipliers quarterly against fresh data via the existing study scripts (never tune on the live ledger — curve-fitting).
5. Optional UX: notification dropdown panel, persisted settings storage across restarts, per-view auto-refresh indicators.

---

## 10. FILE MAP (quick reference)

```
bot.py                      testnet engine entry point
testnet_engine/service.py   TestnetService — scan, gates, orders, heartbeat, governance
testnet_engine/market_scanner.py   websocket streams, 250-bar cache, warm-seeding
testnet_engine/profitability_gate.py / risk_gate.py / protection.py
strategy_adx_ema.py         THE validated strategy (V2-spot rev3, config-driven)
config_strategy.py          ADX_EMA_STRATEGY_V2 + PRODUCTION_STRATEGY_REGISTRY + evidence tables
config.py                   runtime knobs (5 positions, 0.5% risk, cooldowns)
dashboard.py                Flask API (52 routes) + static serving
static/                     index.html (10 views), app.js, ui-compat.js, style.css
research/upgrade_2026_08/   param_study.py, expansion_study.py, results.jsonl (+git-ignored data/)
tests/                      529 tests incl. test_governance_enforcement, test_strategy_v2_upgrade
DIARY.md / DIARY_SPEC.md    append-only engineering diary (DAY 1..9, bugs #01..#51)
reset_all_statistics.py     statistics reset with explicit target balance
backup/                     pre-reset ledger archives (git-ignored)
```
