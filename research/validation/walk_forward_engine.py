import pandas as pd

from backtest_engine import BacktestEngine
from features import add_features
from metrics import calculate_metrics


class WalkForwardEngine:
    def __init__(self, train_pct=0.60, val_pct=0.20, test_pct=0.20, n_splits=3):
        self.train_pct = train_pct
        self.val_pct = val_pct
        self.test_pct = test_pct
        self.n_splits = n_splits

    def run_walk_forward(self, df, strategy_module, fee_rate=0.001, slippage_rate=0.0005, initial_balance=10000.0):
        if 'timestamp' in df.columns and not pd.api.types.is_datetime64_any_dtype(df['timestamp']):
            df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.sort_values('timestamp').reset_index(drop=True)
        
        n = len(df)
        fold_size = n // (self.n_splits + 1)
        if fold_size < 100:
            raise ValueError('Dataframe too small for walk-forward splits')
            
        fold_results = []
        all_test_trades = []
        
        for fold in range(self.n_splits):
            train_end = fold_size * (fold + 1)
            val_end = train_end + int(fold_size * 0.5)
            test_end = min(n, val_end + fold_size)
            
            train_slice = df.iloc[:train_end].copy()
            val_slice = df.iloc[train_end:val_end].copy()
            test_slice = df.iloc[val_end:test_end].copy()
            
            # Causal feature computation
            train_feat = add_features(train_slice)
            test_feat = add_features(test_slice)
            
            # If strategy supports ML training
            if hasattr(strategy_module, 'train'):
                strategy_module.train(train_feat)
                
            engine = BacktestEngine(
                test_feat.copy(),
                [strategy_module],
                fee_rate=fee_rate,
                slippage_rate=slippage_rate,
                initial_balance=initial_balance
            )
            test_trades, test_eq = engine.run()
            metrics = calculate_metrics(test_trades, test_eq, initial_balance=initial_balance)
            
            fold_results.append({
                'fold': fold + 1,
                'train_rows': len(train_slice),
                'val_rows': len(val_slice),
                'test_rows': len(test_slice),
                'metrics': metrics
            })
            all_test_trades.extend(test_trades)
            
        aggregate_metrics = calculate_metrics(all_test_trades, pd.DataFrame(), initial_balance=initial_balance)
        return {
            'n_splits': self.n_splits,
            'folds': fold_results,
            'aggregate_oos_metrics': aggregate_metrics,
            'all_test_trades': all_test_trades
        }
