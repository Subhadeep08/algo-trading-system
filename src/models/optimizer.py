"""
Portfolio Optimizer — Markowitz Mean-Variance Optimization via cvxpy.

Core idea (Modern Portfolio Theory, Harry Markowitz 1952):
  - Every asset has an expected return and a risk (volatility)
  - Combining assets REDUCES risk if they don't move in lockstep (low correlation)
  - The optimizer finds the ideal weight for each asset given your risk tolerance

Java analogy: cvxpy is like a specialized SQL engine — you declare WHAT you want
(objective + constraints), not HOW to compute it. The solver figures out the math.

Three strategies implemented:
  1. max_sharpe     — best return per unit of risk (most common choice)
  2. min_volatility — safest portfolio regardless of return
  3. efficient_return — hit a target return with minimum risk
"""

import numpy as np
import pandas as pd
import cvxpy as cp


# ─────────────────────────────────────────────────────────────────────────────
# PORTFOLIO METRICS (the numbers shown in the UI)
# ─────────────────────────────────────────────────────────────────────────────

def portfolio_metrics(
    weights: np.ndarray,
    mean_returns: pd.Series,
    cov_matrix: pd.DataFrame,
    trading_days: int = 252,
) -> dict:
    """
    Compute annualised return, volatility, and Sharpe ratio for a given set of weights.

    Args:
        weights      : array of portfolio weights, must sum to 1
        mean_returns : daily mean returns per asset (from historical data)
        cov_matrix   : covariance matrix of daily returns
        trading_days : 252 = standard US market trading days per year

    Returns dict with:
        annual_return : expected yearly return (e.g. 0.12 = 12%)
        annual_vol    : yearly volatility / std deviation (risk)
        sharpe_ratio  : return / risk — higher is better. >1 is good, >2 is great.
    """
    # Daily portfolio return = dot product of weights and individual returns
    # Java: IntStream.range(0,n).mapToDouble(i -> w[i] * r[i]).sum()
    port_return = float(np.dot(weights, mean_returns)) * trading_days

    # Portfolio variance = w^T * Σ * w  (quadratic form — measures combined risk)
    # Σ (cov_matrix) captures how much stocks move together
    port_variance = float(np.dot(weights.T, np.dot(cov_matrix.values, weights)))
    port_vol = np.sqrt(port_variance) * np.sqrt(trading_days)

    # Sharpe ratio: risk-adjusted return. Risk-free rate assumed ~4.5% (2026 T-bill)
    risk_free_rate = 0.045
    sharpe = (port_return - risk_free_rate) / port_vol if port_vol > 0 else 0.0

    return {
        "annual_return": port_return,
        "annual_vol": port_vol,
        "sharpe_ratio": sharpe,
    }


def compute_expected_returns(returns: pd.DataFrame) -> pd.Series:
    """Mean daily return per ticker (annualise later in portfolio_metrics)."""
    return returns.mean()


def compute_covariance_matrix(returns: pd.DataFrame) -> pd.DataFrame:
    """
    Covariance matrix — the core of Markowitz optimisation.

    Shape: (n_assets × n_assets)
    Diagonal = each stock's own variance (self-covariance)
    Off-diagonal = how much two stocks move together

    High correlation between two stocks = less diversification benefit.
    This is why mixing tech + healthcare + energy reduces portfolio risk.
    """
    return returns.cov()


# ─────────────────────────────────────────────────────────────────────────────
# OPTIMIZERS
# ─────────────────────────────────────────────────────────────────────────────

def _base_constraints(weights: cp.Variable, max_weight: float) -> list:
    """Shared constraints used by all three strategies."""
    return [
        cp.sum(weights) == 1,   # weights must sum to 100%
        weights >= 0,           # long-only: no short selling
        weights <= max_weight,  # concentration limit (default 40%)
    ]


def optimize_max_sharpe(
    mean_returns: pd.Series,
    cov_matrix: pd.DataFrame,
    max_weight: float = 0.40,
    trading_days: int = 252,
) -> dict:
    """
    Find weights that maximise the Sharpe ratio.

    Mathematical trick: maximising Sharpe directly is non-convex (hard).
    We use the Sharpe-maximising equivalent: maximise return - λ*risk
    where λ (risk_aversion) is swept to find the efficient frontier peak.

    In practice: we solve for a range of risk_aversion values and pick
    the weights that produced the highest Sharpe ratio.
    """
    n = len(mean_returns)
    best_sharpe = -np.inf
    best_result = None

    # Sweep risk aversion from very aggressive (0.1) to very conservative (100)
    # This traces the Efficient Frontier — each λ gives one point on the curve
    for risk_aversion in np.logspace(-1, 2, 40):
        weights = cp.Variable(n)
        objective = cp.Maximize(
            mean_returns.values @ weights
            - 0.5 * risk_aversion * cp.quad_form(weights, cov_matrix.values)
        )
        constraints = _base_constraints(weights, max_weight)
        problem = cp.Problem(objective, constraints)

        try:
            problem.solve(solver=cp.CLARABEL, verbose=False)
        except Exception:
            continue

        if weights.value is None:
            continue

        w = np.array(weights.value)
        w = np.clip(w, 0, max_weight)          # numerical safety
        w /= w.sum()                            # re-normalise after clipping

        metrics = portfolio_metrics(w, mean_returns, cov_matrix, trading_days)
        if metrics["sharpe_ratio"] > best_sharpe:
            best_sharpe = metrics["sharpe_ratio"]
            best_result = {"weights": w, **metrics}

    if best_result is None:
        # Fallback: equal weight
        w = np.ones(n) / n
        best_result = {"weights": w, **portfolio_metrics(w, mean_returns, cov_matrix)}

    return best_result


