import datetime
import json
import logging
import os
import threading
import time

logger = logging.getLogger("telemetry_manager")

def validate_signal_event(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Signal event must be a dict")
    validated = {
        "timestamp": data.get("timestamp", datetime.datetime.utcnow().isoformat() + "Z"),
        "signal_id": str(data.get("signal_id", f"SIG_{int(time.time()*1000)}")),
        "symbol": str(data.get("symbol", "")),
        "timeframe": str(data.get("timeframe", "5m")),
        "strategy": str(data.get("strategy", "")),
        "decision": str(data.get("decision", "HOLD")),
        "entry": float(data.get("entry", data.get("entry_price", 0.0))),
        "stop": float(data.get("stop", data.get("sl", 0.0))),
        "target": float(data.get("target", data.get("tp", 0.0))),
        "confidence": float(data.get("confidence", 0.0)),
        "expected_gross": float(data.get("expected_gross", 0.0)),
        "expected_net": float(data.get("expected_net", 0.0)),
        "profitability_decision": str(data.get("profitability_decision", "PENDING")),
        "profitability_reason": str(data.get("profitability_reason", "")),
        "risk_decision": str(data.get("risk_decision", "PENDING")),
        "risk_reason": str(data.get("risk_reason", "")),
        "final_decision": str(data.get("final_decision", "ACCEPTED" if data.get("decision") in ["BUY", "SELL"] else "REJECTED"))
    }
    return validated

def validate_execution_event(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Execution event must be a dict")
    validated = {
        "timestamp": data.get("timestamp", datetime.datetime.utcnow().isoformat() + "Z"),
        "event_type": str(data.get("event_type", "execution_attempt")),
        "symbol": str(data.get("symbol", "")),
        "trade_id": str(data.get("trade_id", "")),
        "strategy": str(data.get("strategy", "")),
        "timeframe": str(data.get("timeframe", "5m")),
        "order_id": str(data.get("order_id", "")),
        "side": str(data.get("side", "BUY")),
        "quantity": float(data.get("quantity", 0.0)),
        "price": float(data.get("price", 0.0)),
        "status": str(data.get("status", "SUCCESS")),
        "error_code": str(data.get("error_code", "")),
        "error_message": str(data.get("error_message", ""))
    }
    return validated

def validate_trade_event(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Trade event must be a dict")
    validated = {
        "trade_id": str(data.get("trade_id", f"TRD_{int(time.time()*1000)}")),
        "exchange": str(data.get("exchange", "BINANCE_TESTNET")),
        "symbol": str(data.get("symbol", "")),
        "strategy": str(data.get("strategy", "")),
        "timeframe": str(data.get("timeframe", "5m")),
        "side": str(data.get("side", "BUY")),
        "status": str(data.get("status", "SUBMITTED")),
        "signal_timestamp": data.get("signal_timestamp", datetime.datetime.utcnow().isoformat() + "Z"),
        "entry_signal_timestamp": data.get("entry_signal_timestamp", data.get("signal_timestamp", datetime.datetime.utcnow().isoformat() + "Z")),
        "order_submit_timestamp": data.get("order_submit_timestamp", datetime.datetime.utcnow().isoformat() + "Z"),
        "fill_timestamp": data.get("fill_timestamp", ""),
        "exit_signal_timestamp": data.get("exit_signal_timestamp", ""),
        "exit_order_timestamp": data.get("exit_order_timestamp", ""),
        "close_timestamp": data.get("close_timestamp", ""),
        "entry_order_id": str(data.get("entry_order_id", "")),
        "exit_order_id": str(data.get("exit_order_id", "")),
        "oco_order_id": str(data.get("oco_order_id", "")),
        "tp_order_id": str(data.get("tp_order_id", "")),
        "sl_order_id": str(data.get("sl_order_id", "")),
        "entry_price": float(data.get("entry_price", 0.0)),
        "average_entry_price": float(data.get("average_entry_price", data.get("entry_price", 0.0))),
        "exit_price": float(data.get("exit_price", 0.0)),
        "quantity": float(data.get("quantity", 0.0)),
        "notional": float(data.get("notional", float(data.get("entry_price", 0.0)) * float(data.get("quantity", 0.0)))),
        "stop_loss": float(data.get("stop_loss", data.get("sl_price", 0.0))),
        "take_profit": float(data.get("take_profit", data.get("tp_price", 0.0))),
        "risk_amount": float(data.get("risk_amount", 0.0)),
        "risk_percent": float(data.get("risk_percent", 0.0)),
        "expected_gross_return": float(data.get("expected_gross_return", 0.0)),
        "expected_net_return": float(data.get("expected_net_return", 0.0)),
        "profitability_decision": str(data.get("profitability_decision", "ACCEPTED")),
        "profitability_reason": str(data.get("profitability_reason", "")),
        "risk_decision": str(data.get("risk_decision", "ACCEPTED")),
        "risk_reason": str(data.get("risk_reason", "")),
        "entry_fee": float(data.get("entry_fee", 0.0)),
        "exit_fee": float(data.get("exit_fee", 0.0)),
        "total_fees": float(data.get("total_fees", float(data.get("entry_fee", 0.0)) + float(data.get("exit_fee", 0.0)))),
        "gross_pnl": float(data.get("gross_pnl", 0.0)),
        "net_pnl": float(data.get("net_pnl", 0.0)),
        "equity_before_entry": float(data.get("equity_before_entry", 0.0)),
        "equity_after_entry": float(data.get("equity_after_entry", 0.0)),
        "equity_before_exit": float(data.get("equity_before_exit", 0.0)),
        "equity_after_exit": float(data.get("equity_after_exit", 0.0)),
        "cash_before_entry": float(data.get("cash_before_entry", 0.0)),
        "cash_after_entry": float(data.get("cash_after_entry", 0.0)),
        "cash_before_exit": float(data.get("cash_before_exit", 0.0)),
        "cash_after_exit": float(data.get("cash_after_exit", 0.0)),
        "asset_quantity_before": float(data.get("asset_quantity_before", 0.0)),
        "asset_quantity_after": float(data.get("asset_quantity_after", 0.0)),
        "duration_seconds": float(data.get("duration_seconds", 0.0)),
        "close_reason": str(data.get("close_reason", "")),
        "source": str(data.get("source", "BINANCE_EXECUTION")),
        "provenance": str(data.get("provenance", "PRODUCTION_TESTNET"))
    }
    return validated

def validate_position_event(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Position event must be a dict")
    validated = {
        "position_id": str(data.get("position_id", data.get("trade_id", f"POS_{data.get('symbol', 'UNK')}"))),
        "trade_id": str(data.get("trade_id", "")),
        "symbol": str(data.get("symbol", "")),
        "strategy": str(data.get("strategy", "")),
        "timeframe": str(data.get("timeframe", "5m")),
        "side": str(data.get("side", "BUY")),
        "entry_timestamp": data.get("entry_timestamp", datetime.datetime.utcnow().isoformat() + "Z"),
        "entry_price": float(data.get("entry_price", 0.0)),
        "quantity": float(data.get("quantity", 0.0)),
        "current_price": float(data.get("current_price", data.get("entry_price", 0.0))),
        "current_unrealized_pnl": float(data.get("current_unrealized_pnl", 0.0)),
        "stop_loss": float(data.get("stop_loss", data.get("sl_price", 0.0))),
        "take_profit": float(data.get("take_profit", data.get("tp_price", 0.0))),
        "exit_timestamp": str(data.get("exit_timestamp", "")),
        "exit_price": float(data.get("exit_price", 0.0)),
        "realized_pnl": float(data.get("realized_pnl", 0.0)),
        "status": str(data.get("status", "OPEN"))
    }
    return validated

def validate_balance_event(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Balance event must be a dict")
    validated = {
        "timestamp": data.get("timestamp", datetime.datetime.utcnow().isoformat() + "Z"),
        "event_type": str(data.get("event_type", "OTHER")),
        "reason": str(data.get("reason", "")),
        "balance_before": float(data.get("balance_before", 0.0)),
        "balance_after": float(data.get("balance_after", 0.0)),
        "delta": float(data.get("delta", float(data.get("balance_after", 0.0)) - float(data.get("balance_before", 0.0)))),
        "realized_pnl_delta": float(data.get("realized_pnl_delta", 0.0)),
        "unrealized_pnl_delta": float(data.get("unrealized_pnl_delta", 0.0)),
        "fees_delta": float(data.get("fees_delta", 0.0)),
        "trade_id": str(data.get("trade_id", "")),
        "symbol": str(data.get("symbol", "")),
        "strategy": str(data.get("strategy", "")),
        "timeframe": str(data.get("timeframe", ""))
    }
    return validated

def validate_equity_snapshot(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Equity snapshot must be a dict")
    cash = float(data.get("cash_usdt", data.get("cash", 0.0)))
    crypto = float(data.get("asset_market_value", data.get("crypto_holdings_value", 0.0)))
    eq = float(data.get("total_equity", data.get("equity", cash + crypto)))
    validated = {
        "timestamp": data.get("timestamp", datetime.datetime.utcnow().isoformat() + "Z"),
        "cash_usdt": cash,
        "asset_market_value": crypto,
        "total_equity": eq,
        "realized_pnl": float(data.get("realized_pnl", 0.0)),
        "unrealized_pnl": float(data.get("unrealized_pnl", 0.0)),
        "fees": float(data.get("fees", 0.0)),
        "drawdown": float(data.get("drawdown", 0.0)),
        "open_position_count": int(data.get("open_position_count", data.get("open_positions", 0))),
        "trigger_event": str(data.get("trigger_event", "PERIODIC_INTERVAL"))
    }
    return validated

class TelemetryManager:
    """
    Thread-safe master telemetry and audit logging manager for the Binance Testnet trading bot.
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        base_dir = kwargs.get("base_dir")
        if base_dir:
            # If a custom base_dir is requested (e.g. unit tests), create a new instance
            instance = super().__new__(cls)
            instance._initialized = False
            return instance

        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, base_dir: str | None = None):
        if getattr(self, "_initialized", False) and base_dir is None:
            return
        with self._lock:
            if getattr(self, "_initialized", False) and base_dir is None:
                return
            self._data_lock = threading.RLock()
            self.base_dir = base_dir or ""
            
            # File paths
            if self.base_dir:
                self.trade_events_file = os.path.join(self.base_dir, "testnet_trade_events.jsonl")
                self.equity_history_file = os.path.join(self.base_dir, "testnet_equity_history.jsonl")
                self.balance_events_file = os.path.join(self.base_dir, "testnet_balance_events.jsonl")
                self.signals_log_file = os.path.join(self.base_dir, "testnet_signals_log.jsonl")
                self.execution_events_file = os.path.join(self.base_dir, "testnet_execution_events.jsonl")
                self.position_history_file = os.path.join(self.base_dir, "testnet_position_history.jsonl")
            else:
                self.trade_events_file = os.getenv("TESTNET_TRADE_EVENTS_FILE", "testnet_trade_events.jsonl")
                self.equity_history_file = os.getenv("TESTNET_EQUITY_HISTORY_FILE", "testnet_equity_history.jsonl")
                self.balance_events_file = os.getenv("TESTNET_BALANCE_EVENTS_FILE", "testnet_balance_events.jsonl")
                self.signals_log_file = os.getenv("TESTNET_SIGNALS_LOG_FILE", "testnet_signals_log.jsonl")
                self.execution_events_file = os.getenv("TESTNET_EXECUTION_EVENTS_FILE", "testnet_execution_events.jsonl")
                self.position_history_file = os.getenv("TESTNET_POSITION_HISTORY_FILE", "testnet_position_history.jsonl")
            
            # In-memory indices for rapid query response
            self._trade_events: dict[str, dict] = {}
            self._recent_snapshots: list[dict] = []
            self._recent_balance_events: list[dict] = []
            self._recent_signals: list[dict] = []
            self._recent_execution_events: list[dict] = []
            self._positions_history: dict[str, dict] = {}
            
            self._load_persisted_state()
            self._initialized = True
            logger.info(f"[TELEMETRY] TelemetryManager initialized successfully (base_dir='{self.base_dir}').")

    def _now_iso(self) -> str:
        return datetime.datetime.utcnow().isoformat() + "Z"

    def _now_ts(self) -> float:
        return datetime.datetime.utcnow().timestamp()

    def _append_jsonl(self, filepath: str, record: dict):
        try:
            line = json.dumps(record) + "\n"
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception as e:
            logger.error(f"[TELEMETRY] Error writing to {filepath}: {e}")

    def _load_persisted_state(self):
        """Loads historical records into in-memory caches."""
        if os.path.exists(self.trade_events_file):
            try:
                with open(self.trade_events_file, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            rec = json.loads(line)
                            tid = rec.get("trade_id")
                            if tid:
                                self._trade_events[tid] = rec
            except Exception as e:
                logger.warning(f"[TELEMETRY] Error loading trade events: {e}")

        if os.path.exists(self.position_history_file):
            try:
                with open(self.position_history_file, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            rec = json.loads(line)
                            pid = rec.get("position_id") or rec.get("trade_id")
                            if pid:
                                self._positions_history[pid] = rec
            except Exception as e:
                logger.warning(f"[TELEMETRY] Error loading position history: {e}")

    # =========================================================================
    # 1. TRADE EVENT RECORDS (Canonical 40+ Field Model)
    # =========================================================================
    def record_trade_event(self, event_data: dict) -> dict:
        """
        Creates or updates a canonical trade event record.
        Maintains full lifecycle timestamps, pre/post equity, fees, and metrics.
        """
        with self._data_lock:
            trade_id = event_data.get("trade_id")
            if not trade_id:
                trade_id = f"TRD_{int(self._now_ts() * 1000)}_{event_data.get('symbol', 'UNKNOWN')}"
                event_data["trade_id"] = trade_id

            existing = self._trade_events.get(trade_id, {})
            # Merge fields cleanly
            merged = dict(existing)
            merged.update(event_data)

            # Ensure all canonical fields are present with sensible defaults
            canonical = {
                "trade_id": merged.get("trade_id", trade_id),
                "exchange": merged.get("exchange", "BINANCE_TESTNET"),
                "symbol": merged.get("symbol", ""),
                "strategy": merged.get("strategy", ""),
                "timeframe": merged.get("timeframe", "5m"),
                "side": merged.get("side", "BUY"),
                "status": merged.get("status", "SUBMITTED"), # SUBMITTED, OPEN, FILLED, CLOSED, CANCELLED, REJECTED
                
                # Timestamps
                "signal_timestamp": merged.get("signal_timestamp", self._now_iso()),
                "entry_signal_timestamp": merged.get("entry_signal_timestamp", merged.get("signal_timestamp", self._now_iso())),
                "order_submit_timestamp": merged.get("order_submit_timestamp", self._now_iso()),
                "fill_timestamp": merged.get("fill_timestamp", ""),
                "exit_signal_timestamp": merged.get("exit_signal_timestamp", ""),
                "exit_order_timestamp": merged.get("exit_order_timestamp", ""),
                "close_timestamp": merged.get("close_timestamp", ""),
                
                # Order IDs
                "entry_order_id": merged.get("entry_order_id", ""),
                "exit_order_id": merged.get("exit_order_id", ""),
                "oco_order_id": merged.get("oco_order_id", ""),
                "tp_order_id": merged.get("tp_order_id", ""),
                "sl_order_id": merged.get("sl_order_id", ""),
                
                # Pricing & Execution
                "entry_price": float(merged.get("entry_price", 0.0)),
                "average_entry_price": float(merged.get("average_entry_price", merged.get("entry_price", 0.0))),
                "exit_price": float(merged.get("exit_price", 0.0)),
                "quantity": float(merged.get("quantity", 0.0)),
                "notional": float(merged.get("notional", float(merged.get("entry_price", 0.0)) * float(merged.get("quantity", 0.0)))),
                
                # Protection
                "stop_loss": float(merged.get("stop_loss", merged.get("sl_price", 0.0))),
                "take_profit": float(merged.get("take_profit", merged.get("tp_price", 0.0))),
                
                # Risk & Profitability Rationale
                "risk_amount": float(merged.get("risk_amount", 0.0)),
                "risk_percent": float(merged.get("risk_percent", 0.0)),
                "expected_gross_return": float(merged.get("expected_gross_return", 0.0)),
                "expected_net_return": float(merged.get("expected_net_return", 0.0)),
                "profitability_decision": merged.get("profitability_decision", "ACCEPTED"),
                "profitability_reason": merged.get("profitability_reason", ""),
                "risk_decision": merged.get("risk_decision", "ACCEPTED"),
                "risk_reason": merged.get("risk_reason", ""),
                
                # Fees & PnL
                "entry_fee": float(merged.get("entry_fee", 0.0)),
                "exit_fee": float(merged.get("exit_fee", 0.0)),
                "total_fees": float(merged.get("total_fees", float(merged.get("entry_fee", 0.0)) + float(merged.get("exit_fee", 0.0)))),
                "gross_pnl": float(merged.get("gross_pnl", 0.0)),
                "net_pnl": float(merged.get("net_pnl", 0.0)),
                
                # Account & Cash State Transitions
                "equity_before_entry": float(merged.get("equity_before_entry", 0.0)),
                "equity_after_entry": float(merged.get("equity_after_entry", 0.0)),
                "equity_before_exit": float(merged.get("equity_before_exit", 0.0)),
                "equity_after_exit": float(merged.get("equity_after_exit", 0.0)),
                "cash_before_entry": float(merged.get("cash_before_entry", 0.0)),
                "cash_after_entry": float(merged.get("cash_after_entry", 0.0)),
                "cash_before_exit": float(merged.get("cash_before_exit", 0.0)),
                "cash_after_exit": float(merged.get("cash_after_exit", 0.0)),
                "asset_quantity_before": float(merged.get("asset_quantity_before", 0.0)),
                "asset_quantity_after": float(merged.get("asset_quantity_after", 0.0)),
                
                # Metadata
                "duration_seconds": float(merged.get("duration_seconds", 0.0)),
                "close_reason": merged.get("close_reason", ""),
                "source": merged.get("source", "BINANCE_EXECUTION"),
                "provenance": merged.get("provenance", "PRODUCTION_TESTNET")
            }

            # Calculate duration if closed
            if canonical["status"] == "CLOSED" and canonical["fill_timestamp"] and canonical["close_timestamp"]:
                try:
                    t1 = datetime.datetime.fromisoformat(canonical["fill_timestamp"].replace("Z", "+00:00")).timestamp()
                    t2 = datetime.datetime.fromisoformat(canonical["close_timestamp"].replace("Z", "+00:00")).timestamp()
                    canonical["duration_seconds"] = max(0.0, t2 - t1)
                except Exception:
                    pass

            self._trade_events[trade_id] = canonical
            self._append_jsonl(self.trade_events_file, canonical)
            return canonical

    def get_trade_events(self, symbol: str | None = None, status: str | None = None, limit: int = 100) -> list[dict]:
        with self._data_lock:
            events = list(self._trade_events.values())
            # Filter out synthetic test records
            events = [e for e in events if e.get("source") != "TEST" and e.get("provenance") != "SYNTHETIC_TEST"]
            if symbol:
                events = [e for e in events if e.get("symbol") == symbol]
            if status and status != "ALL":
                events = [e for e in events if e.get("status") == status]
            
            # Sort newest first
            events.sort(key=lambda x: x.get("order_submit_timestamp", ""), reverse=True)
            return events[:limit]

    # =========================================================================
    # 2. ACCOUNT SNAPSHOT SYSTEM
    # =========================================================================
    def record_equity_snapshot(self, snapshot_data: dict) -> dict:
        """
        Records an authoritative account-equity snapshot.
        Invoked periodically (every 5-10s) and on key trading events.
        """
        with self._data_lock:
            snap = {
                "timestamp": snapshot_data.get("timestamp", self._now_iso()),
                "cash_usdt": float(snapshot_data.get("cash_usdt", snapshot_data.get("cash", 0.0))),
                "asset_market_value": float(snapshot_data.get("asset_market_value", snapshot_data.get("crypto_holdings_value", 0.0))),
                "total_equity": float(snapshot_data.get("total_equity", snapshot_data.get("equity", 0.0))),
                "realized_pnl": float(snapshot_data.get("realized_pnl", 0.0)),
                "unrealized_pnl": float(snapshot_data.get("unrealized_pnl", 0.0)),
                "fees": float(snapshot_data.get("fees", 0.0)),
                "drawdown": float(snapshot_data.get("drawdown", 0.0)),
                "open_position_count": int(snapshot_data.get("open_position_count", snapshot_data.get("open_positions", 0))),
                "trigger_event": snapshot_data.get("trigger_event", "PERIODIC_INTERVAL")
            }
            
            # Mathematical invariant validation
            if snap["total_equity"] <= 0 and (snap["cash_usdt"] > 0 or snap["asset_market_value"] > 0):
                snap["total_equity"] = snap["cash_usdt"] + snap["asset_market_value"]

            self._recent_snapshots.append(snap)
            if len(self._recent_snapshots) > 1000:
                self._recent_snapshots.pop(0)

            self._append_jsonl(self.equity_history_file, snap)
            return snap

    def get_equity_timeline(self, time_range: str = "all") -> list[dict]:
        """
        Returns equity timeline snapshots filtered by time range:
        '1h', '6h', '24h', '7d', '30d', 'all'
        """
        with self._data_lock:
            snapshots = []
            if os.path.exists(self.equity_history_file):
                try:
                    with open(self.equity_history_file, "r", encoding="utf-8") as f:
                        for line in f:
                            if line.strip():
                                try:
                                    s = json.loads(line)
                                    if float(s.get("total_equity", s.get("equity", 0.0))) > 0:
                                        snapshots.append(s)
                                except Exception:
                                    pass
                except Exception as e:
                    logger.error(f"[TELEMETRY] Error reading equity history: {e}")
            
            if not snapshots:
                snapshots = list(self._recent_snapshots)

            if time_range == "all" or not snapshots:
                return snapshots

            # Compute cut-off timestamp
            now_ts = self._now_ts()
            range_map = {
                "1h": 3600,
                "6h": 21600,
                "24h": 86400,
                "7d": 604800,
                "30d": 2592000
            }
            duration = range_map.get(time_range, 86400)
            cutoff_ts = now_ts - duration

            filtered = []
            for s in snapshots:
                ts_str = s.get("timestamp", "")
                try:
                    ts = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
                    if ts >= cutoff_ts:
                        filtered.append(s)
                except Exception:
                    filtered.append(s)
            return filtered

    # =========================================================================
    # 3. BALANCE CHANGE EVENTS
    # =========================================================================
    def record_balance_event(self, event: dict) -> dict:
        """
        Records an audit log entry for every cash/balance transition.
        Event types: TRADE_OPEN, TRADE_CLOSE, FEE, DEPOSIT, WITHDRAWAL, RECONCILIATION, MARK_TO_MARKET, OTHER.
        """
        with self._data_lock:
            bal_event = {
                "timestamp": event.get("timestamp", self._now_iso()),
                "event_type": event.get("event_type", "OTHER"),
                "reason": event.get("reason", ""),
                "balance_before": float(event.get("balance_before", 0.0)),
                "balance_after": float(event.get("balance_after", 0.0)),
                "delta": float(event.get("delta", float(event.get("balance_after", 0.0)) - float(event.get("balance_before", 0.0)))),
                "realized_pnl_delta": float(event.get("realized_pnl_delta", 0.0)),
                "unrealized_pnl_delta": float(event.get("unrealized_pnl_delta", 0.0)),
                "fees_delta": float(event.get("fees_delta", 0.0)),
                "trade_id": event.get("trade_id", ""),
                "symbol": event.get("symbol", ""),
                "strategy": event.get("strategy", ""),
                "timeframe": event.get("timeframe", "")
            }

            self._recent_balance_events.append(bal_event)
            if len(self._recent_balance_events) > 500:
                self._recent_balance_events.pop(0)

            self._append_jsonl(self.balance_events_file, bal_event)
            return bal_event

    def get_balance_events(self, limit: int = 100) -> list[dict]:
        with self._data_lock:
            events = []
            if os.path.exists(self.balance_events_file):
                try:
                    with open(self.balance_events_file, "r", encoding="utf-8") as f:
                        for line in f:
                            if line.strip():
                                try:
                                    events.append(json.loads(line))
                                except Exception:
                                    pass
                except Exception as e:
                    logger.error(f"[TELEMETRY] Error reading balance events: {e}")
            if not events:
                events = list(self._recent_balance_events)
            events.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
            return events[:limit]

    # =========================================================================
    # 4. SIGNAL EVENT LOGS
    # =========================================================================
    def record_signal_event(self, signal_data: dict) -> dict:
        """Persists every strategy signal decision for the terminal Signals page."""
        with self._data_lock:
            sig = {
                "timestamp": signal_data.get("timestamp", self._now_iso()),
                "signal_id": signal_data.get("signal_id", f"SIG_{int(self._now_ts()*1000)}"),
                "symbol": signal_data.get("symbol", ""),
                "timeframe": signal_data.get("timeframe", signal_data.get("tf", "5m")),
                "strategy": signal_data.get("strategy", ""),
                "decision": signal_data.get("decision", signal_data.get("side", "HOLD")), # BUY, SELL, HOLD
                "entry": float(signal_data.get("entry", signal_data.get("entry_price", 0.0))),
                "stop": float(signal_data.get("stop", signal_data.get("sl", 0.0))),
                "target": float(signal_data.get("target", signal_data.get("tp", 0.0))),
                "confidence": float(signal_data.get("confidence", signal_data.get("score", 0.0))),
                "expected_gross": float(signal_data.get("expected_gross", 0.0)),
                "expected_net": float(signal_data.get("expected_net", 0.0)),
                "profitability_decision": signal_data.get("profitability_decision", "PENDING"),
                "profitability_reason": signal_data.get("profitability_reason", ""),
                "risk_decision": signal_data.get("risk_decision", "PENDING"),
                "risk_reason": signal_data.get("risk_reason", ""),
                "final_decision": signal_data.get("final_decision", "REJECTED" if signal_data.get("decision") == "HOLD" else "EXECUTED")
            }

            self._recent_signals.append(sig)
            if len(self._recent_signals) > 500:
                self._recent_signals.pop(0)

            self._append_jsonl(self.signals_log_file, sig)
            return sig

    def get_signals_log(self, limit: int = 100, symbol: str | None = None, strategy: str | None = None) -> list[dict]:
        with self._data_lock:
            signals = []
            if os.path.exists(self.signals_log_file):
                try:
                    with open(self.signals_log_file, "r", encoding="utf-8") as f:
                        for line in f:
                            if line.strip():
                                try:
                                    signals.append(json.loads(line))
                                except Exception:
                                    pass
                except Exception as e:
                    logger.error(f"[TELEMETRY] Error reading signals log: {e}")
            if not signals:
                signals = list(self._recent_signals)

            if symbol:
                signals = [s for s in signals if s.get("symbol") == symbol]
            if strategy:
                signals = [s for s in signals if s.get("strategy") == strategy]

            signals.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
            return signals[:limit]

    # =========================================================================
    # 5. EXECUTION EVENT LOGS
    # =========================================================================
    def record_execution_event(self, exec_data: dict) -> dict:
        """
        Persists granular execution steps:
        execution_attempt, order_submitted, order_failed, order_partially_filled,
        order_filled, position_opened, position_closed, protection_placed, protection_failed.
        """
        with self._data_lock:
            evt = {
                "timestamp": exec_data.get("timestamp", self._now_iso()),
                "event_type": exec_data.get("event_type", "execution_attempt"),
                "symbol": exec_data.get("symbol", ""),
                "trade_id": exec_data.get("trade_id", ""),
                "strategy": exec_data.get("strategy", ""),
                "timeframe": exec_data.get("timeframe", "5m"),
                "order_id": str(exec_data.get("order_id", "")),
                "side": exec_data.get("side", "BUY"),
                "quantity": float(exec_data.get("quantity", 0.0)),
                "price": float(exec_data.get("price", 0.0)),
                "status": exec_data.get("status", "SUCCESS"),
                "error_code": str(exec_data.get("error_code", "")),
                "error_message": str(exec_data.get("error_message", ""))
            }

            self._recent_execution_events.append(evt)
            if len(self._recent_execution_events) > 500:
                self._recent_execution_events.pop(0)

            self._append_jsonl(self.execution_events_file, evt)
            return evt

    def get_execution_events(self, limit: int = 100) -> list[dict]:
        with self._data_lock:
            events = []
            if os.path.exists(self.execution_events_file):
                try:
                    with open(self.execution_events_file, "r", encoding="utf-8") as f:
                        for line in f:
                            if line.strip():
                                try:
                                    events.append(json.loads(line))
                                except Exception:
                                    pass
                except Exception as e:
                    logger.error(f"[TELEMETRY] Error reading execution events: {e}")
            if not events:
                events = list(self._recent_execution_events)
            events.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
            return events[:limit]

    # =========================================================================
    # 6. POSITION LIFECYCLE HISTORY
    # =========================================================================
    def record_position_update(self, pos_data: dict) -> dict:
        """Maintains full open and historical positions."""
        with self._data_lock:
            position_id = pos_data.get("position_id") or pos_data.get("trade_id") or f"POS_{pos_data.get('symbol', 'UNK')}"
            existing = self._positions_history.get(position_id, {})
            merged = dict(existing)
            merged.update(pos_data)

            canonical_pos = {
                "position_id": position_id,
                "trade_id": merged.get("trade_id", position_id),
                "symbol": merged.get("symbol", ""),
                "strategy": merged.get("strategy", ""),
                "timeframe": merged.get("timeframe", "5m"),
                "side": merged.get("side", "BUY"),
                "entry_timestamp": merged.get("entry_timestamp", self._now_iso()),
                "entry_price": float(merged.get("entry_price", 0.0)),
                "quantity": float(merged.get("quantity", 0.0)),
                "current_price": float(merged.get("current_price", merged.get("entry_price", 0.0))),
                "current_unrealized_pnl": float(merged.get("current_unrealized_pnl", 0.0)),
                "stop_loss": float(merged.get("stop_loss", merged.get("sl_price", 0.0))),
                "take_profit": float(merged.get("take_profit", merged.get("tp_price", 0.0))),
                "exit_timestamp": merged.get("exit_timestamp", ""),
                "exit_price": float(merged.get("exit_price", 0.0)),
                "realized_pnl": float(merged.get("realized_pnl", 0.0)),
                "status": merged.get("status", "OPEN") # OPEN / CLOSED
            }

            self._positions_history[position_id] = canonical_pos
            self._append_jsonl(self.position_history_file, canonical_pos)
            return canonical_pos

    def get_positions(self, status: str | None = None) -> list[dict]:
        with self._data_lock:
            positions = list(self._positions_history.values())
            if status and status != "ALL":
                positions = [p for p in positions if p.get("status") == status]
            positions.sort(key=lambda x: x.get("entry_timestamp", ""), reverse=True)
            return positions

    # Query Aliases
    def query_trades(self, status: str | None = None, symbol: str | None = None, strategy: str | None = None, timeframe: str | None = None, limit: int = 100) -> list[dict]:
        events = self.get_trade_events(symbol=symbol, status=status, limit=limit)
        if strategy:
            events = [e for e in events if e.get("strategy") == strategy]
        if timeframe:
            events = [e for e in events if e.get("timeframe") == timeframe]
        return events

    def query_signals(self, symbol: str | None = None, strategy: str | None = None, limit: int = 100) -> list[dict]:
        return self.get_signals_log(limit=limit, symbol=symbol, strategy=strategy)

    def query_positions(self, status: str | None = None) -> list[dict]:
        return self.get_positions(status=status)

    def query_equity_curve(self, time_range: str = "all", limit: int = 500) -> list[dict]:
        timeline = self.get_equity_timeline(time_range=time_range)
        return timeline[-limit:] if limit else timeline

    def query_balance_events(self, limit: int = 100) -> list[dict]:
        return self.get_balance_events(limit=limit)

    def compute_summary_analytics(self) -> dict:
        """
        Computes institutional-grade performance analytics across all closed trades:
        - Total Trades, Wins, Losses, Break-even
        - Win Rate (%)
        - Total Gross PnL, Total Fees, Total Net PnL
        - Profit Factor, Payoff Ratio (Avg Win / Avg Loss)
        - Expectancy ($ and %)
        - Max Drawdown ($ and %)
        - Breakdown by Strategy, Timeframe, Symbol
        """
        with self._data_lock:
            trades = [t for t in self._trade_events.values() if t.get("status") == "CLOSED" and t.get("source") != "TEST"]
            
            if not trades:
                return {
                    "total_trades": 0,
                    "winning_trades": 0,
                    "losing_trades": 0,
                    "win_rate_pct": 0.0,
                    "total_gross_pnl": 0.0,
                    "total_fees": 0.0,
                    "total_net_pnl": 0.0,
                    "profit_factor": 0.0,
                    "payoff_ratio": 0.0,
                    "expectancy": 0.0,
                    "by_strategy": {},
                    "by_timeframe": {},
                    "by_symbol": {}
                }

            wins = [t for t in trades if float(t.get("net_pnl", 0.0)) > 0]
            losses = [t for t in trades if float(t.get("net_pnl", 0.0)) < 0]
            total_net_pnl = sum(float(t.get("net_pnl", 0.0)) for t in trades)
            total_gross_pnl = sum(float(t.get("gross_pnl", 0.0)) for t in trades)
            total_fees = sum(float(t.get("total_fees", 0.0)) for t in trades)
            
            win_pnl = sum(float(t.get("net_pnl", 0.0)) for t in wins)
            loss_pnl = abs(sum(float(t.get("net_pnl", 0.0)) for t in losses))
            
            win_rate = (len(wins) / len(trades)) * 100.0 if trades else 0.0
            profit_factor = (win_pnl / loss_pnl) if loss_pnl > 0 else (None if win_pnl > 0 else 0.0)
            avg_win = (win_pnl / len(wins)) if wins else 0.0
            avg_loss = (loss_pnl / len(losses)) if losses else 0.0
            payoff_ratio = (avg_win / avg_loss) if avg_loss > 0 else 0.0
            expectancy = (total_net_pnl / len(trades)) if trades else 0.0

            # Sub-aggregations
            by_strategy = {}
            for t in trades:
                st = t.get("strategy", "DEFAULT") or "DEFAULT"
                if st not in by_strategy:
                    by_strategy[st] = {"trades": 0, "wins": 0, "net_pnl": 0.0, "fees": 0.0}
                by_strategy[st]["trades"] += 1
                if float(t.get("net_pnl", 0.0)) > 0:
                    by_strategy[st]["wins"] += 1
                by_strategy[st]["net_pnl"] += float(t.get("net_pnl", 0.0))
                by_strategy[st]["fees"] += float(t.get("total_fees", 0.0))

            by_timeframe = {}
            for t in trades:
                tf = t.get("timeframe", "5m") or "5m"
                if tf not in by_timeframe:
                    by_timeframe[tf] = {"trades": 0, "wins": 0, "net_pnl": 0.0}
                by_timeframe[tf]["trades"] += 1
                if float(t.get("net_pnl", 0.0)) > 0:
                    by_timeframe[tf]["wins"] += 1
                by_timeframe[tf]["net_pnl"] += float(t.get("net_pnl", 0.0))

            by_symbol = {}
            for t in trades:
                sym = t.get("symbol", "UNKNOWN")
                if sym not in by_symbol:
                    by_symbol[sym] = {"trades": 0, "wins": 0, "net_pnl": 0.0}
                by_symbol[sym]["trades"] += 1
                if float(t.get("net_pnl", 0.0)) > 0:
                    by_symbol[sym]["wins"] += 1
                by_symbol[sym]["net_pnl"] += float(t.get("net_pnl", 0.0))

            return {
                "total_trades": len(trades),
                "winning_trades": len(wins),
                "losing_trades": len(losses),
                "win_rate_pct": round(win_rate, 2),
                "total_gross_pnl": round(total_gross_pnl, 4),
                "total_fees": round(total_fees, 4),
                "total_net_pnl": round(total_net_pnl, 4),
                "profit_factor": round(profit_factor, 2),
                "payoff_ratio": round(payoff_ratio, 2),
                "expectancy": round(expectancy, 4),
                "by_strategy": by_strategy,
                "by_timeframe": by_timeframe,
                "by_symbol": by_symbol
            }

def get_telemetry_manager() -> TelemetryManager:
    return TelemetryManager()

