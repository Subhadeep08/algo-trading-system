"""
Feature Engineering — Technical indicators + risk metrics for ML input.

Every ML model needs a feature matrix X where:
  - rows    = trading days (samples)
  - columns = indicators (features)

Java analogy: this module is a data transformation pipeline,
like a chain of Stream.map() operations applied to raw price data.

Libraries used:
  - ta (technical analysis): pre-built indicators, wraps pandas math
  - pandas: rolling windows, pct_change, shift
"""

import pandas as pd
import numpy as np
import ta


# ─────────────────────────────────────────────────────────────────────────────
# SINGLE TICKER FEATURE BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def build_features(df: pd.DataFrame, forward_days: int = 5) -> pd.DataFrame:
    """
    Given OHLCV data for ONE ticker, return a feature matrix ready for ML.

    Args:
        df           : DataFrame with columns [Open, High, Low, Close, Volume]
        forward_days : prediction horizon — we predict returns N days ahead

    Returns:
        DataFrame with all features + target column 'future_return'
        NaN rows (from rolling windows warming up) are dropped.

    The 'future_return' column is what the ML model learns to predict:
        future_return[t] = (Close[t + forward_days] - Close[t]) / Close[t]
    This is a REGRESSION target (predict a number, not a category).
    """
    feat = pd.DataFrame(index=df.index)

    close = df["Close"]
    high  = df["High"]
    low   = df["Low"]
    vol   = df["Volume"]

    # ── 1. MOMENTUM INDICATORS ───────────────────────────────────────────────
    # RSI (Relative Strength Index, 14-day)
    # Range: 0–100.  >70 = overbought (might drop),  <30 = oversold (might rise)
    feat["rsi_14"] = ta.momentum.RSIIndicator(close, window=14).rsi()

    # MACD — difference between 12-day EMA and 26-day EMA
    # macd_diff > 0 = bullish momentum,  < 0 = bearish
    macd_obj = ta.trend.MACD(close, window_slow=26, window_fast=12, window_sign=9)
    feat["macd"]        = macd_obj.macd()           # MACD line
    feat["macd_signal"] = macd_obj.macd_signal()    # Signal line (9-day EMA of MACD)
    feat["macd_diff"]   = macd_obj.macd_diff()      # Histogram = MACD - Signal

    # ── 2. TREND INDICATORS ──────────────────────────────────────────────────
    # Simple Moving Averages — lag indicator: price crossing SMA = signal
    feat["sma_20"] = ta.trend.SMAIndicator(close, window=20).sma_indicator()
    feat["sma_50"] = ta.trend.SMAIndicator(close, window=50).sma_indicator()

    # Price relative to moving averages (normalised so it works across stocks)
    feat["price_to_sma20"] = close / feat["sma_20"] - 1   # 0.05 = 5% above SMA
    feat["price_to_sma50"] = close / feat["sma_50"] - 1

    # EMA (Exponential Moving Average) — weights recent prices more
    feat["ema_12"] = ta.trend.EMAIndicator(close, window=12).ema_indicator()
    feat["ema_26"] = ta.trend.EMAIndicator(close, window=26).ema_indicator()
    feat["ema_cross"] = feat["ema_12"] / feat["ema_26"] - 1  # > 0 = bullish

    # ── 3. VOLATILITY INDICATORS ─────────────────────────────────────────────
    # Bollinger Bands — envelope around price based on std deviation
    # Price near upper band = overbought,  near lower band = oversold
    bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
    feat["bb_upper"]   = bb.bollinger_hband()
    feat["bb_lower"]   = bb.bollinger_lband()
    feat["bb_mid"]     = bb.bollinger_mavg()
    feat["bb_width"]   = (feat["bb_upper"] - feat["bb_lower"]) / feat["bb_mid"]  # band width
    feat["bb_pct"]     = bb.bollinger_pband()   # where price sits within bands (0–1)

    # ATR (Average True Range, 14-day) — raw volatility in price units
    feat["atr_14"] = ta.volatility.AverageTrueRange(high, low, close, window=14).average_true_range()

    # Rolling std of daily returns — statistical volatility
    daily_returns = close.pct_change()
    feat["vol_10d"]  = daily_returns.rolling(10).std()
    feat["vol_20d"]  = daily_returns.rolling(20).std()

    # ── 4. VOLUME INDICATORS ─────────────────────────────────────────────────
    # OBV (On-Balance Volume) — cumulative volume flow; rising OBV = buying pressure
    feat["obv"] = ta.volume.OnBalanceVolumeIndicator(close, vol).on_balance_volume()
    # Normalise OBV (raw OBV is non-stationary like price)
    feat["obv_pct"] = feat["obv"].pct_change()

    # Volume relative to 20-day average — spike > 1 = unusual activity
    feat["volume_ratio"] = vol / vol.rolling(20).mean()

    # ── 5. LAGGED RETURNS (momentum features) ────────────────────────────────
    # "How much did this stock move in the last N days?"
    # These encode short-term and medium-term momentum for the ML model.
    for lag in [1, 3, 5, 10, 20]:
        feat[f"return_{lag}d"] = close.pct_change(lag)

    # ── 6. TARGET VARIABLE ───────────────────────────────────────────────────
    # shift(-forward_days) moves future price to current row
    # e.g. shift(-5): row at 2024-01-01 gets the close price from 2024-01-08
    feat["future_return"] = close.pct_change(forward_days).shift(-forward_days)

    # Drop NaN rows (created by rolling windows warming up + final shifted rows)
    feat.dropna(inplace=True)

    return feat


# ─────────────────────────────────────────────────────────────────────────────
# MULTI-TICKER FEATURE BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def build_all_features(
    raw_data: dict,
    forward_days: int = 5,
) -> dict:
    """
    Apply build_features() to every ticker in raw_data.

    Returns: { "AAPL": DataFrame, "MSFT": DataFrame, ... }

    raw_data comes from fetch_stock_data() in fetcher.py.
    """
    all_features = {}
    for ticker, df in raw_data.items():
        try:
            all_features[ticker] = build_features(df, forward_days)
        except Exception as e:
            print(f"[indicators] Failed to build features for {ticker}: {e}")
    return all_features


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE COLUMN NAMES (used by predictor to split X and y)
# ─────────────────────────────────────────────────────────────────────────────

FEATURE_COLS = [
    "rsi_14",
    "macd", "macd_signal", "macd_diff",
    "sma_20", "sma_50", "price_to_sma20", "price_to_sma50",
    "ema_12", "ema_26", "ema_cross",
    "bb_upper", "bb_lower", "bb_mid", "bb_width", "bb_pct",
    "atr_14",
    "vol_10d", "vol_20d",
    "obv", "obv_pct",
    "volume_ratio",
    "return_1d", "return_3d", "return_5d", "return_10d", "return_20d",
]

TARGET_COL = "future_return"
