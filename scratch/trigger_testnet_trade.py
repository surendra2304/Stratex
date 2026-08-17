import sys
import os
sys.path.append(os.getcwd())

from testnet_engine.binance_execution import BinanceTestnetExecution
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_exec")

if __name__ == "__main__":
    exec_module = BinanceTestnetExecution()
    
    # Fake a legitimate BUY signal
    signal = {
        "signal_id": "LEGIT_TEST_123",
        "symbol": "BTCUSDT",
        "strategy": "TEST",
        "side": "BUY",
        "timeframe": "1m",
        "entry_price": 50000.0, # Just for local tracking
        "confidence": 0.95,
        "predicted_move": 0.02,
        "holding_horizon": "1H",
        "expected_gross_return": 0.02,
        "expected_net_return": 0.018,
        "estimated_fees": 0.002
    }
    
    logger.info("Executing mock signal...")
    success = exec_module.execute_trade(signal)
    
    if success:
        logger.info("TRADE EXECUTED SUCCESSFULLY.")
    else:
        logger.error("TRADE FAILED.")
