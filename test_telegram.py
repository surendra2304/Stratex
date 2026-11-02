#!/usr/bin/env python
"""Test Telegram bot functionality"""
from notifications.telegram_bot import TelegramBotClient
import requests

token = '8870995842:AAGOdFrKX22nT5Wfx08sgtY-akjSADvcUWQ'
chat_id = '6341781431'

bot = TelegramBotClient(bot_token=token, chat_id=chat_id)
print('Configured:', bot.is_configured)
print('Chat ID match:', bot.chat_id == chat_id)

# Test handle_command directly
response = bot.handle_command('/status')
with open('response.txt', 'w', encoding='utf-8') as f:
    f.write(response)
print('Response written to response.txt (length:', len(response), ')')

# Test send_message
result = bot.send_message('Direct test message from test script')
print('Direct send result:', result)