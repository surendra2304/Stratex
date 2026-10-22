import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

import strategy_aggressor as aggressor
import strategy_ml as ml
import strategy_scalper as scalper
import strategy_swing as swing
from backtest_engine import BacktestEngine
from diagnostics import calculate_diagnostics


class StrategyOrchestrator:
    """
    Intelligently routes signals to specific strategies based on the current market regime.
    Learns the optimal Regime-to-Strategy mapping strictly from Train/Val data.
    """
    __name__ = "orchestrator"
    
    def __init__(self, fee_rate, slippage_rate, min_trades=10, min_pf=1.05):
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate
        self.min_trades = min_trades
        self.min_pf = min_pf
        
        self.strats = {
            "scalper": scalper,
            "swing": swing,
            "aggressor": aggressor,
            "ml": ml
        }
        
        # Mapping: Regime String -> Strategy Name (or None if no edge)
        self.regime_mapping = {}
        self.regime_stats = {}

    def train(self, train_df, val_df):
        """
        Runs a backtest of all strategies on Train+Val to determine regime edge.
        """
        print("\n    [ORCHESTRATOR] Training Regime Routing on Train+Val split...")
        combined_df = pd.concat([train_df, val_df]).drop_duplicates(subset=['timestamp']).reset_index(drop=True)
        
        regime_performance = {}
        
        for s_name, strat in self.strats.items():
            if hasattr(strat, 'train'):
                strat.train(train_df, val_df)
                
            engine = BacktestEngine(
                combined_df, [strat], 
                fee_rate=self.fee_rate, slippage_rate=self.slippage_rate, 
                initial_balance=10000.0, risk_per_trade=0.01, symbol="SIM"
            )
            trades, equity = engine.run()
            
            if not trades:
                continue
                
            diag = calculate_diagnostics(trades, equity, 10000.0)
            strat_regime_stats = diag.get('regime_performance', {})
            
            for regime, stats in strat_regime_stats.items():
                if regime not in regime_performance:
                    regime_performance[regime] = {}
                regime_performance[regime][s_name] = stats
                
        # Select best strategy per regime based on Profit Factor after costs
        self.regime_mapping = {}
        self.regime_stats = regime_performance
        
        for regime, strats_in_regime in regime_performance.items():
            best_strat = None
            best_pf = 0
            
            for s_name, stats in strats_in_regime.items():
                pf = stats['profit_factor']
                trades = stats['trades']
                
                if trades >= self.min_trades and pf >= self.min_pf:
                    if pf != float('inf') and pf > best_pf:
                        best_pf = pf
                        best_strat = s_name
                    elif pf == float('inf') and best_pf != float('inf'):
                        best_pf = float('inf')
                        best_strat = s_name
                        
            self.regime_mapping[regime] = best_strat
            if best_strat:
                print(f"      -> Assigned {regime} to {best_strat.upper()} (PF: {best_pf})")
            else:
                print(f"      -> {regime} has no profitable strategy. Standing aside.")

    def get_signal(self, df):
        """Generates a signal exclusively from the active regime's mapped strategy."""
        if len(df) < 1:
            return None, None, None
            
        current_regime = df.iloc[-1].get('regime', 'UNKNOWN')
        assigned_strat_name = self.regime_mapping.get(current_regime)
        
        if assigned_strat_name and assigned_strat_name in self.strats:
            strat = self.strats[assigned_strat_name]
            res = strat.get_signal(df)
            if res[0]:
                self.__name__ = f"orchestrator_{assigned_strat_name}"
                return res
                
        return None, None, None
