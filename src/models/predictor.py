"""
ML Return Predictor — Random Forest trained per ticker to predict forward returns.

Why Random Forest?
  - Handles non-linear relationships (stock markets are non-linear)
  - Robust to outliers (earnings shocks, flash crashes)
  - No feature scaling required (unlike neural nets)
  - Built-in feature importance (tells us which indicators matter most)
  - Fast to train on ~700 rows — no GPU needed

Why NOT LSTM/neural nets for a prototype?
  - Needs much more data + tuning time
  - Black box — hard to explain to stakeholders
  - Random Forest gets 80% of the value in 20% of the complexity

Training strategy: Walk-Forward Validation
  - Train on first 70% of history → predict on last 30%
  - This simulates real trading: you only ever know the past
  - NEVER train on future data (look-ahead bias = the #1 mistake in quant finance)

Java analogy:
  - RandomForestRegressor ≈ an ensemble of decision trees (like N parallel if-else trees)
  - StandardScaler ≈ normalizing inputs before a neural net layer
  - Pipeline ≈ a chain of middleware/filters applied in sequence
"""

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.metrics import mean_absolute_error, r2_score
import warnings
warnings.filterwarnings("ignore")

from src.features.indicators import FEATURE_COLS, TARGET_COL


# ─────────────────────────────────────────────────────────────────────────────
# SINGLE TICKER MODEL
# ─────────────────────────────────────────────────────────────────────────────

def train_ticker_model(
    features_df: pd.DataFrame,
    train_ratio: float = 0.70,
) -> dict:
    """
    Train a Random Forest on one ticker's feature matrix.

    Walk-forward split:
        |────────── TRAIN (70%) ──────────|── TEST (30%) ──|
        oldest                                          newest

    Returns:
        model        : trained sklearn Pipeline (scaler + forest)
        metrics      : MAE and R² on the test set
        feature_imp  : dict of feature → importance score
        last_pred    : predicted return for the LATEST available data point
                       (this is what gets fed into the optimizer)
    """
    # Keep only columns that exist in this feature set
    available = [c for c in FEATURE_COLS if c in features_df.columns]
    X = features_df[available].values
    y = features_df[TARGET_COL].values

    # Walk-forward split — never shuffle time series data
    # (shuffling would leak future info into training set)
    split_idx = int(len(X) * train_ratio)
    X_train, X_test = X[:split_idx], X[split_idx:]
    y_train, y_test = y[:split_idx], y[split_idx:]

    if len(X_train) < 50:
        # Not enough data to train — return equal-weight fallback
        return {"last_pred": float(np.mean(y)), "metrics": {}, "model": None, "feature_imp": {}}

    # Pipeline: StandardScaler → RandomForest
    # StandardScaler: mean=0, std=1 per feature (helps some tree ensembles slightly)
    model = Pipeline([
        ("scaler", StandardScaler()),
        ("forest", RandomForestRegressor(
            n_estimators=200,      # 200 trees — more = better but slower
            max_depth=6,           # cap depth to prevent overfitting on small data
            min_samples_leaf=10,   # each leaf needs ≥10 samples (regularization)
            max_features="sqrt",   # each split considers sqrt(n_features) features
            random_state=42,
            n_jobs=-1,             # use all CPU cores (Java: ForkJoinPool)
        )),
    ])

    model.fit(X_train, y_train)

    # ── Evaluate on held-out test set ────────────────────────────────────────
    y_pred = model.predict(X_test)
    mae  = mean_absolute_error(y_test, y_pred)
    r2   = r2_score(y_test, y_pred)

    # ── Feature importance ───────────────────────────────────────────────────
    # Random Forest tracks how much each feature reduces prediction error
    forest_step = model.named_steps["forest"]
    importance_map = dict(zip(available, forest_step.feature_importances_))
    top_features = dict(
        sorted(importance_map.items(), key=lambda x: x[1], reverse=True)[:10]
    )

    # ── Predict on the LATEST data point (most recent trading day) ───────────
    last_X = X[-1].reshape(1, -1)   # reshape: single row → 2D array (sklearn requirement)
    last_pred = float(model.predict(last_X)[0])

    return {
        "model":       model,
        "metrics":     {"mae": mae, "r2": r2, "n_train": split_idx, "n_test": len(X_test)},
        "feature_imp": top_features,
        "last_pred":   last_pred,       # 5-day forward return prediction
    }


# ─────────────────────────────────────────────────────────────────────────────
# MULTI-TICKER RUNNER
# ─────────────────────────────────────────────────────────────────────────────

def train_all_models(
    all_features: dict,
    train_ratio: float = 0.70,
) -> dict:
    """
    Train one model per ticker.

    Args:
        all_features : { "AAPL": DataFrame, "MSFT": DataFrame, ... }
                       output from build_all_features() in indicators.py

    Returns:
        { "AAPL": { model, metrics, feature_imp, last_pred }, ... }
    """
    results = {}
    for ticker, features_df in all_features.items():
        print(f"  Training {ticker}...")
        results[ticker] = train_ticker_model(features_df, train_ratio)
    return results


def get_ml_predictions(model_results: dict) -> pd.Series:
    """
    Extract the latest predicted return for each ticker.

    Returns a pd.Series:  { "AAPL": 0.023, "MSFT": -0.005, ... }
    These are 5-day forward return predictions (annualised by optimizer).

    This Series is passed as ml_predictions= to run_optimizer() in optimizer.py
    to override historical mean returns with ML-predicted returns.
    """
    preds = {
        ticker: res["last_pred"]
        for ticker, res in model_results.items()
        if res.get("last_pred") is not None
    }
    # Annualise: 5-day prediction → yearly (252 trading days / 5 = ~50 periods)
    # Divide by 5 to get daily rate, then multiply by 252
    return pd.Series({t: v / 5 for t, v in preds.items()})


def get_model_report(model_results: dict) -> pd.DataFrame:
    """
    Build a summary DataFrame of model performance per ticker.
    Shown in the UI as a diagnostics table.

         MAE      R²   n_train  n_test  top_feature
    AAPL 0.012   0.08   490     210     vol_20d
    MSFT 0.011   0.05   490     210     rsi_14
    """
    rows = []
    for ticker, res in model_results.items():
        m = res.get("metrics", {})
        fi = res.get("feature_imp", {})
        top = next(iter(fi), "N/A") if fi else "N/A"
        rows.append({
            "Ticker":      ticker,
            "MAE":         round(m.get("mae", 0), 4),
            "R²":          round(m.get("r2", 0), 4),
            "Train Days":  m.get("n_train", 0),
            "Test Days":   m.get("n_test", 0),
            "Top Feature": top,
            "Prediction":  f"{res.get('last_pred', 0)*100:.2f}%",
        })
    return pd.DataFrame(rows).set_index("Ticker")
