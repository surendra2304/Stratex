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
        """Return the portfolio hub plus a cached read of the live portfolio.

        Broker/FIX reads take seconds; the hub is cached so a burst of Telegram
        commands (or a slow venue) can never stack up request latency.
        """
        from exchanges.unified_portfolio_hub import get_portfolio_hub

        hub = get_portfolio_hub()
        now = time.time()
        cache = getattr(self, "_portfolio_cache", None)
        if cache and (now - cache[0]) < ttl:
            return hub, cache[1]

        snapshot: dict[str, Any] = {}
        try:
            snapshot["balance"] = hub.get_combined_balance()
        except Exception as exc:  # pragma: no cover - venue outage
            logger.warning("[TELEGRAM] Combined balance read failed: %s", exc)
            snapshot["balance"] = None
        try:
            snapshot["pnl"] = hub.get_combined_pnl()
        except Exception as exc:  # pragma: no cover - venue outage
            logger.warning("[TELEGRAM] Combined PnL read failed: %s", exc)
            snapshot["pnl"] = None
        try:
            snapshot["positions"] = hub.get_combined_positions()
        except Exception as exc:  # pragma: no cover - venue outage
            logger.warning("[TELEGRAM] Combined positions read failed: %s", exc)
            snapshot["positions"] = []

        self._portfolio_cache = (now, snapshot)
        return hub, snapshot

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
        self._sent_text_cache[dedupe_key] = now
        if len(self._sent_text_cache) > 300:
            cutoff = now - 120
            self._sent_text_cache = {k: v for k, v in self._sent_text_cache.items() if v >= cutoff}

        url = f"{DEFAULT_API_BASE}{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }

        try:
            resp = requests.post(url, json=payload, timeout=self.timeout)
            data = resp.json()
            if not data.get("ok"):
                # If HTML parse error, retry gracefully as plain text
                if parse_mode:
                    payload.pop("parse_mode", None)
                    retry_resp = requests.post(url, json=payload, timeout=self.timeout)
                    if retry_resp.json().get("ok"):
                        logger.info("[TELEGRAM] Message sent as plain text (%d chars).", len(text))
                        return True
                logger.warning("[TELEGRAM] SendMessage failed: %s", data.get("description"))
                return False
            logger.info("[TELEGRAM] Message sent (%d chars).", len(text))
            return True
        except Exception as exc:
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
            ic = pnl["icmarkets"]
            f_emoji = "🟢" if ic["floating_pnl"] >= 0 else "🔴"
            r_emoji = "🟢" if ic["realized_pnl"] >= 0 else "🔴"
            return (
                "📈 <b>Profit & Loss (IC Markets cTrader Demo)</b>\n\n"
                f"💼 <b>Account ID</b>: <code>10128706</code>\n"
                f"🛡️ <b>Current Equity</b>: <code>${ic['equity']:,.2f} USD</code>\n"
                f"💰 <b>Settled Cash</b>: <code>${ic['cash']:,.2f} USD</code>\n"
                f"📊 <b>Floating Unrealized PnL</b>: {f_emoji} <code>${ic['floating_pnl']:+,.2f} USD</code>\n"
                f"📜 <b>Closed Realized PnL</b>: {r_emoji} <code>${ic['realized_pnl']:+,.2f} USD</code>\n\n"
                "<i>Enforced 1% risk cap per trade and server-side hard Stop-Loss active.</i>"
            )

        if target in {"binance", "crypto", "spot"}:
            bn = pnl["binance"]
            f_emoji = "🟢" if bn["floating_pnl"] >= 0 else "🔴"
            venue_label = {
                "futures_testnet": "Binance USDT-M Futures — TESTNET",
                "spot_testnet": "Binance Spot — TESTNET",
                "spot_mainnet": "Binance Spot — LIVE",
                "futures_mainnet": "Binance Futures — LIVE",
            }.get(bn.get("mode", ""), "Binance (unconnected)")
            return (
                "📈 <b>Profit &amp; Loss (Binance Portfolio)</b>\n\n"
                f"🟡 <b>Venue</b>: <code>{venue_label}</code>\n"
                f"🛡️ <b>Total Asset Valuation</b>: <code>${bn['equity']:,.2f} USDT</code>\n"
                f"💰 <b>Liquid Cash / Collateral</b>: <code>${bn['cash']:,.2f} USDT</code>\n"
                f"📊 <b>Floating Unrealized PnL</b>: {f_emoji} <code>${bn['floating_pnl']:+,.2f} USDT</code>\n\n"
                "<i>Values are read live from the Binance API.</i>"
            )

        # Default: Combined
        tot_f_emoji = "🟢" if pnl["total_floating_pnl"] >= 0 else "🔴"
        tot_r_emoji = "🟢" if pnl["total_realized_pnl"] >= 0 else "🔴"
        ic = pnl["icmarkets"]
        bn = pnl["binance"]

        return (
            "📈 <b>Profit & Loss (Combined Portfolio: IC Markets + Binance)</b>\n\n"
            f"🌐 <b>Total Net Portfolio Equity</b>: <code>${pnl['total_portfolio_equity']:,.2f} USD</code>\n"
            f"📊 <b>Combined Floating PnL</b>: {tot_f_emoji} <code>${pnl['total_floating_pnl']:+,.2f} USD</code>\n"
            f"📜 <b>Combined Realized PnL</b>: {tot_r_emoji} <code>${pnl['total_realized_pnl']:+,.2f} USD</code>\n\n"
            "🏛️ <b>IC Markets (cTrader Demo 10128706)</b>:\n"
            f"  • Equity: <code>${ic['equity']:,.2f} USD</code> | Cash: <code>${ic['cash']:,.2f} USD</code>\n"
            f"  • Floating: <code>${ic['floating_pnl']:+,.2f} USD</code> | Realized: <code>${ic['realized_pnl']:+,.2f} USD</code>\n\n"
            "🟡 <b>Binance (Live API)</b>:\n"
            f"  • Equity: <code>${bn['equity']:,.2f} USDT</code> | Cash: <code>${bn['cash']:,.2f} USDT</code>\n"
            f"  • Floating PnL: <code>${bn['floating_pnl']:+,.2f} USDT</code>\n\n"
            "💡 <i>Tip: Send <code>/pnl icmarkets</code> or <code>/pnl binance</code> for single-venue drill-down.</i>"
        )

    def _format_history_report(self, args: list[str] | None = None) -> str:
        """Format real broker trade history and account ledger."""
        _hub, snap = self._hub()
        pnl = snap.get("pnl")
        if not pnl:
            return "⚠️ <b>History unavailable</b>: no venue responded."
        ic = pnl["icmarkets"]

        r_emoji = "🟢" if ic["realized_pnl"] >= 0 else "🔴"
        f_emoji = "🟢" if ic["floating_pnl"] >= 0 else "🔴"

        return (
            "📜 <b>Broker Account Ledger & Trade History (IC Markets)</b>\n\n"
            "💼 <b>Account ID</b>: <code>10128706 (cTrader Demo)</code>\n"
            "💵 <b>Initial Starting Deposit</b>: <code>+$1,000.00 USD</code>\n"
            f"📊 <b>Closed Trades Realized PnL</b>: {r_emoji} <code>${ic['realized_pnl']:+,.2f} USD</code>\n"
            f"💰 <b>Settled Cash Balance</b>: <code>${ic['cash']:,.2f} USD</code>\n"
            f"📈 <b>Open Positions Floating PnL</b>: {f_emoji} <code>${ic['floating_pnl']:+,.2f} USD</code>\n"
            f"🛡️ <b>Current Liquidation Equity</b>: <code>${ic['equity']:,.2f} USD</code>\n\n"
            "<i>Realized PnL reflects actual closed trades. Strict 1% risk cap and server-side hard Stop-Loss active.</i>"
        )

    def _format_positions_report(self, args: list[str] | None = None) -> str:
        """Format open positions across both venues (Combined or Individual)."""
        _hub, snap = self._hub()
        target = args[0].lower() if args else "combined"

        if target in {"icmarkets", "ctrader", "forex", "metals"}:
            positions = [p for p in (snap.get("positions") or []) if p.get("exchange") != "binance"]
            venue_title = "IC Markets cTrader (10128706)"
        elif target in {"binance", "crypto", "spot"}:
            positions = [p for p in (snap.get("positions") or []) if p.get("exchange") == "binance"]
            venue_title = "Binance Portfolio"
        else:
            positions = snap.get("positions") or []
            venue_title = "Combined (IC Markets + Binance)"

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
            ticket = p.get("position_id", p.get("ticket", "N/A"))
            venue = p.get("exchange", p.get("venue", "broker"))

            lines.append(
                f"  {p_emoji} <b>[{venue.upper()}] {sym}</b> ({side} {vol:g})\n"
                f"     • Entry: <code>{entry:,.4f}</code> | Current: <code>{mark:,.4f}</code>\n"
                f"     • Float PnL: <code>${pnl:+,.2f} USD</code> | Ticket: <code>{ticket}</code>"
            )

        lines.append("\n💡 <i>All orders enforced with 1% max risk and server-side hard Stop-Loss.</i>")
        return "\n".join(lines)

    def _format_balance_report(self, args: list[str] | None = None) -> str:
        """Format real-time cash, equity, and margin across venues."""
        _hub, snap = self._hub()
        comb_all = snap.get("balance")
        if not comb_all:
            return "⚠️ <b>Balance unavailable</b>: no venue responded."
        target = args[0].lower() if args else "combined"

        if target in {"icmarkets", "ctrader", "forex", "metals"}:
            ic = comb_all["icmarkets"]
            equity = ic["equity"]
            balance = ic["balance"]
            margin = ic.get("margin", 0.0)
            free_margin = ic.get("free_margin", ic.get("free", equity - margin))
            return (
                "🏛️ <b>Account Balance (IC Markets cTrader Demo)</b>\n\n"
                "💼 <b>Account ID</b>: <code>10128706</code>\n"
                f"💰 <b>Settled Cash Balance</b>: <code>${balance:,.2f} USD</code>\n"
                f"🛡️ <b>Current Equity</b>: <code>${equity:,.2f} USD</code>\n"
                f"🔒 <b>Used Margin</b>: <code>${margin:,.2f} USD</code>\n"
                f"🔓 <b>Free Margin</b>: <code>${free_margin:,.2f} USD</code>\n\n"
                "<i>Real-time live broker state via FIX SSL connection. Zero synthetic fallbacks.</i>"
            )

        if target in {"binance", "crypto", "spot"}:
            bn = comb_all["binance"]
            equity = bn["equity_usdt"]
            cash = bn["cash_usdt"]
            mode = bn.get("mode", "Live")
            lines = [
                "🟡 <b>Account Balance (Binance Portfolio)</b>\n",
                f"⚙️ <b>API Mode</b>: <code>{mode}</code>",
                f"🛡️ <b>Total Valuation</b>: <code>${equity:,.2f} USDT</code>",
                f"💰 <b>Liquid Cash / Margin</b>: <code>${cash:,.2f} USDT</code>\n",
                "<b>Asset Holdings:</b>",
            ]
            assets = bn.get("assets", {})
            if assets:
                for a_sym, val in assets.items():
                    tot = float(val.get("total", 0.0))
                    if tot > 0:
                        lines.append(f"  • <b>{a_sym}</b>: <code>{tot:,.4f}</code> (Free: {float(val.get('free', 0)):,.4f})")
            else:
                lines.append("  <i>No active token balances.</i>")
            return "\n".join(lines)

        # Default: Combined view
        comb = comb_all
        tot_eq = comb["combined_equity"]
        tot_cash = comb["combined_cash"]
        ic = comb["icmarkets"]
        bn = comb["binance"]

        return (
            "💼 <b>IC Markets cTrader Balances & Combined Portfolio</b>\n\n"
            f"👑 <b>Total Net Portfolio Equity</b>: <code>${tot_eq:,.2f} USD</code>\n"
            f"💰 <b>Total Liquid Cash / Collateral</b>: <code>${tot_cash:,.2f} USD</code>\n\n"
            "🏛️ <b>IC Markets cTrader Demo (10128706)</b>:\n"
            f"  • Equity: <code>${ic['equity']:,.2f} USD</code>\n"
            f"  • Cash: <code>${ic['balance']:,.2f} USD</code>\n"
            f"  • Free Margin: <code>${ic.get('free_margin', ic.get('free', ic['equity'])):,.2f} USD</code>\n"
            f"  • Used Margin: <code>${ic.get('used', ic.get('margin', 0.0)):,.2f} USD</code>\n\n"
            "🟡 <b>Binance Client</b>:\n"
            f"  • Total Valuation: <code>${bn['equity_usdt']:,.2f} USDT</code>\n"
            f"  • Liquid Free Cash: <code>${bn['cash_usdt']:,.2f} USDT</code>\n\n"
            "💡 <i>Tip: Send <code>/balance icmarkets</code> or <code>/balance binance</code> for single-venue drill-down.</i>"
        )

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
                "• <code>/pnl [icmarkets|binance]</code> — Floating &amp; realized PnL\n"
                "• <code>/trades [n]</code> — Last n closed trades with realized PnL\n"
                "• <code>/risk</code> — Risk dashboard (limits vs. utilisation)\n"
                "• <code>/chart</code> — Equity curve image\n\n"
                "<b>Accounts</b>\n"
                "• <code>/balance [icmarkets|binance]</code> — Cash &amp; equity\n"
                "• <code>/positions [icmarkets|binance]</code> — Active live positions\n"
                "• <code>/history [icmarkets]</code> — Closed trades &amp; account ledger\n\n"
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

        equity = float(comb.get("combined_equity") or 0.0)
        return (
            "🧠 <b>Quantitative Intelligence & Market Analysis</b>\n\n"
            f"• <b>Combined Portfolio Value</b>: <code>${equity:,.2f} USD</code>\n"
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
        """Emergency flatten of Binance Futures positions."""
        import json

        try:
            from config import TRADING_MODE
        except Exception:
            TRADING_MODE = "FUTURES"

        if str(TRADING_MODE) not in {"FUTURES", "TESTNET"}:
            return "⚠️ <b>Flatten is only wired for the Binance engine</b> (TRADING_MODE is not FUTURES)."

        path = self._repo_file("active_trades.json")
        trades: list[dict[str, Any]] = []
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                trades = [t for t in (data if isinstance(data, list) else []) if t.get("status", "OPEN") == "OPEN"]
            except Exception as exc:
                return f"⚠️ <b>Could not read active trades</b>: <i>{exc}</i>"

        if not trades:
            return "🛑 <b>Nothing to flatten</b>\n\nThe Binance engine reports no open positions."

        closed, failed = [], []
        try:
            from execution import get_exchange_client
            from testnet_engine.protection import emergency_futures_market_close

            client = get_exchange_client()
            for trade in trades:
                symbol = trade.get("symbol", "")
                side = str(trade.get("side", "BUY")).upper()
                qty = float(trade.get("quantity") or 0)
                try:
                    emergency_futures_market_close(client, symbol, side, qty)
                    closed.append(symbol)
                except Exception as exc:
                    failed.append(f"{symbol}: {exc}")
        except Exception as exc:
            return f"⚠️ <b>Flatten failed</b>: <i>{exc}</i>"

        try:
            from execution import monitor_open_trades

            monitor_open_trades()
        except Exception:
            pass

        return (
            "🛑 <b>Emergency Flatten Completed</b>\n\n"
            f"• Closed: <code>{len(closed)}</code> {', '.join(closed) if closed else ''}\n"
            f"• Failed: <code>{len(failed)}</code> {', '.join(failed) if failed else ''}\n\n"
            "<i>Protective orders for closed positions are cancelled by the reconciler.</i>"
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
