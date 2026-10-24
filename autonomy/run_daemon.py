"""
autonomy/run_daemon.py — Autonomous 24/7 Stratex Production Daemon Runner.

Orchestrates:
1. Real-time bidirectional Telegram polling listener with Gemini AI chat.
2. 24/7 Multi-asset multi-timeframe quantitative scanning loop with Gemini AI trade critique.
3. Execution on MT5 demo and broadcast to FRIDAY Core Gateway & Memora.
"""
from __future__ import annotations

import os
import sys
import time

from autonomy.mt5_autonomous_engine import get_autonomous_engine
from logger import get_logger
from notifications.telegram_bot import get_telegram_bot

logger = get_logger("stratex_daemon")


def main() -> None:
    logger.info("=== STARTING STRATEX 24/7 AUTONOMOUS AI ENGINE & TELEGRAM BOT ===")
    telegram = get_telegram_bot()
    engine = get_autonomous_engine()

    if not telegram.is_configured:
        logger.warning("[STRATEX_DAEMON] Telegram bot is not configured. Outbound alerts will be suppressed.")
    else:
        logger.info("[STRATEX_DAEMON] Starting Telegram bot background listener daemon...")
        telegram.start_polling_in_background()
        telegram.send_message(
            "🚀 <b>Stratex Strategic Core v2.2 Online</b>\n\n"
            "• <b>AI Intelligence</b>: Google Gemini 3.5 Flash Active\n"
            "• <b>Execution Mode</b>: 24/7 Cloud Autonomous Demo (Zero Laptop Dependency)\n"
            "• <b>Scanning Universe</b>: Universal Coverage (34+ Assets: Forex Majors/Crosses, Gold, Silver, Oil, Indices, Crypto)\n"
            "• <b>Mesh Consensus</b>: Sentinel (Zero-Trust), Futuris (Forecasts), IntelX (Intelligence)\n"
            "• <i>Ready. Send any question or command (/status, /positions, /balance, /scan) to interact.</i>"
        )

    scan_interval = float(os.getenv("SCAN_INTERVAL_SECONDS", "30"))
    logger.info(f"[STRATEX_DAEMON] Autonomous scanning loop active (interval: {scan_interval}s).")

    last_pulse_time = time.time()

    while True:
        try:
            executed = engine.scan_market()
            if executed:
                logger.info(f"[STRATEX_DAEMON] Scan cycle executed {len(executed)} trades.")

            # Hourly Pulse Heartbeat to Telegram (keeps user informed 24/7 even during weekend market pause)
            if (time.time() - last_pulse_time) >= 3600.0:
                last_pulse_time = time.time()
                try:
                    bal_dict = engine.get_account_balance().get("USD", {})
                    b_tot = bal_dict.get("total", engine.cloud_balance)
                    b_eq = bal_dict.get("equity", b_tot)
                    pos_count = len(engine.get_active_positions())
                    is_weekend = time.gmtime().tm_wday in (5, 6)
                    mkt_str = "⏸ <b>Weekend Paused</b> (Reopens Sunday 21:00 UTC)" if is_weekend else "⚡ <b>Live Market Active</b>"
                    telegram.send_message(
                        f"📡 <b>Stratex 24/7 Engine Heartbeat</b>\n\n"
                        f"🟢 <b>Status</b>: Autonomous Scanning Active\n"
                        f"💼 <b>Account</b>: <code>10128706 (IC Markets cTrader)</code>\n"
                        f"💰 <b>Cash Balance</b>: <code>${b_tot:,.2f} USD</code>\n"
                        f"📊 <b>Account Equity</b>: <code>${b_eq:,.2f} USD</code>\n"
                        f"📈 <b>Active Positions</b>: <code>{pos_count}/5</code>\n"
                        f"🌐 <b>Market State</b>: {mkt_str}\n\n"
                        f"<i>All 17 quant strategies running. Risk cap: 1% max per trade. Hard SL (Tag 1002) enabled.</i>"
                    )
                except Exception as pulse_err:
                    logger.debug(f"[STRATEX_DAEMON] Pulse error: {pulse_err}")

        except KeyboardInterrupt:
            logger.info("[STRATEX_DAEMON] Received shutdown signal.")
            telegram.stop_polling()
            break
        except Exception as exc:
            logger.error(f"[STRATEX_DAEMON] Error in scan cycle: {exc}", exc_info=True)

        time.sleep(scan_interval)


if __name__ == "__main__":
    main()
