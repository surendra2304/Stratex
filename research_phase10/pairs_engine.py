import sys
import os
import pandas as pd
import numpy as np
from statsmodels.tsa.stattools import adfuller
import statsmodels.api as sm

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research_phase8.data_resampler import resample_timeframe
from research_phase9.cost_engine import CostEngine
from research_phase7.walk_forward import generate_walk_forward_splits

class PairsEngine:
    """
    Part 3-13: Proper Pairs Trading Methodology
    Implements True Walk-Forward estimation of the hedge ratio and tests out-of-sample.
    """
    def __init__(self, df_a, df_b, asset_a, asset_b, timeframe='15m'):
        self.asset_a = asset_a
        self.asset_b = asset_b
        
        print(f"[PAIRS ENGINE] Resampling {asset_a} and {asset_b} to {timeframe}...")
        self.df_a = resample_timeframe(df_a, timeframe)[['timestamp', 'close']].rename(columns={'close': 'close_a'}).set_index('timestamp')
        self.df_b = resample_timeframe(df_b, timeframe)[['timestamp', 'close']].rename(columns={'close': 'close_b'}).set_index('timestamp')
        
        self.df = self.df_a.join(self.df_b, how='inner').dropna()
        self.cost_engine = CostEngine.get_binance_taker_config() # Pay taker fees to guarantee entry/exit
        
    def estimate_hedge_ratio(self, train_df):
        """
        Uses OLS to find Beta.
        close_a = beta * close_b
        """
        # We don't use intercept for spread: Spread = A - beta*B
        # This makes dollar-neutral sizing cleaner.
        X = train_df['close_b']
        y = train_df['close_a']
        model = sm.OLS(y, X).fit()
        return model.params.iloc[0]
        
    def check_cointegration(self, train_df, beta):
        """
        Calculates ADF p-value of the spread in the train set.
        """
        spread = train_df['close_a'] - (beta * train_df['close_b'])
        adf = adfuller(spread)
        return adf[1] # p-value
        
    def run_walk_forward(self, entry_z=2.0, exit_z=0.0):
        if len(self.df) < 500:
            return {"status": "UNAVAILABLE", "reason": "Not enough data"}
            
        splits = generate_walk_forward_splits(self.df.reset_index(), num_windows=3, train_pct=0.5, val_pct=0.0)
        
        all_trades = []
        all_oos_gross_pnl = []
        all_oos_net_pnl = []
        all_p_values = []
        
        print(f"\n[PAIRS ENGINE] Running {len(splits)} Walk-Forward Folds for {self.asset_a}/{self.asset_b}...")
        
        for s in splits:
            train = s['train'].set_index('timestamp')
            test = s['test'].set_index('timestamp')
            
            # 1. Estimate Beta exclusively on TRAIN
            beta = self.estimate_hedge_ratio(train)
            
            # 2. Check Cointegration on TRAIN
            p_val = self.check_cointegration(train, beta)
            all_p_values.append(p_val)
            
            # 3. Build Test Spread using TRAIN Beta (Zero Lookahead)
            test = test.copy()
            test['spread'] = test['close_a'] - (beta * test['close_b'])
            
            # We need rolling mean/std of spread to calculate Z-Score. 
            # To avoid dropping test data, we prepend the end of train data.
            lookback = 100
            train_spread = train['close_a'] - (beta * train['close_b'])
            combined_spread = pd.concat([train_spread.iloc[-lookback:], test['spread']])
            
            roll_mean = combined_spread.rolling(window=lookback).mean()
            roll_std = combined_spread.rolling(window=lookback).std()
            
            test['z_score'] = (test['spread'] - roll_mean.loc[test.index]) / (roll_std.loc[test.index] + 1e-9)
            
            # 4. Simulate Execution on Test
            in_trade = 0
            entry_price_a = 0
            entry_price_b = 0
            
            for i in range(len(test)):
                z = test['z_score'].iloc[i]
                pa = test['close_a'].iloc[i]
                pb = test['close_b'].iloc[i]
                
                if in_trade == 0:
                    if z > entry_z:
                        # Short Spread: Short A, Long B
                        in_trade = -1
                        entry_price_a = pa
                        entry_price_b = pb
                    elif z < -entry_z:
                        # Long Spread: Long A, Short B
                        in_trade = 1
                        entry_price_a = pa
                        entry_price_b = pb
                elif in_trade == 1:
                    # Exit Long Spread
                    if z >= exit_z:
                        # PnL Leg A (Long)
                        pnl_a = (pa - entry_price_a) / entry_price_a
                        # PnL Leg B (Short, but scaled by beta)
                        # Dollar neutral means notional A = notional B
                        # So weight is 50/50. 
                        # We just take the average of the return percentages.
                        pnl_b = (entry_price_b - pb) / entry_price_b
                        
                        gross = (pnl_a + pnl_b) / 2
                        
                        # Double friction
                        friction = self.cost_engine.get_total_friction() * 2
                        net = gross - friction
                        
                        all_trades.append({"gross": gross, "net": net})
                        all_oos_gross_pnl.append(gross)
                        all_oos_net_pnl.append(net)
                        in_trade = 0
                elif in_trade == -1:
                    # Exit Short Spread
                    if z <= -exit_z:
                        # PnL Leg A (Short)
                        pnl_a = (entry_price_a - pa) / entry_price_a
                        # PnL Leg B (Long)
                        pnl_b = (pb - entry_price_b) / entry_price_b
                        
                        gross = (pnl_a + pnl_b) / 2
                        friction = self.cost_engine.get_total_friction() * 2
                        net = gross - friction
                        
                        all_trades.append({"gross": gross, "net": net})
                        all_oos_gross_pnl.append(gross)
                        all_oos_net_pnl.append(net)
                        in_trade = 0
                        
        total_net = sum(all_oos_net_pnl)
        avg_p_val = np.mean(all_p_values)
        
        return {
            "status": "AVAILABLE",
            "pair": f"{self.asset_a}/{self.asset_b}",
            "trades": len(all_trades),
            "total_net_pnl_pct": total_net,
            "avg_adf_p_value": avg_p_val,
            "stationary": avg_p_val < 0.05,
            "viable": total_net > 0 and avg_p_val < 0.05
        }
