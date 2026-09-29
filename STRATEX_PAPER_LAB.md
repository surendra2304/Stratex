# Stratex historical paper lab

`stratex_paper_lab.py` evaluates the unregistered Donchian candidate, Swing,
ADX/EMA, Bollinger reversion, RSI burst, VWAP trend, and Supertrend candidates
against one local OHLCV CSV. It is separate from
`paper_forward_runner.py`: it does not create or update experiment configs,
forward signals, paper positions, or forward ledgers. It has no exchange order
client and cannot place Binance testnet or live orders.

## Input contract

The CSV must contain `timestamp,open,high,low,close,volume`. Supply a JSON
sidecar with a SHA-256 digest of the exact CSV bytes and real-data provenance:

```json
{
  "sha256": "<sha256 of candles.csv>",
  "source": "BINANCE_PUBLIC",
  "symbol": "BTCUSDT",
  "timeframe": "1h",
  "requested_start": "2025-01-01",
  "fetched_at_utc": "2026-09-29T00:00:00Z"
}
```

The lab verifies the digest, schema, finite OHLCV values, unique timestamps,
and candle high/low consistency. It sorts rows chronologically. It does not
download data, generate substitute data, or treat test fixtures as results.

## Run

```powershell
python stratex_paper_lab.py `
  --candles D:\research\BTCUSDT_1h.csv `
  --manifest D:\research\BTCUSDT_1h.csv.json `
  --output-dir D:\research\paper-lab-run-001 `
  --strategies donchian20 swing adx_ema bb_reversion rsi_burst vwap_trend supertrend
```

The only output is `paper_lab_report.json` in the requested output directory.
Forward experiment directories are rejected as output locations.

## Simulation rules and limits

Signals use a completed candle close; entry is at the following candle open.
One position at a time is allowed per candidate. Initial risk sizing is 1% of
current realized equity to the signal stop. Binance taker assumptions are
applied to both sides: 10 bps entry and exit fee, 5 bps entry and exit
slippage, and 1 bp spread on each side. Exposure is capped at 1x equity.
Drawdown is marked on completed candle closes while positions are open.
Intrabar stop and target ambiguity is resolved in favor of the stop. Donchian
exits also use the opposite 10-bar channel. Remaining positions close at the
final candle close.

Results are historical paper research only. They do not establish out-of-
sample profitability, forward validation, or permission to promote or execute
a strategy. Use chronological train/holdout periods and report costs and data
provenance when interpreting results. No performance result is bundled with
the capability.
