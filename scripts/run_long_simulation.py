import os
import sys
import time

# Add root directory to python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from paper_engine.alerts import AlertManager
from paper_engine.data_monitor import DataMonitor
from paper_engine.heartbeat import HeartbeatState
from paper_engine.portfolio import PaperPortfolio
from paper_engine.session import SessionState


def run_simulation(events=100000):
    print("Starting 100,000 Event Simulation...")
    
    session = SessionState(filename="sim_session.json")
    session.start_session({"mode": "SIMULATION"})
    
    hb = HeartbeatState(filename="sim_heartbeat.json")
    dm = DataMonitor(hb)
    AlertManager(filename="sim_alerts.json")
    port = PaperPortfolio(filename="sim_portfolio.json")
    
    start_t = time.time()
    
    # Run loop
    for i in range(events):
        t = start_t + i * 60
        price = 50000 + (i % 100) * 10
        
        # 1. Feed Data
        dm.process_tick("BTCUSDT", price, t)
        
        # 2. Chaos Injection: 1% Duplicate Data
        if i % 100 == 0:
            dm.process_tick("BTCUSDT", price, t)
            
        # 3. Chaos Injection: 1% Missing Data
        if i % 150 == 0:
            t += 120 # skip 2 minutes
            dm.process_tick("BTCUSDT", price, t)
            
        # 4. Strategy / Portfolio interaction (Trade every 1000 ticks)
        if i % 1000 == 0:
            pos_id = f"pos_{i}"
            port.allocate_margin(100.0, f"event_{i}")
            port.add_position(pos_id, "BTCUSDT", "BUY", price, 0.002)
            
        if i % 1000 == 500:
            # Close trade
            entry_i = i - 500
            pos_id = f"pos_{entry_i}"
            port.release_margin(100.0, f"event_exit_{i}")
            port.close_position(pos_id, price + 50, 0.5, t, 0.0)
            
        if i % 5000 == 0:
            # Reconcile memory growth limits or equity bounds visually in tests
            eq = port.get_equity({"BTCUSDT": price})
            assert eq > 0, "Equity corrupted!"
            
    session.stop_session()
    
    print("Simulation Complete!")
    print(f"Events Processed: {events}")
    print(f"Data Duplicates Handled: {dm.symbols['BTCUSDT']['duplicates']}")
    print(f"Data Gaps Detected: {dm.symbols['BTCUSDT']['gaps']}")
    print(f"Closed Trades: {port.realized_pnl > 0}")
    print(f"Final Equity: {port.get_equity({'BTCUSDT': 50000})}")

if __name__ == "__main__":
    run_simulation(100000)
