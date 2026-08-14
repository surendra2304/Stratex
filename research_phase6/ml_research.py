import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import precision_score, recall_score, roc_auc_score
from strategy_ml import MLStrategy

def run_ml_comparison(train_df, val_df):
    """Part 11: Compare simple Logistic Regression against XGBoost."""
    strat = MLStrategy()
    train_df = strat._create_labels(train_df.copy()).dropna()
    val_df = strat._create_labels(val_df.copy()).dropna()
    
    if train_df.empty or val_df.empty:
        return {}
        
    X_train = train_df[strat.features]
    y_train = train_df['target']
    X_val = val_df[strat.features]
    y_val = val_df['target']
    
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_val_scaled = scaler.transform(X_val)
    
    # 1. Logistic Regression Baseline
    lr = LogisticRegression(random_state=42, max_iter=1000)
    lr.fit(X_train_scaled, y_train)
    lr_preds = lr.predict(X_val_scaled)
    lr_probs = lr.predict_proba(X_val_scaled)[:, 1]
    
    # 2. XGBoost
    xgb_model = xgb.XGBClassifier(n_estimators=100, learning_rate=0.05, max_depth=3, random_state=42)
    xgb_model.fit(X_train_scaled, y_train)
    xgb_preds = xgb_model.predict(X_val_scaled)
    xgb_probs = xgb_model.predict_proba(X_val_scaled)[:, 1]
    
    # Safely calculate AUC
    lr_auc = roc_auc_score(y_val, lr_probs) if len(np.unique(y_val)) > 1 else 0
    xgb_auc = roc_auc_score(y_val, xgb_probs) if len(np.unique(y_val)) > 1 else 0
    
    return {
        "LogisticRegression": {
            "Precision": precision_score(y_val, lr_preds, zero_division=0),
            "Recall": recall_score(y_val, lr_preds, zero_division=0),
            "ROC_AUC": lr_auc
        },
        "XGBoost": {
            "Precision": precision_score(y_val, xgb_preds, zero_division=0),
            "Recall": recall_score(y_val, xgb_preds, zero_division=0),
            "ROC_AUC": xgb_auc
        }
    }

def run_probability_calibration(val_df, model, scaler, features):
    """Part 12: Test whether P(WIN) matches actual win rate in buckets."""
    from strategy_ml import MLStrategy
    strat = MLStrategy()
    val_df = strat._create_labels(val_df.copy()).dropna()
    
    if val_df.empty:
        return {}
        
    X_val = val_df[features]
    y_val = val_df['target']
    X_val_scaled = scaler.transform(X_val)
    
    probs = model.predict_proba(X_val_scaled)[:, 1]
    
    # Create buckets
    buckets = {
        "0.50-0.55": [],
        "0.55-0.60": [],
        "0.60-0.65": [],
        "0.65-0.70": [],
        "0.70+": []
    }
    
    for p, y in zip(probs, y_val):
        if 0.50 <= p < 0.55: buckets["0.50-0.55"].append(y)
        elif 0.55 <= p < 0.60: buckets["0.55-0.60"].append(y)
        elif 0.60 <= p < 0.65: buckets["0.60-0.65"].append(y)
        elif 0.65 <= p < 0.70: buckets["0.65-0.70"].append(y)
        elif p >= 0.70: buckets["0.70+"].append(y)
        
    calibration = {}
    for b_name, targets in buckets.items():
        if len(targets) > 0:
            win_rate = sum(targets) / len(targets)
            calibration[b_name] = {"count": len(targets), "actual_win_rate": win_rate}
        else:
            calibration[b_name] = {"count": 0, "actual_win_rate": 0}
            
    return calibration
