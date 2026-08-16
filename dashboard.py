import os
import csv
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace') if __name__ == '__main__' else sys.stdout
from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS
from data import get_candles as fetch_candles
from config import ACTIVE_STRATEGIES

app = Flask(__name__, static_folder='static')
CORS(app)

LOG_FILE = "trade_log.csv"

@app.route('/')
def serve_index():
    return send_from_directory('static', 'index.html')


@app.route('/api/candles')
def get_candles():
    """Fetches live candles for the chart via the centralized data module."""
    try:
        symbol = request.args.get('symbol', 'BTCUSDT')
        df = fetch_candles(symbol, "15m", 500)
        
        if df.empty:
            return jsonify({"error": "No data returned"}), 500
            
        formatted = []
        for _, row in df.iterrows():
            formatted.append({
                "time": int(row["timestamp"].timestamp()),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"])
            })
        return jsonify(formatted)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def get_engine_health_data():
    """Reads engine heartbeat and verifies live process state."""
    import datetime
    import json
    
    hb_env = os.getenv("TESTNET_HEARTBEAT_FILE")
    if hb_env:
        hb_file = hb_env
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
        
        # Check process existence
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
            except:
                pid_alive = False
                
        is_healthy = worker_alive and age <= 60 and (pid_alive or pid is None)
        engine_status = "ONLINE" if is_healthy else "OFFLINE"
        
        return {
            "engine_status": engine_status,
            "healthy": is_healthy,
            "worker_alive": worker_alive,
            "heartbeat_age_seconds": round(age, 2),
            "pid": pid,
            "pid_alive": pid_alive,
            "binance_connected": hb.get("binance_connected", False),
            "websocket_connected": hb.get("websocket_connected", False),
            "active_strategy": hb.get("strategy", "adx_ema"),
            "strategies": hb.get("strategies", ["adx_ema"]),
            "timeframes": hb.get("timeframes", ["4h"]),
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
    """UptimeRobot and Render platform health check endpoint."""
    import datetime
    from config import TRADING_MODE
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
    """Authoritative trading engine health and telemetry endpoint."""
    return jsonify(get_engine_health_data())

# Legacy /api/backtest removed to focus purely on live Testnet operations.

@app.route('/api/chart_trades')
def get_chart_trades():
    """Parses trade_log.csv to return markers specifically formatted for the chart."""
    if not os.path.exists(LOG_FILE):
        return jsonify([])
        
    trades = []
    try:
        with open(LOG_FILE, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                import datetime
                dt = datetime.datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S")
                timestamp = int(dt.timestamp())
                side = row["side"].upper()
                
                if side == "BUY":
                    trades.append({"time": timestamp, "position": "belowBar", "color": "#26a69a", "shape": "arrowUp"})
                elif side == "SELL":
                    trades.append({"time": timestamp, "position": "aboveBar", "color": "#ef5350", "shape": "arrowDown"})
                elif "WIN" in side:
                    trades.append({"time": timestamp, "position": "aboveBar", "color": "#2196F3", "shape": "circle", "text": f"WIN +$"})
                elif "LOSS" in side:
                    trades.append({"time": timestamp, "position": "belowBar", "color": "#FF9800", "shape": "circle", "text": f"LOSS -$"})
                    
        return jsonify(trades)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/status')
def get_status():
    from config import TRADING_MODE
    import time
    from logger import get_logger
    logger = get_logger("dashboard")
    
    # Defaults
    overall = "OK"
    components = {}
    session_info = {"status": "ACTIVE"}
    alerts = []
    equity = 10000.0
    cash = 10000.0
    realized_pnl = 0.0
    unrealized_pnl = 0.0
    fees = 0.0
    funding = 0.0
    used_margin = 0.0
    open_positions = 0
    mdd = 0.0

    bot_start_time = None
    equity_high = None
    equity_low = None
    equity_change = None

    if TRADING_MODE == "TESTNET":
        # 1. Real Testnet Balance
        from execution import get_exchange_client
        try:
            client = get_exchange_client()
            if not client:
                equity = "DATA UNAVAILABLE"
                cash = "DATA UNAVAILABLE"
            else:
                account = client.get_account()
                usdt = next((item for item in account['balances'] if item['asset'] == 'USDT'), None)
                if usdt:
                    cash = float(usdt['free']) + float(usdt['locked'])
                    equity = cash # Will add unrealized later
                else:
                    equity = "DATA UNAVAILABLE"
                    cash = "DATA UNAVAILABLE"
        except Exception as e:
            logger.error(f"[EXEC] Error fetching balance: {e}")
            equity = "DATA UNAVAILABLE"
            cash = "DATA UNAVAILABLE"

        # 2. Read Testnet Portfolio
        port_file = os.getenv("TESTNET_PORTFOLIO_FILE", "testnet_portfolio.json")
        if os.path.exists(port_file):
            try:
                import json
                with open(port_file, "r") as f:
                    port = json.load(f)
                
                bot_start_time = port.get("service_start_time")
                
                # Compute unrealized PnL across all open positions independently
                for pos in port.get("positions", {}).values():
                    if not isinstance(pos, dict):
                        continue
                    if pos.get('status', 'OPEN') != "OPEN":
                        continue
                    try:
                        sym = pos.get("symbol", "BTCUSDT")
                        entry_price = float(pos.get("entry_price", 0.0))
                        quantity = float(pos.get("quantity", 0.0))
                        direction = pos.get("direction", pos.get("side", "BUY"))
                        
                        # Fetch recent market price for this specific symbol
                        current_price = 0.0
                        try:
                            df = fetch_candles(sym, "1m", 1)
                            if not df.empty:
                                current_price = float(df['close'].iloc[-1])
                        except Exception as pe:
                            logger.warning(f"Failed to fetch live price for {sym}: {pe}")
                            
                        if current_price > 0 and entry_price > 0 and quantity > 0:
                            if direction in ["LONG", "BUY"]:
                                unrealized_pnl += (current_price - entry_price) * quantity
                            else:
                                unrealized_pnl += (entry_price - current_price) * quantity
                    except Exception as pos_err:
                        logger.error(f"Failed calculating unrealized PnL for position {pos}: {pos_err}")
                        continue
                            
                if isinstance(equity, float):
                    equity += unrealized_pnl
                    
                realized_pnl = float(port.get("realized_pnl", 0.0))
                fees = float(port.get("cumulative_fees", port.get("fees", 0.0)))
                open_positions = len([p for p in port.get("positions", {}).values() if isinstance(p, dict) and p.get("status", "OPEN") == "OPEN"])
                mdd = float(port.get("max_drawdown", 0.0)) * 100
                
            except Exception as e:
                logger.error(f"Failed to process Testnet portfolio: {e}")

        # 3. Read Genuine UTC-Day Equity History for Daily High/Low/Change
        hist_file = os.getenv("TESTNET_EQUITY_HISTORY_FILE", "testnet_equity_history.jsonl")
        if os.path.exists(hist_file):
            try:
                import json
                import datetime
                today_utc = datetime.datetime.utcnow().date().isoformat()
                today_pts = []
                with open(hist_file, "r") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            snap = json.loads(line.strip())
                            ts_str = snap.get("timestamp", "")
                            # Verify timestamp belongs to today's UTC date
                            if ts_str.startswith(today_utc):
                                eq_val = float(snap.get("equity", 0.0))
                                if eq_val > 0:
                                    today_pts.append(eq_val)
                        except Exception:
                            pass
                if len(today_pts) >= 2:
                    equity_high = max(today_pts)
                    equity_low = min(today_pts)
                    start_today = today_pts[0]
                    curr_today = today_pts[-1]
                    if start_today > 0:
                        equity_change = ((curr_today - start_today) / start_today) * 100
            except Exception as e:
                logger.error(f"Failed reading equity history: {e}")
                
    elif TRADING_MODE == "PAPER":
        try:
            from paper_engine.heartbeat import HeartbeatState
            hb = HeartbeatState()
            components = hb.components
            overall = hb.get_overall_health().value
        except: pass
            
        try:
            from paper_engine.session import SessionState
            session = SessionState()
            session_info = {"id": session.session_id, "status": session.status, "start": session.start_time}
            bot_start_time = session.start_time
        except: pass
            
        try:
            from paper_engine.alerts import AlertManager
            alerts = [v for k, v in AlertManager().active_alerts.items()]
        except: pass
            
        if os.path.exists("paper_portfolio.json"):
            try:
                import json
                with open("paper_portfolio.json", "r") as f:
                    port = json.load(f)
                    
                cash = port.get("cash", 10000.0)
                equity = cash
                realized_pnl = port.get("realized_pnl", 0.0)
                fees = port.get("cumulative_fees", 0.0)
                
                from paper_engine.portfolio import PaperPortfolio
                temp_port = PaperPortfolio("paper_portfolio.json")
                mdd = temp_port.get_max_drawdown() * 100
            except: pass

    import datetime
    engine_data = get_engine_health_data()
    components["engine"] = "OK" if engine_data["healthy"] else "ERROR"
    components["binance"] = "OK" if engine_data.get("binance_connected") else "ERROR"
    components["data"] = "OK" if engine_data.get("websocket_connected") else "ERROR"
    components["execution"] = "OK" if engine_data["healthy"] else "ERROR"
    components["strategy"] = "OK" if engine_data["healthy"] else "ERROR"
    if not engine_data["healthy"]:
        overall = "DEGRADED"

    return jsonify({
        "mode": TRADING_MODE,
        "overall_health": overall,
        "engine_status": engine_data["engine_status"],
        "engine_healthy": engine_data["healthy"],
        "engine_data": engine_data,
        "components": components,
        "session": session_info,
        "alerts": alerts,
        "equity": equity,
        "cash": cash,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "fees": fees,
        "funding": funding,
        "used_margin": used_margin,
        "open_positions": open_positions,
        "max_drawdown": mdd,
        "server_time": datetime.datetime.utcnow().isoformat() + "Z",
        "bot_start_time": bot_start_time,
        "equity_high": equity_high,
        "equity_low": equity_low,
        "equity_change": equity_change
    })

@app.route('/api/trades')
def get_trades():
    """Parses logs or paper portfolio to return positions."""
    from config import TRADING_MODE
    from logger import get_logger
    logger = get_logger("dashboard")
    
    import json
    net_pnl = 0.0
    wins = 0
    losses = 0
    gross_profit = 0.0
    gross_loss = 0.0
    positions = []
    
    # 1. Parse closed trades from ledger
    debug_log = []
    ledger_file = os.getenv("TESTNET_LEDGER_FILE", "testnet_trade_ledger.jsonl") if TRADING_MODE == "TESTNET" else "paper_trade_ledger.jsonl"
    seen_exit_ids = set()
    
    debug_log.append(f"Mode is {TRADING_MODE}, looking for {ledger_file}")
    if os.path.exists(ledger_file):
        debug_log.append("File exists")
        with open(ledger_file, "r") as f:
            for line in f:
                try:
                    trade = json.loads(line)
                    source = trade.get("source", "")
                    strategy = trade.get("strategy", "")
                    entry_oid = trade.get("entry_order_id")
                    exit_oid = trade.get("exit_order_id")
                    
                    # Provenance classification & filtering
                    if TRADING_MODE == "TESTNET":
                        # Exclude synthetic/test trades strictly
                        if source == "TEST" or strategy == "TEST":
                            continue
                        if not (entry_oid or exit_oid):
                            continue
                        if source not in ["BINANCE_EXECUTION", "RECOVERY_FROM_BINANCE"]:
                            # Infer if unclassified legacy
                            if "RECOVERED" in str(trade.get("signal_id", "")) or "RECOVERED" in str(strategy):
                                source = "RECOVERY_FROM_BINANCE"
                            else:
                                continue
                                
                    # Prevent duplicate accounting of identical completed trades
                    exit_id = str(exit_oid) if exit_oid else (str(trade.get("exit_client_id")) if trade.get("exit_client_id") else None)
                    if exit_id:
                        if exit_id in seen_exit_ids:
                            continue
                        seen_exit_ids.add(exit_id)

                    pnl = trade.get("pnl", trade.get("net_pnl", 0.0))
                    
                    if pnl > 0:
                        wins += 1
                        gross_profit += pnl
                    elif pnl < 0:
                        losses += 1
                        gross_loss += abs(pnl)
                        
                    positions.append({
                        "timestamp": trade.get("exit_timestamp", trade.get("timestamp", trade.get("entry_time", 0))),
                        "symbol": trade.get("symbol", ""),
                        "action": trade.get("action", trade.get("exit_reason", trade.get("direction", ""))).replace("CLOSED_", ""),
                        "entry_price": trade.get("entry_price", 0.0),
                        "exit_price": trade.get("exit_price", 0.0),
                        "quantity": trade.get("exit_executed_quantity", trade.get("quantity", 0.0)),
                        "status": "CLOSED",
                        "pnl": pnl,
                        "fees": trade.get("total_fees", trade.get("fees", 0.0)),
                        "order_id": trade.get("signal_id", trade.get("entry_client_id", trade.get("order_id", "CLOSED-ORDER"))),
                        "source": source,
                        "matched": True
                    })
                except Exception as e:
                    logger.error(f"Failed parsing ledger line: {e}")
                        
    with open("dashboard_debug.txt", "w") as f:
        f.write("\n".join(debug_log))
    
    # 2. Add open positions from portfolio
    port_file = os.getenv("TESTNET_PORTFOLIO_FILE", "testnet_portfolio.json") if TRADING_MODE == "TESTNET" else "paper_portfolio.json"
    if os.path.exists(port_file):
        try:
            with open(port_file, "r") as f:
                port = json.load(f)
            
            # testnet_portfolio uses a dict for positions: {"BTCUSDT": {...}}
            # paper used a list. Handle both.
            pos_data = port.get("positions", {})
            if isinstance(pos_data, dict):
                pos_list = pos_data.values()
            else:
                pos_list = pos_data
                
            for p in pos_list:
                # In testnet, if it's in this dict, it's OPEN
                status = p.get("status", "OPEN")
                if status == "OPEN":
                    positions.append({
                        "timestamp": p.get("timestamp", p.get("open_time", 0)),
                        "symbol": p.get("symbol", ""),
                        "action": p.get("side", p.get("direction", "")),
                        "entry_price": p.get("entry_price", 0.0),
                        "quantity": p.get("quantity", 0.0),
                        "status": "OPEN",
                        "pnl": p.get("unrealized_pnl", 0.0),
                        "order_id": p.get("entry_client_id", "LIVE-ORDER"),
                        "sl": p.get("sl", 0.0),
                        "tp": p.get("tp", 0.0)
                    })
        except Exception as e:
            logger.error(f"Failed parsing portfolio for trades: {e}")
            
    positions.sort(key=lambda x: x["timestamp"], reverse=True)
    total_closed = wins + losses
    net_pnl = gross_profit - gross_loss
    win_rate = (wins / total_closed * 100) if total_closed > 0 else "N/A"
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else ("Infinity" if gross_profit > 0 else ("N/A" if total_closed == 0 else 0))
    
    return jsonify({
        "net_pnl": net_pnl, 
        "win_rate": win_rate,
        "total_trades": total_closed,
        "profit_factor": profit_factor,
        "positions": positions
    })

@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory('static', path)

def verify_funnel(s, open_count, closed_count):
    errors = []
    
    # 1. TOTAL_SIGNALS
    sum_rejections = (s.get("PROFITABILITY_REJECTED", 0) + s.get("RISK_REJECTED", 0) + 
                      s.get("COOLDOWN_REJECTED", 0) + s.get("JIT_REJECTED", 0) + 
                      s.get("OTHER_REJECTED", 0) + s.get("QUALIFIED", 0))
    if s.get("TOTAL_SIGNALS", 0) != sum_rejections:
        errors.append(f"TOTAL_SIGNALS {s.get('TOTAL_SIGNALS')} != sum {sum_rejections}")
        
    # 2. QUALIFIED
    sum_qual = s.get("ORDERS_SUBMITTED", 0) + s.get("EXECUTION_REJECTED", 0)
    if s.get("QUALIFIED", 0) != sum_qual:
        errors.append(f"QUALIFIED {s.get('QUALIFIED')} != sum {sum_qual}")
        
    # 3. ORDERS_SUBMITTED
    sum_sub = s.get("ORDERS_FILLED", 0) + s.get("ORDERS_FAILED", 0)
    if s.get("ORDERS_SUBMITTED", 0) != sum_sub:
        errors.append(f"ORDERS_SUBMITTED {s.get('ORDERS_SUBMITTED')} != sum {sum_sub}")
        
    # 4. ORDERS_FILLED
    sum_fill = open_count + closed_count
    if s.get("ORDERS_FILLED", 0) != sum_fill:
        errors.append(f"ORDERS_FILLED {s.get('ORDERS_FILLED')} != sum {sum_fill} (Open: {open_count}, Closed: {closed_count})")
        
    return errors

@app.route('/api/scanner')
def get_scanner():
    from config import TRADING_MODE
    if TRADING_MODE != "TESTNET":
        from flask import jsonify
        return jsonify({})
        
    import json
    from flask import jsonify
    stats = {
        "symbols_scanned": 0,
        "TOTAL_SIGNALS": 0,
        "PROFITABILITY_REJECTED": 0,
        "RISK_REJECTED": 0,
        "COOLDOWN_REJECTED": 0,
        "JIT_REJECTED": 0,
        "OTHER_REJECTED": 0,
        "QUALIFIED": 0,
        "ORDERS_SUBMITTED": 0,
        "EXECUTION_REJECTED": 0,
        "ORDERS_FILLED": 0,
        "ORDERS_FAILED": 0,
        "top_opportunities": [],
        "DEBUG_CWD": os.getcwd(),
        "DEBUG_PORT_EXISTS": os.path.exists("testnet_portfolio.json"),
        "DEBUG_PORT_SIZE": os.path.getsize("testnet_portfolio.json") if os.path.exists("testnet_portfolio.json") else 0
    }
    
    if os.path.exists("testnet_portfolio.json"):
        try:
            with open("testnet_portfolio.json", "r") as f:
                port = json.load(f)
                stats.update(port.get("scanner_stats", {}))
        except:
            pass
            
    if os.path.exists("testnet_opportunity_log.jsonl"):
        try:
            import datetime
            opps = []
            now = datetime.datetime.utcnow()
            with open("testnet_opportunity_log.jsonl", "r") as f:
                for line in f:
                    if not line.strip(): continue
                    opp = json.loads(line)
                    ts_str = opp.get("timestamp", "")
                    try:
                        # Try parsing ISO 8601
                        # Example: 2026-08-15T13:28:16.650863Z
                        ts = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00")).replace(tzinfo=None)
                        if (now - ts).total_seconds() < 300: # Last 5 minutes only
                            opp["current_price"] = opp.get("current_price", 0.0) # Not present in old logs, but requested
                            opps.append(opp)
                    except:
                        pass
            
            # Sort by timestamp descending
            stats["top_opportunities"] = sorted(opps, key=lambda x: x.get("timestamp", ""), reverse=True)[:5]
        except Exception as e:
            from logger import get_logger
            get_logger("dashboard").error(f"Failed to read opportunity log: {e}")
            
    # Mathematically Verify Funnel
    closed_positions_count = 0
    if os.path.exists("testnet_trade_ledger.jsonl"):
        with open("testnet_trade_ledger.jsonl", "r") as f:
            closed_positions_count = sum(1 for line in f if line.strip())
            
    open_positions_count = 0
    if os.path.exists("testnet_portfolio.json"):
        with open("testnet_portfolio.json", "r") as f:
            port_temp = json.load(f)
            open_positions_count = len([p for p in port_temp.get("positions", {}).values() if p.get("status") == "OPEN"])

    funnel_errors = verify_funnel(stats, open_positions_count, closed_positions_count)
    if funnel_errors:
        stats["FUNNEL_ERRORS"] = funnel_errors
        with open("dashboard_debug.txt", "a") as f:
            f.write(f"{datetime.datetime.utcnow().isoformat()}Z - FUNNEL DISCREPANCY: {funnel_errors}\n")
    else:
        stats["FUNNEL_ERRORS"] = ["Verified OK"]
            
    return jsonify(stats)

if __name__ == '__main__':
    print("🚀 Starting Live Dashboard...")
    port = int(os.environ.get('PORT', 5000))
    print(f"👉 Open http://127.0.0.1:{port} in your browser")
    is_debug = os.environ.get('FLASK_DEBUG') == '1'
    app.run(host='0.0.0.0', debug=is_debug, port=port, load_dotenv=False)
