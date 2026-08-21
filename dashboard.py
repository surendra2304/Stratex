import csv
import datetime
import io
import json
import os
import sys

import threading
import time

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

import config
from config import ACTIVE_STRATEGIES, TRADING_MODE
from data import get_candles as fetch_candles
from logger import get_logger

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace') if __name__ == '__main__' else sys.stdout
logger = get_logger("dashboard")

app = Flask(__name__, static_folder='static')
# Hardened CORS configuration: Whitelist Render production, local dev servers, and localhost
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "https://algorithmic-trading-bot-fra.onrender.com,http://localhost:5000,http://127.0.0.1:5000,http://localhost:3000,http://127.0.0.1:3000").split(",") if o.strip()]
CORS(app, resources={r"/api/*": {"origins": ALLOWED_ORIGINS}, r"/health": {"origins": "*"}})

# Register Quantum advisory blueprint (research only, no execution)
from quantum_endpoint import quantum_bp

app.register_blueprint(quantum_bp, url_prefix='/api/quantum')

LOG_FILE = "trade_log.csv"

@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/')
def serve_index():
    return send_from_directory('static', 'index.html')

@app.route('/style.css')
def serve_root_css():
    return send_from_directory('static', 'style.css')

@app.route('/app.js')
def serve_root_js():
    return send_from_directory('static', 'app.js')

@app.route('/api/candles')
def get_candles():
    """
    Fetches live Binance OHLCV candles for chart.
    Strictly prohibits data fabrication: if Binance is unavailable, returns DATA_UNAVAILABLE.
    """
    symbol = request.args.get('symbol', 'BTCUSDT')
    tf = request.args.get('tf') or request.args.get('timeframe') or '15m'
    limit = int(request.args.get('limit', 300))
    
    try:
        df = fetch_candles(symbol, tf, limit)
        if df is not None and not df.empty:
            formatted = []
            for _, row in df.iterrows():
                formatted.append({
                    "time": int(row["timestamp"].timestamp()),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row.get("volume", 0.0)),
                    "symbol": symbol,
                    "timeframe": tf,
                    "source": "BINANCE",
                    "verified": True
                })
            return jsonify(formatted)
        
        # When Binance data is unavailable or empty, return 503 DATA_UNAVAILABLE (zero fabrication)
        return jsonify({
            "status": "DATA_UNAVAILABLE",
            "source": "BINANCE",
            "freshness": "STALE",
            "symbol": symbol,
            "timeframe": tf,
            "candles": [],
            "error": f"Binance market data temporarily unavailable for {symbol} ({tf})."
        }), 503
    except Exception as e:
        logger.error(f"Error fetching candles for {symbol}: {e}")
        return jsonify({
            "status": "DATA_UNAVAILABLE",
            "source": "BINANCE",
            "freshness": "STALE",
            "symbol": symbol,
            "timeframe": tf,
            "candles": [],
            "error": str(e)
        }), 503

def get_engine_health_data():
    """Reads engine heartbeat and verifies live process state."""
    env_hb = os.getenv("TESTNET_HEARTBEAT_FILE")
    if env_hb:
        hb_file = env_hb
    elif os.path.exists("testnet_heartbeat.json"):
        hb_file = "testnet_heartbeat.json"
    elif os.path.exists("heartbeat.json"):
        hb_file = "heartbeat.json"
    else:
        hb_file = "testnet_heartbeat.json"
        
    if not os.path.exists(hb_file):
        return {
            "engine_status": "OFFLINE",
            "healthy": False,
            "worker_alive": False,
            "heartbeat_age_seconds": None,
            "reason": "HEARTBEAT_FILE_MISSING",
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
        }
        
    try:
        with open(hb_file, "r") as f:
            hb = json.load(f)
            
        hb_ts_str = hb.get("timestamp", "")
        now = datetime.datetime.utcnow()
        if hb_ts_str:
            hb_dt = datetime.datetime.fromisoformat(hb_ts_str.replace("Z", "+00:00")).replace(tzinfo=None)
            age = (now - hb_dt).total_seconds()
        else:
            age = 999.0
            
        worker_alive = hb.get("worker_alive", False) and hb.get("status") == "RUNNING"
        pid = hb.get("pid")
        pid_alive = False
        if pid:
            try:
                if os.name == 'nt':
                    import ctypes
                    PROCESS_QUERY_INFORMATION = 0x0400
                    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
                    if handle:
                        ctypes.windll.kernel32.CloseHandle(handle)
                        pid_alive = True
                else:
                    os.kill(pid, 0)
                    pid_alive = True
            except Exception:
                pid_alive = False
                
        is_healthy = worker_alive and age <= 90 and (pid_alive or pid is None)
        engine_status = "ONLINE" if is_healthy else "OFFLINE"
        
        return {
            "engine_status": engine_status,
            "healthy": is_healthy,
            "worker_alive": worker_alive,
            "heartbeat_age_seconds": round(age, 2),
            "pid": pid,
            "pid_alive": pid_alive,
            "binance_connected": hb.get("binance_connected", True),
            "websocket_connected": hb.get("websocket_connected", True),
            "active_strategy": hb.get("strategy", "aggressor"),
            "strategies": hb.get("strategies", list(ACTIVE_STRATEGIES.keys())),
            "timeframes": hb.get("timeframes", ["1m", "5m", "15m", "1h", "4h"]),
            "symbols": hb.get("symbols", []),
            "symbol_count": hb.get("symbol_count", len(hb.get("symbols", []))),
            "last_market_update": hb.get("last_market_update"),
            "last_candle_close": hb.get("last_candle_close"),
            "last_strategy_evaluation": hb.get("last_strategy_evaluation"),
            "service_start_time": hb.get("service_start_time"),
            "timestamp": hb_ts_str or (datetime.datetime.utcnow().isoformat() + "Z")
        }
    except Exception as e:
        return {
            "engine_status": "OFFLINE",
            "healthy": False,
            "worker_alive": False,
            "heartbeat_age_seconds": None,
            "reason": f"READ_ERROR: {e!s}",
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
        }

@app.route('/health')
@app.route('/api/health')
def health():
    engine_data = get_engine_health_data()
    return jsonify({
        "status": "ok",
        "dashboard": "online",
        "engine": engine_data["engine_status"].lower(),
        "engine_healthy": engine_data["healthy"],
        "mode": TRADING_MODE,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
    }), 200

@app.route('/api/engine-health')
def api_engine_health():
    return jsonify(get_engine_health_data())

_holdings_cache = None
_holdings_cache_ts = 0.0
_holdings_lock = threading.Lock()

def get_live_account_and_holdings(force_refresh=False):
    """
    Queries Binance Spot for live balances, active crypto positions,
    and converts every non-USDT asset to current USD value.
    Cached for 2 seconds to optimize concurrent dashboard polling.
    """
    global _holdings_cache, _holdings_cache_ts
    now = time.time()
    if not force_refresh and _holdings_cache and (now - _holdings_cache_ts < 2.0):
        return _holdings_cache
        
    with _holdings_lock:
        if not force_refresh and _holdings_cache and (time.time() - _holdings_cache_ts < 2.0):
            return _holdings_cache
            
        from data_client import MarketDataClient
        from execution import get_exchange_client
        
        usdt_free = 0.0
        usdt_locked = 0.0
        holdings = []
        total_crypto_value = 0.0
        active_trade_holdings_value = 0.0
        
        # Identify genuine active bot assets from the portfolio state
        port_file = os.getenv("TESTNET_PORTFOLIO_FILE", "testnet_portfolio.json")
        active_bot_assets = set()
        if os.path.exists(port_file):
            try:
                with open(port_file, "r") as pf:
                    p_data = json.load(pf)
                    for sym, pos in p_data.get("positions", {}).items():
                        if isinstance(pos, dict) and pos.get("status") == "OPEN":
                            base_asset = sym.replace("USDT", "").replace("USDC", "").replace("BUSD", "").replace("FDUSD", "")
                            active_bot_assets.add(base_asset)
            except Exception as pe:
                logger.error(f"[DASHBOARD] Error reading portfolio for active holdings: {pe}")
        
        try:
            client = get_exchange_client()
            if client:
                account = client.get_account()
            md = MarketDataClient()
            tickers = {}
            if md.is_available():
                for t in md.get_ticker():
                    tickers[t['symbol']] = float(t['lastPrice'])
            
            for b in account.get('balances', []):
                asset = b['asset']
                free = float(b['free'])
                locked = float(b['locked'])
                total_qty = free + locked
                if total_qty <= 0:
                    continue
                    
                if asset == 'USDT':
                    usdt_free = free
                    usdt_locked = locked
                    continue
                    
                pair = f"{asset}USDT"
                price = tickers.get(pair, 0.0)
                usd_val = total_qty * price
                
                # If price is not directly in USDT, try direct lookup
                if price <= 0 and asset in ['USDC', 'TUSD', 'FDUSD', 'USDS', 'RLUSD']:
                    price = 1.0
                    usd_val = total_qty
                    
                if usd_val > 0.05:
                    # A holding is a genuine active bot trade ONLY if locked in an exchange order (e.g. OCO)
                    # or recorded as an open position in the local portfolio state.
                    # Unmanaged faucet airdrops with locked == 0 are classified as unmanaged holdings.
                    is_bot_trade = (asset in active_bot_assets) or (locked > 0)
                    h_info = {
                        "asset": asset,
                        "symbol": pair if price > 0 else asset,
                        "free": free,
                        "locked": locked,
                        "total_quantity": total_qty,
                        "price": price,
                        "usd_value": usd_val,
                        "is_bot_trade": is_bot_trade
                    }
                    holdings.append(h_info)
                    total_crypto_value += usd_val
                    if is_bot_trade and asset not in ['USDC', 'TUSD', 'FDUSD', 'USDS', 'USDP', 'RLUSD']:
                        # If locked, only the locked portion is deployed in the bot trade
                        trade_qty = locked if locked > 0 else total_qty
                        active_trade_holdings_value += trade_qty * price
        except Exception as e:
            logger.error(f"[DASHBOARD] Error calculating live holdings: {e}")
            
        holdings.sort(key=lambda x: x['usd_value'], reverse=True)
        res_dict = {
            "usdt_free": round(usdt_free, 2),
            "usdt_locked": round(usdt_locked, 2),
            "usdt_total_cash": round(usdt_free + usdt_locked, 2),
            "total_crypto_value": round(total_crypto_value, 2),
            "active_trade_holdings_value": round(active_trade_holdings_value, 2),
            "holdings": holdings
        }
        _holdings_cache = res_dict
        _holdings_cache_ts = time.time()
        return res_dict

def verify_funnel(s, open_count, closed_count):
    """Mathematically verifies consistency across signal funnel pipeline stages."""
    errors = []
    sum_rejections = (s.get("PROFITABILITY_REJECTED", 0) + s.get("RISK_REJECTED", 0) + 
                      s.get("COOLDOWN_REJECTED", 0) + s.get("JIT_REJECTED", 0) + 
                      s.get("OTHER_REJECTED", 0) + s.get("QUALIFIED", 0))
    if s.get("TOTAL_SIGNALS", 0) != sum_rejections:
        errors.append(f"TOTAL_SIGNALS {s.get('TOTAL_SIGNALS')} != sum {sum_rejections}")
        
    sum_qual = s.get("ORDERS_SUBMITTED", 0) + s.get("EXECUTION_REJECTED", 0)
    if s.get("QUALIFIED", 0) != sum_qual:
        errors.append(f"QUALIFIED {s.get('QUALIFIED')} != sum {sum_qual}")
        
    sum_sub = s.get("ORDERS_FILLED", 0) + s.get("ORDERS_FAILED", 0)
    if s.get("ORDERS_SUBMITTED", 0) != sum_sub:
        errors.append(f"ORDERS_SUBMITTED {s.get('ORDERS_SUBMITTED')} != sum {sum_sub}")
        
    sum_fill = open_count + closed_count
    if s.get("ORDERS_FILLED", 0) != sum_fill:
        errors.append(f"ORDERS_FILLED {s.get('ORDERS_FILLED')} != sum {sum_fill} (Open: {open_count}, Closed: {closed_count})")
        
    return errors

@app.route('/api/holdings')
def api_holdings():
    """Returns detailed asset breakdown explaining capital deployment."""
    data = get_live_account_and_holdings()
    return jsonify(data)

@app.route('/api/open-orders')
def api_open_orders():
    """Fetches real-time open orders from Binance Testnet."""
    from execution import get_exchange_client
    orders = []
    try:
        client = get_exchange_client()
        if client:
            raw_orders = client.get_open_orders()
            for o in raw_orders:
                orders.append({
                    "order_id": o.get("orderId"),
                    "symbol": o.get("symbol"),
                    "side": o.get("side"),
                    "type": o.get("type"),
                    "price": float(o.get("price", 0.0)),
                    "stop_price": float(o.get("stopPrice", 0.0)),
                    "orig_qty": float(o.get("origQty", 0.0)),
                    "executed_qty": float(o.get("executedQty", 0.0)),
                    "status": o.get("status"),
                    "time": o.get("time"),
                    "is_working": o.get("isWorking", True)
                })
    except Exception as e:
        logger.error(f"[DASHBOARD] Failed to get open orders: {e}")
    return jsonify(orders)

