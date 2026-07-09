"""
Shared fixtures and DataFrame factory helpers for the NSE Portfolio Cockpit test suite.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import numpy as np
import pandas as pd
import pytest

# Make scripts/ importable without installation
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


# ── DataFrame factory helpers (used across multiple test modules) ─────────────

def make_ohlcv(
    n_rows: int,
    closes,
    opens=None,
    volume: float = 1_000.0,
) -> pd.DataFrame:
    """Create an OHLCV DataFrame with a business-day DatetimeIndex."""
    dates = pd.bdate_range("2023-01-01", periods=n_rows)
    close_arr = np.asarray(closes, dtype=float) if hasattr(closes, "__len__") else np.full(n_rows, float(closes))
    open_arr = (
        np.asarray(opens, dtype=float)
        if opens is not None and hasattr(opens, "__len__")
        else (np.full(n_rows, float(opens)) if opens is not None else close_arr * 0.995)
    )
    vol_arr = np.full(n_rows, float(volume))
    return pd.DataFrame(
        {
            "Open":   open_arr,
            "High":   close_arr * 1.01,
            "Low":    close_arr * 0.99,
            "Close":  close_arr,
            "Volume": vol_arr,
        },
        index=dates,
    )


def make_financial_df(**row_values) -> pd.DataFrame:
    """
    Annual financials DataFrame (yfinance layout): rows = metric names, cols = years.
    Keyword args: metric_name=[most_recent, yr_minus1, yr_minus2, yr_minus3]
    """
    n_cols = max(len(v) for v in row_values.values())
    return pd.DataFrame(row_values, index=range(n_cols)).T


def make_quarterly_df(**row_values) -> pd.DataFrame:
    """
    Quarterly financials DataFrame: rows = metric names, cols = quarters (≥5 needed).
    Keyword args: metric_name=[q0, q1, q2, q3, q4] (most recent first)
    """
    n_cols = max(len(v) for v in row_values.values())
    return pd.DataFrame(row_values, index=range(n_cols)).T


def make_mock_ticker(
    *,
    history_1y: Optional[pd.DataFrame] = None,
    history_3mo: Optional[pd.DataFrame] = None,
    history_6mo: Optional[pd.DataFrame] = None,
    history_2mo: Optional[pd.DataFrame] = None,
    history_21d: Optional[pd.DataFrame] = None,
    info: Optional[dict] = None,
    financials: Optional[pd.DataFrame] = None,
    quarterly_financials: Optional[pd.DataFrame] = None,
    cashflow: Optional[pd.DataFrame] = None,
    balance_sheet: Optional[pd.DataFrame] = None,
    fast_info_price: Optional[float] = None,
    fast_info_prev_close: Optional[float] = None,
):
    """Return a mock yf.Ticker instance with pre-configured return values."""
    _histories = {
        "1y":   history_1y  if history_1y  is not None else pd.DataFrame(),
        "3mo":  history_3mo if history_3mo is not None else pd.DataFrame(),
        "6mo":  history_6mo if history_6mo is not None else pd.DataFrame(),
        "2mo":  history_2mo if history_2mo is not None else pd.DataFrame(),
        "21d":  history_21d if history_21d is not None else pd.DataFrame(),
    }

    class _MockTicker:
        def history(self, period=None, **_):
            return _histories.get(period, pd.DataFrame())

        fast_info = SimpleNamespace(
            last_price=fast_info_price,
            previous_close=fast_info_prev_close,
        )

    ticker = _MockTicker()
    ticker.info = info or {}
    ticker.financials = financials if financials is not None else pd.DataFrame()
    ticker.quarterly_financials = quarterly_financials if quarterly_financials is not None else pd.DataFrame()
    ticker.cashflow = cashflow if cashflow is not None else pd.DataFrame()
    ticker.balance_sheet = balance_sheet if balance_sheet is not None else pd.DataFrame()
    return ticker
