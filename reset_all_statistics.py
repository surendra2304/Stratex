import datetime
import json
import os


def reset_all():
    now_iso = datetime.datetime.utcnow().isoformat() + "Z"
    print(f"Starting trade statistics reset at {now_iso}...")

    # 1. Truncate JSONL files
    jsonl_files = [
        "testnet_trade_ledger.jsonl",
        "testnet_trade_events.jsonl",
        "testnet_execution_events.jsonl",
        "testnet_position_history.jsonl",
        "testnet_equity_history.jsonl",
        "testnet_signals_log.jsonl",
        "testnet_opportunity_log.jsonl",
        "testnet_balance_events.jsonl",
        "paper_trade_ledger.jsonl",
        "paper_equity_curve.jsonl",
        "forward_reconciliation.jsonl",
        "forward_signal_log.jsonl",
    ]
    for jf in jsonl_files:
        if os.path.exists(jf):
            with open(jf, "w", encoding="utf-8") as f:
                f.write("")
            print(f"  [CLEARED] {jf}")
        else:
            with open(jf, "w", encoding="utf-8") as f:
                f.write("")
            print(f"  [CREATED EMPTY] {jf}")

    # 2. Reset CSV logs
    csv_header = "timestamp,strategy,symbol,side,price,qty,sl,tp,order_id,status\n"
    with open("trade_log.csv", "w", encoding="utf-8") as f:
        f.write(csv_header)
    print("  [RESET] trade_log.csv with clean header")

    # 3. Reset active / render trades
    for tf in ["active_trades.json", "render_trades.json", "render_trades_audit.json"]:
        with open(tf, "w", encoding="utf-8") as f:
            json.dump([], f, indent=2)
        print(f"  [RESET] {tf} -> []")

    # 4. Reset testnet portfolio
    port_equity = 11290.3896015
    if os.path.exists("testnet_portfolio.json"):
        try:
            with open("testnet_portfolio.json", "r", encoding="utf-8") as f:
                tp = json.load(f)
            port_equity = float(tp.get("equity", port_equity))
            tp["initial_deposit"] = port_equity
            tp["cash"] = port_equity
            tp["equity"] = port_equity
            tp["realized_pnl"] = 0.0
            tp["used_margin"] = 0.0
            tp["fees"] = 0.0
            tp["funding"] = 0.0
            tp["open_positions"] = 0
            tp["positions"] = {}
            tp["max_drawdown"] = 0.0
            tp["service_start_time"] = now_iso
            if "scanner_stats" in tp and isinstance(tp["scanner_stats"], dict):
                sc = tp["scanner_stats"]
                for k in ["TOTAL_SIGNALS", "PROFITABILITY_ACCEPTED", "PROFITABILITY_REJECTED",
                          "RISK_ACCEPTED", "RISK_REJECTED", "EXECUTION_ELIGIBLE", "EXECUTION_REJECTED",
                          "COOLDOWN_REJECTED", "MARKET_DATA_REJECTED", "JIT_REJECTED", "OTHER_REJECTED",
                          "QUALIFIED", "ORDERS_SUBMITTED", "ORDERS_FILLED", "ORDERS_FAILED",
                          "OPEN_POSITIONS", "CLOSED_TRADES", "strategy_evaluations",
                          "buy_predictions", "sell_predictions", "TOTAL_CANDLES",
                          "BUY_SIGNALS", "SELL_SIGNALS", "HOLD_SIGNALS"]:
                    if k in sc:
                        sc[k] = 0
                if "strategy_metrics" in sc and isinstance(sc["strategy_metrics"], dict):
                    for strat in sc["strategy_metrics"]:
                        sc["strategy_metrics"][strat] = {"signals": 0, "qualified": 0, "rejected": 0, "executed": 0}
                if "timeframe_metrics" in sc and isinstance(sc["timeframe_metrics"], dict):
                    for tfm in sc["timeframe_metrics"]:
                        sc["timeframe_metrics"][tfm] = {"signals": 0, "qualified": 0, "rejected": 0, "executed": 0}
            with open("testnet_portfolio.json", "w", encoding="utf-8") as f:
                json.dump(tp, f, indent=2)
            print("  [RESET] testnet_portfolio.json (PnL=0, trades=0, open_positions=0)")
        except Exception as e:
            print(f"  [ERROR] updating testnet_portfolio.json: {e}")

    # 5. Reset baseline and initial deposit
    with open("testnet_baseline.json", "w", encoding="utf-8") as f:
        json.dump({"reset_timestamp": now_iso}, f, indent=2)
    print("  [RESET] testnet_baseline.json")

    with open("testnet_initial_deposit.json", "w", encoding="utf-8") as f:
        json.dump({"initial_deposit": port_equity, "timestamp": now_iso}, f, indent=2)
    print("  [RESET] testnet_initial_deposit.json")

    # 6. Reset render_status files
    for rs_file in ["render_status.json", "render_status_audit.json"]:
        if os.path.exists(rs_file):
            try:
                rs = None
                for enc in ["utf-8", "utf-16", "utf-16le", "cp1252"]:
                    try:
                        with open(rs_file, "r", encoding=enc) as f:
                            rs = json.load(f)
                            break
                    except Exception:
                        continue
                if rs is None:
                    rs = {}
                rs["realized_pnl"] = 0.0
                rs["unrealized_pnl"] = 0.0
                rs["open_positions"] = 0
                rs["used_margin"] = 0.0
                rs["fees"] = 0.0
                rs["funding"] = 0.0
                rs["max_drawdown"] = 0.0
                rs["server_time"] = now_iso
                with open(rs_file, "w", encoding="utf-8") as f:
                    json.dump(rs, f, indent=2)
                print(f"  [RESET] {rs_file}")
            except Exception as e:
                print(f"  [WARN] {rs_file} update error: {e}")

    # 7. Reset heartbeat files
    for hb_file in ["heartbeat.json", "testnet_heartbeat.json"]:
        if os.path.exists(hb_file):
            try:
                with open(hb_file, "r", encoding="utf-8") as f:
                    hb = json.load(f)
                hb["open_positions"] = 0
                hb["current_equity"] = port_equity
                with open(hb_file, "w", encoding="utf-8") as f:
                    json.dump(hb, f, indent=2)
                print(f"  [RESET] {hb_file} (open_positions=0)")
            except Exception as e:
                print(f"  [WARN] {hb_file} update error: {e}")

    print("\nAll trade statistics and ledger history have been successfully reset!")

if __name__ == "__main__":
    reset_all()
