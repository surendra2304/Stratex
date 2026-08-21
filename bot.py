import os
import signal
import socket
import sys

_SOCKET_LOCK = None
PID_FILE = "bot.pid"

def _cleanup():
    global _SOCKET_LOCK
    if _SOCKET_LOCK:
        try:
            _SOCKET_LOCK.close()
        except:
            pass
    if os.path.exists(PID_FILE):
        try:
            os.remove(PID_FILE)
        except:
            pass

def acquire_singleton_lock(port=48888):
    """Binds to a local port to guarantee exactly one bot daemon runs at any time."""
    global _SOCKET_LOCK
    _SOCKET_LOCK = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _SOCKET_LOCK.bind(("127.0.0.1", port))
    except OSError as e:
        print(f"\n[FATAL] Another instance of the trading bot is already running on port {port}!")
        print(f"Aborting to prevent duplicate order submissions. Error: {e}")
        sys.exit(1)
        
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))
    except:
        pass
        
def _signal_handler(signum, frame):
    print(f"\n[BOT] Received signal {signum}. Initiating graceful shutdown...")
    _cleanup()
    sys.exit(0)

def main():
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
    acquire_singleton_lock(port=48888)
    
    print("=" * 60)
    print("  ALGORITHMIC TRADING BOT FRAMEWORK")
    print("  [ACTIVE] TESTNET SERVICE (SINGLETON DAEMON)")
    print(f"  [PID] {os.getpid()}")
    print("=" * 60)
    
    from config import TRADING_MODE
    if TRADING_MODE == "TESTNET":
        from testnet_engine.service import TestnetService
        print("Starting bot execution...")
        service = TestnetService()
        try:
            service.run()
        except KeyboardInterrupt:
            print("\n[BOT] Stopped by user.")
        except Exception as e:
            print(f"\n[BOT] Unexpected exception: {e}")
            raise
        finally:
            _cleanup()
    else:
        print(f"TRADING_MODE {TRADING_MODE} is not supported by this entrypoint. Please configure TRADING_MODE=TESTNET.")

if __name__ == "__main__":
    main()
