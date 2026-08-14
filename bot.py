# ==============================================================================
# BOT.PY - Main Entry Point: orchestrates data, strategy, and execution modules
# ==============================================================================
import sys
import io
import time
from datetime import datetime
from config import ACTIVE_STRATEGY, TOP_COINS_LIMIT, TRADE_QTY, MAX_OPEN_TRADES
from data import get_candles, add_indicators, get_current_price, get_top_gainers
from execution import place_market_order, get_open_orders, get_account_balance, monitor_open_trades
from logger import init_log

# Fix Windows terminal encoding (prevents crashes on special characters)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# Import all strategies
import strategy_scalper as scalper
import strategy_swing   as swing
import strategy_ml      as ml
import strategy_aggressor as aggressor

# Remove machine-specific python paths; use sys.executable if necessary.

def print_banner():
    print("=" * 60)
    print("  ALGORITHMIC TRADING BOT FRAMEWORK")
    print("  🔥 DYNAMIC HOT COIN SCANNER ACTIVATED")
    print(f"  Strategy : {ACTIVE_STRATEGY.upper()}")
    print(f"  Scanning : Top {TOP_COINS_LIMIT} Trending Assets")
    print(f"  Started  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

def get_strategy_signal(df):
    """Routes to the correct strategy based on config."""
    if ACTIVE_STRATEGY == "scalper":
        return scalper.get_signal(df)
    elif ACTIVE_STRATEGY == "swing":
        return swing.get_signal(df)
    elif ACTIVE_STRATEGY == "ml":
        return ml.get_signal(df)
    elif ACTIVE_STRATEGY == "aggressor":
        return aggressor.get_signal(df)
    elif ACTIVE_STRATEGY == "multi":
        for name, strat in [("SCALPER", scalper), ("SWING", swing), ("ML", ml), ("AGGRESSOR", aggressor)]:
            sig, sl, tp = strat.get_signal(df)
            if sig:
                print(f"[MULTI] Signal from {name}: {sig}")
                return sig, sl, tp
        return None, None, None
    return None, None, None

def main():
    init_log()
    print_banner()

    # Show starting balance
    balances = get_account_balance()
    print(f"\n[BOT] Account Balance: {balances}\n")

    poll_interval = 10  # seconds between checks
    refresh_interval = 600  # Refresh top gainers every 10 minutes (600s)
    last_refresh = 0
    active_symbols = []

    try:
        while True:
            current_time = time.time()
            if current_time - last_refresh > refresh_interval or not active_symbols:
                print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 🔄 Refreshing Top Gainers List...")
                active_symbols = get_top_gainers(TOP_COINS_LIMIT)
                last_refresh = current_time

            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Checking market for {len(active_symbols)} trending assets...")
            
            # 0. Check if any previous trades closed
            monitor_open_trades()

            for symbol in active_symbols:
                print(f"[{symbol}] Scanning...")
                # 1. Fetch and prepare data
                df = get_candles(symbol)
                if df is None:
                    print(f"[{symbol}] Failed to fetch data. Skipping...")
                    continue

                df = add_indicators(df)
                price = get_current_price(symbol)
                
                # 2. Check if we already have open trades for THIS symbol
                open_trades = get_open_orders(symbol)
                if open_trades >= MAX_OPEN_TRADES:
                    print(f"[{symbol}] Max open trades reached ({open_trades}). Skipping...")
                    continue

                # 3. Get signal from strategy
                signal, sl, tp = get_strategy_signal(df)

                # 4. Execute if signal found
                if signal:
                    print(f"[{symbol}] [SIGNAL] {signal} | SL: {sl:.2f} | TP: {tp:.2f}")
                    place_market_order(
                        strategy_name=ACTIVE_STRATEGY,
                        side=signal,
                        symbol=symbol,
                        quantity=TRADE_QTY,
                        sl=sl,
                        tp=tp
                    )
                    time.sleep(10)  # Rate limit safety
                else:
                    print(f"[{symbol}] No signal.")

            # Wait before next full market scan
            time.sleep(poll_interval)

    except KeyboardInterrupt:
        print("\n[BOT] Stopped by user.")
    except Exception as e:
        print(f"\n[ERROR] Fatal error: {e}")

if __name__ == "__main__":
    main()
