import json
import os
import sys

import numpy as np

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import xgboost as xgb
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.preprocessing import StandardScaler

from config import BACKTEST_FEE_RATE, BACKTEST_SLIPPAGE_RATE
from research_phase7.barrier_labels import apply_triple_barrier_labels
from research_phase7.ml_research import feature_ablation_test
from research_phase7.research_features import build_institutional_features
from research_phase7.walk_forward import generate_walk_forward_splits
from research_phase8.config_timeframes import TIMEFRAME_CONFIGS
from research_phase8.data_resampler import resample_timeframe
from research_phase8.economic_evaluator import (
    calculate_net_expectancy,
    calculate_timeframe_economics,
)


def run_multi_timeframe_grid(df_1m):
    """
    Part 7-16: Core Evaluation Engine
    Evaluates every timeframe chronologically.
    """
    results = {}
    
    # Run economics first
    tf_econ = calculate_timeframe_economics(TIMEFRAME_CONFIGS, BACKTEST_FEE_RATE, BACKTEST_SLIPPAGE_RATE)
    results['cost_analysis'] = tf_econ
    
    # We will only evaluate 1m, 5m, 15m, 1h for computational feasibility
    # (30m and 4h are skipped in grid to save time, unless explicitly requested)
    target_tfs = ['1m', '5m', '15m', '1h']
    
    for tf in target_tfs:
        print(f"\n[PHASE 8] === EVALUATING TIMEFRAME: {tf} ===")
        cfg = TIMEFRAME_CONFIGS[tf]
        
        # 1. Resample Data
        print(f"  -> Resampling to {tf}...")
        df_tf = resample_timeframe(df_1m, tf)
        if len(df_tf) < 200:
            print(f"  -> Insufficient data ({len(df_tf)} bars). Skipping.")
            continue
            
        # 2. Build Features on Resampled Data (Zero Leakage)
        print("  -> Engineering Features...")
        df_tf = build_institutional_features(df_tf, use_frac_diff=True, d=0.3)
        
        # 3. Target Labels
        print(f"  -> Generating Labels (PT={cfg['pt_pct']}, SL={cfg['sl_pct']})...")
        df_tf = apply_triple_barrier_labels(df_tf, cfg['pt_pct'], cfg['sl_pct'], cfg['time_limit'])
        
        # Drop NaNs after features and labels
        df_tf.dropna(subset=['long_label'], inplace=True)
        
        if len(np.unique(df_tf['long_label'])) < 2:
            print("  -> Only one label class found. Skipping.")
            continue
            
        # 4. Feature Ablation Test (In-Sample proxy)
        print("  -> Running Feature Ablation...")
        ablation = feature_ablation_test(df_tf, y_col='long_label')
        
        # 5. True Walk Forward Execution
        print("  -> Running Walk-Forward Testing...")
        # We will use simple XGBoost across expanding splits
        splits = generate_walk_forward_splits(df_tf, num_windows=3, train_pct=0.5, val_pct=0.25)
        
        fold_results = []
        all_probs = []
        all_y = []
        
        # Select best feature group from ALL (baseline)
        features = ['cvd_raw', 'vol_zscore', 'atr_pct', 'close_frac_diff', 'realized_vol_20', 'rsi_14']
        actual_features = [f for f in features if f in df_tf.columns]
        
        for s in splits:
            train = s['train'].dropna()
            test = s['test'].dropna()
            
            if len(train) < 50 or len(test) < 10:
                continue
                
            X_train = train[actual_features]
            y_train = train['long_label']
            X_test = test[actual_features]
            y_test = test['long_label']
            
            if len(np.unique(y_train)) < 2:
                continue
                
            scaler = StandardScaler()
            X_train_scaled = scaler.fit_transform(X_train)
            X_test_scaled = scaler.transform(X_test)
            
            model = xgb.XGBClassifier(n_estimators=50, max_depth=3, learning_rate=0.05, random_state=42)
            model.fit(X_train_scaled, y_train)
            
            probs = model.predict_proba(X_test_scaled)[:, 1]
            all_probs.extend(probs)
            all_y.extend(y_test)
            
            # Simulated trading: Take trade if prob > 0.55
            signals = (probs > 0.55)
            trades_taken = signals.sum()
            win_rate = y_test[signals].mean() if trades_taken > 0 else 0
            
            econ = calculate_net_expectancy(win_rate, cfg['pt_pct'], cfg['sl_pct'], BACKTEST_FEE_RATE, BACKTEST_SLIPPAGE_RATE)
            
            fold_results.append({
                "fold": s['fold'],
                "trades": int(trades_taken),
                "win_rate": float(win_rate),
                "net_expectancy": float(econ['net_expectancy']),
                "profit_factor": float(econ['profit_factor'])
            })
            
        # Aggregate Performance
        if len(all_y) > 0 and len(np.unique(all_y)) > 1:
            auc = roc_auc_score(all_y, all_probs)
            pr_auc = average_precision_score(all_y, all_probs)
        else:
            auc = 0
            pr_auc = 0
            
        # Extract total economic performance
        total_trades = sum([f['trades'] for f in fold_results])
        avg_win_rate = np.mean([f['win_rate'] for f in fold_results if f['trades'] > 0]) if total_trades > 0 else 0
        total_econ = calculate_net_expectancy(avg_win_rate, cfg['pt_pct'], cfg['sl_pct'], BACKTEST_FEE_RATE, BACKTEST_SLIPPAGE_RATE)
        
        results[tf] = {
            "ablation": ablation,
            "folds": fold_results,
            "aggregate_oos": {
                "roc_auc": float(auc),
                "pr_auc": float(pr_auc),
                "total_trades": int(total_trades),
                "avg_win_rate": float(avg_win_rate),
                "net_expectancy": float(total_econ['net_expectancy']),
                "profit_factor": float(total_econ['profit_factor']),
                "viable": bool(total_econ['viable'])
            }
        }
        
    return results

if __name__ == "__main__":
    from research_phase7.data_loader import download_and_verify_data
    df = download_and_verify_data(days=90, use_cache=True)
    res = run_multi_timeframe_grid(df)
    
    with open("backtest_results/phase8/temp_results.json", "w") as f:
        json.dump(res, f, indent=4)