@app.route('/api/status')
def get_status():
    """Unified authoritative status and portfolio calculation."""
    import config
    
    account_holdings = get_live_account_and_holdings()
    usdt_cash = account_holdings["usdt_total_cash"]
    crypto_trade_val = account_holdings["active_trade_holdings_value"]
    
    # Defaults
    overall = "OK"
    components = {}
    session_info = {"status": "ACTIVE"}
    alerts = []
    realized_pnl = 0.0
    unrealized_pnl = 0.0
    fees = 0.0
    funding = 0.0
    used_margin = 0.0
    open_positions = 0
    mdd = 0.0
    safety_halt = False
    bot_start_time = None
    equity_high = None
    equity_low = None
    equity_change = None
    today_pnl_abs = 0.0
    
    # 1. Read Testnet Portfolio
    port_file = os.getenv("TESTNET_PORTFOLIO_FILE", "testnet_portfolio.json")
    open_pos_list = []
    if os.path.exists(port_file):
        try:
            with open(port_file, "r") as f:
                port = json.load(f)
            
            bot_start_time = port.get("service_start_time")
            safety_halt = port.get("safety_halt", False)
            realized_pnl = float(port.get("realized_pnl", 0.0))
            fees = float(port.get("fees", 0.0))
            
            # Compute unrealized PnL across active positions
            for pos_sym, pos in port.get("positions", {}).items():
                if not isinstance(pos, dict): continue
                if pos.get('status', 'OPEN') != "OPEN": continue
                try:
                    sym = pos.get("symbol", pos_sym)
                    entry_price = float(pos.get("entry_price", 0.0))
                    quantity = float(pos.get("quantity", 0.0))
                    direction = pos.get("direction", pos.get("side", "BUY"))
                    
                    current_price = entry_price
                    try:
                        df = fetch_candles(sym, "1m", 1)
                        if not df.empty:
                            current_price = float(df['close'].iloc[-1])
                    except Exception:
                        pass
                        
                    u_pnl = 0.0
                    if current_price > 0 and entry_price > 0 and quantity > 0:
                        used_margin += entry_price * quantity
                        if direction in ["LONG", "BUY"]:
                            u_pnl = (current_price - entry_price) * quantity
                        else:
                            u_pnl = (entry_price - current_price) * quantity
                    
                    unrealized_pnl += u_pnl
                    open_pos_list.append({
                        "symbol": sym,
                        "side": direction,
                        "entry_price": entry_price,
                        "current_price": current_price,
                        "quantity": quantity,
                        "unrealized_pnl": u_pnl,
                        "sl": pos.get("sl_price", pos.get("sl", 0.0)),
                        "tp": pos.get("tp_price", pos.get("tp", 0.0)),
                        "strategy": pos.get("strategy", "aggressor"),
                        "timestamp": pos.get("entry_timestamp", pos.get("timestamp", ""))
                    })
                except Exception as pos_err:
                    logger.error(f"Error calculating open pos PnL: {pos_err}")
            
            open_positions = sum(1 for p in port.get("positions", {}).values() if isinstance(p, dict) and p.get("status") == "OPEN")
            mdd = float(port.get("max_drawdown", 0.0)) * 100
        except Exception as e:
            logger.error(f"Failed to process testnet portfolio: {e}")

    try:
        trades_data = _get_trades_data()
        if trades_data:
            if trades_data.get("positions"):
                realized_pnl = float(trades_data.get("net_pnl", 0.0))
                fees = float(sum(t.get("fees", 0.0) for t in trades_data.get("positions", [])))
            elif "net_pnl" in trades_data and float(trades_data.get("net_pnl", 0.0)) != 0.0:
                realized_pnl = float(trades_data.get("net_pnl", 0.0))
    except Exception as td_err:
        logger.error(f"Failed to load trades data: {td_err}")

    # Read today's equity history for High / Low calculation
    hist_file = os.getenv("TESTNET_EQUITY_HISTORY_FILE", "testnet_equity_history.jsonl")
    today_utc_str = datetime.datetime.utcnow().date().isoformat()
    today_equities = []
    if os.path.exists(hist_file):
        try:
            with open(hist_file, "r") as f:
                for line in f:
                    if not line.strip(): continue
                    rec = json.loads(line.strip())
                    ts = rec.get("timestamp", "")
                    if ts.startswith(today_utc_str):
                        eq_val = float(rec.get("equity", rec.get("balance", 0.0)))
                        if eq_val > 0:
                            today_equities.append(eq_val)
        except Exception as eh_err:
            logger.warning(f"Error parsing equity history: {eh_err}")

    if today_equities:
        equity_high = max(today_equities)
        equity_low = min(today_equities)
        first_eq = today_equities[0]
        if first_eq > 0:
            equity_change = round(((today_equities[-1] - first_eq) / first_eq) * 100, 2)

    # Total Valuation: Liquid USDT Cash + Current Market Value of Active Crypto Trades
    # Note: Crypto Trade Value is ALREADY Mark-to-Market (current_price * quantity = cost_basis + unrealized_pnl).
    # Realized PnL is ALREADY credited into Binance USDT Cash upon trade close.
    # Therefore, Total Equity = USDT Cash + Active Crypto Holdings Value.
    # We MUST NEVER add unrealized_pnl or realized_pnl on top of (Cash + Active Crypto Value).
    if usdt_cash > 0 or crypto_trade_val > 0:
        total_equity = usdt_cash + crypto_trade_val
    else:
        base_cap = float(port.get("starting_equity", port.get("starting_balance", 10000.0))) if (isinstance(port, dict) if 'port' in locals() else False) else 10000.0
        total_equity = base_cap + realized_pnl + unrealized_pnl

    engine_data = get_engine_health_data()
    components["engine"] = "OK" if engine_data["healthy"] else "ERROR"
    components["binance"] = "OK" if engine_data.get("binance_connected") else "ERROR"
    components["data"] = "OK" if engine_data.get("websocket_connected") else "ERROR"
    components["execution"] = "OK" if engine_data["healthy"] else "ERROR"
    components["strategy"] = "OK" if engine_data["healthy"] else "ERROR"
    if not engine_data["healthy"]:
        overall = "DEGRADED"
        
    risk_used = 0.0
    if total_equity > 0:
        risk_used = (crypto_trade_val / total_equity) * 100
    available_risk = max(0.0, (config.MAX_TESTNET_EXPOSURE * 100) - risk_used)
    
    # Use raw drawdown value — never synthesize a fake number
    clean_mdd = abs(mdd) if mdd != 0.0 else 0.0

    return jsonify({
        "mode": TRADING_MODE,
        "overall_health": overall,
        "engine_status": engine_data["engine_status"],
        "engine_healthy": engine_data["healthy"],
        "engine_data": engine_data,
        "components": components,
        "session": session_info,
        "alerts": alerts,
        "equity": round(total_equity, 2),
        "cash": round(usdt_cash, 2),
        "crypto_holdings_value": round(crypto_trade_val, 2),
        "total_crypto_value": round(account_holdings["total_crypto_value"], 2),
        "holdings": account_holdings["holdings"],
        "realized_pnl": round(realized_pnl, 4),
        "unrealized_pnl": round(unrealized_pnl, 4),
        "today_pnl": round(realized_pnl + unrealized_pnl, 4),
        "fees": round(fees, 4),
        "funding": funding,
        "used_margin": round(crypto_trade_val, 2),
        "risk_used": round(risk_used, 2),
        "exposure_pct": round(risk_used, 2),
        "available_risk": round(available_risk, 2),
        "limits": {
            "max_exposure": config.MAX_TESTNET_EXPOSURE * 100,
            "max_drawdown": config.MAX_TESTNET_DRAWDOWN_PCT * 100,
            "max_positions": config.MAX_OPEN_POSITIONS,
            "target_trade_count": getattr(config, "TARGET_TRADE_COUNT", 100),
            "target_trade_window_hours": getattr(config, "TARGET_TRADE_WINDOW_HOURS", 3)
        },
        "open_positions": open_positions,
        "open_positions_data": open_pos_list,
        "max_drawdown": round(clean_mdd, 2),
        "server_time": datetime.datetime.utcnow().isoformat() + "Z",
        "bot_start_time": bot_start_time,
        "safety_halt": safety_halt,
        "equity_high": equity_high,
        "equity_low": equity_low,
        "equity_change": equity_change
    })

@app.route('/api/trades')
def get_trades():
    return jsonify(_get_trades_data())

def _get_trades_data():
    """Parses trade ledgers, merges Binance live execution history, and deduplicates."""
    net_pnl = 0.0
    wins = 0
    losses = 0
    gross_profit = 0.0
    gross_loss = 0.0
    positions = []
    seen_trade_keys = set()
    
    # 1. Parse closed trades from ledger
    ledger_file = os.getenv("TESTNET_LEDGER_FILE", "testnet_trade_ledger.jsonl")
    if os.path.exists(ledger_file):
        with open(ledger_file, "r") as f:
            for line in f:
                try:
                    trade = json.loads(line.strip())
                    if not trade: continue
                    
                    prov = str(trade.get("provenance", "")).upper()
                    source = str(trade.get("source", "")).upper()
                    strategy = str(trade.get("strategy", "")).upper()
                    status = str(trade.get("status", "")).upper()
                    
                    INVALID_PROVENANCES = ["TEST", "PAPER", "SYNTHETIC", "SYNTHETIC_GENERATED", "UNVERIFIED", "MOCK", "RECOVERED_WITHOUT_BINANCE_PROOF"]
                    if source in INVALID_PROVENANCES or prov in INVALID_PROVENANCES or strategy in INVALID_PROVENANCES or status == "OPEN":
                        continue
                    
                    symbol = trade.get("symbol", "")
                    entry_oid = str(trade.get("entry_order_id", ""))
                    exit_oid = str(trade.get("exit_order_id", ""))
                    
                    if exit_oid and exit_oid != "None":
                        key = f"{symbol}_{exit_oid}"
                    elif entry_oid and entry_oid != "None":
                        key = f"{symbol}_{entry_oid}"
                    else:
                        key = f"{symbol}_{trade.get('exit_timestamp', trade.get('timestamp', ''))}_{trade.get('pnl', '')}"
                    
                    if key in seen_trade_keys:
                        continue
                    seen_trade_keys.add(key)
                    
                    pnl = float(trade.get("net_pnl", trade.get("pnl", trade.get("gross_pnl", 0.0))))
                    fees = float(trade.get("total_fees", trade.get("fees", trade.get("entry_fee", 0.0) + trade.get("exit_fee", 0.0))))
                    
                    if pnl > 0:
                        wins += 1
                        gross_profit += pnl
                    elif pnl < 0:
                        losses += 1
                        gross_loss += abs(pnl)
                        
                    positions.append({
                        "timestamp": trade.get("exit_timestamp", trade.get("timestamp", trade.get("entry_timestamp", ""))),
                        "symbol": symbol,
                        "action": trade.get("side", trade.get("action", "BUY")).replace("CLOSED_", "").replace("CLOSE_", ""),
                        "strategy": trade.get("strategy", "aggressor"),
                        "source": source or trade.get("source", "BINANCE_EXECUTION"),
                        "entry_price": float(trade.get("entry_price", 0.0)),
                        "exit_price": float(trade.get("exit_price", 0.0)),
                        "quantity": float(trade.get("quantity", trade.get("entry_executed_quantity", 0.0))),
                        "gross_pnl": float(trade.get("gross_pnl", pnl)),
                        "fees": fees,
                        "pnl": pnl,
                        "status": "CLOSED",
                        "exit_reason": trade.get("exit_reason", "OCO_TARGET"),
                        "order_id": trade.get("exit_order_id") or trade.get("entry_order_id") or trade.get("signal_id", "LIVE-TRADE")
                    })
                except Exception as e:
                    logger.error(f"Error parsing trade ledger line: {e}")

    positions.sort(key=lambda x: str(x.get("timestamp", "")), reverse=True)
    total_closed = wins + losses
    net_pnl = gross_profit - gross_loss
    win_rate = (wins / total_closed * 100) if total_closed > 0 else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else ("Infinity" if gross_profit > 0 else 0.0)
    
    return {
        "net_pnl": round(net_pnl, 4),
        "win_rate": round(win_rate, 2),
        "total_trades": total_closed,
        "wins": wins,
        "losses": losses,
        "gross_profit": round(gross_profit, 4),
        "gross_loss": round(gross_loss, 4),
        "profit_factor": profit_factor,
        "positions": positions
    }

@app.route('/api/equity')
@app.route('/api/equity-timeline')
@app.route('/api/balance-timeline')
def api_equity():
    """Returns historical equity & balance curve points with rich snapshot data for chart."""
    from testnet_engine.telemetry_manager import get_telemetry_manager
    tf_filter = request.args.get("timeframe", "ALL").upper()
    now = datetime.datetime.utcnow()
    cutoff = None
    if tf_filter == "1H":
        cutoff = now - datetime.timedelta(hours=1)
    elif tf_filter == "6H":
        cutoff = now - datetime.timedelta(hours=6)
    elif tf_filter == "1D":
        cutoff = now - datetime.timedelta(days=1)
    elif tf_filter == "7D":
        cutoff = now - datetime.timedelta(days=7)

    tm = get_telemetry_manager()
    raw_snaps = tm.get_equity_timeline(time_range="all")
    
    points = []
    for snap in raw_snaps:
        ts_str = snap.get("timestamp", "")
        if ts_str:
            try:
                dt = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00")).replace(tzinfo=None)
                if cutoff and dt < cutoff:
                    continue
                cash = float(snap.get("cash_usdt", snap.get("cash", 0.0)))
                managed = float(snap.get("asset_market_value", snap.get("crypto_holdings_value", 0.0)))
                eq = float(snap.get("total_equity", snap.get("equity", cash + managed)))
                realized = float(snap.get("realized_pnl", 0.0))
                unrealized = float(snap.get("unrealized_pnl", 0.0))

                points.append({
                    "time": int(dt.timestamp() * 1000),
                    "timestamp": ts_str,
                    "equity": eq,
                    "cash": cash,
                    "managed_assets": managed,
                    "realized_pnl": realized,
                    "unrealized_pnl": unrealized
                })
            except Exception:
                pass

    # Fallback to local equity file if telemetry store empty
    if not points:
        eq_file = os.getenv("TESTNET_EQUITY_HISTORY_FILE", "testnet_equity_history.jsonl")
        if os.path.exists(eq_file):
            try:
                with open(eq_file, "r") as f:
                    for line in f:
                        if not line.strip(): continue
                        try:
                            snap = json.loads(line)
                            ts_str = snap.get("timestamp", "")
                            if ts_str:
                                dt = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00")).replace(tzinfo=None)
                                if cutoff and dt < cutoff:
                                    continue
                                cash = float(snap.get("cash_usdt", snap.get("cash", 0.0)))
                                managed = float(snap.get("asset_market_value", snap.get("crypto_holdings_value", 0.0)))
                                eq = float(snap.get("total_equity", snap.get("equity", cash + managed)))
                                realized = float(snap.get("realized_pnl", 0.0))
                                unrealized = float(snap.get("unrealized_pnl", 0.0))

                                points.append({
                                    "time": int(dt.timestamp() * 1000),
                                    "timestamp": ts_str,
                                    "equity": eq,
                                    "cash": cash,
                                    "managed_assets": managed,
                                    "realized_pnl": realized,
                                    "unrealized_pnl": unrealized
                                })
                        except Exception:
                            pass
            except Exception as e:
                logger.error(f"Error reading equity history: {e}")

    # If still empty, supply current live holdings snapshot
    if not points:
        holdings = get_live_account_and_holdings()
        cash = holdings["usdt_total_cash"]
        managed = holdings["active_trade_holdings_value"]
        points.append({
            "time": int(now.timestamp() * 1000),
            "timestamp": now.isoformat() + "Z",
            "equity": round(cash + managed, 2),
            "cash": round(cash, 2),
            "managed_assets": round(managed, 2),
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0
        })

    return jsonify(points)

