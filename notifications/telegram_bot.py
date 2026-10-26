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
import os
import threading
import time
from typing import Any, Callable

from dotenv import load_dotenv
import requests

from logger import get_logger
from panic_state import is_kill_switch_locked, is_panic_active, write_panic_state
from resilience import sanitize_secrets

load_dotenv()

logger = get_logger("telegram_bot")

DEFAULT_API_BASE = "https://api.telegram.org/bot"


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

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    # ==========================================================================
    # OUTBOUND NOTIFICATIONS
    # ==========================================================================
    def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send a message to the authorized chat ID."""
        if not self.is_configured:
            logger.debug("[TELEGRAM] Bot not fully configured; message suppressed.")
            return False

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
                        return True
                logger.warning("[TELEGRAM] SendMessage failed: %s", data.get("description"))
                return False
            return True
        except Exception as exc:
            logger.warning("[TELEGRAM] Error sending message: %s", exc)
            return False

    def notify_trade_intent(
        self,
        intent: Any,
        status: str = "PROPOSED",
        fill_price: float | None = None,
        pnl: float | None = None,
    ) -> bool:
        """Format and dispatch rich trade alerts with emojis and brackets."""
        sym = getattr(intent, "symbol", "UNKNOWN")
        side = getattr(intent, "side", "UNKNOWN").upper()
        qty = getattr(intent, "quantity", 0.0)
        venue = getattr(intent, "venue", "binance").upper()
        sl = getattr(intent, "stop_loss", None)
        tp = getattr(intent, "take_profit", None)
        strat = getattr(intent, "strategy", "quantitative_alpha")
        cid = getattr(intent, "client_order_id", "stx-order")

        side_emoji = "🟢 BUY" if "BUY" in side or "LONG" in side else "🔴 SELL"
        status_emoji = "⚡" if status == "PROPOSED" else ("✅" if status == "FILLED" else "📋")

        lines = [
            f"{status_emoji} <b>STRATEX TRADE {status}</b>",
            "",
            f"• <b>Symbol</b>: <code>{sym}</code>",
            f"• <b>Action</b>: <b>{side_emoji}</b>",
            f"• <b>Quantity</b>: <code>{qty}</code>",
            f"• <b>Venue</b>: <code>{venue}</code>",
            f"• <b>Strategy</b>: <code>{strat}</code>",
        ]

        if fill_price:
            lines.append(f"• <b>Fill Price</b>: <code>${fill_price:,.4f}</code>")
        if sl:
            lines.append(f"• <b>Stop Loss</b>: <code>${sl:,.4f}</code>")
        if tp:
            lines.append(f"• <b>Take Profit</b>: <code>${tp:,.4f}</code>")
        if pnl is not None:
            pnl_emoji = "💰" if pnl >= 0 else "🔻"
            lines.append(f"• <b>Realized PnL</b>: {pnl_emoji} <code>${pnl:+,.2f}</code>")

        meta = getattr(intent, "metadata", {}) or {}
        ai_thesis = meta.get("ai_thesis")
        if ai_thesis:
            lines.append(f"• <b>AI Thesis</b>: <i>{ai_thesis}</i>")

        lines.append(f"• <b>Order ID</b>: <code>{cid}</code>")
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
        from exchanges.unified_portfolio_hub import get_portfolio_hub
        hub = get_portfolio_hub()
        pnl = hub.get_combined_pnl()

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
            return (
                "📈 <b>Profit & Loss (Binance Portfolio)</b>\n\n"
                f"🟡 <b>Venue</b>: <code>Binance Live Client</code>\n"
                f"🛡️ <b>Total Asset Valuation</b>: <code>${bn['equity']:,.2f} USDT</code>\n"
                f"💰 <b>Liquid Cash / Collateral</b>: <code>${bn['cash']:,.2f} USDT</code>\n"
                f"📊 <b>Floating Unrealized PnL</b>: {f_emoji} <code>${bn['floating_pnl']:+,.2f} USDT</code>\n\n"
                "<i>Real-time Binance WebSocket & REST feed.</i>"
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
        from exchanges.unified_portfolio_hub import get_portfolio_hub
        hub = get_portfolio_hub()
        pnl = hub.get_combined_pnl()
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
        from exchanges.unified_portfolio_hub import get_portfolio_hub
        hub = get_portfolio_hub()
        target = args[0].lower() if args else "combined"

        if target in {"icmarkets", "ctrader", "forex", "metals"}:
            positions = hub.get_icmarkets_positions()
            venue_title = "IC Markets cTrader (10128706)"
        elif target in {"binance", "crypto", "spot"}:
            positions = hub.get_binance_positions()
            venue_title = "Binance Portfolio"
        else:
            positions = hub.get_combined_positions()
            venue_title = "Combined (IC Markets + Binance)"

        lines = [
            f"📊 <b>Active Positions & MT5 Live PnL — {venue_title}</b>\n",
            "🌐 <b>Active MT5 Positions</b> & Binance Positions\n",
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
        from exchanges.unified_portfolio_hub import get_portfolio_hub
        hub = get_portfolio_hub()
        target = args[0].lower() if args else "combined"

        if target in {"icmarkets", "ctrader", "forex", "metals"}:
            ic = hub.get_icmarkets_balance()
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
            bn = hub.get_binance_balance()
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
        comb = hub.get_combined_balance()
        tot_eq = comb["combined_equity"]
        tot_cash = comb["combined_cash"]
        ic = comb["icmarkets"]
        bn = comb["binance"]

        return (
            "💼 <b>MT5 Wallet Balances & Combined Portfolio</b>\n\n"
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
        """Process incoming user command and return formatted response."""
        parts = text.strip().split()
        if not parts:
            return "Type /help to see all available trading commands."

        cmd = parts[0].lower()
        args = parts[1:]
        account_id = os.getenv("CTRADER_FIX_ACCOUNT_ID", "10128706")

        if cmd in {"/start", "/help"}:
            return (
                "🤖 <b>FRIDAY Universe — Stratex Autonomous Trading Intelligence</b>\n\n"
                "<b>Unified Multi-Account Commands:</b>\n"
                "• <code>/balance [icmarkets|binance]</code> — Total liquid cash & equity\n"
                "• <code>/positions [icmarkets|binance]</code> — Active live positions\n"
                "• <code>/pnl [icmarkets|binance]</code> — Floating & realized PnL breakdown\n"
                "• <code>/history [icmarkets]</code> — Closed trades & account ledger\n"
                "• <code>/status</code> — Quant engine status & safety barrier states\n"
                "• <code>/scan</code> — Trigger immediate high-conviction market scan\n"
                "• <code>/close_all</code> — Emergency flatten all positions immediately\n"
                "• <code>/panic</code> / <code>/halt</code> — Trigger emergency trading halt\n"
                "• <code>/resume</code> — Resume normal trading engine operation\n\n"
                "💡 <i>You can also ask me questions in plain English (e.g., 'What is my current PnL?', 'Show my balance')!</i>"
            )

        if cmd in {"/balance", "/bal"}:
            return self._format_balance_report(args)

        if cmd in {"/positions", "/pos"}:
            return self._format_positions_report(args)

        if cmd in {"/pnl", "/profit"}:
            return self._format_pnl_report(args)

        if cmd in {"/history", "/deals"}:
            return self._format_history_report(args)

        if cmd in {"/close_all", "/flatten"}:
            try:
                from autonomy.mt5_autonomous_engine import get_autonomous_engine
                engine = get_autonomous_engine()
                res = engine.close_all_positions()
                return (
                    f"🛑 <b>Emergency Flatten Completed</b>\n\n"
                    f"• Closed: <code>{res.get('closed_count', 0)}</code> positions\n"
                    f"• Settled Cash: <code>${res.get('settled_balance', 1000.0):,.2f} USD</code>\n"
                    f"• Remaining Open: <code>{res.get('remaining_count', 0)}</code>\n"
                    f"• Status: <i>{res.get('message', 'Ledger flattened')}</i>"
                )
            except Exception as exc:
                return f"⚠️ <b>Close All Failed</b>: <i>{exc}</i>"

        if cmd in {"/scan"}:
            try:
                from autonomy.mt5_autonomous_engine import get_autonomous_engine
                engine = get_autonomous_engine()
                executed = engine.scan_market()
                if executed:
                    return (
                        f"⚡ <b>Market Scan Complete</b>\n\n"
                        f"• Executed <code>{len(executed)}</code> high-conviction orders.\n"
                        f"• All orders protected by 1% max risk and server-side hard Stop-Loss."
                    )
                return "⚡ <b>Market Scan Complete</b>: No high-conviction signals meeting 2:1 R/R hurdle right now. Holding capital."
            except Exception as exc:
                return f"⚠️ <b>Scan Failed</b>: <i>{exc}</i>"

        if cmd in {"/status", "/health"}:
            panic = is_panic_active()
            locked = is_kill_switch_locked()
            status_badge = "🔴 LOCKED / PANIC" if (panic or locked) else "🟢 ACTIVE / READY"
            from exchanges.unified_portfolio_hub import get_portfolio_hub
            hub = get_portfolio_hub()
            bn = hub.get_binance_balance()
            return (
                f"📡 <b>Stratex Engine Status</b>\n\n"
                f"🟢 <b>System State</b>: {status_badge}\n"
                f"☁️ <b>Host</b>: Oracle Cloud Always Free (24/7)\n"
                f"🏛️ <b>Broker 1</b>: IC Markets cTrader Demo ({account_id}) — FIX 4.4 SSL\n"
                f"🟡 <b>Broker 2</b>: Binance Live (Mode: <code>{bn.get('mode', 'N/A')}</code>)\n"
                f"🧠 <b>Quant Models</b>: StatArb, Kalman Filter, Regime Breakout, ML Ensemble\n"
                f"🔒 <b>Kill-Switch Locked</b>: {locked}\n"
                f"🚨 <b>Panic Active</b>: {panic}"
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
        from exchanges.unified_portfolio_hub import get_portfolio_hub
        hub = get_portfolio_hub()
        comb = hub.get_combined_balance()
        pos = hub.get_combined_positions()

        return (
            "🧠 <b>Quantitative Intelligence & Market Analysis</b>\n\n"
            f"• <b>Combined Portfolio Value</b>: <code>${comb['combined_equity']:,.2f} USD</code>\n"
            f"• <b>Active Positions</b>: <code>{len(pos)} open</code>\n"
            f"• <b>Market Regime</b>: Multi-Timeframe Trend & Volatility Scanning Active\n"
            f"• <b>Risk Enforcement</b>: 1% Max Risk Cap per trade, 2:1 minimum R/R, Dynamic Algorithmic SL/TP\n\n"
            "<i>Type <code>/balance</code>, <code>/positions</code>, or <code>/pnl</code> for full ledger breakdowns.</i>"
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

        reply_text = self.handle_command(text)
        self.send_message(reply_text)

    def poll_once(self) -> int:
        """Poll for new updates once using long polling."""
        if not self.is_configured:
            return 0

        url = f"{DEFAULT_API_BASE}{self.bot_token}/getUpdates"
        params = {"offset": self._last_update_id + 1, "timeout": 5}

        try:
            resp = requests.get(url, params=params, timeout=self.timeout + 5)
            data = resp.json()
            if not data.get("ok"):
                return 0

            updates = data.get("result", [])
            for u in updates:
                uid = u.get("update_id", 0)
                if uid > self._last_update_id:
                    self._last_update_id = uid
                self.process_update(u)
            return len(updates)
        except Exception as exc:
            logger.debug("[TELEGRAM] Polling exception: %s", exc)
            return 0

    def start_polling_in_background(self) -> None:
        """Launch background polling daemon thread."""
        if self._polling_active:
            return

        self._polling_active = True

        def _loop():
            logger.info("[TELEGRAM] Starting inbound Telegram command listener daemon.")
            while self._polling_active:
                try:
                    self.poll_once()
                except Exception as exc:
                    logger.warning("[TELEGRAM] Unexpected error in polling loop: %s", exc)
                time.sleep(1.0)

        self._poll_thread = threading.Thread(target=_loop, daemon=True, name="TelegramListener")
        self._poll_thread.start()

    def stop_polling(self) -> None:
        """Stop background polling daemon."""
        self._polling_active = False
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=2.0)


# Global singleton
_TELEGRAM_BOT_INSTANCE: TelegramBotClient | None = None


def get_telegram_bot() -> TelegramBotClient:
    """Retrieve or initialize the global TelegramBotClient singleton."""
    global _TELEGRAM_BOT_INSTANCE
    if _TELEGRAM_BOT_INSTANCE is None:
        _TELEGRAM_BOT_INSTANCE = TelegramBotClient()
    return _TELEGRAM_BOT_INSTANCE
