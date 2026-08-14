import pandas as pd
import numpy as np

class MakerSimulator:
    """
    Part 6 & 7: Realistic Maker Execution Simulator
    Models limit orders resting in the order book.
    """
    def __init__(self, cost_engine):
        self.cost = cost_engine
        
    def simulate_maker_trade(self, entry_price, direction, subsequent_data, pt_pct, sl_pct, timeout_limit):
        """
        Simulates a limit order resting at `entry_price`.
        Uses conservative 'no-touch' logic:
        A buy limit order at $60000 is ONLY filled if the price drops STRICTLY BELOW $60000.
        If the low is exactly $60000, we assume queue position prevents a fill.
        
        Returns:
            dict with fill_status, exit_status, net_pnl
        """
        filled = False
        fill_idx = -1
        
        # 1. Simulate Entry
        for i, row in subsequent_data.iterrows():
            if i > timeout_limit:
                break
                
            if direction == "LONG":
                # Price must drop strictly below our entry for a guaranteed fill
                if row['low'] < entry_price:
                    filled = True
                    fill_idx = i
                    break
            elif direction == "SHORT":
                # Price must rise strictly above our entry
                if row['high'] > entry_price:
                    filled = True
                    fill_idx = i
                    break
                    
        if not filled:
            return {"status": "MISSED_FILL", "net_pnl": 0.0, "duration": timeout_limit}
            
        # 2. Simulate Exit (Also Maker, assuming we rest limit orders for exits)
        exit_pt = entry_price * (1 + pt_pct) if direction == "LONG" else entry_price * (1 - pt_pct)
        exit_sl = entry_price * (1 - sl_pct) if direction == "LONG" else entry_price * (1 + sl_pct)
        
        exit_status = "TIMEOUT"
        gross_pnl_pct = 0.0
        duration = timeout_limit - fill_idx
        
        # We must rest the exit order
        for j in range(fill_idx + 1, min(len(subsequent_data), fill_idx + 1 + timeout_limit)):
            row = subsequent_data.iloc[j]
            
            if direction == "LONG":
                # Did we hit SL? (Taker exit)
                if row['low'] <= exit_sl:
                    exit_status = "HIT_SL_TAKER"
                    gross_pnl_pct = -sl_pct
                    duration = j - fill_idx
                    break
                # Did we hit PT? (Maker exit, must trade strictly above)
                elif row['high'] > exit_pt:
                    exit_status = "HIT_PT_MAKER"
                    gross_pnl_pct = pt_pct
                    duration = j - fill_idx
                    break
            else: # SHORT
                # Hit SL (Taker exit)
                if row['high'] >= exit_sl:
                    exit_status = "HIT_SL_TAKER"
                    gross_pnl_pct = -sl_pct
                    duration = j - fill_idx
                    break
                # Hit PT (Maker exit, must trade strictly below)
                elif row['low'] < exit_pt:
                    exit_status = "HIT_PT_MAKER"
                    gross_pnl_pct = pt_pct
                    duration = j - fill_idx
                    break
                    
        # 3. Apply Costs
        if exit_status == "HIT_PT_MAKER":
            # Maker entry + Maker exit
            net_pnl = gross_pnl_pct - (self.cost.entry_fee + self.cost.exit_fee)
        elif exit_status == "HIT_SL_TAKER":
            # Maker entry + Taker exit (we pay slippage on the Taker stop loss)
            net_pnl = gross_pnl_pct - (self.cost.entry_fee + 0.001 + 0.0005) # Assume 0.1% taker fee + 0.05% slip
        else: # TIMEOUT
            # If timeout, we must market close
            final_price = subsequent_data.iloc[min(len(subsequent_data)-1, fill_idx + timeout_limit)]['close']
            if direction == "LONG":
                gross_pnl_pct = (final_price - entry_price) / entry_price
            else:
                gross_pnl_pct = (entry_price - final_price) / entry_price
            net_pnl = gross_pnl_pct - (self.cost.entry_fee + 0.001 + 0.0005)
            
        return {
            "status": exit_status,
            "net_pnl": net_pnl,
            "duration": duration
        }
