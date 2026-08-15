import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.preprocessing import StandardScaler
from sklearn import metrics
import logging
import os

logger = logging.getLogger("strategy_ml")

class MLStrategy:
    __name__ = "ml"
    
    def __init__(self):
        self.model_buy = None
        self.model_sell = None
        self.scaler = None
        self.features = [
            'returns', 'body_size', 'upper_wick', 'lower_wick', 'range',
            'dist_ema_21', 'dist_ema_200', 'trend_slope_21',
            'rsi_14', 'macd_hist', 'atr_pct', 'bb_width', 'bb_pos',
            'rel_volume'
        ]
        
    def _create_labels(self, df):
        """
        Creates asymmetric binary labels using a barrier simulation.
        TP = 1.5%, SL = 0.5% (3:1 RR), Horizon = 72 (6 hours on 5m)
        """
        df = df.copy()
        upper_pct = 0.015
        lower_pct = -0.005
        horizon = 72
        
        targets_buy = np.zeros(len(df))
        targets_sell = np.zeros(len(df))
        
        closes = df['close'].values
        highs = df['high'].values
        lows = df['low'].values
        
        for i in range(len(df) - horizon):
            entry = closes[i]
            upper_buy = entry * (1 + upper_pct)
            lower_buy = entry * (1 + lower_pct)
            
            # For SELL, the directions are inverted:
            # We want price to drop by 1.5% (TP), and rise no more than 0.5% (SL)
            upper_sell = entry * (1 + abs(lower_pct)) # Stop Loss for short
            lower_sell = entry * (1 - upper_pct)      # Take Profit for short
            
            hit_upper_b = False
            hit_lower_b = False
            hit_upper_s = False
            hit_lower_s = False
            
            for j in range(i + 1, i + 1 + horizon):
                # Check BUY barriers
                if not hit_upper_b and not hit_lower_b:
                    if highs[j] >= upper_buy:
                        targets_buy[i] = 1
                        hit_upper_b = True
                        hit_lower_b = True # Stop checking
                    elif lows[j] <= lower_buy:
                        hit_lower_b = True
                        
                # Check SELL barriers
                if not hit_upper_s and not hit_lower_s:
                    if lows[j] <= lower_sell:
                        targets_sell[i] = 1
                        hit_lower_s = True
                        hit_upper_s = True # Stop checking
                    elif highs[j] >= upper_sell:
                        hit_upper_s = True
                        
        targets_buy[-horizon:] = np.nan
        targets_sell[-horizon:] = np.nan
        
        df['target_buy'] = targets_buy
        df['target_sell'] = targets_sell
        return df
        
    def train(self, train_df, val_df):
        """Fits dual models (BUY and SELL) entirely on the training set without lookahead."""
        # 1. Generate labels
        train_df = self._create_labels(train_df.copy()).dropna(subset=self.features + ['target_buy', 'target_sell'])
        val_df = self._create_labels(val_df.copy()).dropna(subset=self.features + ['target_buy', 'target_sell'])
        
        if train_df.empty or val_df.empty:
            logger.warning("[ML] Not enough data to train.")
            return
            
        X_train = train_df[self.features]
        y_train_buy = train_df['target_buy']
        y_train_sell = train_df['target_sell']
        
        X_val = val_df[self.features]
        y_val_buy = val_df['target_buy']
        y_val_sell = val_df['target_sell']
        
        # 2. Scale features strictly on training data
        self.scaler = StandardScaler()
        X_train_scaled = self.scaler.fit_transform(X_train)
        X_val_scaled = self.scaler.transform(X_val)
        
        # Calculate scale_pos_weight for imbalance
        scale_buy = (len(y_train_buy) - y_train_buy.sum()) / y_train_buy.sum() if y_train_buy.sum() > 0 else 1
        scale_sell = (len(y_train_sell) - y_train_sell.sum()) / y_train_sell.sum() if y_train_sell.sum() > 0 else 1
        
        # 3. Train BUY Model
        logger.info(f"[ML] Training BUY Model (scale_pos_weight={scale_buy:.2f})...")
        self.model_buy = xgb.XGBClassifier(
            n_estimators=100, learning_rate=0.05, max_depth=3,
            subsample=0.8, colsample_bytree=0.8, random_state=42,
            eval_metric='logloss', early_stopping_rounds=10,
            scale_pos_weight=scale_buy
        )
        self.model_buy.fit(
            X_train_scaled, y_train_buy,
            eval_set=[(X_val_scaled, y_val_buy)],
            verbose=False
        )
        
        # 4. Train SELL Model
        logger.info(f"[ML] Training SELL Model (scale_pos_weight={scale_sell:.2f})...")
        self.model_sell = xgb.XGBClassifier(
            n_estimators=100, learning_rate=0.05, max_depth=3,
            subsample=0.8, colsample_bytree=0.8, random_state=42,
            eval_metric='logloss', early_stopping_rounds=10,
            scale_pos_weight=scale_sell
        )
        self.model_sell.fit(
            X_train_scaled, y_train_sell,
            eval_set=[(X_val_scaled, y_val_sell)],
            verbose=False
        )
        
        logger.info("[ML] Dual Model Training Complete.")
        
    def get_signal(self, df):
        """Generates trading signals based on the dual trained models."""
        if self.model_buy is None or self.model_sell is None or self.scaler is None:
            return None, None, None, None
            
        if len(df) < 20:
            return None, None, None, None
            
        last_bar = df.iloc[-1:]
        
        # Check if we have all features
        for f in self.features:
            if f not in last_bar.columns or pd.isna(last_bar[f].iloc[0]):
                return None, None, None, None
                
        X_test = last_bar[self.features]
        X_test_scaled = self.scaler.transform(X_test)
        
        # Predict probability
        prob_buy = self.model_buy.predict_proba(X_test_scaled)[0][1]
        prob_sell = self.model_sell.predict_proba(X_test_scaled)[0][1]
        
        close = last_bar['close'].iloc[0]
        
        # Asymmetric 1.0% TP and 0.5% SL (Matches model training labels exactly)
        tp_pct = 0.010
        sl_pct = 0.005
        
        # Threshold lowered to 0.50 as RR=2.0 requires only 33% win rate to break even
        if prob_sell > 0.50:
            sl = close * (1 + sl_pct)
            tp = close * (1 - tp_pct)
            return "SELL", sl, tp, prob_sell
            
        if prob_buy > 0.50:
            sl = close * (1 - sl_pct)
            tp = close * (1 + tp_pct)
            return "BUY", sl, tp, prob_buy
            
        # DIAGNOSTIC
        try:
            with open("diagnostic_probs.txt", "a") as f:
                f.write(f"HOLD: prob_buy={prob_buy:.4f}, prob_sell={prob_sell:.4f}\n")
        except: pass
            
        return None, None, None, None

# Singleton instance for backwards compatibility with the MultiStrategyWrapper
_instance = MLStrategy()
get_signal = _instance.get_signal
train = _instance.train
__name__ = _instance.__name__