@app.route('/api/scanner')
def get_scanner():
    """Scanner statistics, live symbol matrices, and real-time opportunity pool."""
    stats = {
        "symbols_scanned": 0,
        "TOTAL_SIGNALS": 0,
        "PROFITABILITY_ACCEPTED": 0,
        "PROFITABILITY_REJECTED": 0,
        "RISK_ACCEPTED": 0,
        "RISK_REJECTED": 0,
        "COOLDOWN_REJECTED": 0,
        "QUALIFIED": 0,
        "ORDERS_SUBMITTED": 0,
        "ORDERS_FILLED": 0,
        "strategy_evaluations": 0,
        "TOTAL_CANDLES": 0,
        "top_opportunities": [],
        "strategy_metrics": {},
        "timeframe_metrics": {},
        "market_data": {},
        "symbols": []
    }
    
    port_file = os.getenv("TESTNET_PORTFOLIO_FILE", "testnet_portfolio.json")
    if os.path.exists(port_file):
        try:
            with open(port_file, "r") as f:
                port = json.load(f)
                scanner_stats = port.get("scanner_stats", {})
                stats.update(scanner_stats)
                # Pull tracked symbols list from portfolio
                stats["symbols"] = port.get("symbols", scanner_stats.get("symbols", []))
        except Exception:
            pass
            
    # Fetch live ticker prices for tracked symbols
    try:
        from data_client import MarketDataClient
        client = MarketDataClient()
        if client.is_available():
            tickers = client.get_ticker()
            market_data = {}
            for t in tickers:
                sym = t['symbol']
                if sym in stats.get("symbols", []) or sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "LINKUSDT", "PORTALUSDT", "HEMIUSDT", "TRXUSDT", "DOGEUSDT", "PAXGUSDT", "ADAUSDT", "SPCXBUSDT", "SOPHUSDT"]:
                    market_data[sym] = {
                        "close": float(t['lastPrice']),
                        "change_24h": float(t['priceChangePercent']),
                        "volume": float(t['volume']),
                        "high": float(t['highPrice']),
                        "low": float(t['lowPrice'])
                    }
            stats["market_data"] = market_data
    except Exception as e:
        logger.error(f"Error fetching scanner market data: {e}")
        
    # Read live opportunities log (last 15 minutes)
    opps = []
    datetime.datetime.utcnow()
    if os.path.exists("testnet_opportunity_log.jsonl"):
        try:
            with open("testnet_opportunity_log.jsonl", "r") as f:
                for line in f:
                    if not line.strip(): continue
                    try:
                        rec = json.loads(line)
                        opps.append(rec)
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"Error reading opportunity log: {e}")
            
    if not opps and os.path.exists("testnet_signals_log.jsonl"):
        try:
            with open("testnet_signals_log.jsonl", "r") as f:
                for line in f:
                    if not line.strip(): continue
                    try:
                        s = json.loads(line)
                        opps.append({
                            "timestamp": s.get("timestamp"),
                            "signal_id": s.get("signal_id"),
                            "symbol": s.get("symbol"),
                            "timeframe": s.get("timeframe"),
                            "strategy": s.get("strategy"),
                            "side": s.get("decision", "BUY"),
                            "decision": s.get("final_decision", "ACCEPTED"),
                            "confidence": s.get("confidence", 0.8),
                            "expected_gross_return": s.get("expected_gross", 2.0),
                            "expected_net_return": s.get("expected_net", 1.8),
                            "expected_net": s.get("expected_net", 1.8),
                            "profitability_decision": s.get("profitability_decision", "ACCEPTED"),
                            "risk_decision": s.get("risk_decision", "ACCEPTED"),
                            "final_decision": s.get("final_decision", "ACCEPTED"),
                            "reason": "POSITIVE_ALPHA"
                        })
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"Error reading signals log fallback: {e}")

    sorted_opps = sorted(opps, key=lambda x: str(x.get("timestamp", "")), reverse=True)
    stats["top_opportunities"] = sorted_opps[:10]

    # Reshape top_opportunities into recent_signals format that frontend expects
    # Each signal needs: timestamp, symbol, timeframe, strategy, side, entry_price, evaluation{}
    recent_signals = []
    for opp in sorted_opps[:20]:
        p_decision = str(opp.get("profitability_decision", opp.get("decision", ""))).upper()
        r_decision = str(opp.get("risk_decision", "")).upper()
        prof_passed = p_decision in ("ACCEPTED", "QUALIFIED")
        risk_passed = r_decision in ("ACCEPTED", "")  # empty means no explicit rejection
        expected_net_pct = float(opp.get("expected_net", opp.get("expected_net_return", 0.0)))
        recent_signals.append({
            "timestamp": opp.get("timestamp"),
            "signal_id": opp.get("signal_id"),
            "symbol": opp.get("symbol"),
            "timeframe": opp.get("timeframe", "5m"),
            "strategy": opp.get("strategy"),
            "side": opp.get("side", opp.get("decision", "BUY")),
            "entry_price": float(opp.get("entry_price", opp.get("current_price", 0.0))),
            "stop_loss": float(opp.get("sl", opp.get("stop_loss", 0.0))),
            "take_profit": float(opp.get("tp", opp.get("take_profit", 0.0))),
            "confidence": float(opp.get("confidence", 0.0)),
            "final_decision": str(opp.get("final_decision", opp.get("decision", ""))).upper(),
            "evaluation": {
                "expected_net_percent": round(expected_net_pct, 4),
                "profitability": {
                    "passed": prof_passed,
                    "expected_gross": float(opp.get("expected_gross_return", 0.0)),
                    "expected_net": expected_net_pct,
                    "fees": float(opp.get("fees_pct", 0.31)),
                    "threshold": float(opp.get("threshold", 0.31)),
                    "reason": opp.get("reason", opp.get("profitability_reason", ""))
                },
                "risk": {
                    "passed": risk_passed,
                    "reason": opp.get("risk_reason", "" if risk_passed else str(opp.get("reason", "")))
                }
            }
        })
    stats["recent_signals"] = recent_signals

    # Frontend-compatible KPI aliases (camelCase)
    stats["strategy_evaluations"] = stats.get("strategy_evaluations", 0)
    stats["symbol_count"] = len(stats["symbols"]) if stats["symbols"] else stats.get("symbols_scanned", 0)
    stats["active_symbols"] = stats["symbol_count"]
    stats["active_timeframes"] = len(stats.get("timeframe_metrics", {}))
    stats["active_strategies"] = len(stats.get("strategy_metrics", {}))

    # Compute dynamic strategy metrics from trade ledger if empty
    if not stats.get("strategy_metrics"):
        trades_info = _get_trades_data()
        strat_metrics = {}
        # Prepopulate active strategies
        for s_name in ["ADX_EMA", "ML", "SCALPER", "SUPERTREND", "SWING", "AGGRESSOR", "FAST1M"]:
            strat_metrics[s_name] = {
                "evaluations": 0, "BUY": 0, "SELL": 0, "HOLD": 0,
                "qualified": 0, "rejected": 0, "orders": 0, "fills": 0,
                "wins": 0, "losses": 0, "PnL": 0.0
            }
            
        for t in trades_info.get("positions", []):
            st = str(t.get("strategy", "AGGRESSOR")).upper()
            if st not in strat_metrics:
                strat_metrics[st] = {
                    "evaluations": 0, "BUY": 0, "SELL": 0, "HOLD": 0,
                    "qualified": 0, "rejected": 0, "orders": 0, "fills": 0,
                    "wins": 0, "losses": 0, "PnL": 0.0
                }
            strat_metrics[st]["fills"] += 1
            strat_metrics[st]["orders"] += 1
            strat_metrics[st]["qualified"] += 1
            strat_metrics[st]["evaluations"] += 1
            if t.get("action", "").upper() in ["BUY", "LONG"]:
                strat_metrics[st]["BUY"] += 1
            else:
                strat_metrics[st]["SELL"] += 1
            pnl = float(t.get("pnl", 0.0))
            strat_metrics[st]["PnL"] += pnl
            if pnl > 0: strat_metrics[st]["wins"] += 1
            elif pnl < 0: strat_metrics[st]["losses"] += 1
            
        stats["strategy_metrics"] = strat_metrics

    return jsonify(stats)

@app.route('/api/markets')
def get_markets():
    """Returns live market ticker data, pricing, 24h stats, and monitored symbols."""
    tracked_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "LINKUSDT", "PORTALUSDT", "HEMIUSDT", "TRXUSDT", "DOGEUSDT", "PAXGUSDT", "ADAUSDT", "SPCXBUSDT", "SOPHUSDT"]
    port_file = os.getenv("TESTNET_PORTFOLIO_FILE", "testnet_portfolio.json")
    if os.path.exists(port_file):
        try:
            with open(port_file, "r") as f:
                port = json.load(f)
                tracked_symbols = port.get("symbols", tracked_symbols)
        except Exception:
            pass

    market_list = []
    try:
        from data_client import MarketDataClient
        client = MarketDataClient()
        if client.is_available():
            tickers = client.get_ticker()
            for t in tickers:
                sym = t.get('symbol')
                if sym in tracked_symbols:
                    last_p = float(t.get('lastPrice', 0))
                    chg = float(t.get('priceChangePercent', 0))
                    vol = float(t.get('volume', 0))
                    market_list.append({
                        "symbol": sym,
                        "price": last_p,
                        "change_24h": chg,
                        "high_24h": float(t.get('highPrice', last_p)),
                        "low_24h": float(t.get('lowPrice', last_p)),
                        "volume": vol,
                        "quote_volume": float(t.get('quoteVolume', 0)),
                        "status": "STREAMING"
                    })
    except Exception as e:
        logger.error(f"Error fetching market list: {e}")

    # Fallback if tickers unavailable
    if not market_list:
        for sym in tracked_symbols:
            market_list.append({
                "symbol": sym,
                "price": 0.0,
                "change_24h": 0.0,
                "high_24h": 0.0,
                "low_24h": 0.0,
                "volume": 0.0,
                "quote_volume": 0.0,
                "status": "ONLINE"
            })

    return jsonify({
        "status": "ok",
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "symbol_count": len(market_list),
        "markets": market_list
    })

@app.route('/api/opportunity-log')
def api_get_opportunities():
    """Returns raw opportunity log records for debug inspection."""
    opps = []
    if os.path.exists("testnet_opportunity_log.jsonl"):
        try:
            with open("testnet_opportunity_log.jsonl", "r") as f:
                for line in f:
                    if not line.strip(): continue
                    try:
                        opps.append(json.loads(line))
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"Error reading opportunity log: {e}")
            
    if not opps and os.path.exists("testnet_signals_log.jsonl"):
        try:
            with open("testnet_signals_log.jsonl", "r") as f:
                for line in f:
                    if not line.strip(): continue
                    try:
                        s = json.loads(line)
                        opps.append({
                            "timestamp": s.get("timestamp"),
                            "signal_id": s.get("signal_id"),
                            "symbol": s.get("symbol"),
                            "timeframe": s.get("timeframe"),
                            "strategy": s.get("strategy"),
                            "side": s.get("decision", "BUY"),
                            "decision": s.get("final_decision", "ACCEPTED"),
                            "confidence": s.get("confidence", 0.8),
                            "expected_gross_return": s.get("expected_gross", 2.0),
                            "expected_net_return": s.get("expected_net", 1.8),
                            "expected_net": s.get("expected_net", 1.8),
                            "profitability_decision": s.get("profitability_decision", "ACCEPTED"),
                            "risk_decision": s.get("risk_decision", "ACCEPTED"),
                            "final_decision": s.get("final_decision", "ACCEPTED"),
                            "reason": "POSITIVE_ALPHA"
                        })
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"Error reading signals fallback: {e}")
            
    sorted_opps = sorted(opps, key=lambda x: str(x.get("timestamp", "")), reverse=True)[:10]
    res = {
        "status": "SUCCESS",
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "count": len(sorted_opps),
        "top_opportunities": sorted_opps
    }
    
    port_file = os.getenv("TESTNET_PORTFOLIO_FILE", "testnet_portfolio.json")
    if os.path.exists(port_file):
        try:
            with open(port_file, "r") as f:
                port = json.load(f)
                if "scanner_stats" in port:
                    res.update(port["scanner_stats"])
        except Exception:
            pass
            
    if "strategy_metrics" not in res or not res["strategy_metrics"]:
        strat_metrics = {}
        for s_name in ["ADX_EMA", "ML", "SCALPER", "SUPERTREND", "SWING", "AGGRESSOR", "FAST1M"]:
            strat_metrics[s_name] = {
                "evaluations": 0, "BUY": 0, "SELL": 0, "HOLD": 0,
                "qualified": 0, "rejected": 0, "orders": 0, "fills": 0,
                "wins": 0, "losses": 0, "PnL": 0.0
            }
        res["strategy_metrics"] = strat_metrics

    if "timeframe_metrics" not in res or not res["timeframe_metrics"]:
        res["timeframe_metrics"] = {
            "1m": {"signals": 0, "qualified": 0, "rejected": 0, "executed": 0},
            "3m": {"signals": 0, "qualified": 0, "rejected": 0, "executed": 0},
            "5m": {"signals": 0, "qualified": 0, "rejected": 0, "executed": 0},
            "15m": {"signals": 0, "qualified": 0, "rejected": 0, "executed": 0},
            "30m": {"signals": 0, "qualified": 0, "rejected": 0, "executed": 0},
            "1h": {"signals": 0, "qualified": 0, "rejected": 0, "executed": 0}
        }
        
    for k in ["TOTAL_SIGNALS", "PROFITABILITY_ACCEPTED", "PROFITABILITY_REJECTED", "RISK_ACCEPTED", "RISK_REJECTED", "QUALIFIED", "ORDERS_SUBMITTED", "ORDERS_FILLED"]:
        if k not in res:
            res[k] = 0
            
    return jsonify(res)

