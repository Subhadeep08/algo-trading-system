"""
Backtest Engine — Vectorized monthly-rebalancing portfolio simulation.

Approach: Vectorized backtesting (NOT event-driven like backtrader/zipline).
  - Vectorized = pandas operations across the entire time series at once
  - Event-driven = loop over every tick and fire events (complex, overkill here)
  - For monthly rebalancing, vectorized is 100x faster and much simpler

Strategy simulated:
  1. Start with $10,000
  2. Every month: re-run Markowitz optimizer on trailing 252-day window
  3. Rebalance portfolio to new optimal weights
  4. Apply 0.1% transaction cost per rebalance (realistic estimate)
  5. Track daily portfolio value → equity curve

Benchmark: Buy-and-hold S&P 500 (^GSPC) — what we compare against.

Java analogy:
  - The backtest loop = a stateful reduce() over a time-ordered list
  - Each rebalancing = a scheduled batch job (cron-like, monthly trigger)
"""

import numpy as np
import pandas as pd
from datetime import datetime

from src.models.optimizer import (
    compute_expected_returns,
    compute_covariance_matrix,
    optimize_max_sharpe,
    optimize_min_volatility,
)


# ─────────────────────────────────────────────────────────────────────────────
# CORE BACKTEST
# ─────────────────────────────────────────────────────────────────────────────

def run_backtest(
    prices: pd.DataFrame,
    strategy: str = "max_sharpe",
    initial_capital: float = 10_000.0,
    rebalance_freq: str = "ME",          # "ME" = month-end, "QE" = quarter-end
    transaction_cost: float = 0.001,     # 0.1% per rebalance (realistic for ETFs)
    max_weight: float = 0.40,
    lookback_days: int = 252,            # trailing window used to estimate returns/cov
) -> dict:
    """
    Simulate the portfolio from the first available date to the last.

    Args:
        prices           : Close price DataFrame (date × ticker) from fetcher.py
        strategy         : "max_sharpe" | "min_volatility"
        initial_capital  : starting portfolio value in USD
        rebalance_freq   : pandas offset alias for rebalancing schedule
        transaction_cost : fraction of portfolio value lost per rebalance
        max_weight       : max allocation to any single stock
        lookback_days    : days of history used to compute mean/cov at each rebalance

    Returns dict:
        equity_curve     : pd.Series — portfolio value per day
        benchmark_curve  : pd.Series — $10k buy-and-hold S&P 500
        weights_history  : pd.DataFrame — weights at each rebalance date
        metrics          : dict of performance stats
        rebalance_dates  : list of dates when portfolio was rebalanced
    """
    daily_returns = prices.pct_change().dropna()
    tickers = list(prices.columns)
    n = len(tickers)

    # Equal weight on day 1 (before first optimisation)
    current_weights = np.ones(n) / n

    # Rebalance dates = month-end dates within our data range
    rebalance_dates = pd.date_range(
        start=prices.index[lookback_days],   # need lookback_days of history first
        end=prices.index[-1],
        freq=rebalance_freq,
    )
    rebalance_set = set(rebalance_dates.normalize())

    # ── Day-by-day simulation ─────────────────────────────────────────────────
    portfolio_values = []
    portfolio_value  = initial_capital
    weights_log      = {}

    for i, date in enumerate(daily_returns.index):
        # Check if today is a rebalance date
        if date.normalize() in rebalance_set:
            # Use trailing window of history up to today
            window_start = max(0, i - lookback_days)
            window_returns = daily_returns.iloc[window_start:i]

            if len(window_returns) >= 30:   # need minimum history
                try:
                    mean_ret = compute_expected_returns(window_returns)
                    cov_mat  = compute_covariance_matrix(window_returns)

                    if strategy == "max_sharpe":
                        result = optimize_max_sharpe(mean_ret, cov_mat, max_weight)
                    else:
                        result = optimize_min_volatility(mean_ret, cov_mat, max_weight)

                    new_weights = result["weights"]

                    # Transaction cost: proportional to how much weights changed
                    # turnover = sum of absolute weight changes
                    turnover = np.sum(np.abs(new_weights - current_weights))
                    cost = portfolio_value * transaction_cost * turnover
                    portfolio_value -= cost

                    current_weights = new_weights
                    weights_log[date] = dict(zip(tickers, current_weights))

                except Exception:
                    pass  # keep current weights if optimiser fails

        # Apply today's return to portfolio
        # Portfolio return = weighted sum of individual stock returns
        day_returns = daily_returns.loc[date].values
        portfolio_return = float(np.dot(current_weights, day_returns))
        portfolio_value *= (1 + portfolio_return)
        portfolio_values.append((date, portfolio_value))

    equity_curve = pd.Series(
        dict(portfolio_values),
        name="Strategy",
    )

    weights_history = pd.DataFrame(weights_log).T if weights_log else pd.DataFrame()

    return {
        "equity_curve":    equity_curve,
        "weights_history": weights_history,
        "rebalance_dates": list(weights_log.keys()),
        "metrics":         compute_metrics(equity_curve, initial_capital),
    }


