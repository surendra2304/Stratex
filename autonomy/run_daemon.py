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

    while True:
        try:
            executed = engine.scan_market()
            if executed:
                logger.info(f"[STRATEX_DAEMON] Scan cycle executed {len(executed)} trades.")
        except KeyboardInterrupt:
            logger.info("[STRATEX_DAEMON] Received shutdown signal.")
            telegram.stop_polling()
            break
        except Exception as exc:
            logger.error(f"[STRATEX_DAEMON] Error in scan cycle: {exc}", exc_info=True)

        time.sleep(scan_interval)


if __name__ == "__main__":
    main()
