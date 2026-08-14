import pandas as pd
from binance.client import Client
from config import API_KEY, SECRET_KEY

def run_funding_arbitrage_research(symbol="BTCUSDT"):
    """
    Part 10 & 11: Funding Rate Arbitrage Research
    Evaluates Cash-and-Carry (Long Spot + Short Perp) or purely directional funding captures.
    """
    try:
        client = Client(API_KEY, SECRET_KEY, testnet=True)
        # Fetch funding rate history (limit 1000 per request, we just grab latest 500)
        funding = client.futures_funding_rate(symbol=symbol, limit=500)
        
        if not funding or len(funding) < 10:
            return {"status": "UNAVAILABLE", "reason": "Insufficient historical data on Testnet"}
            
        df = pd.DataFrame(funding)
        df['fundingTime'] = pd.to_datetime(df['fundingTime'], unit='ms')
        df['fundingRate'] = pd.to_numeric(df['fundingRate'])
        
        # Funding arbitrage is risk-free IF we hold both Spot and Perp.
        # But we must pay fees to enter and exit both legs.
        # Total fees = Spot Entry + Spot Exit + Perp Entry + Perp Exit
        # Assume VIP0 Spot (0.1%) and VIP0 Perp Taker (0.05%)
        # Total round trip friction = (0.1% * 2) + (0.05% * 2) = 0.30%
        # Plus slippage. Let's assume 0.40% total hurdle.
        
        round_trip_friction = 0.0040
        
        # How much funding do we collect?
        total_funding_collected_pct = df['fundingRate'].sum()
        avg_funding_rate = df['fundingRate'].mean()
        days_represented = (df['fundingTime'].max() - df['fundingTime'].min()).days
        
        if days_represented == 0:
            days_represented = 1
            
        annualized_yield = (total_funding_collected_pct / days_represented) * 365
        
        # Does the absolute funding collected surpass the entry/exit friction?
        net_edge = total_funding_collected_pct - round_trip_friction
        
        return {
            "status": "AVAILABLE",
            "samples": len(df),
            "days_represented": days_represented,
            "total_gross_funding": float(total_funding_collected_pct),
            "round_trip_friction": round_trip_friction,
            "net_edge": float(net_edge),
            "annualized_yield": float(annualized_yield),
            "viable": net_edge > 0
        }
        
    except Exception as e:
        return {"status": "UNAVAILABLE", "reason": str(e)}

if __name__ == "__main__":
    res = run_funding_arbitrage_research()
    print(res)
