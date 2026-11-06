"""
autonomy/multi_venue_engine.py — Multi-Venue Trading Engine for Stratex.

Orchestrates trading across Binance Futures (Crypto) and OANDA (Forex/Indices/Commodities).
Unified risk management, balance tracking, and position management across venues.
"""
from __future__ import annotations

import json
import os
import time
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from exchanges.base_exchange import (
    BaseExchange,
    UnifiedBalance,
    UnifiedOrderResult,
    UnifiedPosition,
    UnifiedTicker,
)
from exchanges.exchange_implementations import get_exchange_adapter
from logger import get_logger

logger = get_logger("multi_venue_engine")


class MultiVenueEngine:
    """
    Multi-Venue Trading Engine.
    
    Manages trading across:
    - Binance Futures: Crypto perpetual futures (24/7)
    - OANDA: Forex, Indices, Commodities, Crypto CFDs (24/5)
    
    Features:
    - Unified balance tracking across venues
    - Cross-venue position limits
    - Venue-specific risk management
    - Unified candle data for signals
    """

    def __init__(
        self,
        binance_api_key: str = "",
        binance_secret: str = "",
        binance_testnet: bool = True,
        oanda_api_key: str = "",
        oanda_account_id: str = "",
        oanda_practice: bool = True,
        starting_balance: float = 1000.0,
        max_positions_per_venue: int = 9999,
        max_total_positions: int = 9999,
        risk_per_trade_pct: float = 0.02,
        max_daily_loss_pct: float = 1.0,
        telegram_bot: Optional[Any] = None,
    ):
        self.starting_balance = starting_balance
        # No position limits
        self.max_positions_per_venue = max_positions_per_venue
        self.max_total_positions = max_total_positions
        self.risk_per_trade_pct = risk_per_trade_pct
        # No daily loss limit
        self.max_daily_loss_pct = max_daily_loss_pct
        self.telegram_bot = telegram_bot

        # Initialize venues
        self.venues: Dict[str, Any] = {}
        
        # Binance Futures (Crypto)
        binance_key = binance_api_key or os.getenv("BINANCE_API_KEY", "")
        if binance_key:
            try:
                from exchanges.binance_futures_adapter import BinanceFuturesAdapter
                self.venues["binance_futures"] = BinanceFuturesAdapter(
                    api_key=binance_key,
                    secret_key=binance_secret or os.getenv("BINANCE_API_SECRET", ""),
                    testnet=binance_testnet,
                )
                logger.info("[MULTI_VENUE] Binance Futures adapter initialized")
            except Exception as e:
                logger.warning(f"[MULTI_VENUE] Binance Futures init failed: {e}")
        else:
            logger.info("[MULTI_VENUE] Binance Futures skipped (no API key)")

        # OANDA (Forex/Indices/Commodities)
        oanda_key = oanda_api_key or os.getenv("OANDA_API_KEY", "")
        oanda_acct = oanda_account_id or os.getenv("OANDA_ACCOUNT_ID", "")
        if oanda_key and oanda_acct:
            try:
                from exchanges.oanda_adapter import OandaExchangeAdapter
                self.venues["oanda"] = OandaExchangeAdapter(
                    api_key=oanda_key,
                    account_id=oanda_acct,
                    practice=oanda_practice,
                )
                logger.info("[MULTI_VENUE] OANDA adapter initialized")
            except Exception as e:
                logger.warning(f"[MULTI_VENUE] OANDA init failed: {e}")
        else:
            logger.info("[MULTI_VENUE] OANDA skipped (credentials not configured)")

        # State tracking
        self.realized_pnl: float = 0.0
        self.daily_realized_pnl: float = 0.0
        self.daily_start_equity: float = 0.0
        self.daily_start_date: str = ""
        self.positions: Dict[str, UnifiedPosition] = {}  # venue:symbol -> position
        self.position_opened_at: Dict[str, int] = {}  # venue:symbol -> ms epoch
        # Attribution: strategy and timeframe that opened each position
        self.position_strategy: Dict[str, str] = {}
        self.position_timeframe: Dict[str, str] = {}
        self._candle_cache: Dict[str, tuple] = {}
        self.order_history: List[Dict] = []
        self.daily_loss_limit_hit: bool = False

        # Re-entry protection: a symbol that just exited is quarantined for a
        # while so a stale signal cannot immediately put the risk back on.
        self.symbol_cooldown_sec: int = int(os.getenv("STRATEX_SYMBOL_COOLDOWN_SEC", "900"))
        self.last_exit: Dict[str, float] = {}

        # Booked PnL survives restarts.
        self._load_state()

        logger.info(f"[MULTI_VENUE] Initialized with {len(self.venues)} venues: {list(self.venues.keys())}")

    def adopt_existing_positions(self) -> int:
        """Track positions that were already open when the process started.

        Without this a restart forgets open trades, so a later SL/TP fill would
        never be detected or reported.
        """
        adopted = 0
        now_ms = int(time.time() * 1000)
        for venue, adapter in self.venues.items():
            try:
                getter = getattr(adapter, "get_positions_or_none", None)
                positions = getter() if callable(getter) else adapter.get_positions()
                if not positions:
                    continue
                for pos in positions:
                    key = f"{venue}:{pos.symbol}"
                    if key in self.positions:
                        continue
                    self.positions[key] = pos
                    self.position_opened_at[key] = now_ms
                    adopted += 1
            except Exception as e:
                logger.warning(f"[MULTI_VENUE] Adoption failed for {venue}: {e}")

        if adopted:
            logger.info(f"[MULTI_VENUE] Adopted {adopted} existing position(s) into tracking")
        return adopted

    # ------------------------------------------------------------------------
    # Balance & Equity
    # ------------------------------------------------------------------------
    def get_total_balance(self) -> Dict[str, UnifiedBalance]:
        """Get combined balance across all venues."""
        combined: Dict[str, UnifiedBalance] = {}
        for venue_name, venue in self.venues.items():
            try:
                balances = venue.get_balance()
                for currency, bal in balances.items():
                    if currency in combined:
                        combined[currency].free += bal.free
                        combined[currency].used += bal.used
                        combined[currency].total += bal.total
                    else:
                        combined[currency] = UnifiedBalance(
                            currency=currency,
                            free=bal.free,
                            used=bal.used,
                            total=bal.total,
                        )
            except Exception as e:
                logger.warning(f"[MULTI_VENUE] Balance fetch failed for {venue_name}: {e}")
        return combined

    def get_total_equity(self) -> float:
        """Total equity across all venues (balance + unrealized PnL), in USD."""
        total = 0.0
        for venue_name, venue in self.venues.items():
            try:
                equity = 0.0
                if hasattr(venue, "get_account_summary"):
                    summary = venue.get_account_summary() or {}
                    equity = float(summary.get("equity", 0) or 0)
                    if equity <= 0:
                        equity = float(summary.get("balance", 0) or 0)
                if equity <= 0:
                    balances = venue.get_balance() or {}
                    equity = sum(
                        self._to_usd(venue, asset, bal.total)
                        for asset, bal in balances.items()
                    )
                total += equity
            except Exception as e:
                logger.debug(f"Equity calc failed for {venue_name}: {e}")
        return total

    @staticmethod
    def _to_usd(venue, asset: str, qty: float) -> float:
        """Convert a balance to USD terms using the venue's own tickers."""
        if qty <= 0:
            return 0.0
        if asset.upper() in ("USD", "USDT", "USDC", "FDUSD", "BUSD", "USD1", "DAI"):
            return float(qty)
        try:
            ticker = venue.get_ticker(f"{asset.upper()}USDT")
            if ticker and getattr(ticker, "last", 0) > 0:
                return float(qty) * float(ticker.last)
        except Exception:
            pass
        return 0.0

    def get_daily_pnl(self) -> float:
        """Realized PnL today."""
        self._check_daily_reset()
        return self.daily_realized_pnl

    def _check_daily_reset(self) -> None:
        """Reset daily PnL tracking at midnight UTC."""
        today = time.strftime("%Y-%m-%d")
        if today != self.daily_start_date:
            self.daily_start_date = today
            self.daily_realized_pnl = 0.0
            self.daily_start_equity = self.get_total_equity()
            self.daily_loss_limit_hit = False
            self.save_state()

    # ------------------------------------------------------------------------
    # State Persistence
    # ------------------------------------------------------------------------
    def _state_path(self) -> Path:
        """Path of the realized PnL state file (Stratex/state/realized_pnl.json)."""
        override = os.getenv("STRATEX_STATE_DIR")
        base = Path(override) if override else Path(__file__).resolve().parent.parent / "state"
        return base / "realized_pnl.json"

    def _load_state(self) -> None:
        """Restore booked PnL so a restart does not forget realized losses."""
        try:
            path = self._state_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                return
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh) or {}
            self.realized_pnl = float(data.get("realized_pnl", 0.0) or 0.0)
            self.daily_realized_pnl = float(data.get("daily_realized_pnl", 0.0) or 0.0)
            self.daily_start_date = str(data.get("daily_start_date", "") or "")
            logger.info(
                f"[MULTI_VENUE] Restored state: realized=${self.realized_pnl:+.2f} "
                f"daily=${self.daily_realized_pnl:+.2f} ({self.daily_start_date or 'n/a'})"
            )
        except Exception as e:
            logger.warning(f"[MULTI_VENUE] State load failed: {e}")

    def save_state(self) -> None:
        """Persist realized PnL. Never raises."""
        try:
            path = self._state_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "realized_pnl": round(self.realized_pnl, 8),
                "daily_realized_pnl": round(self.daily_realized_pnl, 8),
                "daily_start_date": self.daily_start_date,
                "updated_at": time.time(),
            }
            tmp = path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            os.replace(tmp, path)
        except Exception as e:
            logger.warning(f"[MULTI_VENUE] State save failed: {e}")

    # ------------------------------------------------------------------------
    # Position Management
    # ------------------------------------------------------------------------
    def get_all_positions(self) -> List[UnifiedPosition]:
        """Get all open positions across venues."""
        all_positions = []
        for venue_name, venue in self.venues.items():
            try:
                positions = venue.get_positions()
                for pos in positions:
                    if venue_name not in str(pos.exchange).split(":"):
                        pos.exchange = f"{venue_name}:{pos.exchange}"
                    all_positions.append(pos)
            except Exception as e:
                logger.warning(f"[MULTI_VENUE] Position fetch failed for {venue_name}: {e}")
        return all_positions

    def get_positions_by_venue(self, venue: str) -> List[UnifiedPosition]:
        """Get positions for specific venue."""
        if venue not in self.venues:
            return []
        try:
            return self.venues[venue].get_positions()
        except Exception:
            return []

    def get_position_count(self, venue: Optional[str] = None) -> int:
        """Count open positions."""
        if venue:
            return len(self.get_positions_by_venue(venue))
        return len(self.get_all_positions())

    def mark_exit(self, venue: str, symbol: str) -> None:
        """Record an exit so the symbol is cooldown-gated against instant re-entry."""
        self.last_exit[f"{venue}:{symbol}"] = time.time()
        logger.info(
            f"[MULTI_VENUE] Exit recorded for {venue}:{symbol}; "
            f"re-entry blocked for {self.symbol_cooldown_sec}s"
        )

    def can_open_position(self, venue: str) -> Tuple[bool, str]:
        """Check if we can open a new position on venue. NO RESTRICTIONS."""
        if venue not in self.venues:
            return False, f"Venue {venue} not available"

        # All limits removed for demo testing
        return True, "OK"

    # ------------------------------------------------------------------------
    # Market Data
    # ------------------------------------------------------------------------
    def get_ticker(self, venue: str, symbol: str) -> Optional[UnifiedTicker]:
        """Get ticker from specific venue."""
        if venue not in self.venues:
            return None
        try:
            return self.venues[venue].get_ticker(symbol)
        except Exception:
            return None

    def get_candles(self, venue: str, symbol: str, timeframe: str = "15m", limit: int = 100) -> List[Dict]:
        """Get candles from a venue, cached briefly so one scan cycle reuses them.

        A cycle asks for the same symbol/timeframe while running ~20 strategies;
        without this cache the venue is hit twice per pair (prefetch + scan).
        """
        if venue not in self.venues:
            return []
        key = f"{venue}:{symbol}:{timeframe}:{limit}"
        now = time.time()
        cached = self._candle_cache.get(key)
        if cached and (now - cached[0]) < 55:
            return cached[1]
        try:
            data = self.venues[venue].get_historical_data(symbol, timeframe, limit) or []
        except Exception as e:
            logger.warning(f"[MULTI_VENUE] Candle fetch failed for {venue}:{symbol}: {e}")
            return []
        if len(self._candle_cache) > 300:
            cutoff = now - 120
            self._candle_cache = {
                k: v for k, v in self._candle_cache.items() if v[0] >= cutoff
            }
        self._candle_cache[key] = (now, data)
        return data

    def get_candles_multi(self, symbol: str, timeframe: str = "15m", limit: int = 100) -> Dict[str, List[Dict]]:
        """Get candles from all venues that support the symbol."""
        result = {}
        for venue_name, venue in self.venues.items():
            try:
                candles = venue.get_historical_data(symbol, timeframe, limit)
                if candles:
                    result[venue_name] = candles
            except Exception:
                pass
        return result

    # ------------------------------------------------------------------------
    # Order Execution
    # ------------------------------------------------------------------------
    def place_order(
        self,
        venue: str,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        strategy_name: str = "",
        timeframe: str = "",
    ) -> Optional[UnifiedOrderResult]:
        """Place order on specific venue with native SL/TP. NO RESTRICTIONS."""
        if venue not in self.venues:
            logger.error(f"[MULTI_VENUE] Venue {venue} not available")
            return None

        # All gates removed for demo testing - unlimited trading
        can_open, reason = self.can_open_position(venue)
        if not can_open:
            logger.warning(f"[MULTI_VENUE] Cannot open position on {venue}: {reason}")
            return None

        venue_obj = self.venues[venue]
        try:
            result = venue_obj.place_order(
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=price,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )

            if result and result.status in ("FILLED", "PARTIALLY_FILLED", "PENDING"):
                entry_price = result.avg_price or price or 0.0
                self.order_history.append({
                    "timestamp": time.time(),
                    "venue": venue,
                    "symbol": symbol,
                    "side": side,
                    "quantity": quantity,
                    "price": entry_price,
                    "order_id": result.order_id,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                })
                logger.info(f"[MULTI_VENUE] Order placed: {venue} {symbol} {side} {quantity} @ {result.avg_price}")

                # Track the filled position so reconciliation can detect a
                # broker-side SL/TP exit and book its PnL.
                if result.status in ("FILLED", "PARTIALLY_FILLED"):
                    filled_qty = result.executed_qty or quantity
                    self.positions[key] = UnifiedPosition(
                        symbol=symbol,
                        side="LONG" if side in ("BUY", "LONG") else "SHORT",
                        quantity=filled_qty,
                        entry_price=entry_price,
                        mark_price=entry_price,
                        unrealized_pnl=0.0,
                        exchange=venue,
                        stop_loss=float(stop_loss or 0.0),
                        take_profit=float(take_profit or 0.0),
                        strategy=strategy_name,
                        timeframe=timeframe,
                    )
                    self.position_opened_at[key] = int(time.time() * 1000)
                    self.position_strategy[key] = strategy_name
                    self.position_timeframe[key] = timeframe

                # Send Telegram notification
                if result.status == "FILLED" and self.telegram_bot and self.telegram_bot.is_configured:
                    try:
                        emoji = "🟢" if side in ("BUY", "LONG") else "🔴"
                        self.telegram_bot.send_message(
                            f"{emoji} <b>TRADE EXECUTED</b>\n\n"
                            f"• <b>Venue</b>: <code>{venue}</code>\n"
                            f"• <b>Symbol</b>: <code>{symbol}</code>\n"
                            f"• <b>Side</b>: <code>{side}</code>\n"
                            f"• <b>Qty</b>: <code>{quantity}</code>\n"
                            f"• <b>Entry</b>: <code>{entry_price:.5f}</code>\n"
                            f"• <b>SL</b>: <code>{(stop_loss or 0.0):.5f}</code>\n"
                            f"• <b>TP</b>: <code>{(take_profit or 0.0):.5f}</code>\n"
                            f"• <b>Ticket</b>: <code>{result.order_id}</code>\n"
                            f"• <b>Status</b>: <code>{result.status}</code>"
                        )
                    except Exception as e:
                        logger.debug(f"Telegram notification failed: {e}")

            return result
        except Exception as e:
            logger.error(f"[MULTI_VENUE] Order failed on {venue}: {e}")
            return None

    def close_position(self, venue: str, symbol: str) -> bool:
        """Close position on specific venue."""
        if venue not in self.venues:
            return False
        try:
            positions = self.venues[venue].get_positions()
            for pos in positions:
                if pos.symbol == symbol:
                    # Place opposite order to close
                    close_side = "SELL" if pos.side in ("LONG", "BUY") else "BUY"
                    result = self.venues[venue].place_order(
                        symbol=symbol,
                        side=close_side,
                        order_type="MARKET",
                        quantity=pos.quantity,
                    )
                    if result is None:
                        logger.warning(
                            f"[MULTI_VENUE] Close order failed for {venue}:{symbol}; "
                            f"protective orders left in place"
                        )
                        return False

                    # Cancel the SL/TP conditional orders only once the position
                    # is actually closed, so they can't fire against no position.
                    adapter = self.venues[venue]
                    if hasattr(adapter, "cancel_protective_orders"):
                        try:
                            cancelled = adapter.cancel_protective_orders(symbol)
                            if cancelled:
                                logger.info(
                                    f"[MULTI_VENUE] Cancelled {cancelled} protective order(s) on {symbol}"
                                )
                        except Exception as e:
                            logger.debug(
                                f"[MULTI_VENUE] Protective order cleanup failed on {symbol}: {e}"
                            )

                    # Stop tracking it and start the re-entry cooldown.
                    key = f"{venue}:{symbol}"
                    self.position_opened_at.pop(key, None)
                    self.positions.pop(key, None)
                    self.mark_exit(venue, symbol)

                    if self.telegram_bot and self.telegram_bot.is_configured:
                        try:
                            emoji = "💰" if pos.unrealized_pnl >= 0 else "🔻"
                            self.telegram_bot.send_message(
                                f"{emoji} <b>POSITION CLOSED</b>\n\n"
                                f"• <b>Venue</b>: <code>{venue}</code>\n"
                                f"• <b>Symbol</b>: <code>{symbol}</code>\n"
                                f"• <b>Side</b>: <code>{pos.side}</code>\n"
                                f"• <b>Qty</b>: <code>{pos.quantity}</code>\n"
                                f"• <b>Entry</b>: <code>{pos.entry_price:.5f}</code>\n"
                                f"• <b>Exit</b>: <code>{pos.mark_price:.5f}</code>\n"
                                f"• <b>Realized PnL</b>: {emoji} <code>${pos.unrealized_pnl:+.2f}</code>\n"
                                f"• <b>Ticket</b>: <code>{pos.position_id}</code>"
                            )
                        except Exception as e:
                            logger.debug(f"Telegram notification failed: {e}")
                    return result is not None
            return False
        except Exception as e:
            logger.error(f"[MULTI_VENUE] Close position failed: {e}")
            return False

    # ------------------------------------------------------------------------
    # Risk Management
    # ------------------------------------------------------------------------
    def update_realized_pnl(self, venue: str, symbol: str, realized_pnl: float) -> None:
        """Record realized PnL from closed position."""
        self.realized_pnl += realized_pnl
        self.daily_realized_pnl += realized_pnl
        self.save_state()
        logger.info(f"[MULTI_VENUE] Realized PnL: {venue}:{symbol} = ${realized_pnl:+.2f} (Total: ${self.realized_pnl:+.2f})")

    def get_risk_summary(self) -> Dict[str, Any]:
        """Get comprehensive risk summary."""
        self._check_daily_reset()
        
        total_equity = 0.0
        total_balance = 0.0
        unrealized = 0.0
        for venue_name, venue in self.venues.items():
            try:
                summary = {}
                if hasattr(venue, "get_account_summary"):
                    summary = venue.get_account_summary() or {}
                equity = float(summary.get("equity", 0) or 0)
                balance = float(summary.get("balance", 0) or 0)

                if equity <= 0 and balance <= 0:
                    # Venue without an account summary: value wallets in USD.
                    for asset, bal in (venue.get_balance() or {}).items():
                        balance += self._to_usd(venue, asset, bal.total)
                    equity = balance

                total_equity += equity
                total_balance += balance if balance > 0 else equity
                if "unrealized_pnl" in summary:
                    unrealized += float(summary.get("unrealized_pnl", 0) or 0)
                else:
                    unrealized += equity - balance
            except Exception as e:
                logger.debug(f"Risk valuation failed for {venue_name}: {e}")

        return {
            "total_equity": total_equity,
            "total_balance": total_balance,
            "unrealized_pnl": unrealized,
            "realized_pnl": self.realized_pnl,
            "daily_realized_pnl": self.daily_realized_pnl,
            "daily_loss_pct": (self.daily_start_equity - total_equity) / self.daily_start_equity if self.daily_start_equity > 0 else 0,
            "positions": self.get_position_count(),
            "max_positions": self.max_total_positions,
            "daily_loss_limit_hit": self.daily_loss_limit_hit,
            "venues": {v: self.get_position_count(v) for v in self.venues},
        }

    # ------------------------------------------------------------------------
    # Symbol Universe
    # ------------------------------------------------------------------------
    def get_tradeable_symbols(self) -> Dict[str, List[str]]:
        """Get tradeable symbols per venue."""
        # This would ideally fetch from exchange info
        # For now, return known symbols
        return {
            "binance_futures": [
                "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
                "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "MATICUSDT", "DOTUSDT",
                "LTCUSDT", "LINKUSDT", "BCHUSDT", "UNIUSDT", "ATOMUSDT",
            ],
            "oanda": [
                "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
                "EURGBP", "EURJPY", "EURAUD", "EURCAD", "EURCHF",
                "GBPJPY", "GBPAUD", "GBPCAD", "GBPCHF",
                "AUDJPY", "CADJPY", "CHFJPY", "NZDJPY",
                "XAUUSD", "XAGUSD", "US30", "US500", "USTEC", "DE30", "UK100",
                "USOIL", "UKOIL", "BCOUSD", "NATGAS",
            ],
        }

    def get_symbol_venue(self, symbol: str) -> Optional[str]:
        """Determine which venue trades a symbol."""
        symbols = self.get_tradeable_symbols()
        for venue, syms in symbols.items():
            if symbol in syms:
                return venue
        return None


# Factory function
def create_multi_venue_engine(
    binance_api_key: str = "",
    binance_secret: str = "",
    binance_testnet: bool = True,
    oanda_api_key: str = "",
    oanda_account_id: str = "",
    oanda_practice: bool = True,
    **kwargs
) -> "MultiVenueEngine":
    """Create multi-venue engine from environment or parameters."""
    return MultiVenueEngine(
        binance_api_key=binance_api_key,
        binance_secret=binance_secret,
        binance_testnet=binance_testnet,
        oanda_api_key=oanda_api_key,
        oanda_account_id=oanda_account_id,
        oanda_practice=oanda_practice,
        **kwargs
    )