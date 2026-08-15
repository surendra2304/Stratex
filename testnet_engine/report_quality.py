import json
import os

OPPORTUNITY_LOG = os.getenv("TESTNET_OPPORTUNITY_LOG", "testnet_opportunity_log.jsonl")
LEDGER_LOG = os.getenv("TESTNET_LEDGER_FILE", "testnet_trade_ledger.jsonl")

def generate_report():
    print("========================================")
    print("PHASE 5: LIVE TESTNET STRATEGY QUALITY CONTROL REPORT")
    print("========================================")
    
    signals_evaluated = 0
    signals_accepted = 0
    signals_rejected = 0
    
    expected_gross_sum = 0.0
    expected_net_sum = 0.0
    accepted_with_metrics = 0
    
    if os.path.exists(OPPORTUNITY_LOG):
        with open(OPPORTUNITY_LOG, "r") as f:
            for line in f:
                if not line.strip(): continue
                try:
                    data = json.loads(line)
                    signals_evaluated += 1
                    
                    decision = data.get("decision", "")
                    if "ACCEPTED" in decision:
                        signals_accepted += 1
                        gr = data.get("expected_gross_return")
                        nr = data.get("expected_net_return")
                        if gr is not None and nr is not None:
                            expected_gross_sum += float(gr)
                            expected_net_sum += float(nr)
                            accepted_with_metrics += 1
                    else:
                        signals_rejected += 1
                except Exception:
                    pass
                    
    num_trades = 0
    total_net_pnl = 0.0
    wins = 0
    gross_profit = 0.0
    gross_loss = 0.0
    
    total_fees = 0.0
    total_slippage = 0.0
    
    peak_equity = 10000.0  # Assuming 10k start for reporting
    current_equity = 10000.0
    max_drawdown = 0.0
    
    if os.path.exists(LEDGER_LOG):
        with open(LEDGER_LOG, "r") as f:
            for line in f:
                if not line.strip(): continue
                try:
                    data = json.loads(line)
                    if "CLOSE" in data.get("action", ""):
                        num_trades += 1
                        pnl = float(data.get("pnl", 0.0))
                        total_net_pnl += pnl
                        
                        if pnl > 0:
                            wins += 1
                            gross_profit += pnl
                        else:
                            gross_loss += abs(pnl)
                            
                        # Try to parse simulated fees/slippage if recorded
                        total_fees += float(data.get("fees", 0.0))
                        total_slippage += float(data.get("slippage", 0.0))
                        
                        current_equity += pnl
                        if current_equity > peak_equity:
                            peak_equity = current_equity
                        
                        dd = (peak_equity - current_equity) / peak_equity
                        if dd > max_drawdown:
                            max_drawdown = dd
                except Exception:
                    pass
                    
    avg_expected_gross = (expected_gross_sum / accepted_with_metrics) if accepted_with_metrics > 0 else 0.0
    avg_expected_net = (expected_net_sum / accepted_with_metrics) if accepted_with_metrics > 0 else 0.0
    
    actual_avg_net = (total_net_pnl / num_trades) if num_trades > 0 else 0.0
    win_rate = (wins / num_trades) if num_trades > 0 else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float('inf') if gross_profit > 0 else 0.0
    
    print(f"Signals Evaluated: {signals_evaluated}")
    print(f"Signals Accepted:  {signals_accepted}")
    print(f"Signals Rejected:  {signals_rejected}")
    print(f"Avg Expected Gross Return: {avg_expected_gross:.4%}")
    print(f"Avg Expected Net Return:   {avg_expected_net:.4%}")
    print("----------------------------------------")
    print(f"Number of Trades:  {num_trades}")
    print(f"Actual Avg Net Return: {actual_avg_net:.2f} USDT")
    print(f"Win Rate:          {win_rate:.2%}")
    print(f"Profit Factor:     {profit_factor:.2f}")
    print(f"Maximum Drawdown:  {max_drawdown:.2%}")
    print(f"Estimated Fees:    {total_fees:.2f} USDT")
    print(f"Estimated Slippage:{total_slippage:.2f} USDT")
    print("========================================")
    print("PROFITABILITY NOT GUARANTEED")
    print("LIVE REMAINS BLOCKED.")
    print("========================================")

if __name__ == "__main__":
    generate_report()
