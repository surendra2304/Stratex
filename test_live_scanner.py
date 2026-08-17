import time
from testnet_engine.market_scanner import MarketScanner
from testnet_engine.service import TestnetService

def run_live_scanner_test():
    service = TestnetService()

    def mock_callback(symbol, tf, df, health):
        print(f"Candle closed for {symbol} {tf}. Rows: {len(df)}")
        try:
            service.on_candle_closed(symbol, tf, df, health)
            print(f"on_candle_closed success. last_evaluation: {service.last_evaluation.get(symbol)}")
        except Exception as e:
            import traceback
            traceback.print_exc()

    scanner = MarketScanner(
        symbols=["BTCUSDT"],
        timeframes=["1m"]
    )
    scanner.register_callback(mock_callback)

    print("Starting scanner...")
    scanner.start()
    time.sleep(15)
    print("Stopping scanner...")
    scanner.stop()

if __name__ == "__main__":
    run_live_scanner_test()
