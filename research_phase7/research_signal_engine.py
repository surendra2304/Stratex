import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.linear_model import LogisticRegression

class ResearchSignalEngine:
    """
    Part 21: Decoupled Signal Engine
    Transforms Features -> Model -> Direction, SL, TP
    Supports Model comparison and threshold optimization.
    """
    def __init__(self, model_type='xgboost', features=None):
        self.model_type = model_type
        self.features = features or ['cvd_raw', 'vol_zscore', 'atr_pct']
        self.model = None
        self.scaler = None
        
    def train(self, X_train, y_train):
        from sklearn.preprocessing import StandardScaler
        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X_train[self.features])
        
        if self.model_type == 'xgboost':
            self.model = xgb.XGBClassifier(n_estimators=100, learning_rate=0.05, max_depth=3, random_state=42)
        else:
            self.model = LogisticRegression(random_state=42, max_iter=1000)
            
        self.model.fit(X_scaled, y_train)
        
    def get_signal(self, current_bar):
        """
        Takes the current feature row and outputs a signal.
        Returns:
            signal (bool): True if trade should be taken
            direction (str): "LONG" or "SHORT"
            confidence (float): probability of success
        """
        if self.model is None or self.scaler is None:
            return False, None, 0.0
            
        # Extract features
        try:
            x_vals = current_bar[self.features].values.reshape(1, -1)
            x_scaled = self.scaler.transform(x_vals)
            
            prob = self.model.predict_proba(x_scaled)[0][1]
            
            # Simple threshold rule
            if prob > 0.55:
                return True, "LONG", prob
            elif prob < 0.45:
                return True, "SHORT", 1 - prob
                
        except Exception:
            pass
            
        return False, None, 0.5
