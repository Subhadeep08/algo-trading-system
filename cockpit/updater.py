"""Portfolio updater — reads and writes references/portfolio.yaml only.

Replaces the old scripts/update_portfolio.py AST-patching approach.
portfolio.yaml is the single source of truth; this module mutates it and
regenerates the companion Markdown reference consumed by the cockpit skill.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Optional

import yaml

from cockpit.config import PORTFOLIO_DATA_MD, PORTFOLIO_YAML


# ---------------------------------------------------------------------------
# Internal state dataclasses
# ---------------------------------------------------------------------------

@dataclass
class _Holding:
    ticker: str
    qty: int
    cost: float
    sl: float
    target: Optional[float]
    catalyst: Optional[str]

    @classmethod
    def from_dict(cls, ticker: str, d: dict) -> "_Holding":
        return cls(
            ticker=ticker,
            qty=int(d["qty"]),
            cost=float(d["cost"]),
            sl=float(d["sl"]),
            target=float(d["target"]) if d.get("target") is not None else None,
            catalyst=d.get("catalyst"),
        )

    def to_dict(self) -> dict:
        return {
            "qty": self.qty,
            "cost": round(self.cost, 2),
            "sl": round(self.sl, 2),
            "target": round(self.target, 2) if self.target is not None else None,
            "catalyst": self.catalyst,
        }


@dataclass
class _GttOrder:
    ticker: str
    qty: int
    gtt: float
    target: Optional[float]
    catalyst: Optional[str]

    @classmethod
    def from_dict(cls, ticker: str, d: dict) -> "_GttOrder":
        return cls(
            ticker=ticker,
            qty=int(d["qty"]),
            gtt=float(d["gtt"]),
            target=float(d["target"]) if d.get("target") is not None else None,
            catalyst=d.get("catalyst"),
        )

    def to_dict(self) -> dict:
        return {
            "qty": self.qty,
            "gtt": round(self.gtt, 2),
            "target": round(self.target, 2) if self.target is not None else None,
            "catalyst": self.catalyst,
        }


# ---------------------------------------------------------------------------
# PortfolioUpdater
# ---------------------------------------------------------------------------

class PortfolioUpdater:
    """Mutates portfolio.yaml holdings and GTT orders, then saves."""

    def __init__(self) -> None:
        self._raw: dict = self._load()
        self._holdings: dict[str, _Holding] = {
            ticker: _Holding.from_dict(ticker, data)
            for ticker, data in self._raw.get("holdings", {}).items()
        }
        self._gtt_orders: dict[str, _GttOrder] = {
            ticker: _GttOrder.from_dict(ticker, data)
            for ticker, data in self._raw.get("gtt_orders", {}).items()
        }

    # ------------------------------------------------------------------
    # Public mutation methods
    # ------------------------------------------------------------------

    def buy(
        self,
        ticker: str,
        qty: int,
        price: float,
        sl: Optional[float] = None,
        target: Optional[float] = None,
        catalyst: Optional[str] = None,
    ) -> str:
        ticker = ticker.upper()
        if ticker in self._holdings:
            existing = self._holdings[ticker]
            new_qty = existing.qty + qty
            # Weighted-average cost
            avg_cost = (existing.qty * existing.cost + qty * price) / new_qty
            new_sl = sl if sl is not None else existing.sl
            new_target = target if target is not None else existing.target
            new_catalyst = catalyst if catalyst is not None else existing.catalyst
            self._holdings[ticker] = _Holding(
                ticker=ticker,
                qty=new_qty,
                cost=avg_cost,
                sl=new_sl,
                target=new_target,
                catalyst=new_catalyst,
            )
            return (
                f"BUY {ticker} {qty} @ ₹{price:.2f} | "
                f"avg cost ₹{avg_cost:.2f} (accumulated, total qty {new_qty})"
            )
        else:
            if sl is None:
                raise ValueError(f"sl is required for new holding {ticker}")
            if target is None:
                raise ValueError(f"target is required for new holding {ticker}")
            self._holdings[ticker] = _Holding(
                ticker=ticker,
                qty=qty,
                cost=price,
                sl=sl,
                target=target,
                catalyst=catalyst,
            )
            return (
                f"BUY {ticker} {qty} @ ₹{price:.2f} | avg cost ₹{price:.2f}"
            )

    def sell(self, ticker: str, qty: int = 0) -> str:
        ticker = ticker.upper()
        if ticker not in self._holdings:
            raise ValueError(f"{ticker} not found in holdings")
        existing = self._holdings[ticker]
        if qty == 0 or qty >= existing.qty:
            del self._holdings[ticker]
            return f"SELL {ticker} {existing.qty} @ full exit — removed from holdings"
        remaining = existing.qty - qty
        self._holdings[ticker] = _Holding(
            ticker=ticker,
            qty=remaining,
            cost=existing.cost,
            sl=existing.sl,
            target=existing.target,
            catalyst=existing.catalyst,
        )
        return (
            f"SELL {ticker} {qty} — remaining qty {remaining} @ cost ₹{existing.cost:.2f}"
        )

    def update_sl(self, ticker: str, new_sl: float) -> str:
        ticker = ticker.upper()
        if ticker not in self._holdings:
            raise ValueError(f"{ticker} not found in holdings")
        existing = self._holdings[ticker]
        old_sl = existing.sl
        self._holdings[ticker] = _Holding(
            ticker=ticker,
            qty=existing.qty,
            cost=existing.cost,
            sl=new_sl,
            target=existing.target,
            catalyst=existing.catalyst,
        )
        return f"SL {ticker}: ₹{old_sl:.2f} → ₹{new_sl:.2f}"

    def add_gtt(
        self,
        ticker: str,
        qty: int,
        gtt_price: float,
        target: float,
        catalyst: Optional[str] = None,
    ) -> str:
        ticker = ticker.upper()
        self._gtt_orders[ticker] = _GttOrder(
            ticker=ticker,
            qty=qty,
            gtt=gtt_price,
            target=target,
            catalyst=catalyst,
        )
        return (
            f"GTT ADD {ticker} qty={qty} @ ₹{gtt_price:.2f} | "
            f"target ₹{target:.2f}"
        )

    def remove_gtt(self, ticker: str) -> str:
        ticker = ticker.upper()
        if ticker not in self._gtt_orders:
            raise ValueError(f"{ticker} not found in gtt_orders")
        del self._gtt_orders[ticker]
        return f"GTT REMOVE {ticker} — removed from pending orders"

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self) -> None:
        """Write mutations back to portfolio.yaml and regenerate the Markdown."""
        data = dict(self._raw)  # preserve keys like risk_parameters
        data["holdings"] = {
            ticker: holding.to_dict()
            for ticker, holding in self._holdings.items()
        }
        data["gtt_orders"] = {
            ticker: order.to_dict()
            for ticker, order in self._gtt_orders.items()
        }
        with open(PORTFOLIO_YAML, "w", encoding="utf-8") as fh:
            yaml.dump(
                data,
                fh,
                sort_keys=False,
                allow_unicode=True,
                default_flow_style=False,
            )
        self._write_markdown()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load(self) -> dict:
        with open(PORTFOLIO_YAML, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    def _write_markdown(self) -> None:
        _d = datetime.date.today()
        today = f"{_d.strftime('%B')} {_d.day}, {_d.year}"
        lines: list[str] = []

        lines.append(f"# Portfolio Data — Last Updated: {today}")
        lines.append("")
        lines.append(
            "Update this file whenever holdings, stop-losses, GTT levels, or targets change."
        )
        lines.append("")

        # Active holdings table
        lines.append("## Active Holdings")
        lines.append("")
        lines.append(
            "| Ticker (NSE)        | Qty | Cost Price (₹) | "
            "Trailing Stop-Loss (₹) | Target Price (₹) | Key Catalyst |"
        )
        lines.append(
            "|----------------------|-----|-----------------|"
            "--------------------------|-------------------|--------------|"
        )
        for ticker, h in self._holdings.items():
            target_str = f"{h.target:,.2f}" if h.target is not None else "—"
            catalyst_str = h.catalyst or "—"
            lines.append(
                f"| {ticker:<20} | {h.qty:<3} | {h.cost:>15,.2f} | "
                f"{h.sl:>24,.2f} | {target_str:>17} | {catalyst_str} |"
            )
        lines.append("")

        # GTT orders table
        lines.append("## Pending GTT Limit Buys")
        lines.append("")
        lines.append(
            "| Ticker (NSE)        | GTT Limit (₹) | Target Price (₹) | Key Catalyst |"
        )
        lines.append(
            "|----------------------|----------------|-------------------|--------------|"
        )
        for ticker, g in self._gtt_orders.items():
            target_str = f"{g.target:,.2f}" if g.target is not None else "—"
            catalyst_str = g.catalyst or "—"
            lines.append(
                f"| {ticker:<20} | {g.gtt:>14,.2f} | {target_str:>17} | {catalyst_str} |"
            )
        lines.append("")

        # Benchmark indices
        lines.append("## Benchmark Indices")
        lines.append("")
        lines.append("- Nifty 50")
        lines.append("- Nifty 500")
        lines.append("")

        # Risk parameters
        risk = self._raw.get("risk_parameters", {})
        if risk:
            lines.append("## Risk Parameters")
            lines.append("")
            vol_valid = risk.get("valid_breakout_volume_multiplier", 1.5)
            vol_ideal = risk.get("ideal_breakout_volume_multiplier", 2.0)
            rsi = risk.get("rsi_overbought_threshold", 70)
            rr = risk.get("min_risk_reward_ratio", 1.5)
            lines.append(
                f"- Valid breakout volume threshold: {vol_valid}x of 20-day average "
                f"(ideal: {vol_ideal}x+)"
            )
            lines.append(f"- RSI overbought warning: daily RSI > {rsi} (avoid new entries)")
            lines.append(
                f"- Minimum risk-reward for new entry: mathematically optimal only "
                f"at/below GTT levels"
            )
            lines.append("")

        PORTFOLIO_DATA_MD.parent.mkdir(parents=True, exist_ok=True)
        with open(PORTFOLIO_DATA_MD, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))


# ---------------------------------------------------------------------------
# Module-level convenience function
# ---------------------------------------------------------------------------

_VALID_ACTIONS = {"buy", "sell", "update_sl", "add_gtt", "remove_gtt"}


def update(action: str, ticker: str, **kwargs) -> None:
    """Instantiate PortfolioUpdater, apply *action*, save, and print the summary.

    Valid actions: buy, sell, update_sl, add_gtt, remove_gtt

    Examples
    --------
    update("buy", "APARINDS", qty=4, price=14838.00, sl=13678.00, target=18000.00)
    update("sell", "APARINDS", qty=0)
    update("update_sl", "APARINDS", new_sl=14000.00)
    update("add_gtt", "SYRMA", qty=30, gtt_price=1275.00, target=1600.00)
    update("remove_gtt", "SYRMA")
    """
    if action not in _VALID_ACTIONS:
        raise ValueError(
            f"Unknown action {action!r}. Valid actions: {sorted(_VALID_ACTIONS)}"
        )
    updater = PortfolioUpdater()
    method = getattr(updater, action)
    summary = method(ticker, **kwargs)
    updater.save()
    print(summary)
