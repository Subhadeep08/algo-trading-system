"""Fetch live NSE prices via yfinance and write references/live-prices.md."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import yfinance as yf

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

OUTPUT_FILE      = Path(__file__).resolve().parent.parent / "references" / "live-prices.md"
NSE_TICKER_SUFFIX = ".NS"

# IMPORTANT: variable names and inner dict keys are read and written by
# update_portfolio.py via AST. Do not rename HOLDINGS / GTT_ORDERS or their keys.

HOLDINGS = {
    "APTUS":      {"qty": 310, "cost": 294.70, "sl": 280.00, "target": 350.00},
    "KIRLOSENG":  {"qty": 25, "cost": 2349.88, "sl": 2290.00, "target": 3100.00},
    "AEGISLOG":   {"qty": 40, "cost": 1002.55, "sl": 1190.00, "target": 1350.00},
    "CUMMINSIND": {"qty": 9, "cost": 5660.83, "sl": 5472.00, "target": 7300.00},
    "NAVINFLUOR": {"qty": 6, "cost": 7510.00, "sl": 7103.00, "target": 9250.00},
    "WELCORP":    {"qty": 30, "cost": 1491.70, "sl": 1350.00, "target": None},
    "CGPOWER":    {"qty": 50, "cost": 898.46, "sl": 880.00, "target": 1120.00},
}

GTT_ORDERS = {
    "PRIVISCL": {"gtt": 3350.00, "target": 4025.00},
    "SYRMA":    {"gtt": 1275.00, "target": 1600.00},
}


# ── Domain value objects ──────────────────────────────────────────────────────

@dataclass
class HoldingPriceRow:
    """Fetched price data for a single held position, with derived risk metrics."""

    ticker: str
    current_price: Optional[float]
    previous_close: Optional[float]
    quantity: int
    average_cost: float
    stop_loss_price: float
    target_price: Optional[float]

    @property
    def day_change_pct(self) -> Optional[float]:
        if self.current_price is None or self.previous_close is None:
            return None
        return (self.current_price - self.previous_close) / self.previous_close * 100

    @property
    def profit_loss_pct(self) -> Optional[float]:
        if self.current_price is None:
            return None
        return (self.current_price - self.average_cost) / self.average_cost * 100

    @property
    def stop_loss_buffer_pct(self) -> Optional[float]:
        if self.current_price is None:
            return None
        return (self.current_price - self.stop_loss_price) / self.stop_loss_price * 100

    @property
    def upside_to_target_pct(self) -> Optional[float]:
        if self.current_price is None or self.target_price is None:
            return None
        return (self.target_price - self.current_price) / self.current_price * 100

    @property
    def market_value(self) -> Optional[float]:
        if self.current_price is None:
            return None
        return self.current_price * self.quantity

    @property
    def book_value(self) -> float:
        return self.average_cost * self.quantity


@dataclass
class GttPriceRow:
    """Fetched price data for a single pending GTT limit-buy order."""

    ticker: str
    current_price: Optional[float]
    trigger_price: float
    target_price: float

    @property
    def distance_to_trigger_pct(self) -> Optional[float]:
        if self.current_price is None:
            return None
        return (self.current_price - self.trigger_price) / self.trigger_price * 100

    @property
    def upside_from_trigger_pct(self) -> float:
        return (self.target_price - self.trigger_price) / self.trigger_price * 100


# ── Price fetcher ─────────────────────────────────────────────────────────────

class NsePriceFetcher:
    """Fetches current and previous-close prices from yfinance for NSE-listed stocks."""

    def fetch_raw_price(
        self, nse_ticker: str
    ) -> tuple[Optional[float], Optional[float]]:
        """Return (current_price, previous_close) for an NSE symbol, or (None, None) on failure."""
        try:
            quote = yf.Ticker(f"{nse_ticker}{NSE_TICKER_SUFFIX}").fast_info
            current_price  = quote.last_price
            previous_close = quote.previous_close
            if current_price is None:
                raise ValueError("No price returned by yfinance feed")
            return (
                round(current_price, 2),
                round(previous_close, 2) if previous_close is not None else None,
            )
        except Exception as exc:
            logger.warning("Price fetch failed for %s: %s", nse_ticker, exc)
            return None, None

    def build_holding_price_rows(self) -> list[HoldingPriceRow]:
        """Fetch prices for all HOLDINGS and return as typed rows."""
        rows: list[HoldingPriceRow] = []
        for ticker, config in HOLDINGS.items():
            current_price, previous_close = self.fetch_raw_price(ticker)
            rows.append(HoldingPriceRow(
                ticker=ticker,
                current_price=current_price,
                previous_close=previous_close,
                quantity=config["qty"],
                average_cost=config["cost"],
                stop_loss_price=config["sl"],
                target_price=config["target"],
            ))
        return rows

    def build_gtt_price_rows(self) -> list[GttPriceRow]:
        """Fetch prices for all GTT_ORDERS and return as typed rows."""
        rows: list[GttPriceRow] = []
        for ticker, config in GTT_ORDERS.items():
            current_price, _ = self.fetch_raw_price(ticker)
            rows.append(GttPriceRow(
                ticker=ticker,
                current_price=current_price,
                trigger_price=config["gtt"],
                target_price=config["target"],
            ))
        return rows


# ── Report renderer ───────────────────────────────────────────────────────────

class LivePriceReport:
    """Renders holding and GTT price data as a Markdown report."""

    def generate(
        self,
        holding_rows: list[HoldingPriceRow],
        gtt_rows: list[GttPriceRow],
        generated_at: datetime,
    ) -> str:
        timestamp_header = (
            f"# Live NSE Prices — Fetched: {generated_at.strftime('%Y-%m-%d %H:%M IST')}"
        )
        sections = [
            timestamp_header,
            self._render_holdings_table(holding_rows),
            self._render_portfolio_summary(holding_rows),
            self._render_gtt_table(gtt_rows),
        ]
        return "\n\n".join(sections)

    def _render_holdings_table(self, rows: list[HoldingPriceRow]) -> str:
        header_lines = [
            "## Active Holdings",
            "",
            "| Ticker | CMP (₹) | Prev Close (₹) | Day Chg% | Cost (₹) | P&L% |"
            " SL (₹) | SL Buffer% | Target (₹) | Upside% |",
            "|--------|---------|----------------|----------|----------|------|"
            "--------|------------|------------|---------|",
        ]
        data_lines = [self._format_holding_row(row) for row in rows]
        return "\n".join(header_lines + data_lines)

    def _format_holding_row(self, row: HoldingPriceRow) -> str:
        target_label = f"{row.target_price:,.2f}" if row.target_price is not None else "TBD"
        if row.current_price is None:
            return (
                f"| {row.ticker} | N/A | N/A | N/A | {row.average_cost:,.2f} | "
                f"N/A | {row.stop_loss_price:,.2f} | N/A | {target_label} | N/A |"
            )
        prev_close_label  = f"{row.previous_close:,.2f}" if row.previous_close is not None else "N/A"
        day_change_label  = f"{row.day_change_pct:+.2f}%" if row.day_change_pct is not None else "N/A"
        upside_label      = f"{row.upside_to_target_pct:+.2f}%" if row.upside_to_target_pct is not None else "TBD"
        return (
            f"| {row.ticker} | {row.current_price:,.2f} | {prev_close_label} | {day_change_label} | "
            f"{row.average_cost:,.2f} | {row.profit_loss_pct:+.2f}% | {row.stop_loss_price:,.2f} | "
            f"{row.stop_loss_buffer_pct:+.2f}% | {target_label} | {upside_label} |"
        )

    def _render_portfolio_summary(self, rows: list[HoldingPriceRow]) -> str:
        total_book_value   = sum(row.book_value for row in rows)
        total_market_value = sum(
            row.market_value for row in rows if row.market_value is not None
        )
        if total_book_value > 0:
            unrealised_pnl_pct = (total_market_value - total_book_value) / total_book_value * 100
            pnl_label = f"{unrealised_pnl_pct:+.2f}%"
        else:
            pnl_label = "N/A"
        return (
            f"**Portfolio Total Cost:** ₹ {total_book_value:,.2f}  \n"
            f"**Portfolio Current Value:** ₹ {total_market_value:,.2f}  \n"
            f"**Unrealised P&L:** {pnl_label}"
        )

    def _render_gtt_table(self, rows: list[GttPriceRow]) -> str:
        header_lines = [
            "## Pending GTT Orders",
            "",
            "| Ticker | CMP (₹) | GTT Level (₹) | Distance% | Target (₹) | Upside from GTT% |",
            "|--------|---------|---------------|-----------|------------|-----------------|",
        ]
        data_lines = [self._format_gtt_row(row) for row in rows]
        return "\n".join(header_lines + data_lines)

    def _format_gtt_row(self, row: GttPriceRow) -> str:
        if row.current_price is None:
            return (
                f"| {row.ticker} | N/A | {row.trigger_price:,.2f} | "
                f"N/A | {row.target_price:,.2f} | N/A |"
            )
        distance_pct        = row.distance_to_trigger_pct
        upside_from_trigger = row.upside_from_trigger_pct
        proximity_flag = ""
        if distance_pct is not None:
            if distance_pct <= 0:
                proximity_flag = " [AT/BELOW GTT — CHECK]"
            elif distance_pct <= 3:
                proximity_flag = " [APPROACHING]"
        distance_label = f"{distance_pct:+.2f}%{proximity_flag}" if distance_pct is not None else "N/A"
        return (
            f"| {row.ticker} | {row.current_price:,.2f} | {row.trigger_price:,.2f} | "
            f"{distance_label} | {row.target_price:,.2f} | {upside_from_trigger:+.2f}% |"
        )


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    fetcher = NsePriceFetcher()
    holding_rows = fetcher.build_holding_price_rows()
    gtt_rows     = fetcher.build_gtt_price_rows()

    report_content = LivePriceReport().generate(
        holding_rows=holding_rows,
        gtt_rows=gtt_rows,
        generated_at=datetime.now(),
    )

    OUTPUT_FILE.write_text(report_content, encoding="utf-8")
    logger.info("Written to %s", OUTPUT_FILE)
    print(report_content)


if __name__ == "__main__":
    main()
