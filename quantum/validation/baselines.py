# quantum/validation/baselines.py
"""Classical Baseline Strategies (Rule-based and Classical ML)."""

import numpy as np
import pandas as pd
from typing import Dict, Any, Optional, Tuple
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import GradientBoostingClassifier

try:
    from features import add_features
except ImportError:
    from ..features import add_features

class ClassicalRuleBasedStrategy:
    """
    Classical ADX / EMA Trend & Momentum Strategy.
    Generates signals purely based on historical indicator thresholds with strict SL/TP.
    """
    def __init__(self, ema_fast: int = 9, ema_slow: int = 21, rsi_buy: float = 50.0, rsi_sell: float = 50.0):
        self.name = "Classical_Rule_Based"
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.rsi_buy = rsi_buy
        self.rsi_sell = rsi_sell
        
    def fit(self, train_df: pd.DataFrame):
        """Rule-based strategy has fixed quantitative rules; fit is deterministic."""
        pass
        
    def generate_signal(self, window_df: pd.DataFrame) -> Dict[str, Any]:
        """Evaluates latest candle for buy/sell signal and risk boundaries."""
        if len(window_df) < 30:
            return {"signal": "HOLD", "confidence": 0.0, "entry": 0.0, "sl": 0.0, "tp": 0.0}
            
        df = add_features(window_df.copy())
        latest = df.iloc[-1]
        close = float(latest['close'])
        atr = float(latest.get('atr_14', close * 0.01))
        if atr <= 0:
            atr = close * 0.01
            
        ema_fast_val = float(latest.get('ema_9', close))
        ema_slow_val = float(latest.get('ema_21', close))
        rsi_val = float(latest.get('rsi_14', 50.0))
        
        # Long condition: Fast EMA > Slow EMA and RSI > 50
        if ema_fast_val > ema_slow_val and rsi_val > self.rsi_buy:
            sl = close - (1.5 * atr)
            tp = close + (2.0 * atr)
            return {
                "signal": "BUY",
                "confidence": min(0.95, max(0.5, (rsi_val - 50.0) / 50.0 + 0.5)),
                "entry": close,
                "sl": sl,
                "tp": tp,
                "atr": atr
            }
        # Short condition: Fast EMA < Slow EMA and RSI < 50
        elif ema_fast_val < ema_slow_val and rsi_val < self.rsi_sell:
            sl = close + (1.5 * atr)
            tp = close - (2.0 * atr)
            return {
                "signal": "SELL",
                "confidence": min(0.95, max(0.5, (50.0 - rsi_val) / 50.0 + 0.5)),
                "entry": close,
                "sl": sl,
                "tp": tp,
                "atr": atr
            }
            
        return {"signal": "HOLD", "confidence": 0.0, "entry": close, "sl": 0.0, "tp": 0.0}

class ClassicalMLStrategy:
    """
    Classical Machine Learning Baseline (StandardScaler + GradientBoostingClassifier).
    Trained strictly inside each fold's training slice without look-ahead.
    """
    def __init__(self, horizon: int = 5, profit_target: float = 0.01, stop_loss: float = 0.007):
        self.name = "Classical_ML_Baseline"
        self.horizon = horizon
        self.profit_target = profit_target
        self.stop_loss = stop_loss
        self.scaler = StandardScaler()
        self.model = GradientBoostingClassifier(n_estimators=40, max_depth=3, random_state=42)
        self.feature_cols = [
            "returns", "body_size", "upper_wick", "lower_wick", "range",
            "dist_ema_21", "dist_ema_200", "trend_slope_21", "rsi_14",
            "macd", "macd_hist", "atr_pct", "bb_pos", "rel_volume"
        ]
        self.is_trained = False
        
    def _create_dataset(self, df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
        df_feat = add_features(df.copy())
        # Labels: Triple barrier
        closes = df_feat['close'].values
        n = len(df_feat)
        X_list = []
        y_list = []
        
        for i in range(50, n - self.horizon):
            entry = closes[i]
            window_slice = closes[i+1 : i+1+self.horizon]
            max_p = np.max(window_slice)
            min_p = np.min(window_slice)
            
            # Target 1 = BUY profit target hit before stop loss
            hit_tp = (max_p - entry) / entry >= self.profit_target
            hit_sl = (entry - min_p) / entry >= self.stop_loss
            
            label = 1 if (hit_tp and not hit_sl) else 0
            
            row = df_feat[self.feature_cols].iloc[i].fillna(0.0).values
            X_list.append(row)
            y_list.append(label)
            
        if not X_list:
            return np.empty((0, len(self.feature_cols))), np.empty((0,))
        return np.array(X_list, dtype=np.float32), np.array(y_list, dtype=int)
        
    def fit(self, train_df: pd.DataFrame):
        X, y = self._create_dataset(train_df)
        if len(X) > 20 and len(np.unique(y)) > 1:
            X_scaled = self.scaler.fit_transform(X)
            self.model.fit(X_scaled, y)
            self.is_trained = True
        else:
            self.is_trained = False
            
    def generate_signal(self, window_df: pd.DataFrame) -> Dict[str, Any]:
        if not self.is_trained or len(window_df) < 50:
            return {"signal": "HOLD", "confidence": 0.0, "entry": 0.0, "sl": 0.0, "tp": 0.0}
            
        df_feat = add_features(window_df.copy())
        latest_row = df_feat[self.feature_cols].iloc[-1:].fillna(0.0).values
        scaled_row = self.scaler.transform(latest_row)
        
        prob_buy = float(self.model.predict_proba(scaled_row)[0, 1])
        close = float(df_feat['close'].iloc[-1])
        atr = float(df_feat['atr_14'].iloc[-1]) if 'atr_14' in df_feat else close * 0.01
        if atr <= 0:
            atr = close * 0.01
            
        if prob_buy > 0.58:
            return {
                "signal": "BUY",
                "confidence": prob_buy,
                "entry": close,
                "sl": close - (1.5 * atr),
                "tp": close + (2.0 * atr),
                "atr": atr
            }
        return {"signal": "HOLD", "confidence": prob_buy, "entry": close, "sl": 0.0, "tp": 0.0}
