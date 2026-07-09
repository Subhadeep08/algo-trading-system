"""Shared constants and configuration for the cockpit package."""

from __future__ import annotations

import os
from pathlib import Path

# -- Repository paths ----------------------------------------------------------
REPO_ROOT = Path(os.environ.get("REPO_ROOT", Path(__file__).resolve().parent.parent))
PORTFOLIO_YAML      = REPO_ROOT / "references" / "portfolio.yaml"
WATCHLIST_PATH      = REPO_ROOT / ".claude" / "skills" / "portfolio-cockpit" / "references" / "watchlist.md"
SCREENING_RESULTS   = REPO_ROOT / ".claude" / "skills" / "portfolio-cockpit" / "references" / "screening-results.md"
LIVE_PRICES_MD      = REPO_ROOT / "references" / "live-prices.md"
PORTFOLIO_DATA_MD   = REPO_ROOT / ".claude" / "skills" / "portfolio-cockpit" / "references" / "portfolio-data.md"

# -- Market data ---------------------------------------------------------------
NSE_SUFFIX              = ".NS"
NIFTY_50_SYMBOL         = "^NSEI"
NIFTY_500_SYMBOL        = "^CRSLDX"
VOLUME_LOOKBACK_PERIOD  = "21d"   # ~1 calendar month of trading sessions
IST_ZONE                = "Asia/Kolkata"

# -- Cockpit thresholds --------------------------------------------------------
WINNER_PNL_PCT              = 15.0    # P&L above -> ratchet candidate
UNDERPERFORMER_PNL_PCT      = -10.0   # P&L below -> review flag
NEAR_COST_BAND_PCT          = 5.0     # |P&L| <= this -> near-cost band
SL_DANGER_BUFFER_PCT        = 2.0     # buffer < 2% -> danger zone
SL_MONITOR_BUFFER_PCT       = 5.0     # buffer < 5% -> monitor zone
TRAILING_SL_PULLBACK        = 0.10    # ratchet to 10% below current price
HIGH_VOLUME_MULTIPLIER      = 2.0     # >= 2x 20-day avg -> high volume alert
ELEVATED_VOLUME_MULTIPLIER  = 1.5     # >= 1.5x 20-day avg -> elevated volume
RSI_PROXY_MOVE_PCT          = 5.0     # single-day move exceeding this flags RSI risk
VOLATILE_DAY_MOVE_PCT       = 2.0     # stay-put reminder threshold
GTT_APPROACHING_PCT         = 3.0     # CMP within 3% above GTT -> approaching flag

# -- Telegram ------------------------------------------------------------------
TELEGRAM_MESSAGE_LIMIT = 4000         # safe ceiling below the 4096-char API limit

# -- PMS screening gate thresholds (Gate 1-4 + secondary overlay) -------------
UD_ENTRY_THRESHOLD          = 1.25    # Gate 2 -- minimum U/D ratio to qualify
UD_DISTRIBUTION_THRESHOLD   = 0.75    # Gate 2 -- U/D_21 below this flags distribution
UD_LOOKBACK_50              = 50      # sessions for long-term U/D ratio
UD_LOOKBACK_21              = 21      # sessions for near-term U/D ratio
STAGE2_MA_PERIOD            = 150     # days (~= 30-week Weinstein MA)
EBITDA_CFO_MIN_RATIO        = 0.85    # Gate 3 -- CFO/EBITDA floor (earnings quality)
QUARTERLY_EPS_GROWTH_MIN    = 25.0    # Gate 4 -- minimum quarterly EPS growth %
ANNUAL_PROFIT_GROWTH_MIN    = 20.0    # Gate 4 -- minimum annual PAT CAGR %
ROCE_MIN_PCT                = 15.0    # Gate 4 -- minimum ROCE %
DE_RATIO_MAX                = 0.5     # Gate 4 -- maximum debt-to-equity
PE_STRESS_RETRACEMENT_MAX   = 0.10    # within 10% of 52-week high (active markup phase)
RISK_PER_TRADE_PCT          = 0.02    # position sizing -- 2% of portfolio capital at risk
MAX_POSITION_PCT            = 0.20    # position sizing -- 20% single-holding cap
SL_BELOW_ENTRY_PCT          = 0.09    # default GTT stop-loss 9% below entry
