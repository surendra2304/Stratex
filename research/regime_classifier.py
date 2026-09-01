

class RegimeClassifier:
    @staticmethod
    def classify_regime(df):
        if df is None or len(df) < 50:
            return 'UNKNOWN'
            
        last = df.iloc[-1]
        close = float(last.get('close', 0.0))
        ema50 = float(last.get('ema_50', close))
        ema200 = float(last.get('ema_200', close))
        adx = float(last.get('adx', last.get('adx_14', 20.0)))
        bb_width = float(last.get('bb_width', 0.02))
        atr_pct = float(last.get('atr_pct', 0.01))
        
        if atr_pct > 0.035 or bb_width > 0.08:
            return 'HIGH_VOLATILITY'
            
        if adx >= 25:
            if close > ema200 and ema50 > ema200:
                return 'TREND_UP'
            elif close < ema200 and ema50 < ema200:
                return 'TREND_DOWN'
                
        if adx < 20 and bb_width < 0.02:
            return 'LOW_VOLATILITY'
        elif adx < 22:
            return 'RANGE'
            
        return 'UNKNOWN'