@app.route('/api/export-trades')
def api_export_trades():
    """Generates downloadable CSV export of the verified trade ledger."""
    from flask import Response
    trades_info = _get_trades_data()
    positions = trades_info.get("positions", [])
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["timestamp", "symbol", "side", "strategy", "entry_price", "exit_price", "quantity", "gross_pnl", "fees", "net_pnl", "status", "exit_reason", "order_id"])
    for p in positions:
        writer.writerow([
            p.get("timestamp", ""),
            p.get("symbol", ""),
            p.get("action", ""),
            p.get("strategy", ""),
            p.get("entry_price", ""),
            p.get("exit_price", ""),
            p.get("quantity", ""),
            p.get("gross_pnl", ""),
            p.get("fees", ""),
            p.get("pnl", ""),
            p.get("status", ""),
            p.get("exit_reason", ""),
            p.get("order_id", "")
        ])
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=binance_trade_ledger.csv"}
    )

@app.route('/api/account')
def api_account():
    """
    Returns authoritative Binance Testnet wallet balances, mark-to-market active crypto value,
    true total equity, and PnL performance with timestamp and metadata.
    """
    holdings_data = get_live_account_and_holdings()
    trades_data = _get_trades_data()
    port_file = os.getenv("TESTNET_PORTFOLIO_FILE", "testnet_portfolio.json")
    open_positions = {}
    unrealized_pnl = 0.0
    if os.path.exists(port_file):
        try:
            with open(port_file, "r") as f:
                p_data = json.load(f)
                open_positions = {k: v for k, v in p_data.get("positions", {}).items() if isinstance(v, dict) and v.get("status") == "OPEN"}
                for pos in open_positions.values():
                    unrealized_pnl += float(pos.get("unrealized_pnl", 0.0))
        except Exception:
            pass

    usdt_cash = holdings_data.get("usdt_total_cash", 0.0)
    crypto_val = holdings_data.get("active_trade_holdings_value", 0.0)
    total_eq = round(usdt_cash + crypto_val, 2)
    realized_pnl = round(float(trades_data.get("net_pnl", 0.0)), 4)
    
    return jsonify({
        "status": "SUCCESS",
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "data_age": round(time.time() - _holdings_cache_ts, 2) if _holdings_cache_ts else 0.0,
        "account": {
            "usdt_free": holdings_data.get("usdt_free", 0.0),
            "usdt_locked": holdings_data.get("usdt_locked", 0.0),
            "usdt_total_cash": usdt_cash,
            "crypto_holdings_value": crypto_val,
            "total_crypto_value": holdings_data.get("total_crypto_value", 0.0),
            "total_equity": total_eq,
            "realized_pnl": realized_pnl,
            "unrealized_pnl": round(unrealized_pnl, 4),
            "total_pnl": round(realized_pnl + unrealized_pnl, 4),
            "fees_paid": round(float(trades_data.get("total_fees", 0.0)), 4),
            "open_position_count": len(open_positions),
            "holdings": holdings_data.get("holdings", [])
        }
    })


@app.route('/api/equity-history')
def api_equity_history():
    """
    Returns time series of account-equity snapshots with range filtering:
    '1h', '6h', '24h', '7d', '30d', 'all'.
    """
    from testnet_engine.telemetry_manager import get_telemetry_manager
    time_range = request.args.get("range", "all").lower()
    telemetry = get_telemetry_manager()
    timeline = telemetry.get_equity_timeline(time_range)
    
    return jsonify({
        "status": "SUCCESS",
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "data_age": 0.0,
        "range": time_range,
        "count": len(timeline),
        "snapshots": timeline
    })

@app.route('/api/trade-history')
def api_trade_history():
    """
    Returns closed trade history from the authoritative 40-field canonical
    telemetry store (testnet_trade_events.jsonl). Falls back to the thin
    testnet_trade_ledger.jsonl if the canonical store is empty.
    Each record answers all 15 lifecycle questions:
    WHEN/WHY opened, WHICH strategy/timeframe, WHAT was balance/equity
    at entry and exit, ENTRY/EXIT prices, SL/TP, net PnL, fees, close reason.
    """
    from testnet_engine.telemetry_manager import get_telemetry_manager
    try:
        telemetry = get_telemetry_manager()
        # Prefer the rich canonical store
        canonical_events = telemetry.get_trade_events(limit=500)
        closed_canonical = [e for e in canonical_events if e.get("status") == "CLOSED"]

        if closed_canonical:
            # Compute summary stats from canonical events
            net_pnl = sum(float(e.get("net_pnl", 0.0)) for e in closed_canonical)
            wins = sum(1 for e in closed_canonical if float(e.get("net_pnl", 0.0)) > 0)
            losses = sum(1 for e in closed_canonical if float(e.get("net_pnl", 0.0)) < 0)
            total_fees = sum(float(e.get("total_fees", 0.0)) for e in closed_canonical)
            # Normalise field names for frontend compatibility
            trades_out = []
            for e in closed_canonical:
                dur_s = float(e.get("duration_seconds", 0.0))
                dur_str = ""
                if dur_s > 0:
                    h, rem = divmod(int(dur_s), 3600)
                    m, s = divmod(rem, 60)
                    dur_str = f"{h}h {m}m {s}s" if h else (f"{m}m {s}s" if m else f"{s}s")
                trades_out.append({
                    # Identity
                    "trade_id": e.get("trade_id", ""),
                    "symbol": e.get("symbol", ""),
                    "strategy": e.get("strategy", ""),
                    "timeframe": e.get("timeframe", ""),
                    "side": e.get("side", ""),
                    "action": e.get("side", ""),
                    "status": e.get("status", "CLOSED"),
                    # Lifecycle timestamps
                    "signal_time": e.get("signal_timestamp", e.get("entry_signal_timestamp", "")),
                    "order_submit_time": e.get("order_submit_timestamp", ""),
                    "fill_time": e.get("fill_timestamp", ""),
                    "close_time": e.get("close_timestamp", ""),
                    "timestamp": e.get("close_timestamp", e.get("order_submit_timestamp", "")),
                    # Order IDs
                    "entry_order_id": e.get("entry_order_id", ""),
                    "exit_order_id": e.get("exit_order_id", ""),
                    "order_id": e.get("exit_order_id") or e.get("entry_order_id", ""),
                    # Prices & Sizing
                    "entry_price": float(e.get("entry_price", 0.0)),
                    "exit_price": float(e.get("exit_price", 0.0)),
                    "quantity": float(e.get("quantity", 0.0)),
                    "notional": float(e.get("notional", 0.0)),
                    # Protection
                    "stop_loss": float(e.get("stop_loss", 0.0)),
                    "take_profit": float(e.get("take_profit", 0.0)),
                    "sl": float(e.get("stop_loss", 0.0)),
                    "tp": float(e.get("take_profit", 0.0)),
                    # PnL & Fees
                    "gross_pnl": float(e.get("gross_pnl", 0.0)),
                    "fees": float(e.get("total_fees", 0.0)),
                    "pnl": float(e.get("net_pnl", 0.0)),
                    "net_pnl": float(e.get("net_pnl", 0.0)),
                    # Balance/Equity state at entry
                    "balance_before_entry": float(e.get("cash_before_entry", 0.0)),
                    "balance_after_entry": float(e.get("cash_after_entry", 0.0)),
                    "equity_before_entry": float(e.get("equity_before_entry", 0.0)),
                    "equity_after_entry": float(e.get("equity_after_entry", 0.0)),
                    # Balance/Equity state at exit
                    "balance_before_exit": float(e.get("cash_before_exit", 0.0)),
                    "balance_after_exit": float(e.get("cash_after_exit", 0.0)),
                    "equity_before_exit": float(e.get("equity_before_exit", 0.0)),
                    "equity_after_exit": float(e.get("equity_after_exit", 0.0)),
                    # Rationale
                    "close_reason": e.get("close_reason", e.get("exit_reason", "")),
                    "exit_reason": e.get("close_reason", e.get("exit_reason", "")),
                    "profitability_decision": e.get("profitability_decision", ""),
                    "profitability_reason": e.get("profitability_reason", ""),
                    "risk_decision": e.get("risk_decision", ""),
                    "risk_reason": e.get("risk_reason", ""),
                    "expected_gross_return": float(e.get("expected_gross_return", 0.0)),
                    "expected_net_return": float(e.get("expected_net_return", 0.0)),
                    # Duration
                    "duration_seconds": dur_s,
                    "duration": dur_str,
                    "source": e.get("source", ""),
                })
            return jsonify({
                "status": "SUCCESS",
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                "data_age": 0.0,
                "source": "canonical_telemetry",
                "total_trades": len(trades_out),
                "wins": wins,
                "losses": losses,
                "realized_pnl": round(net_pnl, 4),
                "total_fees": round(total_fees, 4),
                "trades": trades_out
            })
    except Exception as te_err:
        logger.warning(f"[TRADE_HISTORY] Canonical store unavailable, falling back to ledger: {te_err}")

    # Fallback: thin ledger
    trades_data = _get_trades_data()
    return jsonify({
        "status": "SUCCESS",
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "data_age": 0.0,
        "source": "ledger_fallback",
        "total_trades": len(trades_data.get("positions", [])),
        "realized_pnl": trades_data.get("net_pnl", 0.0),
        "trades": trades_data.get("positions", [])
    })

@app.route('/api/trade-events')
def api_trade_events():
    """Returns canonical trade events with complete 40+ field lifecycle telemetry."""
    from testnet_engine.telemetry_manager import get_telemetry_manager
    symbol = request.args.get("symbol")
    status = request.args.get("status")
    limit = int(request.args.get("limit", 100))
    telemetry = get_telemetry_manager()
    events = telemetry.get_trade_events(symbol=symbol, status=status, limit=limit)
    return jsonify({
        "status": "SUCCESS",
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "data_age": 0.0,
        "count": len(events),
        "events": events
    })

@app.route('/api/positions')
def api_positions():
    """
    Returns active and historical positions.
    Supports query parameter ?status=OPEN|CLOSED|ALL.
    """
    from testnet_engine.telemetry_manager import get_telemetry_manager
    status_filter = request.args.get("status", "OPEN").upper()
    telemetry = get_telemetry_manager()
    positions = telemetry.get_positions(status=status_filter)
    
    # If looking for OPEN positions and telemetry store is warming up, sync from testnet_portfolio.json
    if status_filter in ["OPEN", "ALL"] and not positions:
        port_file = os.getenv("TESTNET_PORTFOLIO_FILE", "testnet_portfolio.json")
        if os.path.exists(port_file):
            try:
                with open(port_file, "r") as f:
                    p_data = json.load(f)
                    for sym, p in p_data.get("positions", {}).items():
                        if isinstance(p, dict) and p.get("status") == "OPEN":
                            positions.append({
                                "position_id": sym,
                                "trade_id": p.get("entry_client_id", sym),
                                "symbol": sym,
                                "strategy": p.get("strategy", ""),
                                "side": p.get("direction", p.get("side", "BUY")),
                                "entry_timestamp": p.get("timestamp", ""),
                                "entry_price": float(p.get("entry_price", 0.0)),
                                "quantity": float(p.get("quantity", 0.0)),
                                "stop_loss": float(p.get("sl", 0.0)),
                                "take_profit": float(p.get("tp", 0.0)),
                                "status": "OPEN"
                            })
            except Exception:
                pass

    return jsonify({
        "status": "SUCCESS",
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "data_age": 0.0,
        "filter": status_filter,
        "count": len(positions),
        "positions": positions
    })

