import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.preprocessing import StandardScaler

class MLStrategy:
    __name__ = "ml"
    
    def __init__(self):
        self.model = None
        self.scaler = None
        self.features = [
            'returns', 'body_size', 'upper_wick', 'lower_wick', 'range',
            'dist_ema_21', 'dist_ema_200', 'trend_slope_21',
            'rsi_14', 'macd_hist', 'atr_pct', 'bb_width', 'bb_pos',
            'rel_volume'
        ]
        
    def _create_labels(self, df):
        """
        Creates binary labels. 
        1 = Price hits +0.5% before hitting -0.5% in the next 15 candles.
        For simplicity, we'll just check if the return 15 bars from now is > 0.2%.
        """
        future_return = (df['close'].shift(-15) - df['close']) / df['close']
        df['target'] = (future_return > 0.002).astype(int)
        return df
        
    def train(self, train_df, val_df):
        """Fits the model entirely on the training set without lookahead."""
        # 1. Generate labels
        train_df = self._create_labels(train_df.copy()).dropna(subset=self.features + ['target'])
        val_df = self._create_labels(val_df.copy()).dropna(subset=self.features + ['target'])
        
        if train_df.empty or val_df.empty:
            print("    [ML] Not enough data to train.")
            return
            
        X_train = train_df[self.features]
        y_train = train_df['target']
        X_val = val_df[self.features]
        y_val = val_df['target']
        
        # 2. Scale features strictly on training data
        self.scaler = StandardScaler()
        X_train_scaled = self.scaler.fit_transform(X_train)
        X_val_scaled = self.scaler.transform(X_val)
        
        # 3. Train Model
        self.model = xgb.XGBClassifier(
            n_estimators=100,
            learning_rate=0.05,
            max_depth=3,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            eval_metric='logloss',
            early_stopping_rounds=10
        )
        
        self.model.fit(
            X_train_scaled, y_train,
            eval_set=[(X_val_scaled, y_val)],
            verbose=False
        )
        
    def get_signal(self, df):
        """Generates trading signals based on the trained model."""
        if self.model is None or self.scaler is None:
            return None, None, None
            
        if len(df) < 20:
            return None, None, None
            
        last_bar = df.iloc[-1:]
        
        # Check if we have all features
        for f in self.features:
            if f not in last_bar.columns or pd.isna(last_bar[f].iloc[0]):
                return None, None, None
                
        X_test = last_bar[self.features]
        X_test_scaled = self.scaler.transform(X_test)
        
        # Predict probability
        prob = self.model.predict_proba(X_test_scaled)[0]
        prob_up = prob[1]
        
        close = last_bar['close'].iloc[0]
        atr = last_bar['atr'].iloc[0]
        
        # Lowered threshold to populate confidence buckets and capture more trades
        if prob_up > 0.52:
            sl = close - (atr * 1.5)
            tp = close + (atr * 3.0)
            return "BUY", sl, tp, prob_up
            
        return None, None, None, None

# Singleton instance for backwards compatibility with the MultiStrategyWrapper
_instance = MLStrategy()
get_signal = _instance.get_signal
train = _instance.train
__name__ = _instance.__name__