def optimize_min_volatility(
    mean_returns: pd.Series,
    cov_matrix: pd.DataFrame,
    max_weight: float = 0.40,
    trading_days: int = 252,
) -> dict:
    """
    Find weights that minimise portfolio volatility (safest portfolio).
    Pure convex QP — single solve, no sweep needed.
    """
    n = len(mean_returns)
    weights = cp.Variable(n)

    objective = cp.Minimize(
        cp.quad_form(weights, cov_matrix.values)  # minimise w^T Σ w
    )
    constraints = _base_constraints(weights, max_weight)
    problem = cp.Problem(objective, constraints)
    problem.solve(solver=cp.CLARABEL, verbose=False)

    w = np.array(weights.value) if weights.value is not None else np.ones(n) / n
    w = np.clip(w, 0, max_weight)
    w /= w.sum()

    return {"weights": w, **portfolio_metrics(w, mean_returns, cov_matrix, trading_days)}


def optimize_efficient_return(
    mean_returns: pd.Series,
    cov_matrix: pd.DataFrame,
    target_annual_return: float,
    max_weight: float = 0.40,
    trading_days: int = 252,
) -> dict:
    """
    Minimise risk while hitting a target annual return.
    Used to generate the Efficient Frontier curve in the UI.
    """
    n = len(mean_returns)
    target_daily = target_annual_return / trading_days

    weights = cp.Variable(n)
    objective = cp.Minimize(
        cp.quad_form(weights, cov_matrix.values)
    )
    constraints = _base_constraints(weights, max_weight) + [
        mean_returns.values @ weights >= target_daily,  # must hit target return
    ]
    problem = cp.Problem(objective, constraints)
    problem.solve(solver=cp.CLARABEL, verbose=False)

    if weights.value is None:
        return None  # target return infeasible for this universe

    w = np.clip(np.array(weights.value), 0, max_weight)
    w /= w.sum()
    return {"weights": w, **portfolio_metrics(w, mean_returns, cov_matrix, trading_days)}


# ─────────────────────────────────────────────────────────────────────────────
# EFFICIENT FRONTIER (the curve shown in the scatter plot)
# ─────────────────────────────────────────────────────────────────────────────

def compute_efficient_frontier(
    mean_returns: pd.Series,
    cov_matrix: pd.DataFrame,
    n_points: int = 50,
    max_weight: float = 0.40,
) -> pd.DataFrame:
    """
    Generate N points on the Efficient Frontier by solving optimize_efficient_return
    at evenly spaced target returns between min and max feasible.

    Returns DataFrame with columns: [annual_return, annual_vol, sharpe_ratio]
    Used to draw the risk/return curve in the UI.
    """
    min_ret = float(mean_returns.min()) * 252
    max_ret = float(mean_returns.max()) * 252
    targets = np.linspace(min_ret * 0.5, max_ret * 0.9, n_points)

    frontier_points = []
    for target in targets:
        result = optimize_efficient_return(mean_returns, cov_matrix, target, max_weight)
        if result:
            frontier_points.append({
                "annual_return": result["annual_return"],
                "annual_vol":    result["annual_vol"],
                "sharpe_ratio":  result["sharpe_ratio"],
            })

    return pd.DataFrame(frontier_points)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT (called by the Streamlit UI)
# ─────────────────────────────────────────────────────────────────────────────

def run_optimizer(
    returns: pd.DataFrame,
    strategy: str = "max_sharpe",
    max_weight: float = 0.40,
    ml_predictions: pd.Series = None,
) -> dict:
    """
    High-level function: takes daily returns, runs chosen strategy, returns results.

    Args:
        returns        : daily returns DataFrame (date × ticker)
        strategy       : "max_sharpe" | "min_volatility"
        max_weight     : max allocation to any single stock (0–1)
        ml_predictions : optional override for expected returns from ML model
                         If provided, uses ML-predicted returns instead of historical mean

    Returns dict:
        weights        : np.ndarray — allocation per ticker
        tickers        : list of ticker names matching weights order
        annual_return  : expected annualised return
        annual_vol     : expected annualised volatility
        sharpe_ratio   : Sharpe ratio
        frontier       : DataFrame of efficient frontier points
    """
    mean_returns = compute_expected_returns(returns)
    cov_matrix   = compute_covariance_matrix(returns)

    # Override expected returns with ML predictions if available
    if ml_predictions is not None:
        # Align on tickers (ML might not cover all tickers)
        common = mean_returns.index.intersection(ml_predictions.index)
        mean_returns[common] = ml_predictions[common]

    if strategy == "max_sharpe":
        result = optimize_max_sharpe(mean_returns, cov_matrix, max_weight)
    else:
        result = optimize_min_volatility(mean_returns, cov_matrix, max_weight)

    frontier = compute_efficient_frontier(mean_returns, cov_matrix, max_weight=max_weight)

    return {
        **result,
        "tickers": list(returns.columns),
        "frontier": frontier,
    }
