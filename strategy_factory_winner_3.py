"""
strategy_factory_winner_3.py — Factory Winner 3
"""
import strategy_factory_winners

def add_features(df):
    return strategy_factory_winners.add_features(df)

def get_signal(df, **kwargs):
    return strategy_factory_winners.get_signal_winner_3(df, **kwargs)
