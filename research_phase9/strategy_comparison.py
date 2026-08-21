import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research_phase7.data_loader import download_and_verify_data
from research_phase9.funding_research import run_funding_arbitrage_research
from research_phase9.market_making_research import run_market_making_baseline
from research_phase9.pairs_research import run_pairs_research


def run_strategy_comparison():
    print("==============================================")
    print("PHASE 9: STRATEGY PIVOT & COMPARISON")
    print("==============================================\n")
    
    os.makedirs('backtest_results/phase9', exist_ok=True)
    results = {}
    
    print("[PHASE 9] 1. CONTROL: Loading Directional 15m Baseline (from Phase 8)")
    # From Phase 8, directional taker trading is mathematically unviable (-0.008 net expectancy).
    results['DIRECTIONAL_CONTROL'] = {
        "status": "AVAILABLE",
        "net_expectancy": -0.008,
        "viable": False,
        "notes": "Failed Phase 8 execution friction hurdle."
    }
    
    print("[PHASE 9] Loading BTCUSDT 1m data...")
    df_btc = download_and_verify_data("BTCUSDT", days=30, use_cache=True)
    
    print("[PHASE 9] 2. Evaluating Market Making (Maker Execution)...")
    mm_res = run_market_making_baseline(df_btc)
    results['MARKET_MAKING'] = mm_res
    print(f"  -> MM Net Expectancy: {mm_res['net_expectancy']:.4f}")
    
    print("[PHASE 9] 3. Evaluating Funding Arbitrage...")
    fund_res = run_funding_arbitrage_research("BTCUSDT")
    results['FUNDING_ARBITRAGE'] = fund_res
    if fund_res['status'] == "AVAILABLE":
        print(f"  -> Funding Net Edge: {fund_res['net_edge']:.4f}")
    else:
        print(f"  -> Funding skipped: {fund_res['reason']}")
        
    print("[PHASE 9] 4. Evaluating Pairs Trading (Relative Value)...")
    print("  -> Loading ETHUSDT...")
    df_eth = download_and_verify_data("ETHUSDT", days=30, use_cache=True)
    pairs_res = run_pairs_research(df_btc, df_eth, "BTCUSDT", "ETHUSDT")
    results['PAIRS_TRADING'] = pairs_res
    if pairs_res['status'] == "AVAILABLE":
        print(f"  -> Pairs Net PnL: {pairs_res['net_pnl_pct']:.4f}")
        
    # Write Reports
    with open('backtest_results/phase9/strategy_comparison.md', 'w') as f:
        f.write("# Phase 9: Multi-Strategy Comparison\n\n")
        
        f.write("## 1. Directional Taker (Control Baseline)\n")
        f.write(f"- Viable: {results['DIRECTIONAL_CONTROL']['viable']}\n")
        f.write(f"- Net Expectancy: {results['DIRECTIONAL_CONTROL']['net_expectancy']}\n\n")
        
        f.write("## 2. Market Making (Spread Capture)\n")
        f.write(f"- Viable: {mm_res['net_expectancy'] > 0}\n")
        f.write(f"- Avg 1m Range: {mm_res['avg_candle_range']*100:.3f}%\n")
        f.write(f"- Spread Capture Assumed: {mm_res['spread_capture']*100:.3f}%\n")
        f.write(f"- Net Expectancy: {mm_res['net_expectancy']:.5f}\n\n")
        
        f.write("## 3. Funding Arbitrage (Cash-and-Carry)\n")
        if fund_res['status'] == 'AVAILABLE':
            f.write(f"- Viable: {fund_res['viable']}\n")
            f.write(f"- Annualized Yield: {fund_res['annualized_yield']*100:.2f}%\n")
            f.write(f"- Net Edge over friction: {fund_res['net_edge']*100:.3f}%\n\n")
        else:
            f.write(f"- Status: {fund_res['status']} ({fund_res.get('reason')})\n\n")
            
        f.write("## 4. Pairs Trading (BTC/ETH)\n")
        if pairs_res['status'] == 'AVAILABLE':
            f.write(f"- Viable: {pairs_res['viable']}\n")
            f.write(f"- Stationary Spread (Cointegrated): {pairs_res['stationary']} (p={pairs_res['adf_p_value']:.4f})\n")
            f.write(f"- Trades: {pairs_res['trades']}\n")
            f.write(f"- Net PnL: {pairs_res['net_pnl_pct']*100:.2f}%\n\n")
            
    with open('backtest_results/phase9/phase9_summary.md', 'w') as f:
        f.write("# PHASE 9 EXECUTIVE SUMMARY\n\n")
        f.write("### Strategic Pivot Classification\n\n")
        
        def grade(res_key):
            res = results.get(res_key, {})
            viable = res.get('viable', False)
            if res_key == 'PAIRS_TRADING' and not res.get('stationary', False):
                return "C - Inconclusive (Not statistically cointegrated)"
            if viable or res.get('net_expectancy', -1) > 0:
                return "A - Strong Candidate"
            else:
                return "D - Reject (Negative Net Expectancy)"
                
        f.write("1. Directional Taker: D - Reject\n")
        f.write(f"2. Market Making: {grade('MARKET_MAKING')}\n")
        if fund_res['status'] == 'AVAILABLE':
            f.write(f"3. Funding Arbitrage: {grade('FUNDING_ARBITRAGE')}\n")
        if pairs_res['status'] == 'AVAILABLE':
            f.write(f"4. Pairs Trading: {grade('PAIRS_TRADING')}\n")
            
        f.write("\n### Conclusion\n")
        f.write("The fundamental limitation of crypto algorithmic trading on small timeframes is the fee-to-volatility ratio. ")
        f.write("Strategies that pay the taker fee continuously decay to zero. The only robust paths forward are structural (Funding Rates) or earning the spread (Maker).")
        
    print("\n[PHASE 9] Evaluation complete. Reports written to backtest_results/phase9/")

if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
    run_strategy_comparison()
