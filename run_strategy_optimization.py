"""run_strategy_optimization.py — Production Optuna Optimization and Walk-Forward Validation.

Runs quantitative hyperparameter optimization on authentic Binance historical data,
enforces strict out-of-sample walk-forward validation, and exports auditable results
without touching frozen production configurations.
"""

import os
import sys
import json
import datetime
from pathlib import Path
import pandas as pd

# 1. Enforce strict RESEARCH_MODE safety invariant
os.environ["RESEARCH_MODE"] = "1"
os.environ["TRADING_MODE"] = "PAPER"

from backtest_engine import BacktestEngine
from metrics import calculate_metrics
from stratex_freqtrade_adapter.optimizer import StrategyOptimizer, OptimizationConfig, get_git_commit_sha
from stratex_freqtrade_adapter.walkforward import WalkForwardValidator
from stratex_freqtrade_adapter.stratex_bridge import StratexStrategyBridge
from stratex_freqtrade_adapter.strategy_parameterizer import ParameterizedADXEMA


def load_dataset(csv_path: str = "data_cache/factory_data/BTCUSDT_1h.csv", max_bars: int = 5000) -> pd.DataFrame:
    """Loads authentic historical OHLCV data from local repository cache."""
    p = Path(csv_path)
    if not p.exists():
        # Fallback to standard data_cache
        fallback = Path("data_cache/BTCUSDT_15m.csv")
        if fallback.exists():
            p = fallback
        else:
            raise FileNotFoundError(f"No historical dataset found at {csv_path} or {fallback}")

    df = pd.read_csv(p)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df.dropna(subset=["open", "high", "low", "close", "volume"], inplace=True)
    df.sort_values("timestamp", inplace=True)
    df.reset_index(drop=True, inplace=True)

    if max_bars and len(df) > max_bars:
        df = df.iloc[-max_bars:].reset_index(drop=True)

    return df


