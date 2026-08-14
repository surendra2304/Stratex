from binance.client import Client

API_KEY = "REDACTED_API_KEY"
SECRET_KEY = "REDACTED_SECRET_KEY"

client = Client(API_KEY, SECRET_KEY, testnet=True)

try:
    account = client.get_account()
    balances = [b for b in account["balances"] if float(b["free"]) > 0]
    print("Connection successful!")
    print("Account balances:")
    for b in balances:
        print(f"  {b['asset']}: {b['free']}")

    price = client.get_symbol_ticker(symbol="BTCUSDT")
    print(f"Current BTCUSDT price: {price['price']}")
except Exception as e:
    print(f"Error: {e}")
