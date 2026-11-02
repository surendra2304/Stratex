"""
run_stratex.py — Single entry point for the Stratex trading engine + Telegram.

One process owns:
  * the Binance USDT-M Futures execution engine (TestnetService)
  * the Telegram command listener (getUpdates) — exactly one poller per bot token
  * outbound alerts (trade opened / closed / daily PnL summary)

Usage
    python run_stratex.py                 # engine + telegram
    python run_stratex.py --no-telegram   # engine only (alerts still sent)
    python run_stratex.py --diagnose      # print environment, no trading

Why a single entry point: two processes polling the same bot token make
Telegram return HTTP 409 Conflict and commands silently stop working. That is
the failure mode this launcher exists to prevent.
"""
from __future__ import annotations

import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv()


def _diagnose() -> int:
    import requests

    ok = True
    print("=" * 62)
    print("  STRATEX DIAGNOSTICS")
    print("=" * 62)

    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    print(f"Telegram token : {'set' if token else 'MISSING'}")
    print(f"Telegram chat  : {chat or 'MISSING'}")

    if token:
        try:
            me = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=15).json()
            if me.get("ok"):
                print(f"Bot identity   : @{me['result'].get('username')}")
            else:
                ok = False
                print(f"Bot identity   : FAILED — {me.get('description')}")
        except Exception as exc:
            ok = False
            print(f"Bot identity   : ERROR — {exc}")

        try:
            hook = requests.get(f"https://api.telegram.org/bot{token}/getWebhookInfo", timeout=15).json()
            info = hook.get("result", {})
            print(f"Webhook        : {info.get('url') or '(none)'}")
            print(f"Pending updates: {info.get('pending_update_count', 0)}")
        except Exception as exc:
            print(f"Webhook        : ERROR — {exc}")

        try:
            probe = requests.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params={"timeout": 0, "offset": 0, "limit": 1},
                timeout=15,
            )
            if probe.status_code == 409:
                ok = False
                print("Polling        : 409 CONFLICT — another process owns this bot token")
            else:
                print("Polling        : available")
        except Exception as exc:
            print(f"Polling        : ERROR — {exc}")

    try:
        from exchanges.unified_portfolio_hub import get_portfolio_hub

        hub = get_portfolio_hub()
        bn = hub.get_binance_balance()
        print(f"Binance        : {bn.get('mode')} (connected={bn.get('is_connected')})")
        if bn.get("is_connected"):
            print(f"Binance equity : ${bn.get('equity_usdt', 0):,.2f}")
        else:
            ok = False
        ic = hub.get_icmarkets_balance()
        print(f"IC Markets     : ${ic.get('equity', 0):,.2f} (source={ic.get('source')})")
    except Exception as exc:
        ok = False
        print(f"Venue check    : ERROR — {exc}")

    try:
        from trading_report import metrics as ledger_metrics

        stats = ledger_metrics()
        print(
            f"Ledger         : {stats['trades']} trades · net ${stats['net_pnl']:+,.2f} · "
            f"win {stats['win_rate'] * 100:.1f}%"
        )
    except Exception as exc:
        print(f"Ledger         : ERROR — {exc}")

    print("=" * 62)
    print("RESULT:", "READY" if ok else "DEGRADED (see above)")
    return 0 if ok else 1


def main() -> int:
    args = set(sys.argv[1:])
    if "--diagnose" in args or "-d" in args:
        return _diagnose()

    if "--no-telegram" in args:
        os.environ["TELEGRAM_ENABLED"] = "false"

    print("=" * 62)
    print("  STRATEX — UNIFIED TRADING RUNTIME")
    print("=" * 62)

    # Import here so --diagnose stays fast and side-effect free.
    import bot

    try:
        bot.main()
    except KeyboardInterrupt:
        print("\n[RUNTIME] Stopped by user.")
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
