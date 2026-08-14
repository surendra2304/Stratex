# ==============================================================================
# BOT.PY - Main Entry Point: runs the selected strategy in a live loop
# ==============================================================================
import sys
import io
import time
from datetime import datetime
from config import ACTIVE_STRATEGY, SYMBOL, TRADE_QTY, MAX_OPEN_TRADES
from data import get_candles, add_indicators, get_current_price
from execution import place_market_order, get_open_orders, get_account_balance
from logger import init_log

# Fix Windows terminal encoding (prevents crashes on special characters)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# Import all strategies
import strategy_scalper as scalper
import strategy_swing   as swing
import strategy_ml      as ml

PYTHON_PATH = r"C:\Users\Surendra\AppData\Local\Programs\Python\Python311\python.exe"

def print_banner():
    print("=" * 60)
    print("  [BOT] ANTI GRAVITY TRADING BOT FRAMEWORK")
    print(f"  Strategy : {ACTIVE_STRATEGY.upper()}")
    print(f"  Symbol   : {SYMBOL}")
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
    elif ACTIVE_STRATEGY == "multi":
        # Run all strategies and take the FIRST signal found
        for name, strat in [("SCALPER", scalper), ("SWING", swing), ("ML", ml)]:
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

    try:
        while True:
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Checking market...")

            # 1. Fetch and prepare data
            df = get_candles()
            if df is None:
                print("Failed to fetch data. Retrying...")
                time.sleep(poll_interval)
                continue

            df = add_indicators(df)
            price = get_current_price()
            print(f"  Current Price: {price}")

            # 2. Check if we already have open trades
            open_trades = get_open_orders()
            if open_trades >= MAX_OPEN_TRADES:
                print(f"  Max open trades reached ({open_trades}). Waiting...")
                time.sleep(poll_interval)
                continue

            # 3. Get signal from strategy
            signal, sl, tp = get_strategy_signal(df)

            # 4. Execute if signal found
            if signal:
                print(f"  [SIGNAL] {signal} | SL: {sl:.2f} | TP: {tp:.2f}")
                place_market_order(
                    strategy_name=ACTIVE_STRATEGY,
                    side=signal,
                    symbol=SYMBOL,
                    quantity=TRADE_QTY,
                    sl=sl,
                    tp=tp
                )
                time.sleep(60)  # Wait 60s after a trade to avoid double entries
            else:
                print("  No signal. Waiting...")

            time.sleep(poll_interval)

    except KeyboardInterrupt:
        print("\n[BOT] Stopped by user.")
    except Exception as e:
        print(f"\n[ERROR] Fatal error: {e}")

if __name__ == "__main__":
    main()
