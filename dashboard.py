import os
import threading
import time
import csv
import sys
import io
import json
import datetime
from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS
from data import get_candles as fetch_candles
from config import ACTIVE_STRATEGIES, TRADING_MODE
from logger import get_logger

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace') if __name__ == '__main__' else sys.stdout
logger = get_logger("dashboard")

app = Flask(__name__, static_folder='static')
CORS(app)

LOG_FILE = "trade_log.csv"

@app.route('/')
def serve_index():
    return send_from_directory('static', 'index.html')

@app.route('/api/candles')
def get_candles():
    """Fetches live candles for chart."""
    try:
        symbol = request.args.get('symbol', 'BTCUSDT')
        tf = request.args.get('tf', '15m')
        limit = int(request.args.get('limit', 300))
        df = fetch_candles(symbol, tf, limit)
        if df.empty:
            return jsonify({"error": "No data returned"}), 500
        formatted = []
        for _, row in df.iterrows():
            formatted.append({
                "time": int(row["timestamp"].timestamp()),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume", 0.0))
            })
        return jsonify(formatted)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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
            "reason": f"READ_ERROR: {str(e)}",
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
        }

@app.route('/health')
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
            
        from execution import get_exchange_client
        from data_client import MarketDataClient
        
        usdt_free = 0.0
        usdt_locked = 0.0
        holdings = []
        total_crypto_value = 0.0
        active_trade_holdings_value = 0.0
        
        # Active traded coins by our bot (to distinguish from default testnet faucet assets)
        bot_traded_assets = {"PORTAL", "LINK", "HEMI", "TRX", "SPCXB", "PAXG", "BNB", "DOGE", "SOL", "SOPH", "BTC", "ETH"}
        
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
                    is_bot_trade = (asset in bot_traded_assets) or (locked > 0)
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
            if is_bot_trade and asset not in ['USDC', 'TUSD', 'FDUSD', 'WBTC', 'BTC', 'ETH']:
                active_trade_holdings_value += usd_val
        except Exception as e:
            logger.error(f"[DASHBOARD] Error calculating live holdings: {e}")
            
        holdings.sort(key=lambda x: x['usd_value'], reverse=True)
        res_dict = {
            "usdt_free": usdt_free,
            "usdt_locked": usdt_locked,
            "usdt_total_cash": usdt_free + usdt_locked,
            "total_crypto_value": total_crypto_value,
            "active_trade_holdings_value": active_trade_holdings_value,
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
    from data_client import MarketDataClient
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
            
            trades_data = _get_trades_data()
            if trades_data.get("positions"):
                realized_pnl = float(trades_data.get("net_pnl", 0.0))
                fees = float(sum(t.get("fees", 0.0) for t in trades_data.get("positions", [])))
            else:
                realized_pnl = float(port.get("realized_pnl", 0.0))
                fees = float(port.get("fees", 0.0))
                
            open_positions = sum(1 for p in port.get("positions", {}).values() if isinstance(p, dict) and p.get("status") == "OPEN")
            mdd = float(port.get("max_drawdown", 0.0)) * 100
        except Exception as e:
            logger.error(f"Failed to process testnet portfolio: {e}")

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

    # Total Valuation: Liquid USDT Cash + Capital In Active Trades + Realized + Unrealized
    # If in testnet, base is actual cash + active crypto assets
    if usdt_cash > 0:
        total_equity = usdt_cash + crypto_trade_val + unrealized_pnl
    else:
        total_equity = 10000.0 + realized_pnl + unrealized_pnl

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
        risk_used = ((used_margin + crypto_trade_val) / total_equity) * 100
    available_risk = max(0, (config.MAX_TESTNET_EXPOSURE * 100) - risk_used)

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
        "used_margin": round(used_margin, 2),
        "risk_used": round(risk_used, 2),
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
        "max_drawdown": mdd,
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
                    
                    source = trade.get("source", "")
                    strategy = trade.get("strategy", "")
                    if source == "TEST" or strategy == "TEST":
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
def api_equity():
    """Returns historical equity curve points for chart."""
    eq_file = os.getenv("TESTNET_EQUITY_HISTORY_FILE", "testnet_equity_history.jsonl")
    points = []
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
                            points.append({
                                "time": int(dt.timestamp() * 1000),
                                "equity": float(snap.get("equity", 0.0))
                            })
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"Error reading equity history: {e}")
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
        "top_opportunities": [],
        "strategy_metrics": {},
        "timeframe_metrics": {},
        "market_data": {}
    }
    
    if os.path.exists("testnet_portfolio.json"):
        try:
            with open("testnet_portfolio.json", "r") as f:
                port = json.load(f)
                stats.update(port.get("scanner_stats", {}))
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
    if os.path.exists("testnet_opportunity_log.jsonl"):
        try:
            opps = []
            now = datetime.datetime.utcnow()
            with open("testnet_opportunity_log.jsonl", "r") as f:
                for line in f:
                    if not line.strip(): continue
                    try:
                        opp = json.loads(line)
                        ts_str = opp.get("timestamp", "")
                        ts = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00")).replace(tzinfo=None)
                        if (now - ts).total_seconds() < 900:
                            opps.append(opp)
                    except Exception:
                        pass
            stats["top_opportunities"] = sorted(opps, key=lambda x: x.get("timestamp", ""), reverse=True)[:10]
        except Exception as e:
            logger.error(f"Error reading opportunity log: {e}")
            
    return jsonify(stats)

@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory('static', path)

if __name__ == '__main__':
    print("🚀 Starting Unified Live Trading Dashboard...")
    port = int(os.environ.get('PORT', 5000))
    print(f"👉 Open http://127.0.0.1:{port} in your browser")
    is_debug = os.environ.get('FLASK_DEBUG') == '1'
    app.run(host='0.0.0.0', debug=is_debug, port=port, load_dotenv=False)
