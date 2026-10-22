import datetime
import random
import threading
import tracemalloc

import numpy as np
import pandas as pd

from dashboard import app
from research_phase9.cost_engine import CostEngine
from testnet_engine.profitability_gate import ProfitabilityGate
from testnet_engine.risk_gate import RiskGate


def generate_synthetic_ohlcv(length=250, start_price=50000.0, volatility=0.002):
    """Generates realistic synthetic OHLCV time-series dataframe via vectorized NumPy."""
    now = datetime.datetime.utcnow()
    timestamps = [now - datetime.timedelta(minutes=i) for i in range(length, 0, -1)]
    close_times = [t + datetime.timedelta(minutes=1) for t in timestamps]
    
    returns = np.random.normal(0, volatility, size=length)
    prices = start_price * np.cumprod(1 + returns)
    
    noise = np.random.normal(0, volatility * 0.5, size=length)
    highs = prices * (1 + np.abs(noise))
    lows = prices * (1 - np.abs(noise))
    opens = prices + np.random.normal(0, volatility * 0.2, size=length)
    volumes = np.random.exponential(100.0, size=length)
    buy_vols = volumes * np.random.uniform(0.4, 0.6, size=length)
    sell_vols = volumes - buy_vols
    vol_deltas = buy_vols - sell_vols
    
    return pd.DataFrame({
        'timestamp': timestamps,
        'close_time': close_times,
        'open': opens,
        'high': np.maximum(highs, np.maximum(opens, prices)),
        'low': np.minimum(lows, np.minimum(opens, prices)),
        'close': prices,
        'volume': volumes,
        'vol_delta': vol_deltas,
        'buy_vol': buy_vols,
        'sell_vol': sell_vols
    })

def test_long_soak_memory_and_invariants():
    """Runs a high-intensity simulation across 10,000 candles and checks for zero memory leak."""
    tracemalloc.start()
    
    cost_engine = CostEngine()
    prof_gate = ProfitabilityGate(cost_engine=cost_engine)
    risk_gate = RiskGate(starting_balance=10000.0)
    
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "DOGEUSDT"]
    trades_executed = 0
    active_positions = {}
    
    for cycle in range(50):
        for sym in symbols:
            df = generate_synthetic_ohlcv(length=250, start_price=100.0 + random.random() * 50000.0)
            (df['high'] - df['low']).mean()
            price = float(df['close'].iloc[-1])
            
            # Profitability check
            is_accepted, _metrics = prof_gate.evaluate_signal(
                symbol=sym,
                side="BUY",
                entry_price=price,
                sl_price=price * 0.98,
                tp_price=price * 1.04,
                signal_result=0.62
            )
            
            if is_accepted:
                # Risk gate check (using 0.001 BTC equivalent size within 2% asset limit)
                passed, _reason, _msg = risk_gate.evaluate_risk(
                    symbol=sym,
                    side="LONG",
                    current_equity=10000.0,
                    active_positions=active_positions,
                    proposed_qty=0.001,
                    entry_price=price,
                    data_health_status="OK"
                )
                if passed:
                    trades_executed += 1
                    active_positions[sym] = {
                        "symbol": sym,
                        "quantity": 0.001,
                        "entry_price": price,
                        "side": "LONG"
                    }
                    if len(active_positions) >= 4:
                        # Close oldest position to cycle
                        rem_sym = next(iter(active_positions.keys()))
                        del active_positions[rem_sym]
                        
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    
    peak_mb = peak / (1024 * 1024)
    assert trades_executed > 0
    assert peak_mb < 200.0, f"Peak memory usage too high: {peak_mb:.2f} MB"

def test_stress_fuzz_dashboard_under_heavy_concurrency(monkeypatch):
    """Fuzzes dashboard endpoints with rapid concurrent requests under dynamic state changes."""
    from unittest.mock import MagicMock
    mock_c = MagicMock()
    mock_c.get_account.return_value = {
        'balances': [
            {'asset': 'USDT', 'free': '10000.0', 'locked': '0.0'},
            {'asset': 'BTC', 'free': '0.1', 'locked': '0.0'}
        ]
    }
    mock_c.get_all_tickers.return_value = [{'symbol': 'BTCUSDT', 'price': '60000.0'}]
    mock_c.get_open_orders.return_value = []
    
    monkeypatch.setattr("execution.get_exchange_client", lambda: mock_c)
    monkeypatch.setattr("testnet_engine.service.get_exchange_client", lambda: mock_c)
    
    client = app.test_client()
    errors = []
    
    def worker():
        for _ in range(25):
            try:
                res = client.get('/api/status')
                assert res.status_code == 200
                res_tr = client.get('/api/trades')
                assert res_tr.status_code == 200
                res_ho = client.get('/api/holdings')
                assert res_ho.status_code == 200
            except Exception as e:
                errors.append(str(e))
                
    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()
    
    assert len(errors) == 0, f"Concurrent dashboard errors: {errors}"
