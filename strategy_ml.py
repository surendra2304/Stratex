# ==============================================================================
# STRATEGY_ML.PY - Calibrated Probabilistic ML Prediction Engine (XGBoost)
# ==============================================================================

import logging
import os
import pickle
from collections import namedtuple

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.preprocessing import StandardScaler

SignalResult = namedtuple(
    "SignalResult",
    ["side", "sl", "tp", "strategy_type", "confidence", "rr_ratio"]
)

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
        self._load_saved_models()

    def _load_saved_models(self):
        """Attempts to load pre-trained models and scaler from disk."""
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            buy_path = os.path.join(base_dir, "model_buy.pkl")
            sell_path = os.path.join(base_dir, "model_sell.pkl")
            scaler_path = os.path.join(base_dir, "scaler.pkl")

            if os.path.exists(buy_path) and os.path.exists(scaler_path):
                with open(buy_path, "rb") as f:
                    self.model_buy = pickle.load(f)
                with open(scaler_path, "rb") as f:
                    self.scaler = pickle.load(f)
                logger.info("[ML] ✅ Successfully loaded model_buy.pkl and scaler.pkl.")
                
            if os.path.exists(sell_path):
                with open(sell_path, "rb") as f:
                    self.model_sell = pickle.load(f)
                logger.info("[ML] ✅ Successfully loaded model_sell.pkl.")
        except Exception as e:
            logger.warning(f"[ML] Note: Pre-trained models could not be loaded automatically: {e}")
        
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
            
            upper_sell = entry * (1 + abs(lower_pct)) # Stop Loss for short
            lower_sell = entry * (1 - upper_pct)      # Take Profit for short
            
            hit_upper_b = False
            hit_lower_b = False
            hit_upper_s = False
            hit_lower_s = False
            
            for j in range(i + 1, i + 1 + horizon):
                if not hit_upper_b and not hit_lower_b:
                    if highs[j] >= upper_buy:
                        targets_buy[i] = 1
                        hit_upper_b = True
                    elif lows[j] <= lower_buy:
                        hit_lower_b = True
                        
                if not hit_upper_s and not hit_lower_s:
                    if lows[j] <= lower_sell:
                        targets_sell[i] = 1
                        hit_lower_s = True
                    elif highs[j] >= upper_sell:
                        hit_upper_s = True
                        
        targets_buy[-horizon:] = np.nan
        targets_sell[-horizon:] = np.nan
        
        df['target_buy'] = targets_buy
        df['target_sell'] = targets_sell
        return df
        
    def train(self, train_df, val_df):
        """Fits dual models (BUY and SELL) on training set."""
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
        
        self.scaler = StandardScaler()
        X_train_scaled = self.scaler.fit_transform(X_train)
        X_val_scaled = self.scaler.transform(X_val)
        
        scale_buy = (len(y_train_buy) - y_train_buy.sum()) / y_train_buy.sum() if y_train_buy.sum() > 0 else 1
        scale_sell = (len(y_train_sell) - y_train_sell.sum()) / y_train_sell.sum() if y_train_sell.sum() > 0 else 1
        
        logger.info(f"[ML] Training BUY Model (scale_pos_weight={scale_buy:.2f})...")
        self.model_buy = xgb.XGBClassifier(
            n_estimators=120, learning_rate=0.04, max_depth=4,
            subsample=0.8, colsample_bytree=0.8, random_state=42,
            eval_metric='logloss', early_stopping_rounds=15,
            scale_pos_weight=scale_buy
        )
        self.model_buy.fit(
            X_train_scaled, y_train_buy,
            eval_set=[(X_val_scaled, y_val_buy)],
            verbose=False
        )
        
        logger.info(f"[ML] Training SELL Model (scale_pos_weight={scale_sell:.2f})...")
        self.model_sell = xgb.XGBClassifier(
            n_estimators=120, learning_rate=0.04, max_depth=4,
            subsample=0.8, colsample_bytree=0.8, random_state=42,
            eval_metric='logloss', early_stopping_rounds=15,
            scale_pos_weight=scale_sell
        )
        self.model_sell.fit(
            X_train_scaled, y_train_sell,
            eval_set=[(X_val_scaled, y_val_sell)],
            verbose=False
        )
        
        # Save models
        try:
            with open("model_buy.pkl", "wb") as f:
                pickle.dump(self.model_buy, f)
            with open("model_sell.pkl", "wb") as f:
                pickle.dump(self.model_sell, f)
            with open("scaler.pkl", "wb") as f:
                pickle.dump(self.scaler, f)
            logger.info("[ML] Dual Model Training & Serialization Complete.")
        except Exception as e:
            logger.error(f"[ML] Failed to serialize models: {e}")
        
    def get_signal(self, df):
        """Generates trading signals with volatility-adjusted SL/TP."""
        if self.model_buy is None or self.scaler is None:
            return SignalResult(None, None, None, "PROBABILISTIC", None, None)
        # Guard against None input
        if df is None:
            return SignalResult(None, None, None, "PROBABILISTIC", None, None)
        if len(df) < 20:
            return SignalResult(None, None, None, "PROBABILISTIC", None, None)
            
        last_bar = df.iloc[-1:]
        
        # Check if we have all features
        for f in self.features:
            if f not in last_bar.columns or pd.isna(last_bar[f].iloc[0]):
                return SignalResult(None, None, None, "PROBABILISTIC", None, None)
                
        X_test = last_bar[self.features]
        X_test_scaled = self.scaler.transform(X_test)
        
        prob_buy = float(self.model_buy.predict_proba(X_test_scaled)[0][1])
        prob_sell = float(self.model_sell.predict_proba(X_test_scaled)[0][1]) if self.model_sell is not None else 0.0
        
        close = float(last_bar['close'].iloc[0])
        atr = float(last_bar.get('atr_14', last_bar.get('atr', close * 0.01)).iloc[0])
        
        # Volatility-adjusted 2.5x ATR TP and 1.2x ATR SL (RR = 2.08)
        if prob_buy >= 0.52:
            sl = close - (atr * 1.2)
            tp = close + (atr * 2.5)
            return SignalResult("BUY", sl, tp, "PROBABILISTIC", prob_buy, 2.08)
            
        if prob_sell >= 0.52:
            sl = close + (atr * 1.2)
            tp = close - (atr * 2.5)
            return SignalResult("SELL", sl, tp, "PROBABILISTIC", prob_sell, 2.08)
            
        return SignalResult(None, None, None, "PROBABILISTIC", None, None)

_instance = MLStrategy()
get_signal = _instance.get_signal
train = _instance.train
__name__ = _instance.__name__
