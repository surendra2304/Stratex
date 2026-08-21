
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
        
        # Explicit Capital Accounting
        starting_capital = 10000.0
        current_capital = starting_capital
        
        in_position = False
        entry_time = None
        spot_entry = 0
        perp_entry = 0
        notional_allocation = 0
        accumulated_funding_pct = 0.0
        epochs_held = 0
        
        for i, row in df_funding.iterrows():
            if not in_position:
                # ENTRY CONDITION
                # Check if we can enter (we need valid spot/perp data)
                entry_time = row['fundingTime']
                try:
                    spot_entry = df_spot.loc[:entry_time].iloc[-1]['close']
                    perp_entry = df_perp.loc[:entry_time].iloc[-1]['close']
                except IndexError:
                    continue # Missing data, skip
                    
                in_position = True
                epochs_held = 0
                accumulated_funding_pct = 0.0
                
                # We allocate 100% of current_capital to this trade
                notional_allocation = current_capital
            else:
                # IN POSITION - ACCRUE FUNDING
                rate = row['fundingRate']
                accumulated_funding_pct += rate
                epochs_held += 1
                
                # Check Exit Condition (Hold for exactly `hold_epochs`)
                if epochs_held >= hold_epochs:
                    exit_time = row['fundingTime']
                    try:
                        spot_exit = df_spot.loc[:exit_time].iloc[-1]['close']
                        perp_exit = df_perp.loc[:exit_time].iloc[-1]['close']
                    except IndexError:
                        # If data is missing at exit, we are trapped. 
                        # In a real sim, we'd exit at next available. Here we assume zero basis change for the gap.
                        spot_exit = spot_entry
                        perp_exit = perp_entry
                        
                    # Calculate PnL Components
                    # We are Long Spot, Short Perp
                    spot_return = (spot_exit - spot_entry) / spot_entry
                    perp_return = (perp_entry - perp_exit) / perp_entry
                    basis_pnl_pct = spot_return + perp_return
                    
                    # Margin Liquidation Check
                    max_perp_price = df_perp.loc[entry_time:exit_time]['high'].max() if entry_time in df_perp.index and exit_time in df_perp.index else perp_entry
                    max_adverse_move = (max_perp_price - perp_entry) / perp_entry
                    
                    liquidated = max_adverse_move >= (1.0 / self.max_leverage)
                    
                    # Friction
                    entry_friction_pct = (self.cost.entry_fee * 2) + (self.cost.entry_slip * 2)
                    exit_friction_pct = (self.cost.exit_fee * 2) + (self.cost.exit_slip * 2)
                    
                    if liquidated:
                        # 100% loss of the allocated capital
                        net_pnl_dollar = -notional_allocation
                        basis_pnl_dollar = -notional_allocation
                        funding_pnl_dollar = 0
                    else:
                        basis_pnl_dollar = notional_allocation * basis_pnl_pct
                        funding_pnl_dollar = notional_allocation * accumulated_funding_pct
                        friction_dollar = notional_allocation * (entry_friction_pct + exit_friction_pct)
                        
                        net_pnl_dollar = basis_pnl_dollar + funding_pnl_dollar - friction_dollar
                        
                    current_capital += net_pnl_dollar
                    
                    results.append({
                        "entry_time": entry_time,
                        "exit_time": exit_time,
                        "hold_epochs": epochs_held,
                        "starting_capital": notional_allocation,
                        "basis_pnl": basis_pnl_dollar,
                        "funding_pnl": funding_pnl_dollar,
                        "net_pnl": net_pnl_dollar,
                        "ending_capital": current_capital,
                        "liquidated": liquidated
                    })
                    
                    # Reset State
                    in_position = False
                    
        total_return_pct = (current_capital - starting_capital) / starting_capital
        
        return {
            "status": "AVAILABLE",
            "trades": len(results),
            "total_return_pct": total_return_pct,
            "starting_capital": starting_capital,
            "ending_capital": current_capital,
            "liquidations": sum(1 for r in results if r['liquidated']),
            "viable": total_return_pct > 0 and sum(1 for r in results if r['liquidated']) == 0,
            "ledger": results
        }
