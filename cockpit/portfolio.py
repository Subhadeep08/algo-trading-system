"""PortfolioRegistry — single runtime interface to portfolio.yaml.

Implements the Singleton + Repository patterns. YAML is parsed once on first
access and cached for the lifetime of the process. Call reset() between tests
to force a fresh load.
"""

from __future__ import annotations

from typing import Optional

import yaml

from cockpit.config import PORTFOLIO_YAML
from cockpit.models import GttOrderConfig, HoldingConfig

# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_registry: Optional["PortfolioRegistry"] = None


def get_registry() -> "PortfolioRegistry":
    """Return the module-level PortfolioRegistry singleton, creating it lazily."""
    global _registry
    if _registry is None:
        _registry = PortfolioRegistry()
    return _registry


# ---------------------------------------------------------------------------
# Registry class
# ---------------------------------------------------------------------------


class PortfolioRegistry:
    """Loads and caches portfolio.yaml, exposing typed accessors for all sections."""

    def __init__(self) -> None:
        self._raw: Optional[dict] = None
        self._holdings: Optional[list[HoldingConfig]] = None
        self._gtt_orders: Optional[list[GttOrderConfig]] = None
        self._holdings_dict: Optional[dict[str, HoldingConfig]] = None
        self._gtt_dict: Optional[dict[str, GttOrderConfig]] = None
        self._risk_parameters: Optional[dict] = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> dict:
        """Parse portfolio.yaml exactly once, cache the raw dict, and return it."""
        if self._raw is not None:
            return self._raw

        if not PORTFOLIO_YAML.exists():
            raise FileNotFoundError(
                f"portfolio.yaml not found at {PORTFOLIO_YAML}. "
                "Run `scripts/update_portfolio.py` or copy the file into "
                "references/ before running the cockpit."
            )

        with PORTFOLIO_YAML.open("r", encoding="utf-8") as fh:
            self._raw = yaml.safe_load(fh) or {}

        return self._raw

    # ------------------------------------------------------------------
    # Public accessors — list form
    # ------------------------------------------------------------------

    def holdings(self) -> list[HoldingConfig]:
        """Return all active holdings as a list of HoldingConfig objects."""
        if self._holdings is not None:
            return self._holdings

        raw = self._load()
        result: list[HoldingConfig] = []
        for ticker, attrs in (raw.get("holdings") or {}).items():
            result.append(
                HoldingConfig(
                    ticker=ticker,
                    qty=int(attrs["qty"]),
                    cost=float(attrs["cost"]),
                    sl=float(attrs["sl"]),
                    target=float(attrs["target"]) if attrs.get("target") is not None else None,
                    catalyst=str(attrs.get("catalyst", "")),
                )
            )
        self._holdings = result
        return self._holdings

    def gtt_orders(self) -> list[GttOrderConfig]:
        """Return all pending GTT orders as a list of GttOrderConfig objects."""
        if self._gtt_orders is not None:
            return self._gtt_orders

        raw = self._load()
        result: list[GttOrderConfig] = []
        for ticker, attrs in (raw.get("gtt_orders") or {}).items():
            result.append(
                GttOrderConfig(
                    ticker=ticker,
                    qty=int(attrs["qty"]),
                    gtt=float(attrs["gtt"]),
                    target=float(attrs["target"]) if attrs.get("target") is not None else None,
                    catalyst=str(attrs.get("catalyst", "")),
                )
            )
        self._gtt_orders = result
        return self._gtt_orders

    # ------------------------------------------------------------------
    # Public accessors — dict form (keyed by ticker)
    # ------------------------------------------------------------------

    def holdings_as_dict(self) -> dict[str, HoldingConfig]:
        """Return all active holdings keyed by ticker symbol."""
        if self._holdings_dict is not None:
            return self._holdings_dict
        self._holdings_dict = {h.ticker: h for h in self.holdings()}
        return self._holdings_dict

    def gtt_as_dict(self) -> dict[str, GttOrderConfig]:
        """Return all GTT orders keyed by ticker symbol."""
        if self._gtt_dict is not None:
            return self._gtt_dict
        self._gtt_dict = {g.ticker: g for g in self.gtt_orders()}
        return self._gtt_dict

    # ------------------------------------------------------------------
    # Risk parameters
    # ------------------------------------------------------------------

    def risk_parameters(self) -> dict:
        """Return the risk_parameters block from portfolio.yaml as a plain dict."""
        if self._risk_parameters is not None:
            return self._risk_parameters
        raw = self._load()
        self._risk_parameters = dict(raw.get("risk_parameters") or {})
        return self._risk_parameters

    # ------------------------------------------------------------------
    # Test support
    # ------------------------------------------------------------------

    @classmethod
    def reset(cls) -> None:
        """Destroy the current singleton so the next get_registry() call re-parses.

        Intended for use in tests that need a fresh state between cases.
        """
        global _registry
        _registry = None
