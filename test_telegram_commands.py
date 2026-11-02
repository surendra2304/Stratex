#!/usr/bin/env python
"""Diagnose Telegram bot commands - find which ones fail"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

from notifications.telegram_bot import TelegramBotClient

token = '8870995842:AAGOdFrKX22nT5Wfx08sgtY-akjSADvcUWQ'
chat_id = '6341781431'
bot = TelegramBotClient(bot_token=token, chat_id=chat_id)

commands = [
    '/start', '/help', '/status', '/balance', '/positions', '/pnl',
    '/history', '/scan', '/balance binance', '/positions icmarkets',
    '/pnl combined',
]

for cmd in commands:
    print(f"\n{'='*60}")
    print(f"TESTING: {cmd}")
    print('='*60)
    try:
        resp = bot.handle_command(cmd)
        print(f"OK (len={len(resp)})")
        print(resp[:300])
    except Exception as e:
        import traceback
        print(f"FAILED: {type(e).__name__}: {e}")
        traceback.print_exc()