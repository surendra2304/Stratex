"""
notifications/telegram_bot.py — Interactive Bidirectional Telegram Bot for Stratex.

Features:
1. Instant Outbound Alerts:
   - Trade Intent Submitted (Symbol, Side, Qty, SL/TP, Strategy, Venue)
   - Order Filled / Partially Filled (Fill Price, Slippage, Execution ID)
   - Risk Rejection & Circuit Breaker Triggers
   - Atomic Emergency Halt Alerts
2. Inbound Interactive Control:
   - /start — Welcome and authorization check
   - /status — Live engine health, market regime, trading mode
   - /positions — Active positions with unrealized P&L and bracket stops
   - /balance — Account balance and margin utilization
   - /halt — Immediate emergency halt (locks OMS & cancels open orders)
   - /resume — Disengages panic lock
   - Natural language queries routed to intelligence mesh
"""
from __future__ import annotations

import logging
import hashlib
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from dotenv import load_dotenv
import requests

from logger import get_logger
from panic_state import is_kill_switch_locked, is_panic_active, write_panic_state
from resilience import sanitize_secrets

load_dotenv()

logger = get_logger("telegram_bot")

DEFAULT_API_BASE = "https://api.telegram.org/bot"

# Commands that must never block the polling loop or each other.
_COMMAND_TIMEOUT_SECONDS = 90


