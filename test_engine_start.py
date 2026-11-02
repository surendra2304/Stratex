"""Test that the testnet engine can start without crashing"""
import os
os.environ['TRADING_MODE'] = 'FUTURES'
os.environ['TESTNET_ONLY'] = 'TRUE'
os.environ['TESTNET_ENABLED'] = 'true'

from dotenv import load_dotenv
load_dotenv()

import time
from testnet_engine.service import TestnetService

print('Creating TestnetService...')
try:
    service = TestnetService()
    print('Service created OK')
    print('Strategies:', list(service.strategies.keys()))
    print('Active positions:', len(service.active_positions))
    print('Initial deposit:', service.initial_deposit)
    print('Starting equity:', service.starting_equity)
except Exception as e:
    import traceback
    traceback.print_exc()