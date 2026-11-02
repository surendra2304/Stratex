#!/usr/bin/env python
"""Test Telegram bot polling with debug"""
from notifications.telegram_bot import TelegramBotClient
import time
import requests

token = '8870995842:AAGOdFrKX22nT5Wfx08sgtY-akjSADvcUWQ'
chat_id = '6341781431'

bot = TelegramBotClient(bot_token=token, chat_id=chat_id)
print('Starting polling...')
bot.start_polling_in_background()

# Check initial last_update_id
print(f'Initial _last_update_id: {bot._last_update_id}')

# Send a test message
url = 'https://api.telegram.org/bot' + token + '/sendMessage'
requests.post(url, json={'chat_id': chat_id, 'text': '/pnl'}, timeout=10)
print('Sent /pnl command')

# Wait for processing
print('Waiting 8 seconds for bot to process...')
time.sleep(8)

# Check final last_update_id
print(f'Final _last_update_id: {bot._last_update_id}')

# Check for updates directly
resp = requests.get('https://api.telegram.org/bot' + token + '/getUpdates', timeout=10)
data = resp.json()
print(f'Updates available: {len(data.get("result", []))}')
for u in data.get('result', [])[-3:]:
    msg = u.get('message', {})
    chat = msg.get('chat', {})
    text = msg.get('text', '')
    print(f'  Update ID: {u.get("update_id")}, Chat: {chat.get("id")}, Text: {repr(text)}')

bot.stop_polling()
print('Polling stopped')