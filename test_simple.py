#!/usr/bin/env python
"""Test individual Telegram commands quickly with timeout"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import signal

class TimeoutError(Exception):
    pass

def handler(signum, frame):
    raise TimeoutError()

# Only test the fast ones - /start and /help don't hit external services
from notifications.telegram_bot import TelegramBotClient

token = '8870995842:AAGOdFrKX22nT5Wfx08sgtY-akjSADvcUWQ'
chat_id = '6341781431'
bot = TelegramBotClient(bot_token=token, chat_id=chat_id)

for cmd in ['/start', '/help']:
    print(f"\n=== {cmd} ===")
    try:
        resp = bot.handle_command(cmd)
        print(f"OK len={len(resp)}")
    except Exception as e:
        print(f"FAILED: {e}")