# Stratex Trend Study — November 2026

## Scope and data

Stratex ran 23 intraday strategies on Binance USD-M futures. This study tested whether any
configuration had positive expectancy net of fees. It found none intraday and one robust daily
configuration, which is now live.

| Item | Detail |
|---|---|
| Data | Real Binance USD-M klines (demo-fapi endpoint, verified to match live fapi within ~0.1%) |
| Universe | 16 perpetuals: BTC, ETH, SOL, BNB, XRP, ADA, DOGE, AVAX, LINK, LTC, DOT, BCH, ATOM, UNI, TRX, NEAR |
| Range | 2019-09-23 to 2026-11-04 |
| Bars per symbol | 2600 daily / 10000 15m / 3000 4h |
| Entry | Signals on completed bars only, entry at next bar open |
| Stops | SL-first when SL and TP are hit in the same bar; gaps fill at the open |
| Costs | 10 bps round trip (8 bps taker fees + 2 bps slippage) |
| Lookahead | None |

## Finding 1 — Intraday dies to fees

15m, 3 symbols, all 23 registry strategies, 1000 bars:

| Metric | Value |
|---|---|
| Trades | 56 |
| Win rate | 30.4% |
| Average | -0.190R |
| Total | -10.62R |
| Max drawdown | -14.76R |

Median stop distance was 0.23% BTC, 0.36% ETH, 0.55% SOL, so the 8 bps taker round trip cost
0.15–0.35R per trade (about 0.21R average) — larger than any measured edge.

| Timeframe | Result |
|---|---|
| 1h trend prototypes | Best variant `dc20_2atr_rr3`: +0.059R/trade over 207 trades, but out-of-sample half -0.041R (PF 0.95), rejected as not robust |
| 4h prototypes | Mostly negative; best (dc55_3atr_rr3) +0.109R over only 54 trades, IS -0.208R / OOS +0.450R — unstable sample |
| Daily | Fees are ~0.007R because stops are 15–25% of price |

## Finding 2 — Daily trend, but the exit rule decides everything

16 symbols, full sample (7 years):

| Variant | Trades | Win% | Net R/trade | PF | Total R | Max DD R |
|---|---|---|---|---|---|---|
| fixed 3R target | 228 | 32.0 | +0.012 | 1.02 | +2.7 | -28.8 |
| Donchian(10) channel exit | 429 | 28.9 | +0.094 | 1.25 | +40.4 | -45.1 |
| chandelier 4xATR | 353 | 34.8 | +0.391 | 1.88 | +138.0 | -20.5 |
| EMA20 close exit | 444 | 36.0 | +0.302 | 1.95 | +134.3 | -18.6 |
| chandelier4 + EMA20 | 446 | 35.9 | +0.342 | 2.10 | +152.5 | -17.5 |

Walk-forward (first 60% / last 40% by time) and the recent regime:

| Variant | OOS net R/trade | OOS PF | 2024+ net R/trade | 2024+ PF | 2024+ trades |
|---|---|---|---|---|---|
| fixed 3R target | -0.288 | 0.64 | not measured | not measured | not measured |
| chandelier 4xATR | -0.025 | 0.94 | -0.030 | 0.93 | 188 |
| EMA20 close exit | +0.030 | 1.09 | +0.029 | 1.09 | 236 |
| chandelier4 + EMA20 | +0.021 | 1.06 | +0.024 | 1.07 | 236 |

Year-by-year net R/trade for the EMA20 exit:

| Year | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|
| Net R/trade | +0.73 | +1.05 | +0.12 | +0.67 | +0.04 | -0.23 | +0.35 |

Losing years are expected in trend following; 2025 was the only clearly negative year.

## Finding 3 — Deployed basket

Deployed 10-symbol basket (BTC, ETH, SOL, BNB, XRP, ADA, DOGE, AVAX, LINK, LTC), EMA20 exit,
2020-06-02 to 2026-10-30:

| Metric | Value |
|---|---|
| Trades | 264 |
| Win rate | 37.1% |
| Net R/trade | +0.342 |
| PF | 2.13 |
| Total | +90.3R |
| Max drawdown | -13.4R |
| Frequency | 41.2 trades/year |

At 0.5% equity risk per trade: +52.8% total return, CAGR +6.8%, max drawdown -6.5% over 6.4 years.

| Window | Trades | Win% | Net R/trade | PF | Total R |
|---|---|---|---|---|---|
| 2024 onward | 140 | 36.4 | +0.096 | 1.32 | +13.5 |
| In-sample | 141 | not measured | +0.632 | 3.19 | not measured |
| Out-of-sample | 123 | not measured | +0.010 | 1.03 | not measured |

## Live configuration (deployed 2026-11-04)

Strategy `strategy_trend_daily`: Donchian(20) breakout, EMA50 > EMA200 regime filter, stop
2.5xATR(14) exchange-side, exit on daily close crossing EMA(20), 6xATR safety take-profit.

| Setting | Value |
|---|---|
| Timeframe | 1d |
| Symbols | 10 majors |
| Scan interval | 300s |
| Risk per trade | 0.5% of equity |
| Max notional per position | 10% of equity |
| Leverage | 3x |
| Positions per venue | 5 |
| Per-symbol cooldown after exit | 900s |

Gates: min R:R 1.2, min stop distance 0.8%, fee model 0.0008 round trip, min net expectancy 0.05R,
fail-closed on missing metrics, direction sanity, pause/panic checks.

## Caveats

- 41 trades/year on the basket: per-year samples are small and confidence intervals are wide.
- The recent-regime edge is thin (+0.03 to +0.10R per trade); the system is expected to be roughly flat in chop and to earn in sustained trends.
- Results come from one asset class (crypto perpetuals) over one 6-year window that included two bull markets.
- No funding rates, no liquidation modelling, no order-book impact; slippage is modelled as a flat 2 bps.
- Prior intraday systems remain in the repo but are disabled by the allowlist; they were net-negative after fees.
- The "OOS" windows are still historical backtests, not forward paper trading.
