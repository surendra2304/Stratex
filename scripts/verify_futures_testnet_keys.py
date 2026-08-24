"""
scripts/verify_futures_testnet_keys.py

Safely tests Binance Futures Testnet API credentials without printing or logging them.
"""

import os
from dotenv import load_dotenv
from binance.client import Client

def verify_connection():
    load_dotenv('.env', override=True)
    api_key = os.getenv('API_KEY', '').strip()
    secret_key = os.getenv('SECRET_KEY', '').strip()

    if not api_key or not secret_key:
        print("[AUTH_CHECK] FAILED: API_KEY or SECRET_KEY is missing in .env")
        return False

    try:
        client = Client(api_key=api_key, api_secret=secret_key, testnet=True)
        client.FUTURES_URL = 'https://testnet.binancefuture.com/fapi'
        
        acc = client.futures_account()
        total_wallet_balance = acc.get('totalWalletBalance', '0.0')
        total_unrealized_pnl = acc.get('totalUnrealizedProfit', '0.0')
        total_margin_balance = acc.get('totalMarginBalance', '0.0')
        
        usdt_asset = next((a for a in acc.get('assets', []) if a.get('asset') == 'USDT'), {})
        usdt_wallet = usdt_asset.get('walletBalance', '0.0')
        usdt_available = usdt_asset.get('availableBalance', '0.0')
        
        print("========================================")
        print("BINANCE FUTURES TESTNET AUTHENTICATION")
        print("========================================")
        print("HTTP STATUS: 200 OK")
        print(f"Total Wallet Balance : {total_wallet_balance} USDT")
        print(f"Available Balance    : {usdt_available} USDT")
        print(f"Margin Balance       : {total_margin_balance} USDT")
        print(f"Unrealized PnL       : {total_unrealized_pnl} USDT")
        print("========================================")
        print("SUCCESS: Futures Testnet API credentials are valid and active!")
        return True
    except Exception as e:
        err_msg = str(e)
        print("========================================")
        print("BINANCE FUTURES TESTNET AUTHENTICATION")
        print("========================================")
        print("HTTP STATUS: AUTHENTICATION FAILED")
        if '-2015' in err_msg or 'Invalid API-key' in err_msg:
            print("ERROR: Invalid API-key, IP, or permissions for action.")
            print("ACTION REQUIRED: Ensure keys were created on https://testnet.binancefuture.com and pasted into .env.")
        else:
            print(f"ERROR: {err_msg}")
        print("========================================")
        return False

if __name__ == "__main__":
    verify_connection()
