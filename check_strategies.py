import strategy_adx_ema
import strategy_aggressor
import strategy_ml
import strategy_scalper
import strategy_supertrend
import strategy_swing
from data import add_indicators, get_candles

print("Testing Strategy Signatures:")
for name, mod in [
    ("aggressor", strategy_aggressor),
    ("scalper", strategy_scalper),
    ("supertrend", strategy_supertrend),
    ("ml", strategy_ml),
    ("swing", strategy_swing),
    ("adx_ema", strategy_adx_ema)
]:
    has_get_signal = hasattr(mod, "get_signal")
    print(f"Strategy [{name}]: has get_signal = {has_get_signal}")

df = get_candles("BTCUSDT", "15m", 300)
if df is not None and not df.empty:
    df_ind = add_indicators(df)
    print(f"Candles retrieved: {len(df_ind)} bars with {len(df_ind.columns)} features.")
    for name, mod in [
        ("aggressor", strategy_aggressor),
        ("scalper", strategy_scalper),
        ("supertrend", strategy_supertrend),
        ("ml", strategy_ml),
        ("swing", strategy_swing),
        ("adx_ema", strategy_adx_ema)
    ]:
        try:
            res = mod.get_signal(df_ind)
            print(f"  [{name}] output: {res}")
        except Exception as e:
            print(f"  [{name}] ERROR: {e}")
else:
    print("Could not fetch test candles from Binance.")
