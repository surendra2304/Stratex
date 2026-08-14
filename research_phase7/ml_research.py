import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, precision_score, recall_score, f1_score, average_precision_score
from sklearn.preprocessing import StandardScaler

def evaluate_classification(model, X_val, y_val):
    """
    Part 6: ML Evaluation - Classification Metrics
    """
    probs = model.predict_proba(X_val)[:, 1]
    preds = model.predict(X_val)
    
    auc = roc_auc_score(y_val, probs) if len(np.unique(y_val)) > 1 else 0
    pr_auc = average_precision_score(y_val, probs) if len(np.unique(y_val)) > 1 else 0
    precision = precision_score(y_val, preds, zero_division=0)
    recall = recall_score(y_val, preds, zero_division=0)
    f1 = f1_score(y_val, preds, zero_division=0)
    
    return {
        "ROC_AUC": auc,
        "PR_AUC": pr_auc,
        "Precision": precision,
        "Recall": recall,
        "F1": f1
    }

def feature_ablation_test(df, y_col='long_label'):
    """
    Part 19: Feature Ablation testing.
    Uses XGBoost to evaluate feature groups.
    """
    # Define groups
    groups = {
        "A_Tech": ['ema_21', 'ema_50', 'rsi_14'],
        "B_Micro": ['ret_5', 'ret_15', 'body_ratio', 'wick_asymmetry', 'dist_to_high'],
        "C_Volume_CVD": ['vol_zscore', 'buy_sell_ratio', 'cvd_slope', 'vol_shock'],
        "D_Volatility": ['atr_pct', 'volatility_zscore', 'range_expansion'],
        "E_FracDiff": ['close_frac_diff', 'cvd_frac_diff']
    }
    
    # We will test A, A+B, A+C, A+D, A+E, A+B+C+D+E
    tests = {
        "A": groups["A_Tech"],
        "A+B": groups["A_Tech"] + groups["B_Micro"],
        "A+C": groups["A_Tech"] + groups["C_Volume_CVD"],
        "A+D": groups["A_Tech"] + groups["D_Volatility"],
        "A+E": groups["A_Tech"] + groups["E_FracDiff"],
        "ALL": sum(groups.values(), [])
    }
    
    train_size = int(len(df) * 0.7)
    train_df = df.iloc[:train_size].dropna()
    val_df = df.iloc[train_size:].dropna()
    
    results = {}
    
    for test_name, features in tests.items():
        # Ensure features exist
        actual_features = [f for f in features if f in df.columns]
        
        X_train = train_df[actual_features]
        y_train = train_df[y_col]
        X_val = val_df[actual_features]
        y_val = val_df[y_col]
        
        if len(np.unique(y_train)) < 2:
            continue
            
        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        X_val_scaled = scaler.transform(X_val)
        
        model = xgb.XGBClassifier(n_estimators=100, max_depth=3, learning_rate=0.05, random_state=42)
        model.fit(X_train_scaled, y_train)
        
        metrics = evaluate_classification(model, X_val_scaled, y_val)
        results[test_name] = metrics
        
    return results

def calculate_required_gross_edge(fee_rate, slippage_rate):
    """
    Part 24: Cost Threshold Analysis.
    Calculates exactly how much gross edge (per trade) is needed to break even.
    """
    round_trip_friction = (fee_rate * 2) + (slippage_rate * 2)
    return round_trip_friction