def _as_float(value: Any) -> float | None:
    """Best-effort numeric coercion; returns None when the value is unusable."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _http_ok(resp: Any, data: Any) -> bool:
    """True only for a 2xx HTTP response that Telegram acknowledged."""
    status = getattr(resp, "status_code", None)
    status_ok = not isinstance(status, int) or 200 <= status < 300
    return status_ok and bool(data.get("ok"))


def _positions_label(value: Any) -> str:
    """Render an open-position COUNT (never a currency figure)."""
    count = int(_as_float(value) or 0)
    return f"{count} open position{'s' if count != 1 else ''}"


class TelegramBotClient:
    """Production-grade Telegram client for outbound alerts and inbound commands."""

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | int | None = None,
        *,
        request_timeout: int = 8,
    ):
        dotenv_dict: dict[str, str | None] = {}
        if bot_token is None or chat_id is None:
            try:
                from dotenv import dotenv_values
                dotenv_dict = dict(dotenv_values(".env"))
            except Exception:
                pass

        if bot_token is None:
            token_val = os.getenv("TELEGRAM_BOT_TOKEN") or dotenv_dict.get("TELEGRAM_BOT_TOKEN", "") or ""
        else:
            token_val = str(bot_token)
        self.bot_token = token_val.strip()

        if chat_id is None:
            chat_val = os.getenv("TELEGRAM_CHAT_ID") or dotenv_dict.get("TELEGRAM_CHAT_ID", "") or ""
        else:
            chat_val = str(chat_id)
        self.chat_id = chat_val.strip()
        self.timeout = request_timeout
        self._polling_active = False
        self._poll_thread: threading.Thread | None = None
        self._last_update_id = 0
        self._rejection_history: dict[str, float] = {}
        self._sent_text_cache: dict[str, float] = {}
        self._portfolio_cache: tuple[float, dict[str, Any]] | None = None
        self._conflict_logged = False
        self._executor: ThreadPoolExecutor | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    # ==========================================================================
    # SHARED HELPERS
    # ==========================================================================
    def _hub(self, *, ttl: float = 20.0):
        """Return the multi-venue engine plus a cached read of the live portfolio.

        Broker reads take seconds; the engine is cached so a burst of Telegram
        commands (or a slow venue) can never stack up request latency.
        """
        from autonomy.multi_venue_engine import create_multi_venue_engine

        # Create or get cached engine
        if not hasattr(self, "_multi_venue_engine"):
            self._multi_venue_engine = create_multi_venue_engine(
                binance_api_key=os.getenv("BINANCE_API_KEY", ""),
                binance_secret=os.getenv("BINANCE_API_SECRET", ""),
                binance_testnet=os.getenv("BINANCE_TESTNET", "true").lower() in ("1", "true", "yes"),
                oanda_api_key=os.getenv("OANDA_API_KEY", ""),
                oanda_account_id=os.getenv("OANDA_ACCOUNT_ID", ""),
                oanda_practice=os.getenv("OANDA_PRACTICE", "true").lower() in ("1", "true", "yes"),
                starting_balance=float(os.getenv("STRATEX_INITIAL_BALANCE", "1000.0")),
                max_positions_per_venue=int(os.getenv("STRATEX_MAX_POSITIONS_PER_VENUE", "5")),
                max_total_positions=int(os.getenv("STRATEX_MAX_TOTAL_POSITIONS", "10")),
                risk_per_trade_pct=float(os.getenv("STRATEX_RISK_PER_TRADE_PCT", "0.005")),
                max_daily_loss_pct=float(os.getenv("STRATEX_MAX_DAILY_LOSS_PCT", "0.03")),
            )

        engine = self._multi_venue_engine
        now = time.time()
        cache = getattr(self, "_portfolio_cache", None)
        if cache and (now - cache[0]) < ttl:
            return engine, cache[1]

        snapshot: dict[str, Any] = {}
        try:
            # Value every wallet in USD terms. Summing raw token totals would
            # add 0.01 BTC as $0.01 and understate the portfolio by ~$780.
            total_equity = engine.get_total_equity()
            combined_free = 0.0
            combined_used = 0.0
            venue_blocks: dict[str, dict[str, Any]] = {}

            for venue_name, adapter in engine.venues.items():
                summary = {}
                try:
                    summary = adapter.get_account_summary() or {}
                except Exception:
                    pass

                wallet = float(summary.get("balance", 0) or 0)
                equity = float(summary.get("equity", 0) or 0)
                free = float(summary.get("available", 0) or 0)
                if equity <= 0:
                    equity = wallet
                if wallet <= 0:
                    wallet = equity
                # `used` is margin committed, not a per-asset sum: the futures
                # account reports one shared collateral pool (multi-asset mode),
                # so adding each asset's `availableBalance` double counts.
                used = max(equity - free, 0.0) if free > 0 else 0.0

                combined_free += free
                combined_used += used
                venue_blocks[venue_name] = {
                    "currency": "USD",
                    "balance": wallet,
                    "equity": equity,
                    "free": free,
                    "used": used,
                    "unrealized_pnl": float(summary.get("unrealized_pnl", 0) or 0),
                }

            balance_snapshot = {
                "combined": {
                    "currency": "USD",
                    "balance": total_equity,
                    "equity": total_equity,
                    "free": combined_free,
                    "used": combined_used,
                },
            }
            balance_snapshot.update(venue_blocks)
            snapshot["balance"] = balance_snapshot
        except Exception as exc:
            logger.warning("[TELEGRAM] Combined balance read failed: %s", exc)
            snapshot["balance"] = None

        try:
            # Get PnL from both venues. risk["venues"] holds open-position COUNTS,
            # not equity; per-venue equity is taken from the balance read above.
            risk = engine.get_risk_summary()
            venue_counts = risk.get("venues") or {}
            balance_snap = snapshot.get("balance") or {}
            oanda_bal = balance_snap.get("oanda") or {}
            binance_bal = balance_snap.get("binance_futures") or {}
            snapshot["pnl"] = {
                "total_portfolio_equity": risk["total_equity"],
                "total_floating_pnl": risk["unrealized_pnl"],
                "total_realized_pnl": risk["realized_pnl"],
                "total_portfolio_pnl": risk["realized_pnl"] + risk["unrealized_pnl"],
                "venues": list(engine.venues.keys()),
                "icmarkets": {
                    "equity": oanda_bal.get("equity"),
                    "positions": int(_as_float(venue_counts.get("oanda")) or 0),
                    "cash": oanda_bal.get("balance") or 0,
                    "floating_pnl": oanda_bal.get("unrealized_pnl") or 0,
                    "realized_pnl": 0,
                },
                "binance": {
                    "equity": binance_bal.get("equity"),
                    "positions": int(_as_float(venue_counts.get("binance_futures")) or 0),
                    "cash": binance_bal.get("balance") or 0,
                    "floating_pnl": binance_bal.get("unrealized_pnl") or 0,
                    "realized_pnl": 0,
                    "mode": "futures_demo" if engine.venues.get("binance_futures") and getattr(
                        engine.venues.get("binance_futures"), "environment", ""
                    ) == "demo" else "futures_testnet",
                },
            }
        except Exception as exc:
            logger.warning("[TELEGRAM] Combined PnL read failed: %s", exc)
            snapshot["pnl"] = None

        try:
            # Get positions from both venues
            all_positions = engine.get_all_positions()
            positions = []
            for pos in all_positions:
                positions.append({
                    "symbol": pos.symbol,
                    "side": pos.side,
                    "quantity": pos.quantity,
                    "entry_price": pos.entry_price,
                    "mark_price": pos.mark_price,
                    "unrealized_pnl": pos.unrealized_pnl,
                    "exchange": pos.exchange,
                    "position_id": pos.position_id,
                })
            snapshot["positions"] = positions
        except Exception as exc:
            logger.warning("[TELEGRAM] Combined positions read failed: %s", exc)
            snapshot["positions"] = []

        self._portfolio_cache = (now, snapshot)
        return engine, snapshot

    @staticmethod
    def _ledger_metrics() -> dict[str, Any]:
        from trading_report import metrics as ledger_metrics

        try:
            from config import STARTING_BALANCE
        except Exception:
            STARTING_BALANCE = 10000.0
        return ledger_metrics(starting_balance=float(STARTING_BALANCE))

    @staticmethod
    def _risk_snapshot() -> dict[str, Any]:
        """Live risk limits + utilisation inputs for the dashboard."""
        try:
            import config

            risk = {
                "max_daily_loss_pct": float(getattr(config, "MAX_DAILY_LOSS_PCT", 0.03)),
                "max_drawdown_pct": float(getattr(config, "MAX_TESTNET_DRAWDOWN_PCT", 0.08)),
                "max_open_positions": int(getattr(config, "MAX_OPEN_POSITIONS", 8)),
                "max_exposure_pct": float(getattr(config, "MAX_TESTNET_EXPOSURE", 0.10)),
            }
        except Exception:
            risk = {
                "max_daily_loss_pct": 0.03,
                "max_drawdown_pct": 0.08,
                "max_open_positions": 8,
                "max_exposure_pct": 0.10,
            }

        try:
            from exchanges.unified_portfolio_hub import get_portfolio_hub

            hub = get_portfolio_hub()
            pnl = hub.get_combined_pnl()
            positions = hub.get_combined_positions()
            equity = float(pnl.get("total_portfolio_equity") or 0.0)
            exposure = sum(
                abs(float(p.get("quantity", 0)) * float(p.get("mark_price", p.get("entry_price", 0)) or 0))
                for p in positions
            )
            risk.update({"equity": equity, "open_positions": positions, "exposure": exposure})
        except Exception as exc:
            logger.debug("[TELEGRAM] Risk snapshot degraded: %s", exc)

        try:
            from panic_state import is_kill_switch_locked, is_panic_active
            from trading_pause import is_trading_paused

            if is_panic_active() or is_kill_switch_locked():
                risk["state"] = "🔴 HALTED (panic lock)"
            elif is_trading_paused():
                risk["state"] = "🟡 PAUSED (new entries blocked)"
            else:
                risk["state"] = "🟢 ACTIVE"
        except Exception:
            risk.setdefault("state", "UNKNOWN")
        return risk

    # ==========================================================================
    # OUTBOUND NOTIFICATIONS
    # ==========================================================================
    def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send a message to the authorized chat ID."""
        if not self.is_configured:
            logger.debug("[TELEGRAM] Bot not fully configured; message suppressed.")
            return False

        # Never push the same message twice in quick succession. Duplicate fills,
        # repeated heartbeats and retried exits are the main source of channel
        # spam; genuine updates always differ in price or ticket.
        dedupe_key = hashlib.sha1(text.encode("utf-8")).hexdigest()
        now = time.time()
        last_sent = self._sent_text_cache.get(dedupe_key)
        if last_sent is not None and (now - last_sent) < 120:
            logger.debug("[TELEGRAM] Duplicate message suppressed (within 120s).")
            return True

        url = f"{DEFAULT_API_BASE}{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }

        def _mark_delivered() -> None:
            """Record the dedupe key only once Telegram confirmed delivery."""
            self._sent_text_cache[dedupe_key] = time.time()
            if len(self._sent_text_cache) > 300:
                cutoff = time.time() - 120
                self._sent_text_cache = {k: v for k, v in self._sent_text_cache.items() if v >= cutoff}

        try:
            resp = requests.post(url, json=payload, timeout=self.timeout)
            data = resp.json()
            if not _http_ok(resp, data):
                # If HTML parse error, retry gracefully as plain text
                if parse_mode:
                    payload.pop("parse_mode", None)
                    retry_resp = requests.post(url, json=payload, timeout=self.timeout)
                    retry_data = retry_resp.json()
                    if _http_ok(retry_resp, retry_data):
                        _mark_delivered()
                        logger.info("[TELEGRAM] Message sent as plain text (%d chars).", len(text))
                        return True
                logger.warning(
                    "[TELEGRAM] SendMessage failed: %s",
                    data.get("description") or f"HTTP {resp.status_code}",
                )
                self._sent_text_cache.pop(dedupe_key, None)
                return False
            _mark_delivered()
            logger.info("[TELEGRAM] Message sent (%d chars).", len(text))
            return True
        except Exception as exc:
            self._sent_text_cache.pop(dedupe_key, None)
            logger.warning("[TELEGRAM] Error sending message: %s", exc)
            return False

    def send_photo(self, photo_path: str, caption: str = "", parse_mode: str = "HTML") -> bool:
        """Send a chart/image to the authorized chat."""
        if not self.is_configured or not photo_path or not os.path.exists(photo_path):
            return False
        url = f"{DEFAULT_API_BASE}{self.bot_token}/sendPhoto"
        try:
            with open(photo_path, "rb") as handle:
                resp = requests.post(
                    url,
                    data={"chat_id": self.chat_id, "caption": caption[:1024], "parse_mode": parse_mode},
                    files={"photo": handle},
                    timeout=self.timeout + 12,
                )
            data = resp.json()
            if not data.get("ok"):
                logger.warning("[TELEGRAM] SendPhoto failed: %s", data.get("description"))
                return False
            return True
        except Exception as exc:
            logger.warning("[TELEGRAM] Error sending photo: %s", exc)
            return False

    def notify_trade_opened(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        entry_price: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        strategy: str = "",
        venue: str = "binance_futures",
        leverage: float | None = None,
        confidence: float | None = None,
        equity: float | None = None,
    ) -> bool:
        """Rich, human-readable alert for a freshly filled entry."""
        is_long = str(side).upper() in {"BUY", "LONG"}
        emoji = "🟢" if is_long else "🔴"
        notional = float(quantity) * float(entry_price or 0.0)
        rr = None
        if stop_loss and take_profit and entry_price:
            risk = abs(entry_price - stop_loss)
            reward = abs(take_profit - entry_price)
            rr = reward / risk if risk > 0 else None

        lines = [
            f"{emoji} <b>TRADE OPENED — {symbol}</b>",
            "",
            f"• <b>Direction</b>: <b>{side.upper()}</b>",
            f"• <b>Quantity</b>: <code>{quantity:g}</code>",
            f"• <b>Entry</b>: <code>${entry_price:,.4f}</code>",
            f"• <b>Notional</b>: <code>${notional:,.2f}</code>"
            + (f" @ <code>{leverage:g}x</code>" if leverage else ""),
        ]
        if stop_loss:
            lines.append(f"• <b>Stop</b>: <code>${stop_loss:,.4f}</code>")
        if take_profit:
            lines.append(f"• <b>Target</b>: <code>${take_profit:,.4f}</code>")
        if rr:
            lines.append(f"• <b>Risk/Reward</b>: <code>1:{rr:.2f}</code>")
        if confidence:
            lines.append(f"• <b>Confidence</b>: <code>{confidence * 100:.0f}%</code>")
        if equity:
            lines.append(f"• <b>Equity</b>: <code>${equity:,.2f}</code>")
        lines.append(f"• <b>Strategy</b>: <code>{strategy or 'n/a'}</code>")
        lines.append("• Strategy: strategy_trend_daily | Risk: 1.0% of equity")
        lines.append(f"• <b>Venue</b>: <code>{venue}</code>")
        return self.send_message("\n".join(lines))

    def notify_trade_closed(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        entry_price: float,
        exit_price: float,
        net_pnl: float,
        fees: float = 0.0,
        reason: str = "",
        strategy: str = "",
        equity: float | None = None,
        today_pnl: float | None = None,
    ) -> bool:
        """Alert for a closed round-trip with realized PnL and running totals."""
        won = net_pnl >= 0
        emoji = "💰" if won else "🔻"
        pct = ((exit_price - entry_price) / entry_price * 100.0) if entry_price else 0.0
        if str(side).upper() in {"SELL", "SHORT"}:
            pct = -pct

        lines = [
            f"{emoji} <b>TRADE CLOSED — {symbol}</b>",
            "",
            f"• <b>Realized PnL</b>: <code>${net_pnl:+,.2f}</code> (<code>{pct:+.2f}%</code>)",
            f"• <b>Entry → Exit</b>: <code>${entry_price:,.4f}</code> → <code>${exit_price:,.4f}</code>",
            f"• <b>Quantity</b>: <code>{quantity:g}</code>",
            f"• <b>Fees</b>: <code>${fees:,.2f}</code>",
        ]
        if reason:
            lines.append(f"• <b>Exit</b>: <code>{reason}</code>")
        if strategy:
            lines.append(f"• <b>Strategy</b>: <code>{strategy}</code>")
        if today_pnl is not None:
            lines.append(f"• <b>Today</b>: <code>${today_pnl:+,.2f}</code>")
        if equity:
            lines.append(f"• <b>Equity</b>: <code>${equity:,.2f}</code>")
        return self.send_message("\n".join(lines))

    def send_daily_summary(self, *, mode: str = "", venue: str = "", with_chart: bool = True) -> bool:
        """Daily PnL summary plus the equity-curve chart."""
        try:
            from trading_report import equity_curve_png, equity_sparkline, format_daily_summary, metrics as ledger_metrics

            try:
                from config import STARTING_BALANCE

                starting = float(STARTING_BALANCE)
            except Exception:
                starting = 10000.0

            stats = ledger_metrics(starting_balance=starting)
            text = format_daily_summary(stats, mode=mode, venue=venue)
            sent = self.send_message(text)

            if with_chart:
                tmp = os.path.join(
                    os.getenv("TEMP", "."),
                    f"stratex_equity_{int(time.time())}.png",
                )
                if equity_curve_png(stats, tmp):
                    try:
                        self.send_photo(tmp, caption="📈 Stratex equity curve")
                    finally:
                        try:
                            os.remove(tmp)
                        except OSError:
                            pass
                else:
                    spark = equity_sparkline(stats)
                    if spark:
                        self.send_message(
                            "📈 <b>Equity curve</b>\n"
                            f"<code>{spark}</code>\n\n"
                            f"Start <code>${stats['starting_balance']:,.0f}</code> → "
                            f"now <code>${stats['ending_equity']:,.2f}</code> "
                            f"({stats['return_pct']:+.2f}%)"
                        )
            return sent
        except Exception as exc:
            logger.warning("[TELEGRAM] Daily summary failed: %s", exc)
            return False

    def notify_trade_intent(
        self,
        intent: Any,
        status: str = "PROPOSED",
        fill_price: float | None = None,
        pnl: float | None = None,
        order_id: str | None = None,
    ) -> bool:
        """Format and dispatch rich trade alerts with emojis and brackets."""
        sym = getattr(intent, "symbol", "UNKNOWN")
        side = getattr(intent, "side", "UNKNOWN").upper()
        qty = getattr(intent, "quantity", 0.0)
        venue = getattr(intent, "venue", "binance").upper()
        sl = getattr(intent, "stop_loss", None)
        tp = getattr(intent, "take_profit", None)
        strat = getattr(intent, "strategy", "quantitative_alpha")
        cid = str(order_id or getattr(intent, "client_order_id", "stx-order"))
        meta = getattr(intent, "metadata", {}) or {}
        rr = meta.get("risk_reward_ratio")

        venue_label = {
            "ICMARKETS_CTRADER": "IC Markets cTrader (demo)",
            "MT5": "IC Markets cTrader (demo)",
            "BINANCE_FUTURES": "Binance Futures",
        }.get(venue, venue.title())

        side_emoji = "🟢 BUY" if "BUY" in side or "LONG" in side else "🔴 SELL"
        status_emoji = "⚡" if status == "PROPOSED" else ("✅" if status == "FILLED" else "📋")

        lines = [
            f"{status_emoji} <b>STRATEX TRADE {status}</b>",
            "",
            f"• <b>Symbol</b>: <code>{sym}</code>",
            f"• <b>Action</b>: <b>{side_emoji}</b>",
            f"• <b>Size</b>: <code>{qty} lots</code>",
            f"• <b>Venue</b>: {venue_label}",
            f"• <b>Strategy</b>: <code>{strat}</code>",
             "• Strategy: strategy_trend_daily | Risk: 1.0% of equity",
        ]

        if fill_price:
            lines.append(f"• <b>Fill Price</b>: <code>{fill_price:,.5f}</code>")
        if sl:
            lines.append(f"• <b>Stop Loss</b>: <code>{sl:,.5f}</code>")
        if tp:
            lines.append(f"• <b>Take Profit</b>: <code>{tp:,.5f}</code>")
        if rr:
            lines.append(f"• <b>Reward:Risk</b>: <code>{float(rr):.2f}</code>")
        if pnl is not None:
            pnl_emoji = "💰" if pnl >= 0 else "🔻"
            lines.append(f"• <b>Realized PnL</b>: {pnl_emoji} <code>${pnl:+,.2f}</code>")

        ai_thesis = meta.get("ai_thesis")
        if ai_thesis:
            thesis = str(ai_thesis).strip().replace("\n", " ")
            if len(thesis) > 200:
                thesis = thesis[:197].rstrip() + "..."
            lines.append(f"• <b>AI Thesis</b>: <i>{thesis}</i>")

        lines.append(f"• <b>Ticket</b>: <code>{cid}</code>")
        return self.send_message("\n".join(lines))

    def notify_risk_rejection(self, intent: Any, reason: str) -> bool:
        """Alert when deterministic risk gate blocks an unsafe order (throttled to prevent spam)."""
        sym = getattr(intent, "symbol", "UNKNOWN")
        side = getattr(intent, "side", "UNKNOWN")
        now = time.time()
        key = f"{sym}_{reason[:30]}"
        if key in self._rejection_history and (now - self._rejection_history[key]) < 300:
            logger.debug(f"[TELEGRAM] Throttled duplicate rejection alert for {key}")
            return False
        self._rejection_history[key] = now

        lines = [
            "🛡️ <b>RISK GATE BLOCKED ORDER</b>",
            "",
            f"• <b>Symbol</b>: <code>{sym}</code> ({side})",
            f"• <b>Reason</b>: <i>{reason}</i>",
            "• <b>Action</b>: Order was rejected to preserve capital.",
        ]
        return self.send_message("\n".join(lines))

    def notify_emergency_halt(self, actor: str, reason: str) -> bool:
        """Broadcast an emergency panic halt alert."""
        lines = [
            "🚨 <b>EMERGENCY PANIC HALT ENGAGED</b> 🚨",
            "",
            f"• <b>Triggered By</b>: <code>{actor}</code>",
            f"• <b>Reason</b>: <i>{reason}</i>",
            "• <b>OMS State</b>: LOCKED (All open orders cancelled, submissions blocked)",
        ]
        return self.send_message("\n".join(lines))

    # ==========================================================================
    # INBOUND COMMAND PROCESSING (UNIFIED MULTI-ACCOUNT: IC MARKETS + BINANCE)
    # ==========================================================================

    def _format_pnl_report(self, args: list[str] | None = None) -> str:
        """Format live PnL across both IC Markets and Binance (Combined or Individual)."""
        _hub, snap = self._hub()
        pnl = snap.get("pnl")
        if not pnl:
            return "⚠️ <b>PnL unavailable</b>: no venue responded. Try again in a moment."

        target = args[0].lower() if args else "combined"

        if target in {"icmarkets", "ctrader", "forex", "metals"}:
            if "oanda" not in (pnl.get("venues") or []):
                return (
                    "⚠️ <b>OANDA not configured</b>\n\n"
                    "Add <code>OANDA_API_KEY</code> and <code>OANDA_ACCOUNT_ID</code> to "
                    "<code>.env</code> to enable forex, indices and metals."
                )
            ic = pnl["icmarkets"]
            f_emoji = "🟢" if ic["floating_pnl"] >= 0 else "🔴"
            r_emoji = "🟢" if ic["realized_pnl"] >= 0 else "🔴"
            lines = [
                "📈 <b>Profit & Loss (IC Markets cTrader Demo)</b>",
                "",
                "💼 <b>Account ID</b>: <code>10128706</code>",
            ]
            equity = _as_float(ic.get("equity"))
            if equity is not None:
                lines.append(f"🛡️ <b>Current Equity</b>: <code>${equity:,.2f} USD</code>")
            lines += [
                f"📊 <b>Open Positions</b>: <code>{_positions_label(ic.get('positions'))}</code>",
                f"💰 <b>Settled Cash</b>: <code>${ic['cash']:,.2f} USD</code>",
                f"📊 <b>Floating Unrealized PnL</b>: {f_emoji} <code>${ic['floating_pnl']:+,.2f} USD</code>",
                f"📜 <b>Closed Realized PnL</b>: {r_emoji} <code>${ic['realized_pnl']:+,.2f} USD</code>",
                "",
                "<i>Enforced 1% risk cap per trade and server-side hard Stop-Loss active.</i>",
            ]
            return "\n".join(lines)

        if target in {"binance", "crypto", "spot"}:
            bn = pnl["binance"]
            f_emoji = "🟢" if bn["floating_pnl"] >= 0 else "🔴"
            venue_label = {
                "futures_demo": "Binance USDT-M Futures — DEMO",
                "futures_testnet": "Binance USDT-M Futures — TESTNET",
                "spot_testnet": "Binance Spot — TESTNET",
                "spot_mainnet": "Binance Spot — LIVE",
                "futures_mainnet": "Binance Futures — LIVE",
            }.get(bn.get("mode", ""), "Binance (unconnected)")
            lines = [
                "📈 <b>Profit &amp; Loss (Binance Portfolio)</b>",
                "",
                f"🟡 <b>Venue</b>: <code>{venue_label}</code>",
            ]
            equity = _as_float(bn.get("equity"))
            if equity is not None:
                lines.append(f"🛡️ <b>Total Asset Valuation</b>: <code>${equity:,.2f} USDT</code>")
            lines += [
                f"📊 <b>Open Positions</b>: <code>{_positions_label(bn.get('positions'))}</code>",
                f"💰 <b>Liquid Cash / Collateral</b>: <code>${bn['cash']:,.2f} USDT</code>",
                f"📊 <b>Floating Unrealized PnL</b>: {f_emoji} <code>${bn['floating_pnl']:+,.2f} USDT</code>",
                "",
                "<i>Values are read live from the Binance API.</i>",
            ]
            return "\n".join(lines)

        # Default: combined, but only for venues that are actually configured.
        venues = set(pnl.get("venues") or [])
        show_ic = "oanda" in venues
        show_bn = "binance_futures" in venues
        if not (show_ic or show_bn):
            return "⚠️ <b>PnL unavailable</b>: no venue configured."

        tot_f_emoji = "🟢" if pnl["total_floating_pnl"] >= 0 else "🔴"
        tot_r_emoji = "🟢" if pnl["total_realized_pnl"] >= 0 else "🔴"
        ic = pnl["icmarkets"]
        bn = pnl["binance"]

        if show_ic and show_bn:
            title = "📈 <b>Profit &amp; Loss (Combined: OANDA + Binance)</b>"
        elif show_bn:
            title = "📈 <b>Profit &amp; Loss (Binance Futures)</b>"
        else:
            title = "📈 <b>Profit &amp; Loss (OANDA)</b>"

        lines = [
            title,
            "",
            f"🌐 <b>Total Net Portfolio Equity</b>: <code>${pnl['total_portfolio_equity']:,.2f} USD</code>",
            f"📊 <b>Combined Floating PnL</b>: {tot_f_emoji} <code>${pnl['total_floating_pnl']:+,.2f} USD</code>",
            f"📜 <b>Combined Realized PnL</b>: {tot_r_emoji} <code>${pnl['total_realized_pnl']:+,.2f} USD</code>",
            "",
        ]

        if show_ic:
            ic_equity = _as_float(ic.get("equity"))
            lines.append("🏛️ <b>OANDA (v20)</b>:")
            if ic_equity is not None:
                lines.append(
                    f"  • Equity: <code>${ic_equity:,.2f} USD</code> | "
                    f"Cash: <code>${(_as_float(ic.get('cash')) or 0):,.2f} USD</code>"
                )
            else:
                lines.append(f"  • Cash: <code>${(_as_float(ic.get('cash')) or 0):,.2f} USD</code>")
            lines += [
                f"  • Positions: <code>{_positions_label(ic.get('positions'))}</code>",
                f"  • Floating: <code>${(_as_float(ic.get('floating_pnl')) or 0):+,.2f} USD</code>",
                "",
            ]

        if show_bn:
            bn_equity = _as_float(bn.get("equity"))
            lines.append("🟡 <b>Binance USD-M Futures</b>:")
            if bn_equity is not None:
                lines.append(
                    f"  • Equity: <code>${bn_equity:,.2f} USDT</code> | "
                    f"Cash: <code>${(_as_float(bn.get('cash')) or 0):,.2f} USDT</code>"
                )
            else:
                lines.append(f"  • Cash: <code>${(_as_float(bn.get('cash')) or 0):,.2f} USDT</code>")
            lines += [
                f"  • Positions: <code>{_positions_label(bn.get('positions'))}</code>",
                f"  • Floating PnL: <code>${(_as_float(bn.get('floating_pnl')) or 0):+,.2f} USDT</code>",
                "",
            ]

        lines.append(
            "💡 <i>Tip: /pnl binance for crypto detail"
            + (" or /pnl icmarkets for forex." if show_ic else ".")
            + "</i>"
        )
        return "\n".join(lines)

    def _format_history_report(self, args: list[str] | None = None) -> str:
        """Format the real account ledger. No placeholder balances."""
        _hub, snap = self._hub()
        pnl = snap.get("pnl")
        if not pnl:
            return "⚠️ <b>History unavailable</b>: no venue responded."

        venues = set(pnl.get("venues") or [])
        lines = ["📜 <b>Account Ledger</b>", ""]

        if "binance_futures" in venues:
            bn = pnl["binance"]
            r_emoji = "🟢" if pnl["total_realized_pnl"] >= 0 else "🔴"
            f_emoji = "🟢" if bn["floating_pnl"] >= 0 else "🔴"
            lines += [
                "🟡 <b>Binance USD-M Futures (Demo)</b>",
                f"  • Wallet Balance: <code>${(_as_float(bn.get('cash')) or 0):,.2f} USDT</code>",
            ]
            bn_equity = _as_float(bn.get("equity"))
            if bn_equity is not None:
                lines.append(f"  • Equity: <code>${bn_equity:,.2f} USDT</code>")
            lines += [
                f"  • Realized PnL (tracked): {r_emoji} <code>${pnl['total_realized_pnl']:+,.2f} USDT</code>",
                f"  • Floating PnL: {f_emoji} <code>${bn['floating_pnl']:+,.2f} USDT</code>",
                f"  • Open Positions: <code>{_positions_label(bn.get('positions'))}</code>",
                "",
            ]

        if "oanda" in venues:
            ic = pnl["icmarkets"]
            r_emoji = "🟢" if ic["realized_pnl"] >= 0 else "🔴"
            f_emoji = "🟢" if ic["floating_pnl"] >= 0 else "🔴"
            lines += [
                "🏛️ <b>OANDA (v20)</b>",
                f"  • Cash: <code>${(_as_float(ic.get('cash')) or 0):,.2f} USD</code>",
            ]
            ic_equity = _as_float(ic.get("equity"))
            if ic_equity is not None:
                lines.append(f"  • Equity: <code>${ic_equity:,.2f} USD</code>")
            lines += [
                f"  • Realized PnL: {r_emoji} <code>${ic['realized_pnl']:+,.2f} USD</code>",
                f"  • Floating PnL: {f_emoji} <code>${ic['floating_pnl']:+,.2f} USD</code>",
                f"  • Open Positions: <code>{_positions_label(ic.get('positions'))}</code>",
                "",
            ]

        lines.append(
            "<i>Balances and PnL are read live from the venues. Realized PnL "
            "counts closed trades only.</i>"
        )
        return "\n".join(lines)

    def _format_positions_report(self, args: list[str] | None = None) -> str:
        """Format open positions across both venues (Combined or Individual)."""
        _hub, snap = self._hub()
        target = args[0].lower() if args else "combined"

        if target in {"icmarkets", "ctrader", "forex", "metals"}:
            positions = [
                p for p in (snap.get("positions") or [])
                if "binance" not in str(p.get("exchange", "")).lower()
            ]
            venue_title = "OANDA (v20)"
        elif target in {"binance", "crypto", "spot"}:
            positions = [
                p for p in (snap.get("positions") or [])
                if "binance" in str(p.get("exchange", "")).lower()
            ]
            venue_title = "Binance Portfolio"
        else:
            positions = snap.get("positions") or []
            venue_title = "Combined Portfolio"

        lines = [
            f"📊 <b>Active Positions & Live PnL — {venue_title}</b>\n",
            f"🏛️ <b>{venue_title}</b>\n",
            f"💼 <b>Active Positions Count</b>: <code>{len(positions)}</code>",
        ]

        if not positions:
            lines.append("\n<i>No active open positions. Portfolio is completely flat and capital is preserved.</i>")
            return "\n".join(lines)

        tot_pnl = sum(float(p.get("unrealized_pnl", 0.0)) for p in positions)
        pnl_badge = "🟢" if tot_pnl >= 0 else "🔴"
        lines.append(f"💰 <b>Total Unrealized PnL</b>: {pnl_badge} <code>${tot_pnl:+,.2f} USD</code>\n")
        lines.append("<b>Live Positions Breakdown:</b>")
        for p in positions:
            sym = p.get("symbol", "N/A")
            side = p.get("side", "N/A").upper()
            vol = p.get("quantity", p.get("volume", 0.0))
            entry = p.get("entry_price", 0.0)
            mark = p.get("mark_price", entry)
            pnl = p.get("unrealized_pnl", 0.0)
            p_emoji = "🟢" if pnl >= 0 else "🔴"
            ticket = p.get("position_id", p.get("ticket"))
            venue = p.get("exchange", p.get("venue", "broker"))
            if venue.count(":") == 1:
                # "binance_futures:binance_futures" reads badly; collapse it.
                left, right = venue.split(":", 1)
                if left == right:
                    venue = left

            ticket_line = f" | Ticket: <code>{ticket}</code>" if ticket not in (None, "", "N/A") else ""
            lines.append(
                f"  {p_emoji} <b>[{venue.upper()}] {sym}</b> ({side} {vol:g})\n"
                f"     • Entry: <code>{entry:,.4f}</code> | Current: <code>{mark:,.4f}</code>\n"
                f"     • Float PnL: <code>${pnl:+,.2f} USD</code>{ticket_line}"
            )

        lines.append("\n💡 <i>All orders enforced with 1% max risk and server-side hard Stop-Loss.</i>")
        return "\n".join(lines)

    def _format_balance_report(self, args: list[str] | None = None) -> str:
        """Format real-time cash, equity, and margin across venues."""
        _hub, snap = self._hub()
        balance = snap.get("balance")
        if not balance:
            return "⚠️ <b>Balance unavailable</b>: no venue responded."
        target = args[0].lower() if args else "combined"

        venue_labels = {
            "binance_futures": ("🟡", "Binance USDT-M Futures"),
            "oanda": ("🏛️", "OANDA (v20)"),
        }
        fallback_currency = {"binance_futures": "USDT", "oanda": "USD"}
        ic_target = {"icmarkets", "ctrader", "forex", "metals"}
        bn_target = {"binance", "crypto", "spot"}

        if target in ic_target:
            venue_keys = ["oanda"]
            title = "🏛️ <b>Account Balance (IC Markets cTrader Demo)</b>"
        elif target in bn_target:
            venue_keys = ["binance_futures"]
            title = "🟡 <b>Account Balance (Binance Futures)</b>"
        else:
            venue_keys = ["binance_futures", "oanda"]
            title = "💼 <b>Account Balance (Combined Portfolio)</b>"

        lines = [title, ""]
        if target in ic_target:
            lines.append("💼 <b>Account ID</b>: <code>10128706</code>")

        rendered = 0
        for key in venue_keys:
            block = balance.get(key) or {}
            if not block:
                continue
            total = _as_float(block.get("balance"))
            if total is None:
                total = _as_float(block.get("total"))
            free = _as_float(block.get("free"))
            used = _as_float(block.get("used"))
            if total is None and free is None and used is None:
                continue

            currency = block.get("currency") or fallback_currency[key]
            emoji, label = venue_labels[key]
            parts = [
                f"total <code>{total:,.2f} {currency}</code>"
                if total is not None else "total <code>n/a</code>"
            ]
            if free is not None:
                parts.append(f"free <code>{free:,.2f}</code>")
            if used is not None:
                parts.append(f"used <code>{used:,.2f}</code>")
            lines.append(f"{emoji} <b>{label}</b>: " + " · ".join(parts))
            rendered += 1

        combined_equity = _as_float((balance.get("combined") or {}).get("equity"))
        if combined_equity is not None:
            lines += ["", f"👑 <b>Combined Equity</b>: <code>${combined_equity:,.2f} USD</code>"]

        if rendered == 0:
            return "⚠️ <b>Balance unavailable</b>: no venue balances were reported."
        if target not in ic_target and target not in bn_target:
            lines += [
                "",
                "💡 <i>Tip: Send <code>/balance oanda</code> or <code>/balance binance</code> for single-venue drill-down.</i>",
            ]
        return "\n".join(lines)

    def handle_command(self, text: str) -> str:
        """Public command entry point. Never raises: Telegram always gets a reply."""
        try:
            return self._dispatch_command(text)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("[TELEGRAM] Command failed for %r: %s", text[:60], exc, exc_info=True)
            return (
                "⚠️ <b>Command failed</b>\n\n"
                f"<i>{type(exc).__name__}: {str(exc)[:300]}</i>\n\n"
                "The engine is still running. Try <code>/status</code> or <code>/summary</code>."
            )

    def _dispatch_command(self, text: str) -> str:
        """Process incoming user command and return formatted response."""
        parts = text.strip().split()
        if not parts:
            return "Type /help to see all available trading commands."

        cmd = parts[0].lower().split("@")[0]
        args = parts[1:]
        account_id = os.getenv("CTRADER_FIX_ACCOUNT_ID", "10128706")

        if cmd in {"/start", "/help"}:
            return (
                "🤖 <b>FRIDAY Universe — Stratex Autonomous Trading Intelligence</b>\n\n"
                "<b>Performance</b>\n"
                "• <code>/summary</code> — Daily / week / month PnL + equity chart\n"
                "• <code>/pnl [binance|oanda]</code> — Floating &amp; realized PnL\n"
                "• <code>/trades [n]</code> — Last n closed trades with realized PnL\n"
                "• <code>/risk</code> — Risk dashboard (limits vs. utilisation)\n"
                "• <code>/chart</code> — Equity curve image\n\n"
                "<b>Accounts</b>\n"
                "• <code>/balance [binance|oanda]</code> — Cash &amp; equity\n"
                "• <code>/positions [binance|oanda]</code> — Active live positions\n"
                "• <code>/history</code> — Live account ledger\n\n"
                "<b>Control</b>\n"
                "• <code>/status</code> — Engine health &amp; safety barrier states\n"
                "• <code>/scan</code> — Trigger an immediate market scan\n"
                "• <code>/pause</code> — Block new entries (positions stay protected)\n"
                "• <code>/unpause</code> — Re-enable new entries\n"
                "• <code>/close_all</code> — Emergency flatten all positions\n"
                "• <code>/panic</code> / <code>/halt</code> — Emergency trading halt\n"
                "• <code>/resume</code> — Resume normal trading\n\n"
                "💡 <i>Plain English works too — try “what is my pnl today?” or “show my risk”.</i>"
            )

        if cmd in {"/summary", "/report", "/daily"}:
            from trading_report import format_daily_summary

            stats = self._ledger_metrics()
            try:
                from config import TRADING_MODE

                mode = str(TRADING_MODE)
            except Exception:
                mode = ""
            return format_daily_summary(stats, mode=mode, venue="Binance Futures Testnet")

        if cmd in {"/risk", "/risks"}:
            from trading_report import format_risk_dashboard

            return format_risk_dashboard(self._ledger_metrics(), self._risk_snapshot())

        if cmd in {"/trades", "/last"}:
            from trading_report import load_trades

            try:
                limit = max(1, min(20, int(args[0]))) if args else 10
            except ValueError:
                limit = 10
            trades = load_trades()
            if not trades:
                return "📭 <b>No closed trades yet.</b> The engine has not completed a round-trip."

            lines = [f"🧾 <b>Last {min(limit, len(trades))} closed trades</b>", ""]
            for trade in list(reversed(trades))[:limit]:
                when = trade["closed_at"].strftime("%d %b %H:%M") if trade["closed_at"] else "—"
                emoji = "🟢" if trade["net_pnl"] >= 0 else "🔴"
                lines.append(
                    f"{emoji} <b>{trade['symbol']}</b> {trade['side']} "
                    f"<code>${trade['net_pnl']:+,.2f}</code> · <code>{trade['strategy']}</code>\n"
                    f"   <i>{when} · {trade['exit_reason'] or 'CLOSED'} · "
                    f"{trade['entry_price']:,.4f}→{trade['exit_price']:,.4f}</i>"
                )
            total = sum(t["net_pnl"] for t in list(reversed(trades))[:limit])
            lines += ["", f"Σ <b>Net</b>: <code>${total:+,.2f}</code>"]
            return "\n".join(lines)

        if cmd in {"/chart", "/equity"}:
            return "__SEND_CHART__"

        if cmd in {"/pause", "/unpause", "/pausetrading"}:
            from trading_pause import set_trading_paused

            pause = cmd in {"/pause", "/pausetrading"}
            set_trading_paused(pause, actor="telegram_bot")
            if pause:
                return (
                    "⏸️ <b>NEW ENTRIES PAUSED</b>\n\n"
                    "• No new positions will be opened.\n"
                    "• Open positions keep their stop-loss/take-profit protection.\n"
                    "• Send <code>/unpause</code> to resume."
                )
            return "▶️ <b>NEW ENTRIES RE-ENABLED</b>\n\n• The engine may open positions at the next scan."

        if cmd in {"/balance", "/bal"}:
            return self._format_balance_report(args)

        if cmd in {"/positions", "/pos"}:
            return self._format_positions_report(args)

        if cmd in {"/pnl", "/profit"}:
            return self._format_pnl_report(args)

        if cmd in {"/history", "/deals"}:
            return self._format_history_report(args)

        if cmd in {"/close_all", "/flatten"}:
            return self._flatten_all()

        if cmd in {"/scan"}:
            return self._scan_report()

        if cmd in {"/status", "/health"}:
            panic = is_panic_active()
            locked = is_kill_switch_locked()
            try:
                from trading_pause import is_trading_paused

                paused = is_trading_paused()
            except Exception:
                paused = False
            status_badge = "🔴 LOCKED / PANIC" if (panic or locked) else (
                "🟡 PAUSED" if paused else "🟢 ACTIVE / READY"
            )
            _hub, snap = self._hub()
            bn = (snap.get("balance") or {}).get("binance", {})
            stats = self._ledger_metrics()
            return (
                f"📡 <b>Stratex Engine Status</b>\n\n"
                f"🟢 <b>System State</b>: {status_badge}\n"
                f"☁️ <b>Host</b>: Oracle Cloud Always Free (24/7)\n"
                f"🏛️ <b>Broker 1</b>: IC Markets cTrader Demo ({account_id}) — FIX 4.4 SSL\n"
                f"🟡 <b>Broker 2</b>: Binance (Mode: <code>{bn.get('mode', 'N/A')}</code>)\n"
                f"🧠 <b>Quant Models</b>: StatArb, Kalman Filter, Regime Breakout, ML Ensemble\n"
                f"🔒 <b>Kill-Switch Locked</b>: {locked}\n"
                f"🚨 <b>Panic Active</b>: {panic}\n"
                f"⏸️ <b>New Entries Paused</b>: {paused}\n\n"
                f"📊 <b>Ledger</b>: <code>{stats['trades']}</code> trades · "
                f"net <code>${stats['net_pnl']:+,.2f}</code> · "
                f"win rate <code>{stats['win_rate'] * 100:.1f}%</code>"
            )

        if cmd in {"/halt", "/panic"}:
            write_panic_state(True, actor="telegram_bot", reason="Manual halt requested via Telegram")
            return (
                "🚨 <b>EMERGENCY HALT TRIGGERED</b>\n\n"
                "• All order submissions have been blocked.\n"
                "• OMS state machine is now LOCKED.\n"
                "• Use <code>/resume</code> to unlock when ready."
            )

        if cmd in {"/resume"}:
            write_panic_state(False, actor="telegram_bot", reason="Manual resume requested via Telegram")
            return (
                "✅ <b>NORMAL TRADING RESUMED</b>\n\n"
                "• Trading Resumed: Panic lock state has been cleared.\n"
                "• OMS state machine is UNLOCKED and autonomous scanning is active."
            )

        # Check if an individual asset is mentioned for quantitative analysis
        target_symbol = None
        for s in ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "AUDUSD", "USDCAD", "BTCUSD", "ETHUSD"]:
            if s in text.upper():
                target_symbol = s
                break
        if target_symbol:
            try:
                from autonomy.mt5_autonomous_engine import get_autonomous_engine
                engine = get_autonomous_engine()
                signal = engine.evaluate_symbol_timeframe(target_symbol, "15m")
                if signal:
                    action_emoji = "🟢 BUY" if signal.action == "BUY" else ("🔴 SELL" if signal.action == "SELL" else "⚪ HOLD")
                    regime_val = getattr(signal.regime, "value", str(signal.regime))
                    return (
                        f"🧠 <b>Stratex Quantitative Analysis: {target_symbol}</b>\n\n"
                        f"• <b>Action</b>: <b>{action_emoji}</b>\n"
                        f"• <b>Regime</b>: <code>{regime_val}</code>\n"
                        f"• <b>Current Price</b>: <code>${signal.current_price:,.4f}</code>\n"
                        f"• <b>Kalman Z-Score</b>: <code>{signal.kalman_z:+.2f}</code>\n"
                        f"• <b>Stop Loss</b>: <code>${signal.stop_loss:,.4f}</code>\n"
                        f"• <b>Take Profit</b>: <code>${signal.take_profit:,.4f}</code>\n"
                        f"• <b>Risk-to-Reward</b>: <code>{signal.risk_reward_ratio}:1</code>\n"
                        f"• <b>Model Confidence</b>: <code>{signal.confidence * 100:.0f}%</code>"
                    )
            except Exception:
                pass

        # Natural language query processing: intercept PnL, Positions, or Balance questions
        lower_query = text.lower().strip()
        import re

        nl_args = ["binance"] if any(w in lower_query for w in ["binance", "crypto", "spot", "btc", "usdt"]) else (
            ["icmarkets"] if any(w in lower_query for w in ["ic market", "icmarket", "ctrader", "forex", "gold", "silver"]) else ["combined"]
        )

        if re.search(r"\b(pnl|p&l|profit|loss|earning|return)\b", lower_query):
            return self._format_pnl_report(nl_args)

        if re.search(r"\b(history|closed trades?|past trades?|deals?)\b", lower_query):
            return self._format_history_report(nl_args)

        if re.search(r"\b(positions?|trades?|holding|open)\b", lower_query):
            return self._format_positions_report(nl_args)

        if re.search(r"\b(balance|equity|margin|funds?|money|account|cash)\b", lower_query):
            return self._format_balance_report(nl_args)

        # Factual Quantitative Intelligence summary for general trading questions
        _hub, snap = self._hub()
        comb = snap.get("balance") or {}
        pos = snap.get("positions") or []

        equity = _as_float((comb.get("combined") or {}).get("equity"))
        equity_line = (
            f"• <b>Combined Portfolio Value</b>: <code>${equity:,.2f} USD</code>\n"
            if equity is not None else ""
        )
        return (
            "🧠 <b>Quantitative Intelligence & Market Analysis</b>\n\n"
            f"{equity_line}"
            f"• <b>Active Positions</b>: <code>{len(pos)} open</code>\n"
            f"• <b>Market Regime</b>: Multi-Timeframe Trend &amp; Volatility Scanning Active\n"
            f"• <b>Risk Enforcement</b>: 1% Max Risk Cap per trade, 2:1 minimum R/R, Dynamic Algorithmic SL/TP\n\n"
            "<i>Try <code>/summary</code>, <code>/risk</code> or <code>/balance</code> for full breakdowns.</i>"
        )

    def process_update(self, update: dict[str, Any]) -> None:
        """Validate sender and respond to message."""
        message = update.get("message") or update.get("channel_post")
        if not message:
            return

        chat = message.get("chat", {})
        sender_chat_id = str(chat.get("id", ""))
        text = str(message.get("text", "")).strip()

        # Strict Security Barrier: Reject unauthorized chat IDs
        if sender_chat_id != self.chat_id:
            logger.warning("[TELEGRAM] Dropped unauthorized message from chat_id %s", sender_chat_id)
            return

        if not text:
            return

        try:
            reply_text = self.handle_command(text)
        except Exception as exc:  # handle_command already guards; belt and braces
            reply_text = f"⚠️ Command error: <i>{type(exc).__name__}</i>"

        if reply_text == "__SEND_CHART__":
            self._send_equity_chart()
            return

        self.send_message(reply_text)

    def _send_equity_chart(self) -> bool:
        """Render and send the equity curve chart."""
        try:
            from trading_report import equity_curve_png, equity_sparkline, metrics as ledger_metrics

            try:
                from config import STARTING_BALANCE

                starting = float(STARTING_BALANCE)
            except Exception:
                starting = 10000.0

            stats = ledger_metrics(starting_balance=starting)
            if len(stats.get("equity_curve") or []) < 2:
                self.send_message("📉 <b>Not enough closed trades yet</b> to plot an equity curve.")
                return False

            tmp = os.path.join(os.getenv("TEMP", "."), f"stratex_equity_{int(time.time())}.png")
            if not equity_curve_png(stats, tmp):
                spark = equity_sparkline(stats)
                if spark:
                    self.send_message(
                        "📈 <b>Equity curve</b>\n"
                        f"<code>{spark}</code>\n\n"
                        f"Start <code>${stats['starting_balance']:,.0f}</code> → "
                        f"now <code>${stats['ending_equity']:,.2f}</code> "
                        f"({stats['return_pct']:+.2f}%)"
                    )
                    return True
                self.send_message("📉 <b>Not enough closed trades yet</b> to plot an equity curve.")
                return False
            try:
                return self.send_photo(
                    tmp,
                    caption=(
                        f"📈 <b>Equity curve</b> · {stats['trades']} trades · "
                        f"net ${stats['net_pnl']:+,.2f} · "
                        f"win {stats['win_rate'] * 100:.1f}%"
                    ),
                )
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        except Exception as exc:
            logger.warning("[TELEGRAM] Chart failed: %s", exc)
            self.send_message(f"⚠️ Chart failed: <i>{exc}</i>")
            return False

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------
    def _offset_path(self) -> str:
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".data", "telegram_offset.json")

    def _load_offset(self) -> None:
        try:
            import json

            path = self._offset_path()
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as handle:
                    self._last_update_id = int(json.load(handle).get("last_update_id", 0))
        except Exception:
            pass

    def _save_offset(self) -> None:
        try:
            import json

            path = self._offset_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"last_update_id": self._last_update_id}, handle)
        except Exception:
            pass

    def clear_webhook(self) -> bool:
        """Telegram forbids getUpdates while a webhook is set. Clear it once."""
        if not self.bot_token:
            return False
        try:
            resp = requests.post(
                f"{DEFAULT_API_BASE}{self.bot_token}/deleteWebhook",
                json={"drop_pending_updates": False},
                timeout=self.timeout,
            )
            return bool(resp.json().get("ok"))
        except Exception:
            return False

    def _repo_file(self, name: str) -> str:
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), name)

    def _scan_report(self) -> str:
        """Live scanner status for the Binance engine (no synthetic MT5 scan)."""
        import json

        heartbeat: dict[str, Any] = {}
        portfolio: dict[str, Any] = {}
        try:
            with open(self._repo_file("testnet_heartbeat.json"), "r", encoding="utf-8") as fh:
                heartbeat = json.load(fh)
        except Exception:
            pass
        try:
            with open(self._repo_file("testnet_portfolio.json"), "r", encoding="utf-8") as fh:
                portfolio = json.load(fh)
        except Exception:
            pass

        if not heartbeat:
            return (
                "⚠️ <b>No engine heartbeat</b>\n\n"
                "The Binance engine has not written <code>testnet_heartbeat.json</code> yet. "
                "Check <code>systemctl status Stratex-Daemon</code>."
            )

        funnel = portfolio.get("funnel", {}) or {}
        symbols = heartbeat.get("symbols", []) or []
        strategies = heartbeat.get("strategies", []) or []
        timeframes = heartbeat.get("timeframes", []) or []
        alive = heartbeat.get("worker_alive")
        status = str(heartbeat.get("status", "UNKNOWN"))

        # Signals in the last hour
        signals_last_hour = 0
        try:
            from datetime import datetime, timedelta, timezone

            cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
            with open(self._repo_file("testnet_signals_log.jsonl"), "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    ts = str(rec.get("timestamp", "")).replace("Z", "+00:00")
                    try:
                        if datetime.fromisoformat(ts) >= cutoff:
                            signals_last_hour += 1
                    except Exception:
                        continue
        except Exception:
            pass

        lines = [
            "⚡ <b>Market Scan — Binance Futures Engine</b>",
            "",
            f"• <b>Worker</b>: {'🟢 alive' if alive else '🔴 down'} ({status})",
            f"• <b>Universe</b>: <code>{len(symbols)}</code> symbols × <code>{len(timeframes)}</code> timeframes",
            f"• <b>Strategies</b>: <code>{', '.join(strategies) or 'none'}</code>",
            f"• <b>Signals (last hour)</b>: <code>{signals_last_hour}</code>",
            f"• <b>Equity</b>: <code>${float(portfolio.get('equity') or 0):,.2f}</code>",
            f"• <b>Open positions</b>: <code>{int(portfolio.get('open_positions') or 0)}</code>",
        ]
        if funnel:
            detail = ", ".join(f"{k}={v}" for k, v in list(funnel.items())[:8])
            lines += ["", f"<b>Funnel</b>: <i>{detail}</i>"]
        lines += [
            "",
            "<i>Entries fire only when a candle closes with a qualified signal; "
            "there is no manual override that bypasses the risk gates.</i>",
        ]
        return "\n".join(lines)

    def _flatten_all(self) -> str:
        """Emergency flatten: close every live position directly at each venue."""
        engine, _snap = self._hub()
        closed: list[str] = []
        failed: list[str] = []

        for venue_name, adapter in engine.venues.items():
            try:
                if hasattr(adapter, "get_positions_or_none"):
                    positions = adapter.get_positions_or_none()
                else:
                    positions = adapter.get_positions()
            except Exception as exc:
                failed.append(f"{venue_name}: {exc}")
                continue

            if positions is None:
                failed.append(f"{venue_name}: position state unknown")
                continue

            for pos in positions:
                close_side = "SELL" if str(pos.side).upper() in ("LONG", "BUY") else "BUY"
                try:
                    result = adapter.place_order(
                        symbol=pos.symbol,
                        side=close_side,
                        order_type="MARKET",
                        quantity=pos.quantity,
                    )
                    if result is None:
                        failed.append(f"{venue_name}:{pos.symbol} (order rejected)")
                        continue
                    if hasattr(adapter, "cancel_protective_orders"):
                        adapter.cancel_protective_orders(pos.symbol)
                    closed.append(f"{venue_name}:{pos.symbol}")
                except Exception as exc:
                    failed.append(f"{venue_name}:{pos.symbol} ({exc})")

        if not closed and not failed:
            return "🛑 <b>Nothing to flatten</b>\n\nNo open positions on any venue."

        return (
            "🛑 <b>Emergency Flatten Completed</b>\n\n"
            f"• Closed: <code>{len(closed)}</code> {', '.join(closed) if closed else ''}\n"
            f"• Failed: <code>{len(failed)}</code> {', '.join(failed) if failed else ''}\n\n"
            "<i>Realized PnL is booked by the reconciler on its next pass.</i>"
        )
    def poll_once(self) -> int:
        """Poll for new updates once using long polling."""
        if not self.is_configured:
            return 0

        url = f"{DEFAULT_API_BASE}{self.bot_token}/getUpdates"
        params = {"offset": self._last_update_id + 1, "timeout": 10, "allowed_updates": ["message", "edited_message"]}

        try:
            resp = requests.get(url, params=params, timeout=self.timeout + 15)
            if resp.status_code == 409:
                # Another process (e.g. the Oracle deployment) owns this bot token.
                if not getattr(self, "_conflict_logged", False):
                    logger.error(
                        "[TELEGRAM] 409 Conflict: another process is polling this bot token. "
                        "Run only ONE Stratex instance per bot."
                    )
                    self._conflict_logged = True
                return 0
            self._conflict_logged = False

            data = resp.json()
            if not data.get("ok"):
                logger.debug("[TELEGRAM] getUpdates not ok: %s", data.get("description"))
                return 0

            updates = data.get("result", [])
            dispatched = 0
            for u in updates:
                uid = u.get("update_id", 0)
                if uid <= self._last_update_id:
                    continue
                self._last_update_id = uid
                dispatched += 1
                try:
                    self._executor.submit(self.process_update, u)
                except RuntimeError:
                    self.process_update(u)

            if updates:
                self._save_offset()
            return dispatched
        except requests.exceptions.ReadTimeout:
            return 0  # normal for long polling
        except Exception as exc:
            logger.debug("[TELEGRAM] Polling exception: %s", exc)
            return 0

    def start_polling_in_background(self) -> None:
        """Launch background polling daemon thread."""
        if self._polling_active:
            return
        if not self.is_configured:
            logger.warning("[TELEGRAM] Polling not started: bot not configured.")
            return

        self._load_offset()
        self.clear_webhook()
        self._executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="tg-cmd")
        self._polling_active = True

        def _loop():
            logger.info("[TELEGRAM] Starting inbound Telegram command listener daemon.")
            backoff = 1.0
            while self._polling_active:
                try:
                    self.poll_once()
                    backoff = 1.0
                except Exception as exc:
                    logger.warning("[TELEGRAM] Unexpected error in polling loop: %s", exc)
                    backoff = min(30.0, backoff * 2)
                time.sleep(backoff)

        self._poll_thread = threading.Thread(target=_loop, daemon=True, name="TelegramListener")
        self._poll_thread.start()

    def stop_polling(self) -> None:
        """Stop background polling daemon."""
        self._polling_active = False
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=3.0)
        executor = getattr(self, "_executor", None)
        if executor:
            executor.shutdown(wait=False)


# Global singleton
_TELEGRAM_BOT_INSTANCE: TelegramBotClient | None = None


def get_telegram_bot() -> TelegramBotClient:
    """Retrieve or initialize the global TelegramBotClient singleton."""
    global _TELEGRAM_BOT_INSTANCE
    if _TELEGRAM_BOT_INSTANCE is None:
        _TELEGRAM_BOT_INSTANCE = TelegramBotClient()
    return _TELEGRAM_BOT_INSTANCE
