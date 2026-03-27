"""
Data Pipeline — yfinance wrapper with caching and validation.

Java analogy:
- DataFetcher class  = a DAO (Data Access Object)
- @st.cache_data     = @Cacheable in Spring — result is memoized per unique args
- DataFrame          = a ResultSet that knows linear algebra
"""

import yfinance as yf
import pandas as pd
import numpy as np
import streamlit as st
from datetime import datetime, timedelta


# ── Popular stock universes the user can pick from in the UI ──────────────────
PRESET_UNIVERSES = {
    "Tech Giants": ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META"],
    "S&P 500 Sample": ["AAPL", "MSFT", "JPM", "JNJ", "XOM", "PG", "V", "UNH", "HD", "MA"],
    "Finance": ["JPM", "BAC", "GS", "MS", "WFC", "C", "AXP", "BLK"],
    "Healthcare": ["JNJ", "UNH", "PFE", "ABT", "MRK", "TMO", "DHR", "AMGN"],
    "Custom": [],  # user types their own tickers
}


# ── Cache TTL: 1 hour. Re-fetches if stale. ──────────────────────────────────
@st.cache_data(ttl=3600, show_spinner="Fetching market data...")
def fetch_stock_data(
    tickers: tuple,
    start_date: str,
    end_date: str,
) -> dict:
    """
    Fetch OHLCV data for a list of tickers.

    Returns a dict:  { "AAPL": DataFrame(Date, Open, High, Low, Close, Volume), ... }

    OHLCV = Open, High, Low, Close, Volume — the atomic unit of price data.
    Each row = one trading day.

    Note: tickers is a tuple (not list) because st.cache_data requires hashable args.
    Java analogy: only serializable objects can be cache keys.
    """
    result = {}
    failed = []

    for ticker in tickers:
        try:
            df = yf.download(
                ticker,
                start=start_date,
                end=end_date,
                progress=False,    # suppress noisy console output
                auto_adjust=True,  # adjusts Close for splits/dividends automatically
            )

            if df.empty:
                failed.append(ticker)
                continue

            # Flatten multi-level column index that yfinance sometimes returns
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            df.index = pd.to_datetime(df.index)
            df.dropna(inplace=True)
            result[ticker] = df

        except Exception as e:
            failed.append(ticker)
            print(f"[fetcher] Failed to fetch {ticker}: {e}")

    if failed:
        print(f"[fetcher] Could not fetch: {failed}")

    return result


@st.cache_data(ttl=3600, show_spinner="Building price matrix...")
def get_close_prices(
    tickers: tuple,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """
    Returns a single DataFrame of daily Close prices, one column per ticker.

         AAPL    MSFT    GOOGL
    Date
    2020-01-02  300.35  160.62  ...
    2020-01-03  297.43  158.96  ...

    Drops any ticker that failed to fetch (no silent NaN columns).
    """
    raw = fetch_stock_data(tickers, start_date, end_date)

    prices = pd.DataFrame({
        ticker: df["Close"]
        for ticker, df in raw.items()
    })

    # Forward-fill then drop remaining NaNs (handles market holidays per exchange)
    prices.ffill(inplace=True)
    prices.dropna(inplace=True)

    return prices


def get_daily_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """
    Percentage change day-over-day.
    pct_change() = (today - yesterday) / yesterday

    First row is always NaN (no previous day), so we drop it.
    """
    return prices.pct_change().dropna()


def get_cumulative_returns(returns: pd.DataFrame) -> pd.DataFrame:
    """
    Compound growth: (1 + r1) * (1 + r2) * ... - 1
    Used for equity curve charts.
    cumprod() = cumulative product — Java: stream reduce with multiply.
    """
    return (1 + returns).cumprod() - 1


def get_benchmark_returns(start_date: str, end_date: str) -> pd.Series:
    """
    Fetch S&P 500 (^GSPC) as the benchmark to beat.
    Returns daily returns as a Series indexed by Date.
    """
    prices = get_close_prices(("^GSPC",), start_date, end_date)
    returns = get_daily_returns(prices)
    return returns["^GSPC"].rename("S&P 500")


def validate_tickers(raw_input: str) -> list:
    """
    Parse comma-separated ticker input from UI.
    'aapl , MSFT , gOOgl' -> ['AAPL', 'MSFT', 'GOOGL']
    """
    return [t.strip().upper() for t in raw_input.split(",") if t.strip()]


def get_date_defaults() -> tuple:
    """
    Default date range: last 3 years.
    Enough history for ML training + walk-forward validation.
    """
    end = datetime.today()
    start = end - timedelta(days=3 * 365)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
