"""
autonomy/run_daemon.py — Autonomous 24/7 Stratex Production Daemon Runner.

Orchestrates:
1. Real-time bidirectional Telegram polling listener with Gemini AI chat.
2. 24/7 Multi-asset multi-timeframe quantitative scanning loop with Gemini AI trade critique.
3. Execution on IC Markets cTrader (FIX 4.4) and broadcast to FRIDAY Core Gateway & Memora.
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
            "🚀 <b>Stratex Online — IC Markets cTrader</b>\n\n"
            f"• Account: <code>{os.getenv('CTRADER_FIX_ACCOUNT_ID', '10128706')}</code> (demo)\n"
            "• Venue: cTrader FIX 4.4\n"
            "• Scanning: FX majors/crosses, metals, indices, crypto CFDs\n"
            "• Alerts: fills and exits only\n\n"
            "<i>Send /status, /positions, /balance, /scan for details.</i>"
        )

    scan_interval = float(os.getenv("SCAN_INTERVAL_SECONDS", "20"))
    heartbeat_seconds = float(os.getenv("STRATEX_HEARTBEAT_SECONDS", "21600"))
    logger.info(f"[STRATEX_DAEMON] Autonomous scanning loop active (interval: {scan_interval}s).")

    last_pulse_time = time.time()

    while True:
        try:
            executed = engine.scan_market()
            if executed:
                logger.info(f"[STRATEX_DAEMON] Scan cycle executed {len(executed)} trades.")

            # Low-frequency pulse so the channel is not spammed; fills/exits are
            # pushed in real time by the engine itself.
            if (time.time() - last_pulse_time) >= heartbeat_seconds:
                last_pulse_time = time.time()
                try:
                    bal_dict = engine.get_account_balance().get("USD", {})
                    b_tot = bal_dict.get("total", engine.cloud_balance)
                    b_eq = bal_dict.get("equity", b_tot)
                    pos_count = len(engine.get_active_positions())
                    telegram.send_message(
                        f"📡 <b>Stratex Heartbeat</b>\n"
                        f"💰 Cash <code>${b_tot:,.2f}</code> · Equity <code>${b_eq:,.2f}</code>\n"
                        f"📈 Positions <code>{pos_count}/{engine.max_active_positions}</code>"
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
