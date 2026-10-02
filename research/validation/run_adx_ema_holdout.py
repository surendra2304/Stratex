"""Run the shipped adx_ema configuration through the chronological holdout.

    python research/validation/run_adx_ema_holdout.py

Prints the honest result and exits non-zero when the holdout does not support a
verdict, so this cannot be mistaken for a passing gate in a pipeline.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402

import config_strategy as cfg  # noqa: E402
import strategy_adx_ema  # noqa: E402
from research.validation.holdout_validation import (  # noqa: E402
    load_real_candles,
    validate_holdout,
)


def signal_fn(window: pd.DataFrame) -> str:
    """Adapts the strategy's own signal function to the validator's contract."""
    result = strategy_adx_ema.get_signal(window)
    side = getattr(result, "side", None)
    if side in ("BUY", "SELL"):
        return side
    return ""


def main() -> int:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    timeframe = sys.argv[2] if len(sys.argv) > 2 else "1h"
    start = sys.argv[3] if len(sys.argv) > 3 else "2023-01-01"

    # Load the candles first so the strategy's own ATR can be computed over the
    # exact series the simulation will walk.
    df, _source, _digest = load_real_candles(symbol, timeframe, start)

    result = validate_holdout(
        symbol=symbol,
        timeframe=timeframe,
        strategy_id="adx_ema (shipped config)",
        signal_fn=signal_fn,
        sl_atr_mult=cfg.ADX_EMA_STRATEGY_V2.get("SL_ATR_MULTIPLIER", 3.0),
        tp_atr_mult=cfg.ADX_EMA_STRATEGY_V2.get("TP_ATR_MULTIPLIER", 4.0),
        holdout_fraction=0.35,
        start_str=start,
        fee_rate=cfg.BACKTEST_ASSUMPTIONS["FEE_RATE"],
        slippage_rate=cfg.BACKTEST_ASSUMPTIONS["SLIPPAGE_RATE"],
        df=df,
        atr_fn=lambda frame: strategy_adx_ema.compute_atr(frame, period=14),
    )
    print(result.summary())
    print()
    print(json.dumps(result.as_dict(), indent=2, default=str))
    return 0 if result.verdict == "PASSED_HOLDOUT" else 1


if __name__ == "__main__":
    raise SystemExit(main())
