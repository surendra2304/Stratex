class CooldownFilter:
    """
    Prevents a strategy from trading if it recently closed a trade, avoiding overtrading.
    """
    def __init__(self, cooldown_candles=5):
        self.cooldown_candles = cooldown_candles
        self.last_trade_exit_index = {}

    def is_allowed(self, symbol, current_index):
        last_exit = self.last_trade_exit_index.get(symbol, -999)
        return current_index - last_exit >= self.cooldown_candles

    def register_exit(self, symbol, current_index):
        self.last_trade_exit_index[symbol] = current_index

class VolatilityFilter:
    """
    Prevents entries when current volatility is extreme compared to historical norms.
    """
    def __init__(self, max_atr_pct=0.03):
        self.max_atr_pct = max_atr_pct

    def is_allowed(self, current_bar):
        atr_pct = current_bar.get('atr_pct', 0)
        return atr_pct <= self.max_atr_pct