# ─────────────────────────────────────────────────────────────────────────────
# BENCHMARK
# ─────────────────────────────────────────────────────────────────────────────

def build_benchmark_curve(
    benchmark_returns: pd.Series,
    equity_curve_index: pd.DatetimeIndex,
    initial_capital: float = 10_000.0,
) -> pd.Series:
    """
    Build S&P 500 buy-and-hold equity curve aligned to the strategy dates.
    Reindex to match the strategy's date range (inner join).
    """
    aligned = benchmark_returns.reindex(equity_curve_index).dropna()
    curve = initial_capital * (1 + aligned).cumprod()
    curve.name = "S&P 500"
    return curve


# ─────────────────────────────────────────────────────────────────────────────
# PERFORMANCE METRICS
# ─────────────────────────────────────────────────────────────────────────────

def compute_metrics(
    equity_curve: pd.Series,
    initial_capital: float = 10_000.0,
    risk_free_rate: float = 0.045,
    trading_days: int = 252,
) -> dict:
    """
    Compute standard quant performance metrics from an equity curve.

    All metrics are annualised for comparability.
    """
    if equity_curve.empty or len(equity_curve) < 2:
        return {}

    daily_returns = equity_curve.pct_change().dropna()
    total_days    = len(equity_curve)
    years         = total_days / trading_days

    # Total return: how much did $1 grow?
    total_return = (equity_curve.iloc[-1] / initial_capital) - 1

    # CAGR: Compound Annual Growth Rate — normalised for time period
    # Formula: (end_value / start_value)^(1/years) - 1
    cagr = (equity_curve.iloc[-1] / initial_capital) ** (1 / years) - 1

    # Annualised volatility
    annual_vol = daily_returns.std() * np.sqrt(trading_days)

    # Sharpe ratio (annualised)
    sharpe = (cagr - risk_free_rate) / annual_vol if annual_vol > 0 else 0.0

    # Maximum Drawdown — largest peak-to-trough decline
    # rolling(window).max() = running peak value up to each date
    rolling_peak = equity_curve.cummax()
    drawdown     = (equity_curve - rolling_peak) / rolling_peak
    max_drawdown = float(drawdown.min())   # most negative value

    # Calmar ratio: CAGR / |MaxDrawdown| — measures return per unit of drawdown risk
    calmar = cagr / abs(max_drawdown) if max_drawdown != 0 else 0.0

    # Win rate: % of days with positive return
    win_rate = float((daily_returns > 0).mean())

    return {
        "total_return":  total_return,
        "cagr":          cagr,
        "annual_vol":    annual_vol,
        "sharpe_ratio":  sharpe,
        "max_drawdown":  max_drawdown,
        "calmar_ratio":  calmar,
        "win_rate":      win_rate,
        "final_value":   equity_curve.iloc[-1],
        "initial_value": initial_capital,
    }


def compare_metrics(strategy_metrics: dict, benchmark_metrics: dict) -> pd.DataFrame:
    """
    Side-by-side comparison table of strategy vs S&P 500.
    Shown in the Backtest Dashboard UI.
    """
    labels = {
        "total_return": "Total Return",
        "cagr":         "CAGR",
        "annual_vol":   "Annual Volatility",
        "sharpe_ratio": "Sharpe Ratio",
        "max_drawdown": "Max Drawdown",
        "calmar_ratio": "Calmar Ratio",
        "win_rate":     "Win Rate",
        "final_value":  "Final Value ($)",
    }
    rows = []
    for key, label in labels.items():
        s_val = strategy_metrics.get(key, 0)
        b_val = benchmark_metrics.get(key, 0)

        # Format as percentage or raw number
        if key in ("total_return", "cagr", "annual_vol", "max_drawdown", "win_rate"):
            s_fmt = f"{s_val*100:.1f}%"
            b_fmt = f"{b_val*100:.1f}%"
        elif key == "final_value":
            s_fmt = f"${s_val:,.0f}"
            b_fmt = f"${b_val:,.0f}"
        else:
            s_fmt = f"{s_val:.2f}"
            b_fmt = f"{b_val:.2f}"

        rows.append({"Metric": label, "Strategy": s_fmt, "S&P 500": b_fmt})

    return pd.DataFrame(rows).set_index("Metric")
