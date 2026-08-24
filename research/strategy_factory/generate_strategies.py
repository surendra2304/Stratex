"""
research/strategy_factory/generate_strategies.py

Programmatically generates 100+ systematic strategy variations across indicators:
- EMAs (9/21, 10/20, 20/50, 50/200)
- RSI (14 with 30/70, 14 with 20/80, 7 with 30/70)
- Bollinger Bands (20, 2SD; 20, 3SD)
- MACD (12/26/9, 5/35/5)
- Stochastic (14/3/3, 5/3/3)
- Timeframes: 5m, 15m, 1h
- Dynamic SL/TP ATR multipliers
"""

import json
import itertools
from pathlib import Path


def generate_variations():
    variations = []
    var_id = 1

    ema_pairs = [
        ("ema_crossover", {"fast_ema": 9, "slow_ema": 21}),
        ("ema_crossover", {"fast_ema": 10, "slow_ema": 20}),
        ("ema_crossover", {"fast_ema": 20, "slow_ema": 50}),
        ("ema_crossover", {"fast_ema": 50, "slow_ema": 200}),
    ]

    rsi_configs = [
        ("rsi_mean_reversion", {"rsi_period": 14, "rsi_lower": 30, "rsi_upper": 70}),
        ("rsi_mean_reversion", {"rsi_period": 14, "rsi_lower": 20, "rsi_upper": 80}),
        ("rsi_mean_reversion", {"rsi_period": 7, "rsi_lower": 30, "rsi_upper": 70}),
    ]

    bb_configs = [
        ("bb_break_reenter", {"bb_period": 20, "bb_std": 2.0}),
        ("bb_break_reenter", {"bb_period": 20, "bb_std": 3.0}),
    ]

    macd_configs = [
        ("macd_crossover", {"macd_fast": 12, "macd_slow": 26, "macd_signal": 9}),
        ("macd_crossover", {"macd_fast": 5, "macd_slow": 35, "macd_signal": 5}),
    ]

    stoch_configs = [
        ("stoch_crossover", {"stoch_k": 14, "stoch_d": 3, "stoch_smooth": 3, "stoch_lower": 20, "stoch_upper": 80}),
        ("stoch_crossover", {"stoch_k": 5, "stoch_d": 3, "stoch_smooth": 3, "stoch_lower": 20, "stoch_upper": 80}),
    ]

    combined_types = ema_pairs + rsi_configs + bb_configs + macd_configs + stoch_configs
    timeframes = ["5m", "15m", "1h"]
    sl_tp_ratios = [
        {"sl_atr": 1.0, "tp_atr": 2.0, "rr_ratio": 2.0},
        {"sl_atr": 1.5, "tp_atr": 3.0, "rr_ratio": 2.0},
        {"sl_atr": 0.5, "tp_atr": 1.5, "rr_ratio": 3.0},
        {"sl_atr": 2.0, "tp_atr": 4.0, "rr_ratio": 2.0},
    ]

    # Generate single indicator variations across timeframes and SL/TP ratios
    for (strat_type, params), tf, exit_cfg in itertools.product(combined_types, timeframes, sl_tp_ratios):
        name = f"factory_{strat_type}_{tf}_{var_id:03d}"
        var = {
            "id": var_id,
            "name": name,
            "type": strat_type,
            "timeframe": tf,
            "params": params,
            "exits": exit_cfg,
            "entry_logic": f"{strat_type} on {tf} chart with SL {exit_cfg['sl_atr']}x ATR and TP {exit_cfg['tp_atr']}x ATR"
        }
        variations.append(var)
        var_id += 1

    # Add Confluence variations (e.g. EMA + RSI, MACD + BB)
    confluence_pairs = [
        ("ema_rsi_confluence", {"fast_ema": 9, "slow_ema": 21, "rsi_period": 14, "rsi_long_max": 65, "rsi_short_min": 35}),
        ("ema_rsi_confluence", {"fast_ema": 20, "slow_ema": 50, "rsi_period": 14, "rsi_long_max": 60, "rsi_short_min": 40}),
        ("macd_bb_confluence", {"macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "bb_period": 20, "bb_std": 2.0}),
        ("stoch_ema_confluence", {"fast_ema": 20, "slow_ema": 50, "stoch_k": 14, "stoch_d": 3, "stoch_smooth": 3}),
    ]

    for (strat_type, params), tf, exit_cfg in itertools.product(confluence_pairs, timeframes, sl_tp_ratios):
        name = f"factory_{strat_type}_{tf}_{var_id:03d}"
        var = {
            "id": var_id,
            "name": name,
            "type": strat_type,
            "timeframe": tf,
            "params": params,
            "exits": exit_cfg,
            "entry_logic": f"Confluence {strat_type} on {tf} with SL {exit_cfg['sl_atr']}x ATR and TP {exit_cfg['tp_atr']}x ATR"
        }
        variations.append(var)
        var_id += 1

    output_dir = Path("research/strategy_factory")
    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / "strategy_variations.json"
    with open(out_file, "w") as f:
        json.dump(variations, f, indent=2)

    print(f"Generated {len(variations)} strategy variations saved to {out_file}")
    return variations


if __name__ == "__main__":
    generate_variations()