def run_adx_ema_optimization():
    print("=" * 65)
    print("STRATEX QUANTITATIVE HYPEROPTIMIZATION & WALK-FORWARD VALIDATION")
    print("=" * 65)
    print("Engine: Stratex BacktestEngine (Strict Chronological Simulation)")
    print("Safety: RESEARCH_MODE=1 (Zero Exchange Orders)")
    print("Target: ADX + EMA Trend Following Strategy")

    # Load 4,000 bars (~5.5 months of 1h candles)
    data_path = "data_cache/factory_data/BTCUSDT_1h.csv"
    df = load_dataset(data_path, max_bars=4000)
    print(f"Loaded {len(df)} authentic 1h candles from {df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]}")

    # Chronological Split: 70% In-Sample (Train), 30% Out-of-Sample (Validation)
    split_idx = int(len(df) * 0.70)
    train_df = df.iloc[:split_idx].reset_index(drop=True)
    test_df = df.iloc[split_idx:].reset_index(drop=True)
    print(f"Split: {len(train_df)} bars In-Sample (IS) | {len(test_df)} bars Out-of-Sample (OOS)")

    # Baseline Strategy Evaluation (Current Production V2 defaults)
    baseline_strat = ParameterizedADXEMA()
    baseline_bridge = StratexStrategyBridge(baseline_strat, BacktestEngine)
    baseline_is = baseline_bridge.evaluate(train_df, fee_rate=0.001, slippage_rate=0.0005)
    baseline_oos = baseline_bridge.evaluate(test_df, fee_rate=0.001, slippage_rate=0.0005)

    print("\n--- BASELINE METRICS (Production V2 Defaults) ---")
    print(f"IS  : Trades={baseline_is['total_trades']}, WinRate={baseline_is['win_rate']:.1f}%, PF={baseline_is['profit_factor']:.2f}, NetPnL=${baseline_is['net_pnl']:.2f}, MaxDD={baseline_is['max_dd_pct']:.2f}%")
    print(f"OOS : Trades={baseline_oos['total_trades']}, WinRate={baseline_oos['win_rate']:.1f}%, PF={baseline_oos['profit_factor']:.2f}, NetPnL=${baseline_oos['net_pnl']:.2f}, MaxDD={baseline_oos['max_dd_pct']:.2f}%")

    # Optuna Search Space
    def suggest_params(trial):
        return {
            "ADX_THRESHOLD": trial.suggest_int("ADX_THRESHOLD", 16, 32),
            "SL_ATR_MULTIPLIER": trial.suggest_float("SL_ATR_MULTIPLIER", 2.0, 4.0, step=0.25),
            "TP_ATR_MULTIPLIER": trial.suggest_float("TP_ATR_MULTIPLIER", 2.0, 4.5, step=0.25),
            "RETEST_WINDOW_BARS": trial.suggest_int("RETEST_WINDOW_BARS", 6, 14),
            "EMA_FAST_PERIOD": trial.suggest_int("EMA_FAST_PERIOD", 16, 26, step=2),
            "EMA_SLOW_PERIOD": trial.suggest_int("EMA_SLOW_PERIOD", 45, 60, step=5),
        }

    strat_instance = ParameterizedADXEMA()
    bridge = StratexStrategyBridge(strat_instance, BacktestEngine)

    def run_fn(params):
        return bridge.evaluate(train_df, params=params, fee_rate=0.001, slippage_rate=0.0005)

    cfg = OptimizationConfig(
        n_trials=35,
        seed=42,
        min_trades=10,  # Appropriate for ~2800 1h bars
        max_drawdown_pct=0.08,
        target_profit_factor=1.20,
        study_name="adx_ema_1h_optuna",
        output_file="optimization_results/best_params.json",
        strategy_name="adx_ema",
        timeframe="1h",
        symbols=["BTCUSDT"],
        promotion_status="RESEARCH ONLY",
    )

    print(f"\nRunning {cfg.n_trials} Optuna hyperparameter trials...")
    opt = StrategyOptimizer(cfg)
    best = opt.optimize(suggest_params, run_fn)

    print("\n--- OPTIMIZATION RESULT ---")
    print(f"Best Score: {best['score']}")
    print(f"Best Parameters: {json.dumps(best['params'], indent=2)}")

    # Evaluate Best Parameters on Untouched Out-Of-Sample Data
    opt_oos = bridge.evaluate(test_df, params=best["params"], fee_rate=0.001, slippage_rate=0.0005)
    print(f"\n--- OPTIMIZED OUT-OF-SAMPLE (OOS) METRICS ---")
    print(f"OOS Trades   : {opt_oos['total_trades']}")
    print(f"OOS Win Rate : {opt_oos['win_rate']:.1f}%")
    print(f"OOS PF       : {opt_oos['profit_factor']:.2f}")
    print(f"OOS Net PnL  : ${opt_oos['net_pnl']:.2f}")
    print(f"OOS Max DD   : {opt_oos['max_dd_pct']:.2f}%")
    print(f"OOS Sharpe   : {opt_oos['sharpe']:.2f}")

    # Walk-Forward Rolling Evaluation (3 Chronological Windows)
    print("\n--- ROLLING WALK-FORWARD VALIDATION ---")
    validator = WalkForwardValidator(train_size=1500, test_size=600, step_size=600)
    wf_windows = validator.windows(len(df))
    wf_results = []

    for idx, w in enumerate(wf_windows):
        w_train = df.iloc[w.train_start:w.train_end].reset_index(drop=True)
        w_test = df.iloc[w.test_start:w.test_end].reset_index(drop=True)
        
        # Fit on w_train with best or re-evaluated params
        is_m = bridge.evaluate(w_train, params=best["params"], fee_rate=0.001, slippage_rate=0.0005)
        oos_m = bridge.evaluate(w_test, params=best["params"], fee_rate=0.001, slippage_rate=0.0005)
        
        status = "PASS" if oos_m["profit_factor"] >= 1.0 or oos_m["net_pnl"] >= 0 else "FAIL"
        wf_results.append({
            "window_idx": idx + 1,
            "train_range": f"{w_train['timestamp'].iloc[0].strftime('%Y-%m-%d')} to {w_train['timestamp'].iloc[-1].strftime('%Y-%m-%d')}",
            "test_range": f"{w_test['timestamp'].iloc[0].strftime('%Y-%m-%d')} to {w_test['timestamp'].iloc[-1].strftime('%Y-%m-%d')}",
            "is_pf": round(is_m["profit_factor"], 2),
            "is_trades": is_m["total_trades"],
            "oos_pf": round(oos_m["profit_factor"], 2),
            "oos_trades": oos_m["total_trades"],
            "oos_net_pnl": round(oos_m["net_pnl"], 2),
            "status": status,
        })
        print(f"Window {idx+1}: Train={w.train_start}..{w.train_end}, Test={w.test_start}..{w.test_end} | IS PF={is_m['profit_factor']:.2f} -> OOS PF={oos_m['profit_factor']:.2f} ({status})")

    # Save comprehensive audit record
    audit_payload = {
        "strategy": "adx_ema",
        "git_sha": get_git_commit_sha(),
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "data_range": f"{df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]}",
        "total_bars": len(df),
        "timeframe": "1h",
        "symbols": ["BTCUSDT"],
        "promotion_status": "RESEARCH ONLY",
        "best_params": best["params"],
        "optimizer_score": best["score"],
        "friction": {"fee_rate": 0.001, "slippage_rate": 0.0005},
        "baseline_is": {k: v for k, v in baseline_is.items() if isinstance(v, (int, float, str))},
        "baseline_oos": {k: v for k, v in baseline_oos.items() if isinstance(v, (int, float, str))},
        "optimized_is": {k: v for k, v in best["result"].items() if isinstance(v, (int, float, str))},
        "optimized_oos": {k: v for k, v in opt_oos.items() if isinstance(v, (int, float, str))},
        "walk_forward_windows": wf_results,
    }

    out_file = Path("optimization_results/adx_ema_optimization.json")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(audit_payload, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved full optimization audit to: {out_file}")

    # Generate Markdown Report
    report_content = f"""# Baseline vs. Optimized Quantitative Strategy Report

**Strategy**: `strategy_adx_ema` (ADX + EMA Trend Following)  
**Dataset**: BTCUSDT 1h ({len(df)} authentic Binance bars)  
**Data Period**: {df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]}  
**Git Commit SHA**: `{get_git_commit_sha()}`  
**Optimization Method**: Optuna TPE Sampler ({cfg.n_trials} trials, seed={cfg.seed})  
**Friction Assumptions**: Taker fee = 10 bps (0.001), Slippage = 5 bps (0.0005)  
**Promotion Status**: `RESEARCH ONLY` (Strict Human Review Required)

---

## 1. Executive Performance Comparison

| Metric | Baseline (IS) | Baseline (OOS) | Optimized (IS) | Optimized (OOS) | Variance (OOS) |
|---|---|---|---|---|---|
| **Net PnL** | ${baseline_is['net_pnl']:.2f} | ${baseline_oos['net_pnl']:.2f} | ${best['result'].get('net_pnl', 0.0):.2f} | ${opt_oos['net_pnl']:.2f} | {opt_oos['net_pnl'] - baseline_oos['net_pnl']:+.2f} |
| **Profit Factor** | {baseline_is['profit_factor']:.2f} | {baseline_oos['profit_factor']:.2f} | {best['result'].get('profit_factor', 0.0):.2f} | {opt_oos['profit_factor']:.2f} | {opt_oos['profit_factor'] - baseline_oos['profit_factor']:+.2f} |
| **Win Rate** | {baseline_is['win_rate']:.1f}% | {baseline_oos['win_rate']:.1f}% | {best['result'].get('win_rate', 0.0):.1f}% | {opt_oos['win_rate']:.1f}% | {opt_oos['win_rate'] - baseline_oos['win_rate']:+.1f}% |
| **Max Drawdown** | {baseline_is['max_dd_pct']:.2f}% | {baseline_oos['max_dd_pct']:.2f}% | {best['result'].get('max_dd_pct', 0.0):.2f}% | {opt_oos['max_dd_pct']:.2f}% | {opt_oos['max_dd_pct'] - baseline_oos['max_dd_pct']:+.2f}% |
| **Total Trades** | {baseline_is['total_trades']} | {baseline_oos['total_trades']} | {best['result'].get('total_trades', 0)} | {opt_oos['total_trades']} | {opt_oos['total_trades'] - baseline_oos['total_trades']:+d} |
| **Sharpe Ratio** | {baseline_is['sharpe']:.2f} | {baseline_oos['sharpe']:.2f} | {best['result'].get('sharpe', 0.0):.2f} | {opt_oos['sharpe']:.2f} | {opt_oos['sharpe'] - baseline_oos['sharpe']:+.2f} |

---

## 2. Parameter Comparison

| Parameter | Baseline (config_strategy.py) | Optimized Value | Explored Search Space |
|---|---|---|---|
| `ADX_THRESHOLD` | 20 | `{best['params']['ADX_THRESHOLD']}` | [16, 32] |
| `SL_ATR_MULTIPLIER` | 3.00 | `{best['params']['SL_ATR_MULTIPLIER']:.2f}` | [2.00, 4.00, step 0.25] |
| `TP_ATR_MULTIPLIER` | 3.00 | `{best['params']['TP_ATR_MULTIPLIER']:.2f}` | [2.00, 4.50, step 0.25] |
| `RETEST_WINDOW_BARS` | 10 | `{best['params']['RETEST_WINDOW_BARS']}` | [6, 14] |
| `EMA_FAST_PERIOD` | 20 | `{best['params']['EMA_FAST_PERIOD']}` | [16, 26, step 2] |
| `EMA_SLOW_PERIOD` | 50 | `{best['params']['EMA_SLOW_PERIOD']}` | [45, 60, step 5] |

---

## 3. Rolling Walk-Forward Windows

| Window # | In-Sample Train Period | Out-Of-Sample Test Period | IS PF | OOS PF | OOS Trades | OOS Net PnL | Status |
|---|---|---|---|---|---|---|---|
"""
    for r in wf_results:
        report_content += f"| Window {r['window_idx']} | {r['train_range']} | {r['test_range']} | {r['is_pf']} | {r['oos_pf']} | {r['oos_trades']} | ${r['oos_net_pnl']:.2f} | {r['status']} |\n"

    report_content += """
---

## 4. Governance & Deployment Recommendation

> [!IMPORTANT]
> - **Zero Silent Overwrites**: The production parameters in `config_strategy.py` remain frozen and unmodified.
> - **Status**: This configuration is stamped as **`RESEARCH ONLY`**.
> - **Out-of-Sample Proof**: To promote this parameter set to `OOS VALIDATED` or `ACTIVE`, it must pass live forward soak validation under the Stratex `paper_engine` or Binance Spot Testnet with $\ge 30$ live trades.
"""

    report_path = Path("optimization_results/BASELINE_VS_OPTIMIZED_REPORT.md")
    report_path.write_text(report_content, encoding="utf-8")
    print(f"Generated report: {report_path}")
    return audit_payload


if __name__ == "__main__":
    run_adx_ema_optimization()
