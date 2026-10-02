"""Run the shipped adx_ema configuration inside real drawdown episodes.

    python research/validation/run_regime_holdout.py BTCUSDT 1h 2023-01-01

Prints one line per episode and writes a dated JSON report beside the other
validation artefacts, so the verdict can be re-read and re-checked later instead of
living in a scrollback buffer.

Exits non-zero unless every episode that produced a verdict survived it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import config_strategy as cfg  # noqa: E402
import strategy_adx_ema  # noqa: E402
from research.validation.holdout_validation import load_real_candles  # noqa: E402
from research.validation.regime_holdout import (  # noqa: E402
    DRAWDOWN_THRESHOLD_PCT,
    evaluate_regimes,
    write_report,
)

REPORT_DIR = Path(__file__).resolve().parents[1] / "validation"


def signal_fn(window) -> str:
    result = strategy_adx_ema.get_signal(window)
    side = getattr(result, "side", None)
    return side if side in ("BUY", "SELL") else ""


def main() -> int:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    timeframe = sys.argv[2] if len(sys.argv) > 2 else "1h"
    start = sys.argv[3] if len(sys.argv) > 3 else "2023-01-01"

    df, source, digest = load_real_candles(symbol, timeframe, start)
    print(f"{symbol} {timeframe}: {len(df)} bars {df.index[0]} .. {df.index[-1]}")
    print(f"  source: {source}  sha256={digest[:12]}")
    print(f"  looking for drawdown episodes of >= {DRAWDOWN_THRESHOLD_PCT:.0f}% peak-to-trough\n")

    summary = evaluate_regimes(
        df=df,
        signal_fn=signal_fn,
        sl_atr_mult=cfg.ADX_EMA_STRATEGY_V2.get("SL_ATR_MULTIPLIER", 3.0),
        tp_atr_mult=cfg.ADX_EMA_STRATEGY_V2.get("TP_ATR_MULTIPLIER", 3.0),
        atr=strategy_adx_ema.compute_atr(df, period=14),
        fee_rate=cfg.BACKTEST_ASSUMPTIONS["FEE_RATE"],
        slippage_rate=cfg.BACKTEST_ASSUMPTIONS["SLIPPAGE_RATE"],
        risk_per_trade=0.005,
    )
    summary["symbol"] = symbol
    summary["timeframe"] = timeframe
    summary["data_source"] = source
    summary["data_sha256"] = digest
    summary["strategy_id"] = "adx_ema (shipped config)"

    for entry in summary["results"]:
        window = entry["window"]
        from research.validation.regime_holdout import DrawdownWindow, RegimeResult

        print(
            RegimeResult(
                window=DrawdownWindow(**window),
                trades=entry["trades"],
                expectancy=entry["expectancy"],
                profit_factor=entry["profit_factor"],
                win_rate=entry["win_rate"],
                max_drawdown_pct=entry["max_drawdown_pct"],
                net_return_pct=entry["net_return_pct"],
                verdict=entry["verdict"],
                reasons=entry["reasons"],
            ).line()
        )

    print(
        f"\n  episodes: {summary['episodes_found']}   "
        f"with a verdict: {summary['episodes_with_verdict']}   "
        f"survived: {summary['episodes_survived']}   failed: {summary['episodes_failed']}"
    )
    print(f"  REGIME VERDICT: {summary['regime_verdict']}")

    path = write_report(summary, REPORT_DIR)
    print(f"  report written: {path}")
    return 0 if summary["regime_verdict"] in {"SURVIVED_ALL_MEASURED", "NO_DRAWDOWNS_IN_DATA"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
