import pandas as pd

from config import STARTING_BALANCE, SYMBOL
from metrics import calculate_metrics


def generate_walk_forward_splits(df, num_windows=5, train_pct=0.50, val_pct=0.25):
    """
    Part 3 & 4: True Walk-Forward Splits
    Generates expanding window slices:
    Train -> Validation -> Test.
    Test is NEVER seen during Train or Validation.
    """
    total_bars = len(df)
    train_size = int(total_bars * train_pct)
    val_size = int(total_bars * val_pct)
    remaining_bars = total_bars - train_size - val_size
    test_step = max(1, remaining_bars // num_windows)
    
    splits = []
    
    for w in range(num_windows):
        start_idx = w * test_step # Expanding implies start_idx=0 usually, but here we do rolling to match phase 6 or expanding?
        # Let's do strictly expanding train window to gather more data over time, or strictly rolling.
        # The prompt asks to document it. We will use ROLLING window to adapt to recent regimes.
        # Train window moves forward.
        
        if start_idx + train_size >= total_bars: break
            
        test_end = total_bars if w == num_windows - 1 else start_idx + train_size + val_size + test_step
        
        train_df = df.iloc[start_idx : start_idx+train_size].copy()
        val_df = df.iloc[start_idx+train_size : start_idx+train_size+val_size].copy()
        test_df = df.iloc[start_idx+train_size+val_size : test_end].copy()
        
        splits.append({
            "fold": w + 1,
            "train": train_df,
            "val": val_df,
            "test": test_df
        })
        
    return splits

def run_strict_walk_forward(splits, orchestrator_class, fee_rate, slippage_rate):
    """
    Executes Phase 7 walk-forward architecture.
    1. Orchestrator discovers mapping in TRAIN.
    2. Orchestrator validates/selects in VALIDATION.
    3. Engine evaluates strictly on TEST.
    """
    from backtest_engine import BacktestEngine
    
    all_oos_trades = []
    oos_equity_frames = []
    current_equity = STARTING_BALANCE
    
    fold_logs = []
    
    for split in splits:
        train_df = split['train']
        val_df = split['val']
        test_df = split['test']
        fold_idx = split['fold']
        
        # Instantiate fresh orchestrator
        orch = orchestrator_class(fee_rate=fee_rate, slippage_rate=slippage_rate)
        
        # 1 & 2. Train and Validate
        orch.train(train_df, val_df)
        
        # 3. Test (Execution)
        engine = BacktestEngine(test_df, [orch], fee_rate, slippage_rate, current_equity, 0.02, symbol=SYMBOL)
        trades, equity = engine.run()
        
        all_oos_trades.extend(trades)
        if not equity.empty:
            current_equity = equity.iloc[-1]['equity']
            oos_equity_frames.append(equity)
            
        fold_logs.append({
            "fold": fold_idx,
            "train_bars": len(train_df),
            "val_bars": len(val_df),
            "test_bars": len(test_df),
            "test_trades": len(trades),
            "test_pnl": current_equity - STARTING_BALANCE
        })
        
    if oos_equity_frames:
        combined_equity = pd.concat(oos_equity_frames).drop_duplicates(subset=['timestamp']).reset_index(drop=True)
    else:
        combined_equity = pd.DataFrame()
        
    metrics = calculate_metrics(all_oos_trades, combined_equity, STARTING_BALANCE)
    return metrics, all_oos_trades, combined_equity, fold_logs
