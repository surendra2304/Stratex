# ==============================================================================
# STRATEGY_ML.PY - Machine Learning Bot (Random Forest Price Direction Predictor)
# ==============================================================================
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler

# Global model state (trained once, updated periodically)
_model = None
_scaler = None

def _prepare_features(df):
    """Extracts numerical features from the DataFrame for the ML model."""
    features = df[["rsi", "macd", "macd_signal", "macd_hist",
                   "bb_upper", "bb_lower", "atr", "ema_20", "ema_50", "volume"]].copy()
    return features

def _train_model(df):
    """Trains the Random Forest model on historical data."""
    global _model, _scaler

    features = _prepare_features(df[:-1])  # All rows except the last (current) candle

    # Label: 1 if next close > current close, 0 if it goes down
    labels = []
    for i in range(len(df) - 2):
        if df["close"].iloc[i+1] > df["close"].iloc[i]:
            labels.append(1)
        else:
            labels.append(0)

    # Need at least 50 rows to train
    if len(features) < 50 or len(labels) < 50:
        return False

    X = features.iloc[:len(labels)].values
    y = np.array(labels)

    _scaler = StandardScaler()
    X_scaled = _scaler.fit_transform(X)

    _model = RandomForestClassifier(n_estimators=100, random_state=42, max_depth=5)
    _model.fit(X_scaled, y)
    return True

def get_signal(df):
    """
    ML Strategy:
    - Trains a Random Forest model on recent candle data
    - Predicts whether the next candle will go UP or DOWN
    - Places a trade accordingly
    """
    global _model, _scaler

    if df is None or len(df) < 100:
        return None, None, None

    # Train or retrain model every time (in production, you'd cache this)
    trained = _train_model(df)
    if not trained or _model is None:
        return None, None, None

    # Predict on the latest candle
    last_features = _prepare_features(df.tail(1)).values
    last_scaled = _scaler.transform(last_features)
    prediction = _model.predict(last_scaled)[0]
    confidence = _model.predict_proba(last_scaled)[0][prediction]

    # Only trade if confidence is above 65%
    if confidence < 0.65:
        return None, None, None

    last = df.iloc[-1]
    close = last["close"]
    atr = last["atr"]

    if prediction == 1:  # UP
        sl = close - (atr * 1.5)
        tp = close + (atr * 2.5)
        print(f"[ML] 🤖 BUY signal | Confidence: {confidence:.1%}")
        return "BUY", sl, tp
    else:  # DOWN
        sl = close + (atr * 1.5)
        tp = close - (atr * 2.5)
        print(f"[ML] 🤖 SELL signal | Confidence: {confidence:.1%}")
        return "SELL", sl, tp
