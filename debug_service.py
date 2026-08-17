import sys
import os
sys.path.append(os.getcwd())

import pandas as pd
import numpy as np
import traceback
from testnet_engine.service import TestnetService

service = TestnetService()

# Create dummy DF with 250 rows
df = pd.DataFrame({
    'timestamp': pd.date_range('2026-08-01', periods=250, freq='1min'),
    'open': np.random.uniform(100, 105, 250),
    'high': np.random.uniform(105, 110, 250),
    'low': np.random.uniform(95, 100, 250),
    'close': np.random.uniform(100, 105, 250),
    'volume': np.random.uniform(10, 100, 250),
    'vol_delta': np.random.uniform(-10, 10, 250),
    'buy_vol': np.random.uniform(5, 50, 250),
    'sell_vol': np.random.uniform(5, 50, 250)
})

try:
    service.on_candle_closed("BTCUSDT", "1m", df)
    print("SUCCESS")
except Exception as e:
    print("EXCEPTION CAUGHT:")
    traceback.print_exc()
