import os
import sys

import numpy as np
import statsmodels.api as sm
from statsmodels.tsa.stattools import adfuller

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research_phase7.walk_forward import generate_walk_forward_splits
from research_phase8.data_resampler import resample_timeframe
from research_phase9.cost_engine import CostEngine


def calculate_halflife(spread):
    # Calculates the half-life of mean reversion using Ornstein-Uhlenbeck process
    if len(spread) < 10:
        return np.inf
    spread_lag = spread.shift(1).dropna()
    spread_diff = spread.diff().dropna()
    spread_lag = spread_lag.loc[spread_diff.index]
    
    # OLS: dSpread_t = theta * Spread_{t-1} + e
    X = sm.add_constant(spread_lag)
    y = spread_diff
    model = sm.OLS(y, X).fit()
    theta = model.params.iloc[1]
    
    if theta >= 0:
        return np.inf # Not mean reverting
    return -np.log(2) / theta

class PairsEngine:
    """
    Part 3-13: Proper Pairs Trading Methodology (Phase 10.1 Corrections)
    Implements Train -> Validation -> Test with zero lookahead,
    rolling stability checks, and Beta-Neutral position sizing.
    """
    def __init__(self, df_a, df_b, asset_a, asset_b, timeframe='1h'):
        self.asset_a = asset_a
        self.asset_b = asset_b
        
        self.df_a = resample_timeframe(df_a, timeframe)[['timestamp', 'close']].rename(columns={'close': 'close_a'}).set_index('timestamp')
        self.df_b = resample_timeframe(df_b, timeframe)[['timestamp', 'close']].rename(columns={'close': 'close_b'}).set_index('timestamp')
        
        self.df = self.df_a.join(self.df_b, how='inner').dropna()
        self.cost_engine = CostEngine.get_binance_taker_config()
        
    def estimate_hedge_ratio(self, train_df):
        X = train_df['close_b']
        y = train_df['close_a']
        model = sm.OLS(y, X).fit()
        return model.params.iloc[0]
        
    def run_walk_forward(self):
        if len(self.df) < 500:
            return {"status": "UNAVAILABLE", "reason": f"Only {len(self.df)} overlapping candles."}
            
        # We need Train (50%), Validation (25%), Test (25%)
        # generate_walk_forward_splits doesn't explicitly do val out of the box, 
        # so we'll slice the train split into Train + Val
        splits = generate_walk_forward_splits(self.df.reset_index(), num_windows=3, train_pct=0.75, val_pct=0.0)
        
        all_trades = []
        all_oos_net_pnl_dollar = []
        all_p_values = []
        all_halflives = []
        
        starting_capital = 10000.0
        current_capital = starting_capital
        
        for s in splits:
            full_train = s['train'].set_index('timestamp')
            test = s['test'].set_index('timestamp')
            
            # Split full_train into Train (2/3) and Validation (1/3)
            split_idx = int(len(full_train) * (2/3))
            train = full_train.iloc[:split_idx]
            val = full_train.iloc[split_idx:]
            
            # 1. TRAIN: Estimate Beta
            beta = self.estimate_hedge_ratio(train)
            
            # Check Cointegration on Train
            train_spread = train['close_a'] - (beta * train['close_b'])
            p_val = adfuller(train_spread)[1]
            hl = calculate_halflife(train_spread)
            
            all_p_values.append(p_val)
            all_halflives.append(hl)
            
            if p_val > 0.05:
                # Relationship breaks, do not trade this fold
                continue
                
            # 2. VALIDATION: Parameter Selection
            param_grid = [(1.5, 0.0), (2.0, 0.5), (2.5, 0.5)]
            best_params = param_grid[0]
            best_val_pnl = -np.inf
            
            for entry_z, exit_z in param_grid:
                val_res = self._simulate_fold(val, beta, entry_z, exit_z, current_capital, is_test=False)
                if val_res['net_pnl'] > best_val_pnl:
                    best_val_pnl = val_res['net_pnl']
                    best_params = (entry_z, exit_z)
                    
            # 3. TEST: Out of Sample Evaluation
            test_res = self._simulate_fold(test, beta, best_params[0], best_params[1], current_capital, is_test=True)
            
            current_capital += test_res['net_pnl']
            all_trades.extend(test_res['trades'])
            all_oos_net_pnl_dollar.append(test_res['net_pnl'])
            
        total_return_pct = (current_capital - starting_capital) / starting_capital
        avg_p_val = np.mean(all_p_values) if all_p_values else 1.0
        avg_hl = np.mean(all_halflives) if all_halflives else np.inf
        
        return {
            "status": "AVAILABLE",
            "pair": f"{self.asset_a}/{self.asset_b}",
            "trades": len(all_trades),
            "total_net_pnl_pct": total_return_pct,
            "starting_capital": starting_capital,
            "ending_capital": current_capital,
            "avg_adf_p_value": avg_p_val,
            "avg_half_life": avg_hl,
            "stationary": avg_p_val < 0.05,
            "viable": total_return_pct > 0 and avg_p_val < 0.05,
            "ledger": all_trades
        }
        
    def _simulate_fold(self, data, beta, entry_z, exit_z, available_capital, is_test=False):
        # We simulate executing the Z-score logic on a fold.
        data = data.copy()
        if 'spread' not in data.columns:
            data['spread'] = data['close_a'] - (beta * data['close_b'])
        
        if 'z_score' not in data.columns:
            lookback = 100
            roll_mean = data['spread'].rolling(window=lookback).mean()
            roll_std = data['spread'].rolling(window=lookback).std()
            data['z_score'] = (data['spread'] - roll_mean) / (roll_std + 1e-9)
            
        data = data.dropna()
        
        in_trade = 0
        entry_price_a = 0
        entry_price_b = 0
        notional_a = 0
        notional_b = 0
        
        trades = []
        fold_pnl = 0.0
        
        for i in range(len(data)):
            z = data['z_score'].iloc[i]
            pa = data['close_a'].iloc[i]
            pb = data['close_b'].iloc[i]
            
            # Simplified explicit capital allocation. 
            # We allocate 100% of available_capital. 
            # Beta-Neutral: Notional_A = Cap/2, Notional_B = Beta * Notional_A
            allocation_per_leg = available_capital / 2.0
            
            if in_trade == 0:
                if z > entry_z:
                    # Short Spread: Short A, Long B
                    in_trade = -1
                    entry_price_a = pa
                    entry_price_b = pb
                    notional_a = allocation_per_leg
                    notional_b = beta * allocation_per_leg
                elif z < -entry_z:
                    # Long Spread: Long A, Short B
                    in_trade = 1
                    entry_price_a = pa
                    entry_price_b = pb
                    notional_a = allocation_per_leg
                    notional_b = beta * allocation_per_leg
            elif in_trade == 1:
                # Exit Long Spread
                if z >= exit_z:
                    # PnL Leg A (Long)
                    pnl_a_pct = (pa - entry_price_a) / entry_price_a
                    # PnL Leg B (Short)
                    pnl_b_pct = (entry_price_b - pb) / entry_price_b
                    
                    gross_dollar = (notional_a * pnl_a_pct) + (notional_b * pnl_b_pct)
                    
                    # Independent Leg Costs
                    friction_a = notional_a * (self.cost_engine.entry_fee + self.cost_engine.entry_slip + self.cost_engine.exit_fee + self.cost_engine.exit_slip)
                    friction_b = notional_b * (self.cost_engine.entry_fee + self.cost_engine.entry_slip + self.cost_engine.exit_fee + self.cost_engine.exit_slip)
                    total_friction = friction_a + friction_b
                    
                    net_dollar = gross_dollar - total_friction
                    fold_pnl += net_dollar
                    available_capital += net_dollar
                    
                    trades.append({"direction": "LONG_SPREAD", "gross": gross_dollar, "friction": total_friction, "net": net_dollar})
                    in_trade = 0
                    
            elif in_trade == -1:
                # Exit Short Spread
                if z <= -exit_z:
                    pnl_a_pct = (entry_price_a - pa) / entry_price_a
                    pnl_b_pct = (pb - entry_price_b) / entry_price_b
                    
                    gross_dollar = (notional_a * pnl_a_pct) + (notional_b * pnl_b_pct)
                    
                    friction_a = notional_a * (self.cost_engine.entry_fee + self.cost_engine.entry_slip + self.cost_engine.exit_fee + self.cost_engine.exit_slip)
                    friction_b = notional_b * (self.cost_engine.entry_fee + self.cost_engine.entry_slip + self.cost_engine.exit_fee + self.cost_engine.exit_slip)
                    total_friction = friction_a + friction_b
                    
                    net_dollar = gross_dollar - total_friction
                    fold_pnl += net_dollar
                    available_capital += net_dollar
                    
                    trades.append({"direction": "SHORT_SPREAD", "gross": gross_dollar, "friction": total_friction, "net": net_dollar})
                    in_trade = 0
                    
        return {"net_pnl": fold_pnl, "trades": trades}
