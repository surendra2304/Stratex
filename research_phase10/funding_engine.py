import pandas as pd
import numpy as np

class FundingEngine:
    """
    Part 14-25: Funding Arbitrage Event Simulator
    Models Spot Long + Perp Short holding through funding epochs.
    """
    def __init__(self, cost_engine, max_leverage=3.0):
        self.cost = cost_engine
        self.max_leverage = max_leverage
        
    def simulate_funding_arbitrage(self, df_spot, df_perp, df_funding, hold_epochs=5):
        """
        Steps chronologically through funding events.
        """
        # Ensure funding is sorted chronologically
        df_funding = df_funding.sort_values('fundingTime')
        
        # We need the spot and perp data indexed by timestamp for fast lookup
        df_spot = df_spot.set_index('timestamp')
        df_perp = df_perp.set_index('timestamp')
        
        results = []
        total_net_pnl = 0.0
        
        for i in range(len(df_funding) - hold_epochs):
            entry_event = df_funding.iloc[i]
            exit_event = df_funding.iloc[i + hold_epochs]
            
            entry_time = entry_event['fundingTime']
            exit_time = exit_event['fundingTime']
            
            # We enter 1 minute BEFORE the funding epoch to guarantee capture
            # In a real environment, we'd enter earlier to avoid the pre-funding spread widening,
            # but for research, we assume entry near the epoch.
            
            # Use closest available timestamp in spot/perp
            try:
                spot_entry = df_spot.loc[:entry_time].iloc[-1]['close']
                perp_entry = df_perp.loc[:entry_time].iloc[-1]['close']
                
                spot_exit = df_spot.loc[:exit_time].iloc[-1]['close']
                perp_exit = df_perp.loc[:exit_time].iloc[-1]['close']
            except IndexError:
                continue # Missing data
                
            # 1. Entry Friction
            # We pay Taker on Spot, Taker on Perp
            entry_friction = (self.cost.entry_fee * 2) + (self.cost.entry_slip * 2)
            
            # 2. Accrue Funding
            # We hold for `hold_epochs` epochs.
            # We are SHORT perp. If funding rate is POSITIVE, shorts get paid.
            # If funding rate is NEGATIVE, shorts pay longs.
            collected_funding = 0.0
            for j in range(hold_epochs):
                rate = df_funding.iloc[i + j]['fundingRate']
                collected_funding += rate # Positive rate = income for short
                
            # 3. Basis PnL
            # Spot PnL (Long)
            spot_pnl = (spot_exit - spot_entry) / spot_entry
            # Perp PnL (Short)
            perp_pnl = (perp_entry - perp_exit) / perp_entry
            
            # Gross Basis Convergence PnL (Since it's 1-to-1 hedged, this is pure basis change)
            basis_pnl = spot_pnl + perp_pnl
            
            # 4. Exit Friction
            exit_friction = (self.cost.exit_fee * 2) + (self.cost.exit_slip * 2)
            
            # 5. Margin Liquidation Check
            # If the perp price spikes drastically, our short could be liquidated before we can unwind the spot.
            # Max leverage is 3x, so a 33% adverse move in the Perp liquidates us.
            max_perp_price = df_perp.loc[entry_time:exit_time]['high'].max()
            max_adverse_move = (max_perp_price - perp_entry) / perp_entry
            
            liquidated = False
            if max_adverse_move >= (1.0 / self.max_leverage):
                liquidated = True
                
            # 6. Calculate Net PnL
            if liquidated:
                # If liquidated, we lose the entire short margin (-33% of notional)
                # plus whatever the spot leg is currently worth. 
                # For simplicity, we assign a massive penalty to represent ruin.
                net_pnl = -1.0 # 100% loss of allocated capital
            else:
                gross_pnl = basis_pnl + collected_funding
                net_pnl = gross_pnl - (entry_friction + exit_friction)
                
            total_net_pnl += net_pnl
            results.append({
                "entry_time": entry_time,
                "hold_epochs": hold_epochs,
                "collected_funding": collected_funding,
                "basis_pnl": basis_pnl,
                "net_pnl": net_pnl,
                "liquidated": liquidated
            })
            
        return {
            "status": "AVAILABLE",
            "trades": len(results),
            "total_net_pnl_pct": total_net_pnl,
            "liquidations": sum(1 for r in results if r['liquidated']),
            "viable": total_net_pnl > 0 and sum(1 for r in results if r['liquidated']) == 0
        }