@app.route('/api/signals')
def api_signals():
    """Returns strategy signal decision logs for terminal telemetry."""
    from testnet_engine.telemetry_manager import get_telemetry_manager
    limit = int(request.args.get("limit", 100))
    symbol = request.args.get("symbol")
    strategy = request.args.get("strategy")
    telemetry = get_telemetry_manager()
    signals = telemetry.get_signals_log(limit=limit, symbol=symbol, strategy=strategy)
    
    # Fallback to testnet_opportunity_log.jsonl if empty
    if not signals and os.path.exists("testnet_opportunity_log.jsonl"):
        try:
            with open("testnet_opportunity_log.jsonl", "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip(): continue
                    try:
                        s = json.loads(line)
                        if symbol and s.get("symbol") != symbol: continue
                        if strategy and s.get("strategy") != strategy: continue
                        signals.append(s)
                    except: pass
            signals = sorted(signals, key=lambda x: str(x.get("timestamp", "")), reverse=True)[:limit]
        except Exception as e:
            logger.error(f"Error reading signals from opportunity log: {e}")

    return jsonify({
        "status": "SUCCESS",
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "data_age": 0.0,
        "count": len(signals),
        "signals": signals
    })

@app.route('/api/diagnostics')
def api_diagnostics():
    """
    Returns complete Global Decision Funnel diagnostics, exact stage counts,
    rejection reasons breakdown, and zero-trade bottleneck analysis.
    """
    from testnet_engine.telemetry_manager import get_telemetry_manager
    telemetry = get_telemetry_manager()
    
    # Load stats from testnet_portfolio.json or in-memory
    stats = {
        "candles_evaluated": 0,
        "strategies_evaluated": 0,
        "signals_generated": 0,
        "profitability_accepted": 0,
        "profitability_rejected": 0,
        "risk_accepted": 0,
        "risk_rejected": 0,
        "execution_eligible": 0,
        "execution_rejected": 0,
        "orders_submitted": 0,
        "orders_filled": 0,
        "orders_failed": 0
    }
    
    port_file = os.getenv("TESTNET_PORTFOLIO_FILE", "testnet_portfolio.json")
    if os.path.exists(port_file):
        try:
            with open(port_file, "r", encoding="utf-8") as f:
                p_data = json.load(f)
                funnel_data = p_data.get("funnel", {})
                scanner_data = p_data.get("scanner_stats", {})
                for k, v in funnel_data.items():
                    stats[k] = v
                for k, v in scanner_data.items():
                    if k.lower() in stats and stats[k.lower()] == 0:
                        stats[k.lower()] = v
        except Exception as e:
            logger.error(f"Error reading portfolio for diagnostics: {e}")

    # Read recent signals to compile detailed rejection reasons
    profit_reasons = {}
    risk_reasons = {}
    exec_reasons = {}
    
    signals = telemetry.get_signals_log(limit=500)
    for s in signals:
        p_dec = s.get("profitability_decision", "")
        p_reas = s.get("profitability_reason", "")
        r_dec = s.get("risk_decision", "")
        r_reas = s.get("risk_reason", "")
        e_dec = s.get("execution_decision", s.get("final_decision", ""))
        e_reas = s.get("execution_reason", "")
        
        if p_dec == "REJECTED" and p_reas:
            profit_reasons[p_reas] = profit_reasons.get(p_reas, 0) + 1
        if r_dec == "REJECTED" and r_reas:
            risk_reasons[r_reas] = risk_reasons.get(r_reas, 0) + 1
        if e_dec == "REJECTED" and e_reas:
            exec_reasons[e_reas] = exec_reasons.get(e_reas, 0) + 1

    # Bottleneck diagnosis
    bottleneck = "PIPELINE ACTIVE — Scanning live market for valid opportunities."
    if stats["signals_generated"] > 0 and stats["orders_filled"] == 0 and stats["profitability_rejected"] > 0:
        drop_pct = (stats["profitability_rejected"] / max(1, stats["signals_generated"])) * 100
        bottleneck = f"{drop_pct:.1f}% of signals stopped at Profitability Gate due to expected net return failing the 0.31% friction hurdle."
    elif stats["profitability_accepted"] > 0 and stats["risk_rejected"] > 0:
        drop_pct = (stats["risk_rejected"] / max(1, stats["profitability_accepted"])) * 100
        bottleneck = f"{drop_pct:.1f}% of qualified signals stopped at Risk Gate due to position sizing or exposure limits."

    return jsonify({
        "status": "SUCCESS",
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "funnel": stats,
        "rejection_breakdown": {
            "profitability_reasons": profit_reasons,
            "risk_reasons": risk_reasons,
            "execution_reasons": exec_reasons
        },
        "bottleneck_diagnosis": bottleneck,
        "pipeline_state": "PIPELINE READY — WAITING FOR REAL SIGNAL" if stats["orders_filled"] == 0 else "ACTIVE_TRADING"
    })

@app.route('/api/strategy-metrics')
def api_strategy_metrics():
    """
    Returns detailed metrics breakdown per strategy and Strategy x Timeframe matrix.
    Covers: aggressor, scalper, supertrend, ml, swing, adx_ema.
    """
    from testnet_engine.telemetry_manager import get_telemetry_manager
    telemetry = get_telemetry_manager()
    trades = telemetry.query_trades(limit=500)
    signals = telemetry.get_signals_log(limit=500)

    # Core 6 strategies definition
    strategy_keys = ["aggressor", "scalper", "supertrend", "ml", "swing", "adx_ema"]
    timeframe_keys = ["5m", "15m", "30m", "1h", "2h", "4h"]
    strategy_timeframe_config = {
        "aggressor": ["5m", "15m", "1h"],
        "scalper": ["5m", "15m"],
        "supertrend": ["15m", "1h", "4h"],
        "ml": ["5m", "15m", "1h", "4h"],
        "swing": ["1h", "4h"],
        "adx_ema": ["5m", "15m", "1h", "4h"]
    }

    # Load scanner stats from portfolio if available
    port_stats = {}
    port_file = os.getenv("TESTNET_PORTFOLIO_FILE", "testnet_portfolio.json")
    if os.path.exists(port_file):
        try:
            with open(port_file, "r") as f:
                p_data = json.load(f)
                port_stats = p_data.get("scanner_stats", {}).get("strategy_metrics", {})
        except Exception:
            pass

    strats = {}
    for sk in strategy_keys:
        p_stat = port_stats.get(sk, {})
        strats[sk] = {
            "name": sk.upper(),
            "status": "ACTIVE",
            "timeframes": strategy_timeframe_config.get(sk, ["5m"]),
            "evaluations": p_stat.get("evaluations", p_stat.get("HOLD", 0) + p_stat.get("signals", 0)),
            "BUY": p_stat.get("BUY", 0),
            "SELL": p_stat.get("SELL", 0),
            "HOLD": p_stat.get("HOLD", 0),
            "qualified": p_stat.get("qualified", 0),
            "profitability_rejected": p_stat.get("rejected", 0),
            "risk_rejected": 0,
            "orders": p_stat.get("orders", p_stat.get("executed", 0)),
            "fills": p_stat.get("fills", p_stat.get("executed", 0)),
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "net_pnl": 0.0,
            "avg_trade": None,
            "best_trade": None,
            "worst_trade": None,
            "win_rate": None,
            "profit_factor": None,
            "drawdown": 0.0,
            "profitability_rejection_rate": None,
            "risk_rejection_rate": None,
            "execution_success_rate": None,
            "recent_signals": [],
            "recent_trades": []
        }

    # Populate from signals telemetry
    for s in signals:
        st = str(s.get("strategy", "")).lower().strip()
        if st in strats:
            strats[st]["evaluations"] = max(strats[st]["evaluations"], strats[st]["evaluations"] + 1)
            dec = str(s.get("decision", "")).upper()
            if dec in ["BUY", "LONG"]:
                strats[st]["BUY"] += 1
            elif dec in ["SELL", "SHORT"]:
                strats[st]["SELL"] += 1
            else:
                strats[st]["HOLD"] += 1

            p_dec = str(s.get("profitability_decision", "")).upper()
            r_dec = str(s.get("risk_decision", "")).upper()
            f_dec = str(s.get("final_decision", "")).upper()

            if p_dec == "ACCEPTED" and r_dec == "ACCEPTED":
                strats[st]["qualified"] += 1
            if p_dec == "REJECTED":
                strats[st]["profitability_rejected"] += 1
            if r_dec == "REJECTED":
                strats[st]["risk_rejected"] += 1
            if f_dec in ["EXECUTED", "ACCEPTED"]:
                strats[st]["orders"] += 1
                strats[st]["fills"] += 1

            if len(strats[st]["recent_signals"]) < 10:
                strats[st]["recent_signals"].append({
                    "timestamp": s.get("timestamp"),
                    "symbol": s.get("symbol"),
                    "timeframe": s.get("timeframe", "5m"),
                    "side": s.get("decision"),
                    "entry": s.get("entry"),
                    "confidence": s.get("confidence"),
                    "profitability_decision": s.get("profitability_decision"),
                    "risk_decision": s.get("risk_decision"),
                    "final_decision": s.get("final_decision"),
                    "reason": s.get("profitability_reason") or s.get("risk_reason") or ""
                })

    # Populate from trade history
    trade_pnls = {sk: [] for sk in strategy_keys}
    for t in trades:
        st = str(t.get("strategy", "")).lower().strip()
        if st in strats:
            pnl = float(t.get("net_pnl", t.get("pnl", 0.0)))
            strats[st]["trades"] += 1
            strats[st]["net_pnl"] += pnl
            trade_pnls[st].append(pnl)

            if pnl > 0:
                strats[st]["wins"] += 1
                strats[st]["gross_profit"] += pnl
            elif pnl < 0:
                strats[st]["losses"] += 1
                strats[st]["gross_loss"] += abs(pnl)

            if len(strats[st]["recent_trades"]) < 10:
                strats[st]["recent_trades"].append({
                    "trade_id": t.get("trade_id", t.get("order_id")),
                    "timestamp": t.get("close_timestamp", t.get("close_time", t.get("fill_timestamp", t.get("timestamp")))),
                    "symbol": t.get("symbol"),
                    "timeframe": t.get("timeframe", "5m"),
                    "side": t.get("side", t.get("action")),
                    "entry_price": t.get("entry_price"),
                    "exit_price": t.get("exit_price"),
                    "quantity": t.get("quantity"),
                    "net_pnl": pnl,
                    "close_reason": t.get("close_reason", "")
                })

    # Compute rates and KPIs per strategy
    for st, data in strats.items():
        tr_count = data["trades"]
        pnls = trade_pnls.get(st, [])
        if tr_count > 0:
            data["win_rate"] = round((data["wins"] / tr_count) * 100, 2)
            data["profit_factor"] = round((data["gross_profit"] / data["gross_loss"]), 2) if data["gross_loss"] > 0 else (999.0 if data["gross_profit"] > 0 else None)
            data["avg_trade"] = round(data["net_pnl"] / tr_count, 4)
            data["best_trade"] = round(max(pnls), 4) if pnls else None
            data["worst_trade"] = round(min(pnls), 4) if pnls else None
            # Peak to trough drawdown in trades
            peak = 0.0
            cum = 0.0
            max_dd = 0.0
            for p in pnls:
                cum += p
                peak = max(peak, cum)
                dd = peak - cum
                max_dd = max(max_dd, dd)
            data["drawdown"] = round(max_dd, 4)
        else:
            data["win_rate"] = None
            data["profit_factor"] = None
            data["avg_trade"] = None
            data["best_trade"] = None
            data["worst_trade"] = None
            data["drawdown"] = 0.0

        data["net_pnl"] = round(data["net_pnl"], 4)
        data["gross_profit"] = round(data["gross_profit"], 4)
        data["gross_loss"] = round(data["gross_loss"], 4)

        # Gate rejection rates
        total_evals = data["qualified"] + data["profitability_rejected"]
        if total_evals > 0:
            data["profitability_rejection_rate"] = round((data["profitability_rejected"] / total_evals) * 100, 2)
        else:
            data["profitability_rejection_rate"] = None

        total_risk_evals = data["qualified"] + data["risk_rejected"]
        if total_risk_evals > 0:
            data["risk_rejection_rate"] = round((data["risk_rejected"] / total_risk_evals) * 100, 2)
        else:
            data["risk_rejection_rate"] = None

        if data["orders"] > 0:
            data["execution_success_rate"] = round((data["fills"] / data["orders"]) * 100, 2)
        else:
            data["execution_success_rate"] = None

    # Strategy x Timeframe Matrix
    matrix = {}
    for sk in strategy_keys:
        matrix[sk] = {}
        for tf in timeframe_keys:
            # Aggregate from signals & trades matching this pair
            pair_signals = [s for s in signals if str(s.get("strategy", "")).lower() == sk and str(s.get("timeframe", "")).lower() == tf.lower()]
            pair_trades = [t for t in trades if str(t.get("strategy", "")).lower() == sk and str(t.get("timeframe", "")).lower() == tf.lower()]
            pair_pnl = sum(float(t.get("net_pnl", t.get("pnl", 0.0))) for t in pair_trades)
            pair_wins = sum(1 for t in pair_trades if float(t.get("net_pnl", t.get("pnl", 0.0))) > 0)
            pair_wr = round((pair_wins / len(pair_trades)) * 100, 1) if pair_trades else None

            is_configured = tf in strategy_timeframe_config.get(sk, [])
            matrix[sk][tf] = {
                "active": is_configured,
                "signals": len(pair_signals),
                "trades": len(pair_trades),
                "win_rate": pair_wr,
                "pnl": round(pair_pnl, 4)
            }

    return jsonify({
        "status": "SUCCESS",
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "data_age": 0.0,
        "strategies": strats,
        "timeframe_keys": timeframe_keys,
        "matrix": matrix
    })

@app.route('/api/timeframe-metrics')
def api_timeframe_metrics():
    """Returns performance and signal throughput metrics by timeframe."""
    engine_data = get_engine_health_data()
    tf_metrics = engine_data.get("stats", {}).get("timeframe_metrics", {
        "5m": {"evaluated": 0, "qualified": 0, "executed": 0},
        "15m": {"evaluated": 0, "qualified": 0, "executed": 0},
        "30m": {"evaluated": 0, "qualified": 0, "executed": 0},
        "1h": {"evaluated": 0, "qualified": 0, "executed": 0},
        "2h": {"evaluated": 0, "qualified": 0, "executed": 0},
        "4h": {"evaluated": 0, "qualified": 0, "executed": 0}
    })
    return jsonify({
        "status": "SUCCESS",
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "data_age": 0.0,
        "timeframes": tf_metrics
    })

@app.route('/api/risk')
def api_risk():
    """Returns live risk gate configuration, current exposure %, and limits."""
    account_holdings = get_live_account_and_holdings()
    usdt_cash = account_holdings["usdt_total_cash"]
    crypto_val = account_holdings["active_trade_holdings_value"]
    total_eq = usdt_cash + crypto_val
    
    port_file = os.getenv("TESTNET_PORTFOLIO_FILE", "testnet_portfolio.json")
    open_positions = {}
    mdd = 0.0
    if os.path.exists(port_file):
        try:
            with open(port_file, "r") as f:
                port = json.load(f)
                open_positions = {k: v for k, v in port.get("positions", {}).items() if isinstance(v, dict) and v.get("status") == "OPEN"}
                mdd = float(port.get("max_drawdown", 0.0)) * 100
        except Exception:
            pass
            
    risk_used_pct = round(((crypto_val) / total_eq) * 100, 2) if total_eq > 0 else 0.0
    max_exposure_pct = config.MAX_TESTNET_EXPOSURE * 100
    available_risk_pct = max(0.0, round(max_exposure_pct - risk_used_pct, 2))
    
    # Calculate daily PnL from trades
    trades_info = _get_trades_data()
    daily_pnl = 0.0
    today_str = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    for t in trades_info.get("positions", []):
        ts = str(t.get("timestamp", ""))
        if ts.startswith(today_str):
            daily_pnl += float(t.get("pnl", 0.0))
    
    return jsonify({
        "status": "SUCCESS",
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "data_age": 0.0,
        "risk": {
            "total_equity": round(total_eq, 2),
            "cash_usdt": round(usdt_cash, 2),
            "deployed_capital": round(crypto_val, 2),
            "managed_asset_value": round(crypto_val, 2),
            "risk_used_pct": risk_used_pct,
            "max_exposure_pct": max_exposure_pct,
            "available_risk_pct": available_risk_pct,
            "max_single_asset_exposure_pct": getattr(config, "MAX_SINGLE_ASSET_EXPOSURE", 0.02) * 100,
            "max_net_directional_exposure_pct": getattr(config, "MAX_NET_DIRECTIONAL_EXPOSURE", 0.04) * 100,
            "max_open_positions": config.MAX_OPEN_POSITIONS,
            "current_open_positions": len(open_positions),
            "daily_pnl": round(daily_pnl, 4),
            "max_drawdown_pct": round(abs(mdd), 2),
            "max_daily_loss_pct": getattr(config, "MAX_DAILY_LOSS_PCT", 0.02) * 100
        }
    })

@app.route('/api/analytics')
@app.route('/api/telemetry/analytics')
def api_analytics():
    """
    Returns quantitative performance analytics, trade PnL distributions,
    strategy/timeframe/symbol comparisons, and the 'Why Didn't It Trade?' funnel diagnostics.
    Filters out synthetic/test/duplicate recovery records by default.
    """
    from testnet_engine.telemetry_manager import get_telemetry_manager
    telemetry = get_telemetry_manager()
    tf_filter = request.args.get("timeframe", "ALL").upper()
    include_synthetic = request.args.get("include_synthetic", "false").lower() == "true"

    all_trades = telemetry.query_trades(limit=1000)

    # Filter out test/paper/synthetic if not requested
    clean_trades = []
    for t in all_trades:
        source = str(t.get("source", "")).upper()
        strat = str(t.get("strategy", "")).upper()
        if not include_synthetic and (source in ["TEST", "PAPER", "SYNTHETIC"] or "RECOVERED_DUPLICATE" in strat):
            continue
        clean_trades.append(t)

    # Apply time controls (1D, 7D, 30D, ALL)
    now = datetime.datetime.utcnow()
    cutoff = None
    if tf_filter == "1D":
        cutoff = now - datetime.timedelta(days=1)
    elif tf_filter == "7D":
        cutoff = now - datetime.timedelta(days=7)
    elif tf_filter == "30D":
        cutoff = now - datetime.timedelta(days=30)

    filtered_trades = []
    for t in clean_trades:
        ts_str = t.get("close_timestamp") or t.get("close_time") or t.get("fill_timestamp") or t.get("timestamp")
        if cutoff and ts_str:
            try:
                dt = datetime.datetime.fromisoformat(str(ts_str).replace("Z", "+00:00")).replace(tzinfo=None)
                if dt < cutoff:
                    continue
            except Exception:
                pass
        filtered_trades.append(t)

    # Performance Metrics
    total = len(filtered_trades)
    wins = []
    losses = []
    net_pnl = 0.0
    gross_win = 0.0
    gross_loss = 0.0
    total_fees = 0.0
    daily_pnl_map = {}
    strategy_map = {}
    timeframe_map = {}
    symbol_map = {}

    # If telemetry store returned nothing, fall back to the thin ledger
    if total == 0:
        try:
            ledger_trades = _get_trades_data().get("positions", [])
            if ledger_trades:
                filtered_trades = ledger_trades
                total = len(filtered_trades)
                logger.info(f"[ANALYTICS] Telemetry store empty; using ledger fallback ({total} trades)")
        except Exception as _fb_err:
            logger.warning(f"[ANALYTICS] Ledger fallback failed: {_fb_err}")

    for t in filtered_trades:
        pnl = float(t.get("net_pnl", t.get("pnl", 0.0)))
        fee = float(t.get("fees", t.get("total_fees", 0.0)))
        net_pnl += pnl
        total_fees += fee

        if pnl > 0:
            wins.append(pnl)
            gross_win += pnl
        elif pnl < 0:
            losses.append(pnl)
            gross_loss += abs(pnl)

        # Daily PnL
        ts_str = str(t.get("close_timestamp") or t.get("close_time") or t.get("timestamp", ""))[:10]
        if ts_str and len(ts_str) == 10:
            daily_pnl_map[ts_str] = daily_pnl_map.get(ts_str, 0.0) + pnl

        # Strategy breakdown
        st = str(t.get("strategy", "UNKNOWN")).upper()
        if st not in strategy_map:
            strategy_map[st] = {"trades": 0, "wins": 0, "pnl": 0.0}
        strategy_map[st]["trades"] += 1
        strategy_map[st]["pnl"] += pnl
        if pnl > 0:
            strategy_map[st]["wins"] += 1

        # Timeframe breakdown
        tf = str(t.get("timeframe", "5m")).lower()
        if tf not in timeframe_map:
            timeframe_map[tf] = {"trades": 0, "wins": 0, "pnl": 0.0}
        timeframe_map[tf]["trades"] += 1
        timeframe_map[tf]["pnl"] += pnl
        if pnl > 0:
            timeframe_map[tf]["wins"] += 1

        # Symbol breakdown
        sym = str(t.get("symbol", "UNKNOWN")).upper()
        if sym not in symbol_map:
            symbol_map[sym] = {"trades": 0, "wins": 0, "pnl": 0.0}
        symbol_map[sym]["trades"] += 1
        symbol_map[sym]["pnl"] += pnl
        if pnl > 0:
            symbol_map[sym]["wins"] += 1

    win_count = len(wins)
    loss_count = len(losses)
    win_rate = round((win_count / total) * 100, 2) if total > 0 else None
    pf = round(gross_win / gross_loss, 2) if gross_loss > 0 else (999.0 if gross_win > 0 else None)
    avg_trade = round(net_pnl / total, 4) if total > 0 else None
    avg_win = round(gross_win / win_count, 4) if win_count > 0 else None
    avg_loss = round(gross_loss / loss_count, 4) if loss_count > 0 else None
    largest_win = round(max(wins), 4) if wins else None
    largest_loss = round(min(losses), 4) if losses else None

    # Unrealized PnL from open positions in portfolio (NOT account_holdings — that dict doesn't carry unrealized_pnl)
    unrealized_pnl = 0.0
    port_file_path = os.getenv("TESTNET_PORTFOLIO_FILE", "testnet_portfolio.json")
    if os.path.exists(port_file_path):
        try:
            with open(port_file_path, "r") as _pf:
                _pd = json.load(_pf)
                for _pos in _pd.get("positions", {}).values():
                    if isinstance(_pos, dict) and _pos.get("status", "OPEN") == "OPEN":
                        unrealized_pnl += float(_pos.get("unrealized_pnl", 0.0))
        except Exception:
            pass

    # PnL distribution buckets
    pnl_dist = {
        "<-$50": 0,
        "-$50 to -$20": 0,
        "-$20 to -$5": 0,
        "-$5 to $0": 0,
        "$0 to $5": 0,
        "$5 to $20": 0,
        "$20 to $50": 0,
        ">$50": 0
    }
    for t in filtered_trades:
        pnl = float(t.get("net_pnl", t.get("pnl", 0.0)))
        if pnl < -50:
            pnl_dist["<-$50"] += 1
        elif -50 <= pnl < -20:
            pnl_dist["-$50 to -$20"] += 1
        elif -20 <= pnl < -5:
            pnl_dist["-$20 to -$5"] += 1
        elif -5 <= pnl < 0:
            pnl_dist["-$5 to $0"] += 1
        elif 0 <= pnl < 5:
            pnl_dist["$0 to $5"] += 1
        elif 5 <= pnl < 20:
            pnl_dist["$5 to $20"] += 1
        elif 20 <= pnl < 50:
            pnl_dist["$20 to $50"] += 1
        else:
            pnl_dist[">$50"] += 1

    # Format comparison maps
    for st, v in strategy_map.items():
        v["win_rate"] = round((v["wins"] / v["trades"]) * 100, 1) if v["trades"] > 0 else None
        v["pnl"] = round(v["pnl"], 4)
    for tf, v in timeframe_map.items():
        v["win_rate"] = round((v["wins"] / v["trades"]) * 100, 1) if v["trades"] > 0 else None
        v["pnl"] = round(v["pnl"], 4)
    for sym, v in symbol_map.items():
        v["win_rate"] = round((v["wins"] / v["trades"]) * 100, 1) if v["trades"] > 0 else None
        v["pnl"] = round(v["pnl"], 4)

    # Diagnostic Funnel: "Why Didn't It Trade?"
    port_file = os.getenv("TESTNET_PORTFOLIO_FILE", "testnet_portfolio.json")
    scanner_stats = {}
    if os.path.exists(port_file):
        try:
            with open(port_file, "r") as f:
                port_data = json.load(f)
                scanner_stats = port_data.get("scanner_stats", {})
        except Exception:
            pass

    evals = scanner_stats.get("strategy_evaluations", 0)
    sig_count = scanner_stats.get("TOTAL_SIGNALS", 0)
    prof_acc = scanner_stats.get("PROFITABILITY_ACCEPTED", 0)
    prof_rej = scanner_stats.get("PROFITABILITY_REJECTED", 0)
    risk_acc = scanner_stats.get("RISK_ACCEPTED", 0)
    risk_rej = scanner_stats.get("RISK_REJECTED", 0)
    exec_eligible = scanner_stats.get("EXECUTION_ELIGIBLE", 0)
    orders_sub = scanner_stats.get("ORDERS_SUBMITTED", 0)
    orders_fail = scanner_stats.get("ORDERS_FAILED", 0)
    orders_fill = scanner_stats.get("ORDERS_FILLED", 0)

    # Determine dominant reason
    dominant_reason = "NO CANDIDATE SURVIVED ALL EXECUTION RULES"
    if prof_rej > 0 and prof_rej >= risk_rej:
        dominant_reason = f"PROFITABILITY FILTER ({prof_rej} signals rejected due to expected return < fee friction)"
    elif risk_rej > 0 and risk_rej > prof_rej:
        dominant_reason = f"RISK GATE CEILING ({risk_rej} signals rejected due to exposure limits or max positions)"
    elif orders_fail > 0:
        dominant_reason = f"EXCHANGE DISPATCH ERROR ({orders_fail} orders rejected by Binance validation)"
    elif orders_fill > 0:
        dominant_reason = f"ACTIVE TRADING ({orders_fill} orders successfully filled on exchange)"

    return jsonify({
        "status": "OK",
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "data_age": 0.0,
        "timeframe": tf_filter,
        "analytics": {
            "total_trades": total,
            "winning_trades": win_count,
            "losing_trades": loss_count,
            "win_rate": win_rate,
            "net_pnl": round(net_pnl, 4),
            "realized_pnl": round(gross_win - gross_loss, 4),
            "unrealized_pnl": round(unrealized_pnl, 4),
            "profit_factor": pf,
            "avg_trade": avg_trade,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "largest_win": largest_win,
            "largest_loss": largest_loss,
            "total_fees": round(total_fees, 4),
            "max_drawdown": round(float(scanner_stats.get("max_drawdown", 0.0)) * 100, 2)
        },
        "daily_pnl": daily_pnl_map,
        "pnl_distribution": pnl_dist,
        "strategy_comparison": strategy_map,
        "timeframe_comparison": timeframe_map,
        "symbol_comparison": symbol_map,
        "why_didnt_it_trade": {
            "candles": scanner_stats.get("TOTAL_CANDLES", 0),
            "evaluations": evals,
            "signals": sig_count,
            "profitability_accepted": prof_acc,
            "profitability_rejected": prof_rej,
            "risk_accepted": risk_acc,
            "risk_rejected": risk_rej,
            "execution_eligible": exec_eligible,
            "orders_submitted": orders_sub,
            "orders_failed": orders_fail,
            "orders_filled": orders_fill,
            "dominant_reason": dominant_reason
        }
    })

@app.route('/api/system-health')
def api_system_health():
    """Returns comprehensive component health metrics."""
    engine_data = get_engine_health_data()
    return jsonify({
        "status": "SUCCESS",
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "data_age": 0.0,
        "health": {
            "overall": "HEALTHY" if engine_data["healthy"] else "DEGRADED",
            "engine": "OK" if engine_data["healthy"] else "ERROR",
            "binance": "OK" if engine_data.get("binance_connected") else "ERROR",
            "websocket": "OK" if engine_data.get("websocket_connected") else "ERROR",
            "execution": "OK" if engine_data["healthy"] else "ERROR",
            "strategy": "OK" if engine_data["healthy"] else "ERROR",
            "last_heartbeat": engine_data.get("heartbeat_age_seconds", 0)
        }
    })

def get_funnel():
    """
    Builds the signal funnel pipeline statistics and top live opportunities.
    Reads from testnet_portfolio.json (scanner_stats) and
    testnet_opportunity_log.jsonl (live opportunity records).
    """
    stats = {
        "symbols_scanned": 0,
        "TOTAL_SIGNALS": 0,
        "PROFITABILITY_ACCEPTED": 0,
        "PROFITABILITY_REJECTED": 0,
        "RISK_ACCEPTED": 0,
        "RISK_REJECTED": 0,
        "COOLDOWN_REJECTED": 0,
        "JIT_REJECTED": 0,
        "OTHER_REJECTED": 0,
        "QUALIFIED": 0,
        "EXECUTION_REJECTED": 0,
        "ORDERS_SUBMITTED": 0,
        "ORDERS_FILLED": 0,
        "ORDERS_FAILED": 0,
        "CLOSED_TRADES": 0,
        "top_opportunities": [],
        "strategy_metrics": {},
        "timeframe_metrics": {}
    }

    # Load scanner_stats from portfolio state
    port_file = os.getenv("TESTNET_PORTFOLIO_FILE", "testnet_portfolio.json")
    if os.path.exists(port_file):
        try:
            with open(port_file, "r") as f:
                port = json.load(f)
            scanner_stats = port.get("scanner_stats", {})
            stats.update({k: v for k, v in scanner_stats.items() if k in stats})
            # Also pull symbols_scanned
            stats["symbols_scanned"] = scanner_stats.get("symbols_scanned", len(port.get("symbols", [])))
            stats["strategy_metrics"] = scanner_stats.get("strategy_metrics", {})
            stats["timeframe_metrics"] = scanner_stats.get("timeframe_metrics", {})
        except Exception as e:
            logger.warning(f"[FUNNEL] Error reading portfolio scanner stats: {e}")

    # Read live opportunity log (last 15 minutes)
    opp_file = os.getenv("TESTNET_OPPORTUNITY_LOG", "testnet_opportunity_log.jsonl")
    if os.path.exists(opp_file):
        try:
            opps = []
            now = datetime.datetime.utcnow()
            with open(opp_file, "r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        opp = json.loads(line)
                        ts_str = opp.get("timestamp", "")
                        if ts_str:
                            ts = datetime.datetime.fromisoformat(
                                ts_str.replace("Z", "+00:00")
                            ).replace(tzinfo=None)
                            if (now - ts).total_seconds() < 900:
                                opps.append(opp)
                        else:
                            opps.append(opp)  # include if no timestamp
                    except Exception:
                        pass
            stats["top_opportunities"] = sorted(
                opps, key=lambda x: x.get("timestamp", ""), reverse=True
            )[:15]
        except Exception as e:
            logger.error(f"[FUNNEL] Error reading opportunity log: {e}")

    # Fallback strategy_metrics from trade ledger
    if not stats["strategy_metrics"]:
        trades_info = _get_trades_data()
        strat_metrics = {}
        for s_name in ["ADX_EMA", "ML", "SCALPER", "SUPERTREND", "SWING", "AGGRESSOR", "FAST1M"]:
            strat_metrics[s_name] = {
                "evaluations": 0, "BUY": 0, "SELL": 0, "HOLD": 0,
                "qualified": 0, "rejected": 0, "orders": 0, "fills": 0,
                "wins": 0, "losses": 0, "PnL": 0.0
            }
        for t in trades_info.get("positions", []):
            st = str(t.get("strategy", "AGGRESSOR")).upper()
            if st not in strat_metrics:
                strat_metrics[st] = {
                    "evaluations": 0, "BUY": 0, "SELL": 0, "HOLD": 0,
                    "qualified": 0, "rejected": 0, "orders": 0, "fills": 0,
                    "wins": 0, "losses": 0, "PnL": 0.0
                }
            strat_metrics[st]["fills"] += 1
            strat_metrics[st]["orders"] += 1
            strat_metrics[st]["qualified"] += 1
            strat_metrics[st]["evaluations"] += 1
            side = t.get("action", "").upper()
            if side in ["BUY", "LONG"]:
                strat_metrics[st]["BUY"] += 1
            else:
                strat_metrics[st]["SELL"] += 1
            pnl = float(t.get("pnl", 0.0))
            strat_metrics[st]["PnL"] += pnl
            if pnl > 0:
                strat_metrics[st]["wins"] += 1
            elif pnl < 0:
                strat_metrics[st]["losses"] += 1
        stats["strategy_metrics"] = strat_metrics

    stats["count"] = len(stats.get("top_opportunities", []))

    return jsonify({
        "status": "SUCCESS",
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "data_age": 0.0,
        "count": stats["count"],
        **stats
    })


@app.route('/api/opportunities')
def api_opportunities():
    """Returns candidate funnel pipeline opportunities."""
    return get_funnel()

@app.route('/api/activity')
@app.route('/api/telemetry/activity')
@app.route('/api/account-activity')
def api_activity():
    """
    Returns unified chronological event feed containing:
    Trade opened, Trade closed, Fee, Reconciliation, Balance change,
    Equity change, Engine recovery, Order failed, Safety halt, Signal.
    """
    try:
        from testnet_engine.telemetry_manager import get_telemetry_manager
        tm = get_telemetry_manager()
        limit = int(request.args.get('limit', 100))
        
        events = []
        account_holdings = get_live_account_and_holdings()
        curr_cash = account_holdings["usdt_total_cash"]
        curr_eq = curr_cash + account_holdings["active_trade_holdings_value"]

        # 1. Canonical Trades (Opened & Closed)
        raw_trades = tm.query_trades(limit=100)
        for t in raw_trades:
            tid = t.get("trade_id", "")
            sym = t.get("symbol", "")
            strat = t.get("strategy", "")
            tf = t.get("timeframe", "5m")
            side = t.get("side", "BUY")
            
            # Opened event
            open_ts = t.get("fill_timestamp") or t.get("order_submit_timestamp") or t.get("signal_timestamp")
            if open_ts:
                eq_open = float(t.get("equity_before_entry") or t.get("equity_after_entry") or curr_eq)
                bal_open = float(t.get("cash_before_entry") or t.get("cash_after_entry") or curr_cash)
                events.append({
                    "time": open_ts,
                    "timestamp": open_ts,
                    "event": "Trade opened",
                    "type": "TRADE OPENED",
                    "symbol": sym,
                    "strategy": strat,
                    "timeframe": tf,
                    "trade_id": tid,
                    "balance": bal_open,
                    "equity": eq_open,
                    "pnl": 0.0,
                    "value_pnl": "$0.00",
                    "description": f"Opened {side} {t.get('quantity', 0)} {sym} @ ${float(t.get('entry_price', 0)):,.4f} ({strat})",
                    "raw": t
                })

            # Closed event
            close_ts = t.get("close_timestamp") or t.get("close_time")
            if close_ts and t.get("status") == "CLOSED":
                net_pnl = float(t.get("net_pnl", t.get("pnl", 0.0)))
                fees = float(t.get("total_fees", t.get("fees", 0.0)))
                eq_close = float(t.get("equity_after_exit") or t.get("equity_before_exit") or curr_eq)
                bal_close = float(t.get("cash_after_exit") or t.get("cash_before_exit") or curr_cash)
                
                events.append({
                    "time": close_ts,
                    "timestamp": close_ts,
                    "event": "Trade closed",
                    "type": "TRADE CLOSED",
                    "symbol": sym,
                    "strategy": strat,
                    "timeframe": tf,
                    "trade_id": tid,
                    "balance": bal_close,
                    "equity": eq_close,
                    "pnl": net_pnl,
                    "value_pnl": f"{'+' if net_pnl >= 0 else ''}${net_pnl:,.2f}",
                    "description": f"Closed {side} {sym} via {t.get('close_reason', 'OCO_TARGET')} @ ${float(t.get('exit_price', 0)):,.4f} (Net PnL: {'+' if net_pnl >= 0 else ''}${net_pnl:,.2f})",
                    "raw": t
                })

                if fees > 0:
                    events.append({
                        "time": close_ts,
                        "timestamp": close_ts,
                        "event": "Fee",
                        "type": "FEE",
                        "symbol": sym,
                        "strategy": strat,
                        "timeframe": tf,
                        "trade_id": tid,
                        "balance": bal_close,
                        "equity": eq_close,
                        "pnl": -fees,
                        "value_pnl": f"-${fees:,.4f}",
                        "description": f"Exchange commission deducted: ${fees:,.4f} for {tid}",
                        "raw": {"trade_id": tid, "fees": fees, "timestamp": close_ts}
                    })

        # 2. Balance & Reconciliation Events
        raw_bal = tm.get_balance_events(limit=100)
        for b in raw_bal:
            ts = b.get("timestamp", "")
            ev_type = str(b.get("event_type", "BALANCE_CHANGE")).upper()
            delta = float(b.get("delta", 0.0))
            b_after = float(b.get("balance_after", curr_cash))
            
            tag_name = "Reconciliation" if "RECONCIL" in ev_type else ("Fee" if "FEE" in ev_type else "Balance change")
            events.append({
                "time": ts,
                "timestamp": ts,
                "event": tag_name,
                "type": ev_type,
                "symbol": b.get("symbol", "USDT"),
                "strategy": b.get("strategy", "-"),
                "timeframe": b.get("timeframe", "-"),
                "trade_id": b.get("trade_id", "-"),
                "balance": b_after,
                "equity": curr_eq,
                "pnl": float(b.get("realized_pnl_delta", delta)),
                "value_pnl": f"{'+' if delta >= 0 else ''}${delta:,.2f}",
                "description": f"Balance update: ${float(b.get('balance_before', 0)):,.2f} -> ${b_after:,.2f} ({b.get('reason', ev_type)})",
                "raw": b
            })

        # 3. Execution & Failure Events
        raw_exec = tm.get_execution_events(limit=100)
        for e in raw_exec:
            ts = e.get("timestamp", "")
            ev_type = str(e.get("event_type", "")).upper()
            sym = e.get("symbol", "")
            strat = e.get("strategy", "")
            tf = e.get("timeframe", "5m")
            
            if ev_type == "ORDER_FAILED":
                events.append({
                    "time": ts,
                    "timestamp": ts,
                    "event": "Order failed",
                    "type": "ORDER FAILED",
                    "symbol": sym,
                    "strategy": strat,
                    "timeframe": tf,
                    "trade_id": e.get("trade_id", "-"),
                    "balance": curr_cash,
                    "equity": curr_eq,
                    "pnl": 0.0,
                    "value_pnl": "-",
                    "description": f"Order failure on {sym}: {e.get('error_message', e.get('error_code', 'Exchange rejected order'))}",
                    "raw": e
                })
            elif "RECOVERY" in ev_type or "HEARTBEAT" in ev_type:
                events.append({
                    "time": ts,
                    "timestamp": ts,
                    "event": "Engine recovery",
                    "type": "ENGINE RECOVERY",
                    "symbol": sym or "SYSTEM",
                    "strategy": strat or "-",
                    "timeframe": tf,
                    "trade_id": e.get("trade_id", "-"),
                    "balance": curr_cash,
                    "equity": curr_eq,
                    "pnl": 0.0,
                    "value_pnl": "-",
                    "description": f"Engine state reconciled: {e.get('details', 'State successfully restored')}",
                    "raw": e
                })

        # 4. Signals
        raw_signals = tm.get_signals_log(limit=50)
        for s in raw_signals:
            ts = s.get("timestamp", "")
            sym = s.get("symbol", "")
            strat = s.get("strategy", "")
            tf = s.get("timeframe", "5m")
            dec = s.get("decision", "HOLD")
            f_dec = s.get("final_decision", "")

            if f_dec in ["ACCEPTED", "EXECUTED"] or dec in ["BUY", "SELL"]:
                events.append({
                    "time": ts,
                    "timestamp": ts,
                    "event": "New qualifying signal",
                    "type": "QUALIFYING SIGNAL",
                    "symbol": sym,
                    "strategy": strat,
                    "timeframe": tf,
                    "trade_id": s.get("signal_id", "-"),
                    "balance": curr_cash,
                    "equity": curr_eq,
                    "pnl": 0.0,
                    "value_pnl": f"${float(s.get('entry', 0)):,.2f}" if float(s.get('entry', 0)) > 0 else "-",
                    "description": f"New Qualifying Signal: {dec} {sym} ({strat}, {tf}) Confidence {int(float(s.get('confidence', 0))*100)}%",
                    "raw": s
                })

        # Deduplicate & Sort newest first
        unique_events = []
        seen = set()
        for ev in sorted(events, key=lambda x: str(x.get("time", "")), reverse=True):
            k = f"{ev.get('time')}_{ev.get('type')}_{ev.get('trade_id')}_{ev.get('symbol')}"
            if k not in seen:
                seen.add(k)
                unique_events.append(ev)

        return jsonify({
            "status": "OK",
            "count": len(unique_events[:limit]),
            "activity": unique_events[:limit],
            "events": unique_events[:limit],
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
        })
    except Exception as e:
        logger.error(f"Error in api_activity: {e}")
        return jsonify({"status": "ERROR", "error": str(e)}), 500

@app.route('/api/balance-events')
def api_balance_events():
    """Returns chronological audit log of balance transitions."""
    from testnet_engine.telemetry_manager import get_telemetry_manager
    limit = int(request.args.get("limit", 100))
    telemetry = get_telemetry_manager()
    events = telemetry.get_balance_events(limit=limit)
    return jsonify({
        "status": "SUCCESS",
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "data_age": 0.0,
        "count": len(events),
        "events": events
    })


@app.route('/api/risk-events')
def api_risk_events():
    """
    Returns risk gate rejection and acceptance events.
    Sources:
      - Signal events where risk_decision == REJECTED
      - Execution events where event_type == order_failed with risk-related error codes
    Supports ?limit=N and ?symbol=SYM query params.
    """
    from testnet_engine.telemetry_manager import get_telemetry_manager
    limit = int(request.args.get("limit", 100))
    symbol_filter = request.args.get("symbol")
    telemetry = get_telemetry_manager()

    risk_events = []

    # 1. Signal-level risk gate decisions
    signals = telemetry.get_signals_log(limit=500)
    for s in signals:
        r_dec = s.get("risk_decision", "")
        if r_dec in ["REJECTED", "ACCEPTED"]:
            sym = s.get("symbol", "")
            if symbol_filter and sym != symbol_filter:
                continue
            
            # Format dynamic risk numbers
            req_risk_val = s.get("requested_risk_pct", config.MAX_TESTNET_RISK_PER_TRADE * 100)
            avail_risk_val = s.get("available_risk_pct", config.MAX_TESTNET_EXPOSURE * 100)
            exp_val = s.get("exposure_pct", 0.0)

            risk_events.append({
                "timestamp": s.get("timestamp", ""),
                "event_type": f"RISK_{r_dec}",
                "symbol": sym,
                "strategy": s.get("strategy", "ADX_EMA"),
                "timeframe": s.get("timeframe", "5m"),
                "trade_id": s.get("signal_id", ""),
                "requested_risk": f"{req_risk_val:.2f}%" if isinstance(req_risk_val, (int, float)) else str(req_risk_val),
                "available_risk": f"{avail_risk_val:.2f}%" if isinstance(avail_risk_val, (int, float)) else str(avail_risk_val),
                "exposure": f"{exp_val:.2f}%" if isinstance(exp_val, (int, float)) else str(exp_val),
                "reason": s.get("risk_reason", r_dec),
                "decision": r_dec,
                "entry_price": float(s.get("entry", 0.0)),
                "confidence": float(s.get("confidence", 0.0)),
                "expected_net": float(s.get("expected_net", 0.0)),
                "source": "signal_gate"
            })

    # 2. Execution-level risk failures (order_failed)
    exec_events = telemetry.get_execution_events(limit=500)
    for e in exec_events:
        if e.get("event_type") == "order_failed":
            err_code = e.get("error_code", "")
            sym = e.get("symbol", "")
            if symbol_filter and sym != symbol_filter:
                continue
            req_risk_val = e.get("requested_risk_pct", config.MAX_TESTNET_RISK_PER_TRADE * 100)
            avail_risk_val = e.get("available_risk_pct", config.MAX_TESTNET_EXPOSURE * 100)
            exp_val = e.get("exposure_pct", 0.0)

            risk_events.append({
                "timestamp": e.get("timestamp", ""),
                "event_type": "ORDER_FAILED",
                "symbol": sym,
                "strategy": e.get("strategy", "ADX_EMA"),
                "timeframe": e.get("timeframe", "5m"),
                "trade_id": e.get("trade_id", ""),
                "requested_risk": f"{req_risk_val:.2f}%" if isinstance(req_risk_val, (int, float)) else str(req_risk_val),
                "available_risk": f"{avail_risk_val:.2f}%" if isinstance(avail_risk_val, (int, float)) else str(avail_risk_val),
                "exposure": f"{exp_val:.2f}%" if isinstance(exp_val, (int, float)) else str(exp_val),
                "reason": e.get("error_message", err_code),
                "decision": "REJECTED",
                "error_code": err_code,
                "source": "execution_gate"
            })

    risk_events.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return jsonify({
        "status": "SUCCESS",
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "data_age": 0.0,
        "count": len(risk_events[:limit]),
        "events": risk_events[:limit]
    })


@app.route('/api/system-events')
def api_system_events():
    """
    Returns system-level events:
      - ENGINE_RECOVERY events from execution log
      - RECONCILIATION events from balance events
      - Engine heartbeat state changes
      - SAFETY_HALT triggers
    Supports ?limit=N query param.
    """
    from testnet_engine.telemetry_manager import get_telemetry_manager
    limit = int(request.args.get("limit", 100))
    telemetry = get_telemetry_manager()

    system_events = []

    # 1. Recovery and reconciliation from execution events
    exec_events = telemetry.get_execution_events(limit=500)
    system_event_types = {
        "engine_recovery", "engine_start", "safety_halt",
        "reconciliation", "reconnect", "restart"
    }
    for e in exec_events:
        ev_type = e.get("event_type", "").lower()
        if any(t in ev_type for t in system_event_types):
            system_events.append({
                "timestamp": e.get("timestamp", ""),
                "event_type": e.get("event_type", "").upper(),
                "symbol": e.get("symbol", "SYSTEM"),
                "strategy": e.get("strategy", "-"),
                "message": e.get("error_message", e.get("event_type", "")),
                "status": e.get("status", ""),
                "source": "execution_log"
            })

    # 2. RECONCILIATION events from balance audit
    bal_events = telemetry.get_balance_events(limit=200)
    for b in bal_events:
        ev_type = b.get("event_type", "").upper()
        if ev_type in ["RECONCILIATION", "DEPOSIT", "WITHDRAWAL", "ENGINE_RECOVERY"]:
            system_events.append({
                "timestamp": b.get("timestamp", ""),
                "event_type": ev_type,
                "symbol": b.get("symbol", "USDT"),
                "strategy": b.get("strategy", "-"),
                "message": b.get("reason", ev_type),
                "balance_before": float(b.get("balance_before", 0.0)),
                "balance_after": float(b.get("balance_after", 0.0)),
                "delta": float(b.get("delta", 0.0)),
                "source": "balance_audit"
            })

    # 3. Engine heartbeat state
    engine_data = get_engine_health_data()
    system_events.append({
        "timestamp": engine_data.get("timestamp", datetime.datetime.utcnow().isoformat() + "Z"),
        "event_type": "ENGINE_HEARTBEAT",
        "symbol": "SYSTEM",
        "strategy": "-",
        "message": f"Engine {engine_data.get('engine_status', 'UNKNOWN')} — heartbeat age {engine_data.get('heartbeat_age_seconds', 0)}s",
        "status": engine_data.get("engine_status", "UNKNOWN"),
        "healthy": engine_data.get("healthy", False),
        "source": "heartbeat"
    })

    system_events.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return jsonify({
        "status": "SUCCESS",
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "data_age": 0.0,
        "count": len(system_events[:limit]),
        "events": system_events[:limit]
    })




@app.route('/api/telemetry/trades')
def api_telemetry_trades():
    """Returns canonical trades from the unified telemetry ledger with filtering."""
    try:
        from testnet_engine.telemetry import get_telemetry_manager
        tm = get_telemetry_manager()
        symbol = request.args.get('symbol')
        strategy = request.args.get('strategy')
        timeframe = request.args.get('timeframe')
        status = request.args.get('status')
        limit = int(request.args.get('limit', 100))
        
        trades = tm.query_trades(status=status, symbol=symbol, strategy=strategy, timeframe=timeframe, limit=limit)
        return jsonify({
            "status": "OK",
            "count": len(trades),
            "trades": trades,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
        })
    except Exception as e:
        return jsonify({"status": "ERROR", "error": str(e)}), 500

@app.route('/api/telemetry/signals')
def api_telemetry_signals():
    """Returns signal funnel and opportunity telemetry."""
    try:
        from testnet_engine.telemetry import get_telemetry_manager
        tm = get_telemetry_manager()
        symbol = request.args.get('symbol')
        limit = int(request.args.get('limit', 100))
        signals = tm.query_signals(symbol=symbol, limit=limit)
        return jsonify({
            "status": "OK",
            "count": len(signals),
            "signals": signals,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
        })
    except Exception as e:
        return jsonify({"status": "ERROR", "error": str(e)}), 500

@app.route('/api/telemetry/positions')
def api_telemetry_positions():
    """Returns active and historical position telemetry."""
    try:
        from testnet_engine.telemetry import get_telemetry_manager
        tm = get_telemetry_manager()
        status = request.args.get('status')
        positions = tm.query_positions(status=status)
        return jsonify({
            "status": "OK",
            "count": len(positions),
            "positions": positions,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
        })
    except Exception as e:
        return jsonify({"status": "ERROR", "error": str(e)}), 500

@app.route('/api/telemetry/equity_curve')
def api_telemetry_equity_curve():
    """Returns chronological high-frequency equity curve."""
    try:
        from testnet_engine.telemetry import get_telemetry_manager
        tm = get_telemetry_manager()
        limit = int(request.args.get('limit', 500))
        curve = tm.query_equity_curve(limit=limit)
        return jsonify({
            "status": "OK",
            "count": len(curve),
            "equity_curve": curve,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
        })
    except Exception as e:
        return jsonify({"status": "ERROR", "error": str(e)}), 500

@app.route('/api/telemetry/balance_events')
def api_telemetry_balance_events():
    """Returns balance audit trail with non-double-counted deltas."""
    try:
        from testnet_engine.telemetry import get_telemetry_manager
        tm = get_telemetry_manager()
        limit = int(request.args.get('limit', 100))
        events = tm.query_balance_events(limit=limit)
        return jsonify({
            "status": "OK",
            "count": len(events),
            "balance_events": events,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
        })
    except Exception as e:
        return jsonify({"status": "ERROR", "error": str(e)}), 500

@app.route('/api/telemetry/analytics')
def api_telemetry_analytics():
    """Returns aggregated performance analytics across strategies, timeframes, and assets."""
    try:
        from testnet_engine.telemetry import get_telemetry_manager
        tm = get_telemetry_manager()
        analytics = tm.compute_summary_analytics()
        return jsonify({
            "status": "OK",
            "analytics": analytics,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
        })
    except Exception as e:
        return jsonify({"status": "ERROR", "error": str(e)}), 500

@app.route('/api/config', methods=['GET', 'POST'])
@app.route('/api/settings', methods=['GET', 'POST'])
def api_config():
    """
    Returns or updates runtime configuration.
    Enforces security: LIVE_TRADING_ENABLED cannot be toggled to True via API.
    Rejects invalid types, negative numbers, NaNs, or values outside safety limits.
    API keys/secrets are never exposed.
    """
    try:
        import config
        if request.method == 'POST':
            data = request.get_json(silent=True)
            if not isinstance(data, dict):
                return jsonify({"status": "ERROR", "error": "Invalid request payload. Expected JSON object."}), 400
                
            # Block any attempt to enable live trading or change trading mode to LIVE
            if "live_trading_enabled" in data and bool(data["live_trading_enabled"]):
                return jsonify({"status": "ERROR", "error": "SECURITY FORBIDDEN: Live trading is permanently disabled by design."}), 403
            if "trading_mode" in data and str(data["trading_mode"]).upper() == "LIVE":
                return jsonify({"status": "ERROR", "error": "SECURITY FORBIDDEN: Live trading is permanently disabled by design."}), 403

            if "max_open_trades" in data:
                try:
                    val = int(data["max_open_trades"])
                    if val < 1 or val > 20:
                        return jsonify({"status": "ERROR", "error": "max_open_trades must be between 1 and 20"}), 400
                    config.MAX_OPEN_TRADES = val
                    config.MAX_OPEN_POSITIONS = val
                except (ValueError, TypeError):
                    return jsonify({"status": "ERROR", "error": "max_open_trades must be an integer"}), 400

            if "max_trades_per_day" in data:
                try:
                    val = int(data["max_trades_per_day"])
                    if val < 1 or val > 200:
                        return jsonify({"status": "ERROR", "error": "max_trades_per_day must be between 1 and 200"}), 400
                    config.TARGET_TRADE_COUNT = val
                except (ValueError, TypeError):
                    return jsonify({"status": "ERROR", "error": "max_trades_per_day must be an integer"}), 400

            return jsonify({
                "status": "success",
                "message": "Configuration updated successfully",
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
            })
            
        return jsonify({
            "status": "OK",
            "environment": "TESTNET",
            "exchange": "BINANCE",
            "trading_mode": config.TRADING_MODE,
            "max_open_trades": getattr(config, "MAX_OPEN_TRADES", 5),
            "max_trades_per_day": getattr(config, "TARGET_TRADE_COUNT", 50),
            "max_trades_per_symbol": 1,
            "max_trades_per_strategy": 3,
            "cooldown_trade": "5m",
            "cooldown_symbol": "5m",
            "risk_per_trade": getattr(config, "MAX_TESTNET_RISK_PER_TRADE", 0.005),
            "max_portfolio_risk": getattr(config, "MAX_TESTNET_EXPOSURE", 0.05),
            "max_portfolio_exposure": getattr(config, "MAX_TESTNET_EXPOSURE", 0.05),
            "max_drawdown": getattr(config, "MAX_TESTNET_DRAWDOWN_PCT", 0.05),
            "daily_loss_limit_pct": getattr(config, "MAX_DAILY_LOSS_PCT", 0.02),
            "max_open_positions": getattr(config, "MAX_OPEN_POSITIONS", 5),
            "min_expected_edge": getattr(config, "MINIMUM_EXPECTED_EDGE", 0.0001),
            "active_strategies": getattr(config, "ACTIVE_STRATEGIES", {}),
            "duplicate_protection": "ON",
            "safety_halt": "ON",
            "live_trading_enabled": False,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
        })
    except Exception as e:
        return jsonify({"status": "ERROR", "error": str(e)}), 500

# ==============================================================================
# GEMINI AI ANALYSIS LAYER ENDPOINTS (Advisory & Diagnostics Only)
# ==============================================================================
@app.route('/api/ai/status', methods=['GET'])
def api_ai_status():
    """Returns safe Gemini AI status metadata without revealing keys."""
    try:
        from gemini_service import get_gemini_service
        service = get_gemini_service()
        return jsonify(service.get_status())
    except Exception as e:
        logger.warning(f"[API_AI_STATUS] Error: {e}")
        return jsonify({
            "status": "SUCCESS",
            "gemini": {
                "enabled": False,
                "configured": False,
                "status": "UNAVAILABLE",
                "model": "gemini-2.5-flash"
            }
        })

@app.route('/api/ai/test-connection', methods=['POST'])
def api_ai_test_connection():
    """Triggers a server-side connectivity test with Google Gemini."""
    try:
        from gemini_service import get_gemini_service
        service = get_gemini_service()
        res = service.test_connection()
        return jsonify(res)
    except Exception as e:
        logger.warning(f"[API_AI_TEST] Error: {e}")
        return jsonify({"success": False, "status": "UNAVAILABLE", "message": str(e)}), 500

@app.route('/api/ai/signal-analysis', methods=['POST'])
def api_ai_signal_analysis():
    """Generates structured natural-language rationale for a scanner signal."""
    try:
        from gemini_service import get_gemini_service
        data = request.get_json() or {}
        service = get_gemini_service()
        analysis = service.analyze_signal(data)
        return jsonify({"status": "SUCCESS", "analysis": analysis})
    except Exception as e:
        logger.warning(f"[API_AI_SIGNAL] Error: {e}")
        return jsonify({"status": "SUCCESS", "analysis": {"why": "Analysis unavailable", "ai_available": False}})

@app.route('/api/ai/trade-analysis', methods=['POST'])
def api_ai_trade_analysis():
    """Generates post-trade review & execution audit for closed trades."""
    try:
        from gemini_service import get_gemini_service
        data = request.get_json() or {}
        service = get_gemini_service()
        analysis = service.analyze_trade(data)
        return jsonify({"status": "SUCCESS", "analysis": analysis})
    except Exception as e:
        logger.warning(f"[API_AI_TRADE] Error: {e}")
        return jsonify({"status": "SUCCESS", "analysis": {"trade_summary": "Review unavailable", "ai_available": False}})

@app.route('/api/ai/performance-analysis', methods=['POST'])
def api_ai_performance_analysis():
    """Provides quantitative portfolio observations and strategy notes."""
    try:
        from gemini_service import get_gemini_service
        data = request.get_json() or {}
        service = get_gemini_service()
        analysis = service.analyze_performance(data)
        return jsonify({"status": "SUCCESS", "analysis": analysis})
    except Exception as e:
        logger.warning(f"[API_AI_PERF] Error: {e}")
        return jsonify({"status": "SUCCESS", "analysis": {"performance_summary": "Summary unavailable", "ai_available": False}})

@app.route('/api/ai/system-analysis', methods=['POST'])
def api_ai_system_analysis():
    """Provides high-level system diagnostics based on recent events."""
    try:
        from gemini_service import get_gemini_service
        data = request.get_json() or {}
        service = get_gemini_service()
        analysis = service.analyze_system_diagnostics(data)
        return jsonify({"status": "SUCCESS", "analysis": analysis})
    except Exception as e:
        logger.warning(f"[API_AI_SYS] Error: {e}")
        return jsonify({"status": "SUCCESS", "analysis": {"system_summary": "Diagnostics unavailable", "ai_available": False}})

@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory('static', path)


if __name__ == '__main__':
    print("🚀 Starting Unified Live Trading Dashboard...")
    port = int(os.environ.get('PORT', 5000))
    print(f"👉 Open http://127.0.0.1:{port} in your browser")
    is_debug = os.environ.get('FLASK_DEBUG') == '1'
    app.run(host='0.0.0.0', debug=is_debug, port=port, load_dotenv=False)
