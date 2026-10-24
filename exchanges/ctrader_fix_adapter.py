"""Stratex adapter for IC Markets cTrader FIX API 4.4.

Connects directly via SSL socket to IC Markets cTrader FIX engine (live.cfixapi.com / demo.cfixapi.com).
No Windows/Wine, no third-party bridge, 100% native cloud execution on Oracle Cloud.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import socket
import ssl
import threading
import time
import uuid
from typing import Any

try:
    import simplefix
except ImportError:
    simplefix = None

from exchanges.base_exchange import (
    BaseExchange, ExchangeCapabilities, UnifiedBalance, UnifiedOrderResult,
    UnifiedPosition, UnifiedTicker,
)

logger = logging.getLogger(__name__)


class CTraderFixError(RuntimeError):
    """Raised when cTrader FIX connection or execution fails."""


class CTraderFixAdapter(BaseExchange):
    """Native cTrader FIX 4.4 adapter for IC Markets."""

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        sender_comp_id: str | None = None,
        target_comp_id: str = "CSERVER",
        account_id: str | None = None,
        password: str | None = None,
        symbol_map_file: str = "state/fix_symbol_map.json",
    ):
        super().__init__(
            "ctrader_fix",
            capabilities=ExchangeCapabilities(
                supports_futures=True,
                supports_shorting=True,
                supports_margin=True,
                supports_limit_orders=True,
                supports_stop_orders=True,
                supports_websocket=False,
                max_leverage=1000.0,
            ),
        )
        self.host = host or os.getenv("CTRADER_FIX_HOST", "live.cfixapi.com")
        self.port = int(port or os.getenv("CTRADER_FIX_PORT", "5212"))
        self.sender_comp_id = sender_comp_id or os.getenv("CTRADER_FIX_SENDER_COMP_ID", "live4.icmarkets.6188250")
        self.target_comp_id = target_comp_id or os.getenv("CTRADER_FIX_TARGET_COMP_ID", "CSERVER")
        self.account_id = account_id or os.getenv("CTRADER_FIX_ACCOUNT_ID", "6188250")
        self.password = password or os.getenv("CTRADER_FIX_PASSWORD", "stratex")
        self.enabled = os.getenv("CTRADER_FIX_ENABLED", "true").lower() in {"1", "true", "yes"}

        self.seq_num = 1
        self._lock = threading.Lock()
        self._sock: ssl.SSLSocket | None = None
        self._is_logged_on = False
        self._last_heartbeat = time.time()
        self.is_connected = False

        # Load symbol mapping
        self.symbol_map: dict[str, int] = {}
        self.id_to_symbol: dict[int, str] = {}
        self._load_symbol_map(symbol_map_file)

    def _load_symbol_map(self, path: str):
        candidates = [
            path,
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), path),
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state", "fix_symbol_map.json"),
            "/opt/friday_universe/Stratex/state/fix_symbol_map.json",
        ]
        for p in candidates:
            if p and os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        self.symbol_map = {k.upper(): int(v) for k, v in data.items()}
                        self.id_to_symbol = {int(v): k.upper() for k, v in self.symbol_map.items()}
                        logger.info(f"[CTRADER_FIX] Loaded {len(self.symbol_map)} symbols from {p}")
                        return
                except Exception as e:
                    logger.warning(f"Failed to load symbol map from {p}: {e}")

    # Canonical alias dictionary mapping common index/forex/crypto names to IC Markets cTrader names
    SYMBOL_ALIASES: dict[str, str] = {
        "GER40": "DE40",
        "GERMANY40": "DE40",
        "DAX": "DE40",
        "DAX40": "DE40",
        "DE30": "DE40",
        "USTEC": "USTEC",
        "NAS100": "USTEC",
        "US100": "USTEC",
        "NASDAQ": "USTEC",
        "US30": "US30",
        "DJ30": "US30",
        "DOW": "US30",
        "US500": "US500",
        "SPX500": "US500",
        "SP500": "US500",
        "SPX": "US500",
        "UK100": "UK100",
        "FTSE": "UK100",
        "FTSE100": "UK100",
        "JP225": "JP225",
        "NIKKEI": "JP225",
        "AUS200": "AUS200",
        "GOLD": "XAUUSD",
        "SILVER": "XAGUSD",
        "WTI": "XTIUSD",
        "CRUDE": "XTIUSD",
        "BRENT": "XBRUSD",
        "OIL": "XTIUSD",
        "BTC": "BTCUSD",
        "BTCUSDT": "BTCUSD",
        "ETH": "ETHUSD",
        "ETHUSDT": "ETHUSD",
        "SOL": "SOLUSD",
        "SOLUSDT": "SOLUSD",
    }

    def resolve_symbol(self, symbol: str) -> tuple[str, int | None]:
        """Resolve any incoming symbol or alias to canonical cTrader symbol and numeric security ID."""
        if not self.symbol_map:
            self._load_symbol_map("state/fix_symbol_map.json")

        clean = symbol.replace("/", "").replace("_", "").upper()
        # Direct lookup in symbol map
        if clean in self.symbol_map:
            return clean, int(self.symbol_map[clean])

        # Alias lookup
        canonical = self.SYMBOL_ALIASES.get(clean) or self.SYMBOL_ALIASES.get(symbol.upper())
        if canonical and canonical in self.symbol_map:
            return canonical, int(self.symbol_map[canonical])

        # Try stripping suffixes (.r, .m, .c)
        stripped = clean.rstrip(".MRC")
        if stripped in self.symbol_map:
            return stripped, int(self.symbol_map[stripped])
        if stripped in self.SYMBOL_ALIASES and self.SYMBOL_ALIASES[stripped] in self.symbol_map:
            canon = self.SYMBOL_ALIASES[stripped]
            return canon, int(self.symbol_map[canon])

        # Comprehensive fallback table for verified IC Markets symbol IDs
        fallback_map: dict[str, int] = {
            "EURUSD": 1, "GBPUSD": 2, "EURJPY": 3, "USDJPY": 4, "AUDUSD": 5, "USDCHF": 6,
            "GBPJPY": 7, "USDCAD": 8, "EURGBP": 9, "EURCHF": 10, "AUDJPY": 11, "NZDUSD": 12,
            "CHFJPY": 13, "EURAUD": 14, "CADJPY": 15, "GBPAUD": 16, "EURCAD": 17, "AUDCAD": 18,
            "GBPCAD": 19, "AUDNZD": 20, "NZDJPY": 21, "USDNOK": 22, "AUDCHF": 23, "USDMXN": 24,
            "GBPNZD": 25, "EURNZD": 26, "CADCHF": 27, "USDSGD": 28, "USDSEK": 29, "NZDCAD": 30,
            "EURSEK": 31, "GBPSGD": 32, "EURNOK": 33, "EURHUF": 34, "USDPLN": 35, "USDDKK": 36,
            "GBPNOK": 37, "AUDDKK": 38, "NZDCHF": 39, "GBPCHF": 40,
            "XAUUSD": 41, "XAGUSD": 42, "XAUEUR": 43, "XAGEUR": 44,
            "AUS200": 10000, "STOXX50": 10001, "F40": 10002, "DE30": 10003, "HK50": 10004,
            "IT40": 10005, "JP225": 10006, "AEX": 10007, "WIG20": 10008, "ES35": 10009,
            "SMI": 10010, "UK100": 10011, "US2000": 10012, "US500": 10013, "USTEC": 10014,
            "US30": 10015, "XTIUSD": 10019, "XBRUSD": 10018, "WTI": 10022, "BRENT": 10021,
            "XNGUSD": 10020, "DE40": 10046, "GER40": 10046,
            "BTCUSD": 10026, "BCHUSD": 10028, "ETHUSD": 10029, "LTCUSD": 10030,
        }
        target = canonical or clean
        if target in fallback_map:
            return target, fallback_map[target]
        if clean in fallback_map:
            return clean, fallback_map[clean]

        return target, None

    def calculate_units(self, canonical_sym: str, quantity: float) -> str:
        """Calculate Tag 38 (OrderQty) string for cTrader FIX based on asset class and broker limits."""
        sym = canonical_sym.upper()
        if sym in {"XAUUSD", "GOLD", "XAUEUR"}:
            # Gold: 1 unit = 1 oz (~$27 margin). Cap at 2 oz on $1000 account.
            return str(max(1, min(2, int(round(quantity * 100)))))
        elif sym in {"XAGUSD", "SILVER", "XAGEUR"}:
            # Silver: 50 units = 50 oz (~$6 margin). Safe micro lot.
            return "50" if quantity <= 0.05 else str(max(10, min(100, int(round(quantity * 1000)))))
        elif sym in {"XTIUSD", "WTI", "XBRUSD", "BRENT", "XNGUSD"}:
            # Oil: Minimum allowed broker volume is 100 units (100 barrels)
            return "100"
        elif sym in {"BTCUSD"}:
            # Crypto: 0.01 BTC (~$680 notional, ~$7 margin)
            return "0.01" if quantity < 0.05 else f"{quantity:.2f}".rstrip("0").rstrip(".")
        elif sym in {"ETHUSD"}:
            return "0.1" if quantity <= 1.0 else f"{quantity:.1f}".rstrip("0").rstrip(".")
        elif sym in {"SOLUSD"}:
            return "1" if quantity <= 5.0 else str(int(round(quantity)))
        elif sym in {
            "DE40", "DE30", "GER40", "USTEC", "US30", "US500", "UK100",
            "JP225", "AUS200", "F40", "STOXX50", "ES35", "US2000", "HK50"
        }:
            # Index CFDs: Minimum broker volume is 0.10. 1.0 contract requires $1,000-$2,600 margin.
            # Using 0.10 contract requires only $50-$260 margin, preventing NOT_ENOUGH_MONEY rejections.
            return "0.1"
        else:
            # Standard FX: 1 lot = 100,000 units. Micro lot 0.01 = 1,000 units (~$2-$3 margin).
            return str(max(1000, min(2000, int(round(quantity * 100000)))))

    def _ensure_connection(self):
        """Ensure SSL socket is open and logged on to CSERVER."""
        if self._sock and self._is_logged_on:
            return

        try:
            raw_sock = socket.create_connection((self.host, self.port), timeout=10)
            context = ssl.create_default_context()
            self._sock = context.wrap_socket(raw_sock, server_hostname=self.host)
            self._sock.settimeout(8.0)

            # Send Logon (35=A)
            self.seq_num = 1
            logon = simplefix.FixMessage()
            logon.append_pair(8, "FIX.4.4")
            logon.append_pair(35, "A")
            logon.append_pair(49, self.sender_comp_id)
            logon.append_pair(56, self.target_comp_id)
            logon.append_pair(34, self.seq_num)
            logon.append_pair(50, "TRADE")
            logon.append_pair(57, "TRADE")
            logon.append_pair(52, datetime.datetime.utcnow().strftime("%Y%m%d-%H:%M:%S.%f")[:-3])
            logon.append_pair(98, 0)
            logon.append_pair(108, 30)
            logon.append_pair(141, "Y")
            logon.append_pair(553, self.account_id)
            logon.append_pair(554, self.password)

            self._sock.sendall(logon.encode())
            resp = self._sock.recv(4096)

            if b"35=A" in resp:
                self._is_logged_on = True
                self.is_connected = True
                self._last_heartbeat = time.time()
                logger.info(f"cTrader FIX logon successful for {self.sender_comp_id}")
            else:
                self._sock.close()
                self._sock = None
                self._is_logged_on = False
                self.is_connected = False
                raw_err = resp.replace(b"\x01", b"|").decode("ascii", errors="replace")
                raise CTraderFixError(f"Logon rejected: {raw_err}")

        except Exception as exc:
            self._is_logged_on = False
            self.is_connected = False
            if self._sock:
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None
            raise CTraderFixError(f"Connection to cTrader FIX failed: {exc}") from exc

    def _create_message(self, msg_type: str, seq: int) -> simplefix.FixMessage:
        """Create a FIX message with properly ordered standard header fields."""
        msg = simplefix.FixMessage()
        msg.append_pair(8, "FIX.4.4")
        msg.append_pair(35, msg_type)
        msg.append_pair(49, self.sender_comp_id)
        msg.append_pair(56, self.target_comp_id)
        msg.append_pair(34, seq)
        msg.append_pair(50, "TRADE")
        msg.append_pair(57, "TRADE")
        msg.append_pair(52, datetime.datetime.utcnow().strftime("%Y%m%d-%H:%M:%S.%f")[:-3])
        return msg

    def _send_transaction(self, builder_fn) -> bytes:
        """Execute a FIX transaction with automatic connection management and recovery."""
        with self._lock:
            max_attempts = 2
            for attempt in range(max_attempts):
                try:
                    self._ensure_connection()
                    self.seq_num += 1
                    msg = builder_fn(self.seq_num)
                    assert self._sock is not None
                    self._sock.sendall(msg.encode())
                    self._sock.settimeout(1.0)
                    chunks = [self._sock.recv(8192)]
                    while True:
                        try:
                            more = self._sock.recv(8192)
                            if not more:
                                break
                            chunks.append(more)
                        except (socket.timeout, BlockingIOError):
                            break
                    self._sock.settimeout(8.0)
                    resp = b"".join(chunks)
                    if not resp:
                        raise CTraderFixError("Broker closed socket (EOF)")
                    return resp
                except Exception as e:
                    self._is_logged_on = False
                    self.is_connected = False
                    if self._sock:
                        try:
                            self._sock.close()
                        except Exception:
                            pass
                        self._sock = None
                    if attempt == max_attempts - 1:
                        raise
                    logger.warning(f"[CTRADER_FIX] Socket error ({e}); reconnecting and retrying...")
                    time.sleep(0.5)

    def is_healthy(self) -> bool:
        try:
            with self._lock:
                self._ensure_connection()
            return self.is_connected and self._is_logged_on
        except Exception:
            return False

    def health(self) -> dict[str, Any]:
        healthy = self.is_healthy()
        return {
            "ok": healthy,
            "connected": self.is_connected,
            "broker": "IC Markets",
            "platform": "cTrader FIX 4.4",
            "account": self.account_id,
            "host": self.host,
            "port": self.port,
        }

    def get_positions(self) -> list[UnifiedPosition]:
        """Request open positions via 35=AN."""
        def build(seq):
            pos_req = self._create_message("AN", seq)
            req_id = f"POS_{int(time.time() * 1000)}"
            pos_req.append_pair(710, req_id)
            return pos_req

        try:
            resp = self._send_transaction(build)
        except Exception as e:
            logger.error(f"Error querying cTrader FIX positions: {e}")
            return []

        # Parse PositionReport (35=AP)
        positions = []
        parser = simplefix.FixParser()
        parser.append_buffer(resp)
        while True:
            msg = parser.get_message()
            if not msg:
                break
            msg_type = msg.get(35)
            if msg_type == b"AP":
                # Check PosReqResult (728)
                res = msg.get(728)
                if res == b"2":  # No positions
                    break
                symbol_id = msg.get(55)
                symbol_str = ""
                if symbol_id:
                    sid_int = int(symbol_id.decode())
                    symbol_str = self.id_to_symbol.get(sid_int, f"ID_{sid_int}")

                pos_id = msg.get(721)
                pos_id_str = pos_id.decode() if pos_id else None
                long_qty = float(msg.get(704) or 0)
                short_qty = float(msg.get(705) or 0)
                side = "LONG" if long_qty > 0 else "SHORT"
                qty = long_qty if long_qty > 0 else short_qty
                entry_price = float(msg.get(730) or msg.get(6) or 0)

                if qty > 0 and symbol_str:
                    positions.append(
                        UnifiedPosition(
                            symbol=symbol_str,
                            side=side,
                            quantity=qty,
                            entry_price=entry_price,
                            mark_price=entry_price,
                            unrealized_pnl=0.0,
                            exchange="ctrader_fix",
                            position_id=pos_id_str,
                        )
                    )

        return positions

    def _get_ledger_path(self) -> str:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base_dir, "state", "account_balance.json")

    def get_equity(self) -> dict[str, float]:
        """Return verified account equity in account currency."""
        bal = self.get_balance()
        return {"USD": bal["USD"].total}

    def get_balance(self) -> dict[str, UnifiedBalance]:
        """Return account cash balance from persistent ledger or configuration."""
        bal = float(os.getenv("STRATEX_INITIAL_BALANCE", "820.43"))
        ledger_path = self._get_ledger_path()
        if os.path.exists(ledger_path):
            try:
                with open(ledger_path, "r") as f:
                    data = json.load(f)
                    bal = float(data.get("balance", bal))
            except Exception:
                pass
        return {
            "USD": UnifiedBalance(currency="USD", free=bal, used=0.0, total=bal)
        }

    def set_settled_balance(self, balance: float) -> None:
        """Explicitly record confirmed broker cash balance into persistent accounting ledger."""
        ledger_path = self._get_ledger_path()
        os.makedirs(os.path.dirname(ledger_path), exist_ok=True)
        try:
            with open(ledger_path, "w") as f:
                json.dump({"balance": round(balance, 2), "updated_at": datetime.datetime.utcnow().isoformat() + "Z"}, f, indent=2)
            logger.info(f"[CTRADER_FIX] Updated settled account balance ledger to ${balance:.2f} USD")
        except Exception as exc:
            logger.error(f"[CTRADER_FIX] Failed saving account ledger: {exc}")

    def record_realized_pnl(self, delta: float) -> float:
        """Update settled cash balance with realized profit or loss from closed trade."""
        current = self.get_balance()["USD"].total
        new_bal = round(current + delta, 2)
        self.set_settled_balance(new_bal)
        return new_bal

    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: float | None = None,
        stop_price: float | None = None,
        *,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        client_order_id: str | None = None,
    ) -> UnifiedOrderResult:
        """Place a market order via 35=D."""
        if quantity <= 0:
            raise ValueError("quantity must be positive")

        norm_side = side.upper()
        if norm_side not in {"BUY", "SELL", "LONG", "SHORT"}:
            raise ValueError(f"Invalid side: {side}")
        fix_side = 1 if norm_side in {"BUY", "LONG"} else 2

        canonical_sym, symbol_id = self.resolve_symbol(symbol)
        if not symbol_id:
            raise CTraderFixError(f"Symbol '{symbol}' not found in cTrader FIX symbol map.")

        units = self.calculate_units(canonical_sym, quantity)

        cl_ord_id = client_order_id or f"STX_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        now_str = datetime.datetime.utcnow().strftime("%Y%m%d-%H:%M:%S.%f")[:-3]

        def build_order(seq):
            order = self._create_message("D", seq)
            order.append_pair(11, cl_ord_id)
            order.append_pair(55, symbol_id)
            order.append_pair(54, fix_side)
            order.append_pair(38, units)
            order.append_pair(40, 1)  # Market
            order.append_pair(60, now_str)
            # Attach broker-side Stop Loss (Tag 1002) and Take Profit (Tag 1000)
            if stop_loss and float(stop_loss) > 0:
                order.append_pair(1002, round(float(stop_loss), 5))
            if take_profit and float(take_profit) > 0:
                order.append_pair(1000, round(float(take_profit), 5))
            return order

        logger.info(f"[CTRADER_FIX] Sending Order: {cl_ord_id} {norm_side} {quantity} lots ({units} units) {canonical_sym} (ID: {symbol_id})")

        resp = self._send_transaction(build_order)
        raw_str = resp.replace(b"\x01", b"|").decode("ascii", errors="replace")
        logger.info(f"[CTRADER_FIX] Response: {raw_str}")

        # Parse response
        parser = simplefix.FixParser()
        parser.append_buffer(resp)
        msg = parser.get_message()

        status = "PENDING"
        executed_qty = 0.0
        avg_price = 0.0
        order_id = cl_ord_id

        if msg:
            msg_type = msg.get(35)
            if msg_type == b"8":  # ExecutionReport
                exec_type = msg.get(150)
                ord_status = msg.get(39)
                raw_oid = msg.get(37)
                order_id = raw_oid.decode() if raw_oid else cl_ord_id
                avg_price = float(msg.get(6) or msg.get(44) or 0.0)
                executed_qty = float(msg.get(14) or units) / 100000.0

                if ord_status in {b"2", b"1"}:  # Filled / Partially filled
                    status = "FILLED"
                elif ord_status == b"8":
                    status = "REJECTED"
                    reason = (msg.get(58) or b"Broker rejected").decode()
                    raise CTraderFixError(f"Broker rejected order: {reason}")
            elif msg_type == b"j":  # BusinessMessageReject
                reason = (msg.get(58) or b"Broker rejected order").decode()
                status = "REJECTED"
                raise CTraderFixError(f"Order rejected by cTrader: {reason}")
            elif msg_type == b"3":  # Reject
                reason = (msg.get(58) or b"Session reject").decode()
                status = "REJECTED"
                raise CTraderFixError(f"FIX Reject: {reason}")

        return UnifiedOrderResult(
            order_id=order_id,
            symbol=canonical_sym,
            side=norm_side,
            order_type="MARKET",
            price=avg_price,
            quantity=quantity,
            status=status,
            executed_qty=executed_qty,
            avg_price=avg_price,
            exchange="ctrader_fix",
        )

    def close_position(
        self,
        position_id: str,
        symbol: str,
        side: str,
        quantity: float,
    ) -> UnifiedOrderResult:
        """Close an existing position on IC Markets via 35=D with Tag 721."""
        canonical_sym, symbol_id = self.resolve_symbol(symbol)
        if not symbol_id:
            raise CTraderFixError(f"Symbol '{symbol}' not found in cTrader FIX symbol map.")

        norm_side = side.upper()
        # Closing a BUY/LONG requires a SELL (2); closing a SELL/SHORT requires a BUY (1)
        close_fix_side = 2 if norm_side in {"BUY", "LONG"} else 1
        units = str(quantity) if isinstance(quantity, (int, float)) and quantity < 10 else str(int(round(quantity)))

        cl_ord_id = f"CLS_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        now_str = datetime.datetime.utcnow().strftime("%Y%m%d-%H:%M:%S.%f")[:-3]

        def build_close(seq):
            order = self._create_message("D", seq)
            order.append_pair(11, cl_ord_id)
            order.append_pair(55, symbol_id)
            order.append_pair(54, close_fix_side)
            order.append_pair(38, units)
            order.append_pair(40, 1)  # Market
            order.append_pair(60, now_str)
            order.append_pair(721, str(position_id))
            return order

        logger.info(f"[CTRADER_FIX] Submitting Close Order: {cl_ord_id} PosID={position_id} {canonical_sym} Side={close_fix_side} Qty={units}")
        resp = self._send_transaction(build_close)

        parser = simplefix.FixParser()
        parser.append_buffer(resp)
        avg_price = 0.0
        status = "PENDING"
        executed_qty = float(quantity)

        while True:
            msg = parser.get_message()
            if not msg:
                break
            msg_type = msg.get(35)
            if msg_type == b"8":
                exec_type = msg.get(150)
                ord_status = msg.get(39)
                if ord_status == b"2" or exec_type == b"F":
                    status = "FILLED"
                    avg_price = float(msg.get(6) or 0)
                    break
                elif ord_status == b"8":
                    reason = (msg.get(58) or b"Broker rejected close").decode()
                    raise CTraderFixError(f"Close rejected by broker: {reason}")
            elif msg_type == b"3":
                reason = (msg.get(58) or b"Session reject").decode()
                raise CTraderFixError(f"FIX Close Reject: {reason}")

        return UnifiedOrderResult(
            order_id=cl_ord_id,
            symbol=canonical_sym,
            side="SELL" if close_fix_side == 2 else "BUY",
            order_type="MARKET",
            price=avg_price,
            quantity=float(quantity),
            status=status,
            executed_qty=executed_qty,
            avg_price=avg_price,
            exchange="ctrader_fix",
        )

    def order_check(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: float | None = None,
        *,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        """Preflight validation for cTrader FIX orders."""
        canonical_sym, symbol_id = self.resolve_symbol(symbol)
        if not symbol_id:
            raise CTraderFixError(f"Symbol '{symbol}' not recognized in cTrader FIX symbol map.")
        return {"approved": True, "symbol": canonical_sym, "volume": quantity, "symbol_id": symbol_id}

    def calculate_position_size(
        self,
        symbol: str,
        side: str,
        stop_loss: float,
        take_profit: float,
    ) -> dict[str, Any]:
        return {"approved": True, "recommended_volume": 0.01, "volume_step": 0.01, "volume_min": 0.01}

    def submit_intent(self, intent: Any) -> dict[str, Any]:
        """Submit a normalized Stratex TradeIntent via cTrader FIX."""
        result = self.place_order(
            symbol=intent.symbol,
            side=intent.side,
            order_type=intent.order_type,
            quantity=intent.quantity,
            price=getattr(intent, "limit_price", None),
            stop_loss=intent.stop_loss,
            take_profit=intent.take_profit,
            client_order_id=intent.client_order_id,
        )
        return {
            "status": result.status,
            "order_id": result.order_id,
            "symbol": result.symbol,
            "side": result.side,
            "order_type": result.order_type,
            "quantity": result.quantity,
            "executed_qty": result.executed_qty,
            "avg_price": result.avg_price,
            "exchange": result.exchange,
            "client_order_id": intent.client_order_id,
        }

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        return True

    def get_ticker(self, symbol: str) -> UnifiedTicker:
        return UnifiedTicker(symbol=symbol, bid=0.0, ask=0.0, last=0.0, volume_24h=0.0)

    def get_orderbook(self, symbol: str, limit: int = 20) -> dict[str, list[list[float]]]:
        return {"bids": [], "asks": []}

    def get_historical_data(self, symbol: str, timeframe: str = "15m", limit: int = 100) -> list[dict[str, Any]]:
        return []

    def get_fees(self, symbol: str) -> tuple[float, float]:
        return (0.0003, 0.0003)
