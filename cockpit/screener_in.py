"""Screener.in data client for NSE fundamental data.

Provides ScreenerInClient (authenticated web client) and ScreenerInData
(parsed financial metrics). Used as a fallback when yfinance returns
incomplete data for NSE-listed Indian stocks.

Authentication: Set SCREENER_IN_SESSION env var to the sessionid cookie
value from a logged-in screener.in browser session (~30 day lifetime).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Optional

import requests

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_float(value) -> Optional[float]:
    """Parse a number from a string that may contain %, commas, or spaces."""
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return float(value)
        cleaned = re.sub(r"[,%\s]", "", str(value))
        if not cleaned or cleaned in ("-", "--", "N/A", "NA"):
            return None
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def _find_ratio(ratios: list, *names: str) -> Optional[float]:
    """Search a Screener.in ratios list for any of the given names."""
    if not ratios:
        return None
    names_lower = {n.lower() for n in names}
    for item in ratios:
        try:
            if str(item.get("name", "")).strip().lower() in names_lower:
                return _parse_float(item.get("value"))
        except Exception:
            continue
    return None


def _extract_series(table: dict, *keys: str) -> list[float]:
    """
    Extract a numeric series from a Screener.in table dict.

    The table may be a flat dict (key → list) or have a ``rows`` list of
    {title, values} objects.  Returns values oldest-first as stored by
    Screener.in; callers reverse as needed.
    """
    for key in keys:
        # Format A — flat dict: {"Sales": [1000, 900, ...], ...}
        if key in table and isinstance(table[key], list):
            values = [v for v in (_parse_float(x) for x in table[key]) if v is not None]
            if values:
                return values
        # Format B — rows array: {"rows": [{"title": "Sales", "values": [...]}, ...]}
        for row in table.get("rows", []):
            try:
                if str(row.get("title", "")).strip().lower() == key.lower():
                    raw = row.get("values", [])
                    values = [v for v in (_parse_float(x) for x in raw) if v is not None]
                    if values:
                        return values
            except Exception:
                continue
    return []


# ---------------------------------------------------------------------------
# Data container
# ---------------------------------------------------------------------------


@dataclass
class ScreenerInData:
    """Parsed financial data for one NSE ticker from Screener.in."""

    ticker: str

    # Pre-calculated ratios (Screener.in "Key Ratios" section)
    roce_pct: Optional[float] = None
    roe_pct: Optional[float] = None
    de_ratio: Optional[float] = None
    current_pe: Optional[float] = None
    pb_ratio: Optional[float] = None

    # Annual P&L — most-recent first; need ≥ 4 for 3-year CAGR
    annual_net_profit: list[float] = field(default_factory=list)
    annual_revenue: list[float] = field(default_factory=list)
    annual_ebitda: list[float] = field(default_factory=list)
    annual_cfo: list[float] = field(default_factory=list)
    annual_capex: list[float] = field(default_factory=list)

    # Quarterly — most-recent first; need ≥ 5 for YoY growth
    quarterly_sales: list[float] = field(default_factory=list)
    quarterly_net_profit: list[float] = field(default_factory=list)
    quarterly_eps: list[float] = field(default_factory=list)

    # Supplementary
    promoter_holding_pct: Optional[float] = None
    high_52w: Optional[float] = None

    @classmethod
    def from_json(cls, data: dict, ticker: str) -> ScreenerInData:
        obj = cls(ticker=ticker)

        # ── Ratios section ────────────────────────────────────────────────────
        try:
            ratios = data.get("ratios", [])
            obj.roce_pct = _find_ratio(ratios, "roce", "roce %")
            obj.roe_pct = _find_ratio(ratios, "roe", "roe %")
            obj.current_pe = _find_ratio(ratios, "stock p/e", "p/e", "pe")
            obj.high_52w = _find_ratio(ratios, "52 week high", "high price")
            obj.de_ratio = _find_ratio(ratios, "debt to equity", "d/e", "de ratio", "debt / equity")

            # P/B = Current Price / Book Value
            book_val = _find_ratio(ratios, "book value")
            cmp = _find_ratio(ratios, "current price")
            if book_val and book_val > 0 and cmp:
                obj.pb_ratio = cmp / book_val
        except Exception as exc:
            logger.debug("Screener.in ratios parse error for %s: %s", ticker, exc)

        # ── Annual P&L (oldest-first from Screener.in; reverse to most-recent-first) ─
        try:
            pl = data.get("profit_loss", {})
            obj.annual_net_profit = list(reversed(
                _extract_series(pl, "Net Profit", "Profit after tax", "PAT")
            ))
            obj.annual_revenue = list(reversed(
                _extract_series(pl, "Sales", "Revenue", "Net Sales")
            ))
            obj.annual_ebitda = list(reversed(
                _extract_series(pl, "Operating Profit", "EBITDA")
            ))
        except Exception as exc:
            logger.debug("Screener.in P&L parse error for %s: %s", ticker, exc)

        # ── Cash Flow (oldest-first; reverse to most-recent-first) ────────────
        try:
            cf = data.get("cash_flow", {})
            obj.annual_cfo = list(reversed(
                _extract_series(cf, "Cash from Operating Activity", "Operating Cash Flow")
            ))
            # Capex appears as investing outflow (typically negative); take abs value
            raw_capex = list(reversed(
                _extract_series(cf, "Cash from Investing Activity", "Capital Expenditure")
            ))
            obj.annual_capex = [abs(v) for v in raw_capex]
        except Exception as exc:
            logger.debug("Screener.in cash flow parse error for %s: %s", ticker, exc)

        # ── Quarterly Results (oldest-first; reverse to most-recent-first) ────
        try:
            qr = data.get("quarterly_results", {})
            obj.quarterly_sales = list(reversed(
                _extract_series(qr, "Sales", "Revenue", "Net Sales")
            ))
            obj.quarterly_net_profit = list(reversed(
                _extract_series(qr, "Net Profit", "PAT")
            ))
            obj.quarterly_eps = list(reversed(
                _extract_series(qr, "EPS in Rs", "EPS", "Basic EPS")
            ))
        except Exception as exc:
            logger.debug("Screener.in quarterly parse error for %s: %s", ticker, exc)

        # ── Shareholding ──────────────────────────────────────────────────────
        try:
            sh = data.get("shareholding", {})
            promoters = sh.get("promoters", [])
            if promoters:
                # Most-recent quarter is the last element
                obj.promoter_holding_pct = _parse_float(promoters[-1])
        except Exception as exc:
            logger.debug("Screener.in shareholding parse error for %s: %s", ticker, exc)

        return obj


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


class ScreenerInClient:
    """Authenticated HTTP client for the Screener.in company JSON endpoint.

    Uses the ``SCREENER_IN_SESSION`` environment variable as the sessionid cookie.
    Cookie lifetime is approximately 30 days — refresh after re-login.
    """

    BASE = "https://www.screener.in"

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers["User-Agent"] = (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        cookie = os.environ.get("SCREENER_IN_SESSION", "")
        if cookie:
            self._session.cookies.set("sessionid", cookie, domain="www.screener.in")
        else:
            logger.warning(
                "SCREENER_IN_SESSION not set — Screener.in requests will be "
                "unauthenticated; some financial data may be unavailable"
            )
        self._cache: dict[str, Optional[ScreenerInData]] = {}

    @classmethod
    def is_configured(cls) -> bool:
        """Return True when the session cookie env var is present."""
        return bool(os.environ.get("SCREENER_IN_SESSION"))

    def fetch(self, ticker: str) -> Optional[ScreenerInData]:
        """Fetch and parse data for *ticker*.

        Tries the consolidated view first, falls back to standalone.
        Returns ``None`` on any HTTP or parse error; caches results so
        each ticker is fetched at most once per client instance.
        """
        if ticker in self._cache:
            return self._cache[ticker]

        for path in (f"/company/{ticker}/consolidated/", f"/company/{ticker}/"):
            try:
                url = f"{self.BASE}{path}?format=json"
                resp = self._session.get(url, timeout=10)
                if resp.status_code == 200:
                    data = ScreenerInData.from_json(resp.json(), ticker)
                    self._cache[ticker] = data
                    logger.debug("Fetched Screener.in data for %s via %s", ticker, path)
                    return data
                if resp.status_code == 404:
                    continue
                logger.warning(
                    "Screener.in returned HTTP %d for %s at %s",
                    resp.status_code, ticker, path,
                )
            except Exception as exc:
                logger.debug(
                    "Screener.in fetch error for %s at %s: %s", ticker, path, exc
                )
                continue

        logger.info("Could not fetch Screener.in data for %s", ticker)
        self._cache[ticker] = None
        return None
