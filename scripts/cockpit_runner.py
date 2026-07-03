"""
Daily NSE portfolio cockpit — three-phase market monitoring via Telegram.

Phase 1 (pre-market):  Gift Nifty gap, FII/DII flows, GTT trigger alerts, capital status
Phase 2 (mid-market):  Relative strength vs Nifty 50, volume spikes, RSI proxy warnings
Phase 3 (post-market): Stop-loss audit, trailing-SL ratchet suggestions, P&L close
"""

from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pytz
import requests
import yfinance as yf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Risk thresholds — tune here; all logic adapts automatically ───────────────
WINNER_PNL_THRESHOLD_PCT         = 15.0   # P&L above this → trailing-SL ratchet candidate
UNDERPERFORMER_PNL_THRESHOLD_PCT = -10.0  # P&L below this → review flag in Phase 1
NEAR_COST_BAND_PCT               = 5.0    # |P&L| within ±5% → classified as "near cost"
SL_DANGER_BUFFER_PCT             = 2.0    # buffer below 2% → danger zone in Phase 3
SL_MONITOR_BUFFER_PCT            = 5.0    # buffer below 5% → monitor zone in Phase 3
TRAILING_SL_PULLBACK_FRACTION    = 0.10   # ratchet SL to 10% below current price
HIGH_VOLUME_MULTIPLIER           = 2.0    # today's volume ≥ 2× 20-day avg → high alert
ELEVATED_VOLUME_MULTIPLIER       = 1.5    # today's volume ≥ 1.5× 20-day avg → elevated
LARGE_SINGLE_DAY_MOVE_PCT        = 5.0    # RSI proxy: flag moves larger than this
VOLATILE_DAY_MOVE_PCT            = 2.0    # stay-put reminder threshold
GTT_APPROACHING_DISTANCE_PCT     = 3.0    # CMP within 3% above GTT trigger → approaching
TELEGRAM_MESSAGE_CHAR_LIMIT      = 4000
VOLUME_LOOKBACK_PERIOD           = "21d"  # ~1 calendar month of trading sessions
NSE_TICKER_SUFFIX                = ".NS"
NIFTY_50_SYMBOL                  = "^NSEI"


# ── Portfolio registry ────────────────────────────────────────────────────────
# IMPORTANT: variable names and inner dict keys are read and written by
# update_portfolio.py via AST. Do not rename HOLDINGS / GTT_ORDERS or their keys.

HOLDINGS = {
    "APTUS":      {"qty": 310, "cost": 294.70,   "sl": 280.00,   "target": 350.00},
    "APARINDS":   {"qty": 4,   "cost": 14838.00, "sl": 14154.00, "target": 18000.00},
    "KIRLOSENG":  {"qty": 25,  "cost": 2349.88,  "sl": 2290.00,  "target": 3100.00},
    "AEGISLOG":   {"qty": 40,  "cost": 1002.55,  "sl": 1190.00,  "target": 1350.00},
    "CUMMINSIND": {"qty": 9,   "cost": 5660.83,  "sl": 5472.00,  "target": 7300.00},
    "NAVINFLUOR": {"qty": 6,   "cost": 7510.00,  "sl": 7103.00,  "target": 9250.00},
    "WELCORP":    {"qty": 30,  "cost": 1491.70,  "sl": 1350.00,  "target": None},
    "CGPOWER":    {"qty": 50,  "cost": 898.46,   "sl": 880.00,   "target": 1120.00},
}

GTT_ORDERS = {
    "PRIVISCL": {"gtt": 3350.00, "target": 4025.00},
    "SYRMA":    {"gtt": 1275.00, "target": 1600.00},
}


# ── Domain value objects ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class HoldingSnapshot:
    """Immutable live-market state for a single held position, with derived metrics."""

    ticker: str
    current_price: float
    previous_close: float
    quantity: int
    average_cost: float
    stop_loss_price: float
    target_price: Optional[float]

    @property
    def day_change_pct(self) -> float:
        return (self.current_price - self.previous_close) / self.previous_close * 100

    @property
    def profit_loss_pct(self) -> float:
        return (self.current_price - self.average_cost) / self.average_cost * 100

    @property
    def stop_loss_buffer_pct(self) -> float:
        return (self.current_price - self.stop_loss_price) / self.stop_loss_price * 100

    @property
    def market_value(self) -> float:
        return self.current_price * self.quantity

    @property
    def book_value(self) -> float:
        return self.average_cost * self.quantity

    @property
    def is_stop_loss_breached(self) -> bool:
        return self.current_price < self.stop_loss_price


@dataclass(frozen=True)
class GttOrderSnapshot:
    """Immutable live-market state for a pending GTT limit-buy order."""

    ticker: str
    current_price: float
    trigger_price: float
    target_price: float

    @property
    def distance_to_trigger_pct(self) -> float:
        return (self.current_price - self.trigger_price) / self.trigger_price * 100

    @property
    def is_trigger_breached(self) -> bool:
        return self.current_price <= self.trigger_price


@dataclass(frozen=True)
class VolumeData:
    """Volume reading for a single holding on the current session."""

    ticker: str
    today_volume: int
    average_20d_volume: int
    volume_ratio: float


# ── Market data service ───────────────────────────────────────────────────────

class MarketDataService:
    """Thin yfinance wrapper — fetches prices, volumes, and Nifty data for NSE stocks."""

    def fetch_all_holding_snapshots(self) -> dict[str, Optional[HoldingSnapshot]]:
        """Return a live HoldingSnapshot for every entry in HOLDINGS (None on failure)."""
        return {
            ticker: self._fetch_holding_snapshot(ticker, config)
            for ticker, config in HOLDINGS.items()
        }

    def fetch_all_gtt_snapshots(self) -> dict[str, Optional[GttOrderSnapshot]]:
        """Return a live GttOrderSnapshot for every entry in GTT_ORDERS (None on failure)."""
        return {
            ticker: self._fetch_gtt_snapshot(ticker, config)
            for ticker, config in GTT_ORDERS.items()
        }

    def fetch_nifty_day_change_pct(self) -> Optional[float]:
        """Return Nifty 50 day-change percentage, or None when the feed is unavailable."""
        try:
            quote = yf.Ticker(NIFTY_50_SYMBOL).fast_info
            if quote.last_price is None or quote.previous_close is None:
                raise ValueError("Incomplete Nifty price data from feed")
            return (quote.last_price - quote.previous_close) / quote.previous_close * 100
        except Exception as exc:
            logger.warning("Nifty 50 fetch failed: %s", exc)
            return None

    def fetch_volume_data(self, ticker: str) -> Optional[VolumeData]:
        """Return today's volume and 20-day average ratio for a holding."""
        try:
            price_history = yf.Ticker(f"{ticker}{NSE_TICKER_SUFFIX}").history(
                period=VOLUME_LOOKBACK_PERIOD
            )
            if price_history.empty or len(price_history) < 2:
                raise ValueError("Insufficient price history returned")
            today_volume = int(price_history["Volume"].iloc[-1])
            average_20d_volume = int(price_history["Volume"].iloc[:-1].mean())
            volume_ratio = today_volume / average_20d_volume if average_20d_volume > 0 else 0.0
            return VolumeData(
                ticker=ticker,
                today_volume=today_volume,
                average_20d_volume=average_20d_volume,
                volume_ratio=volume_ratio,
            )
        except Exception as exc:
            logger.warning("Volume fetch failed for %s: %s", ticker, exc)
            return None

    def _fetch_holding_snapshot(
        self, ticker: str, holding_config: dict
    ) -> Optional[HoldingSnapshot]:
        try:
            quote = yf.Ticker(f"{ticker}{NSE_TICKER_SUFFIX}").fast_info
            current_price = quote.last_price
            previous_close = quote.previous_close
            if current_price is None or previous_close is None:
                raise ValueError("Incomplete price data from feed")
            return HoldingSnapshot(
                ticker=ticker,
                current_price=current_price,
                previous_close=previous_close,
                quantity=holding_config["qty"],
                average_cost=holding_config["cost"],
                stop_loss_price=holding_config["sl"],
                target_price=holding_config["target"],
            )
        except Exception as exc:
            logger.warning("Price fetch failed for %s: %s", ticker, exc)
            return None

    def _fetch_gtt_snapshot(
        self, ticker: str, order_config: dict
    ) -> Optional[GttOrderSnapshot]:
        try:
            current_price = yf.Ticker(f"{ticker}{NSE_TICKER_SUFFIX}").fast_info.last_price
            if current_price is None:
                raise ValueError("No price returned from feed")
            return GttOrderSnapshot(
                ticker=ticker,
                current_price=current_price,
                trigger_price=order_config["gtt"],
                target_price=order_config["target"],
            )
        except Exception as exc:
            logger.warning("GTT price fetch failed for %s: %s", ticker, exc)
            return None


# ── External service clients ──────────────────────────────────────────────────

class WebSearchClient:
    """Queries the Claude API with the web_search tool to retrieve real-time market data."""

    _LLM_MODEL            = "claude-haiku-4-5-20251001"
    _MAX_OUTPUT_TOKENS    = 300
    _MAX_WEB_SEARCH_CALLS = 2
    _FALLBACK_RESPONSE    = "N/A (set ANTHROPIC_API_KEY secret for live data)"

    def search(self, prompt: str) -> str:
        """Run a web-search query via Claude and return the plain-text answer."""
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return self._FALLBACK_RESPONSE
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model=self._LLM_MODEL,
                max_tokens=self._MAX_OUTPUT_TOKENS,
                tools=[{
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": self._MAX_WEB_SEARCH_CALLS,
                }],
                messages=[{"role": "user", "content": prompt}],
            )
            for content_block in response.content:
                if hasattr(content_block, "text") and content_block.text:
                    return content_block.text.strip()
            return "N/A"
        except Exception as exc:
            logger.warning("Web search failed: %s", exc)
            return "N/A"


class TelegramNotifier:
    """Delivers cockpit reports to a Telegram chat via the Bot API."""

    _SEND_MESSAGE_URL = "https://api.telegram.org/bot{token}/sendMessage"
    _PARSE_MODE       = "HTML"
    _REQUEST_TIMEOUT  = 10

    def send(self, message: str) -> bool:
        """Post message to Telegram; falls back to stdout when credentials are absent."""
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id   = os.environ.get("TELEGRAM_CHAT_ID", "")
        if not bot_token or not chat_id:
            logger.warning(
                "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — printing report to stdout"
            )
            print(message)
            return False
        truncated_message = message[:TELEGRAM_MESSAGE_CHAR_LIMIT]
        response = requests.post(
            self._SEND_MESSAGE_URL.format(token=bot_token),
            json={"chat_id": chat_id, "text": truncated_message, "parse_mode": self._PARSE_MODE},
            timeout=self._REQUEST_TIMEOUT,
        )
        api_response = response.json()
        delivered = api_response.get("ok", False)
        if delivered:
            logger.info("Telegram message delivered successfully")
        else:
            logger.error("Telegram delivery failed: %s", api_response)
        return delivered


# ── Report builder ────────────────────────────────────────────────────────────

class CockpitReportBuilder:
    """Builds formatted text reports for each cockpit phase."""

    # ── Public build methods ──────────────────────────────────────────────────

    def build_phase1_report(
        self,
        date_label: str,
        gift_nifty_summary: str,
        fii_dii_summary: str,
        gtt_snapshots: dict[str, Optional[GttOrderSnapshot]],
        holding_snapshots: dict[str, Optional[HoldingSnapshot]],
    ) -> str:
        gtt_status_lines, action_items = self._build_gtt_status_section(gtt_snapshots)
        allocation_lines = self._build_capital_allocation_section(holding_snapshots, action_items)
        action_block = self._format_numbered_action_items(action_items)

        sections = [
            f"📊 PHASE 1 PRE-MARKET | {date_label} 09:05 IST",
            f"🌏 GIFT NIFTY\n{gift_nifty_summary}",
            f"⚠️ GTT ALERT STATUS\n" + "\n".join(gtt_status_lines),
            f"📉 FII/DII FLOWS\n{fii_dii_summary}",
            f"💼 CAPITAL ALLOCATION (Phase 4)\n" + "\n".join(allocation_lines),
            f"✅ ACTION ITEMS\n{action_block}",
        ]
        return "\n\n".join(sections)

    def build_phase2_report(
        self,
        date_label: str,
        nifty_day_change_pct: Optional[float],
        holding_snapshots: dict[str, Optional[HoldingSnapshot]],
        volume_data_by_ticker: dict[str, Optional[VolumeData]],
    ) -> str:
        nifty_label = f"{nifty_day_change_pct:+.2f}%" if nifty_day_change_pct is not None else "N/A"
        rs_lines:           list[str] = []
        volume_alert_lines: list[str] = []
        rsi_warning_lines:  list[str] = []
        action_items:       list[str] = []
        total_book_value   = 0.0
        total_market_value = 0.0

        for ticker, snapshot in holding_snapshots.items():
            if snapshot is None:
                rs_lines.append(f"{ticker} | CMP N/A | Day N/A | RS: ❓ UNAVAILABLE")
                continue

            total_book_value   += snapshot.book_value
            total_market_value += snapshot.market_value

            rs_label = self._classify_relative_strength(
                stock_day_change_pct=snapshot.day_change_pct,
                nifty_day_change_pct=nifty_day_change_pct,
                ticker=ticker,
                action_items=action_items,
            )
            rs_lines.append(
                f"{ticker} | CMP ₹{snapshot.current_price:.1f} | "
                f"Day {snapshot.day_change_pct:+.1f}% | RS: {rs_label}"
            )

            volume = volume_data_by_ticker.get(ticker)
            if volume is not None:
                if volume.volume_ratio >= HIGH_VOLUME_MULTIPLIER:
                    volume_alert_lines.append(f"{ticker}: {volume.volume_ratio:.1f}x avg volume 🔥")
                    action_items.append(
                        f"HIGH VOLUME {ticker}: {volume.volume_ratio:.1f}x average — confirm direction"
                    )
                elif volume.volume_ratio >= ELEVATED_VOLUME_MULTIPLIER:
                    volume_alert_lines.append(f"{ticker}: {volume.volume_ratio:.1f}x avg volume ⬆️")

            if abs(snapshot.day_change_pct) > LARGE_SINGLE_DAY_MOVE_PCT:
                rsi_warning_lines.append(
                    f"{ticker}: {snapshot.day_change_pct:+.1f}% single-day move"
                    " — check overbought/oversold"
                )
                action_items.append(
                    f"RSI CHECK {ticker}: {snapshot.day_change_pct:+.1f}% today"
                    " — verify momentum sustainability"
                )

        portfolio_pnl_pct = (
            (total_market_value - total_book_value) / total_book_value * 100
            if total_book_value > 0 else 0.0
        )
        action_block = self._format_numbered_action_items(action_items)

        sections = [
            f"📊 PHASE 2 MID-MARKET | {date_label} 13:30 IST",
            f"📈 RELATIVE STRENGTH vs NIFTY ({nifty_label})\n" + "\n".join(rs_lines),
            f"📦 VOLUME ALERTS\n" + ("\n".join(volume_alert_lines) or "None"),
            f"⚡ RSI WARNINGS\n" + ("\n".join(rsi_warning_lines) or "None"),
            (
                f"💰 PORTFOLIO SNAPSHOT\n"
                f"Invested: ₹{total_book_value:,.0f} | "
                f"Current: ₹{total_market_value:,.0f} | "
                f"P&L: {portfolio_pnl_pct:+.1f}%"
            ),
            f"✅ ACTION ITEMS\n{action_block}",
        ]
        return "\n\n".join(sections)

    def build_phase3_report(
        self,
        date_label: str,
        holding_snapshots: dict[str, Optional[HoldingSnapshot]],
    ) -> str:
        critical_sl_rows:   list[str] = []  # breached / danger rows — shown first
        normal_sl_rows:     list[str] = []
        ratchet_suggestions: list[str] = []
        stay_put_reminders: list[str] = []
        action_items:       list[str] = []
        total_book_value   = 0.0
        total_market_value = 0.0

        for ticker, snapshot in holding_snapshots.items():
            if snapshot is None:
                config = HOLDINGS[ticker]
                normal_sl_rows.append(
                    f"{ticker} | Close N/A | SL ₹{config['sl']:.0f} | Buffer N/A | ❓ UNAVAILABLE"
                )
                continue

            total_book_value   += snapshot.book_value
            total_market_value += snapshot.market_value

            self._classify_sl_status(snapshot, critical_sl_rows, normal_sl_rows, action_items)

            if snapshot.profit_loss_pct > WINNER_PNL_THRESHOLD_PCT:
                suggested_trailing_sl = snapshot.current_price * (1 - TRAILING_SL_PULLBACK_FRACTION)
                ratchet_suggestions.append(
                    f"{ticker}: P&L {snapshot.profit_loss_pct:.1f}% — "
                    f"trail SL to ₹{suggested_trailing_sl:.0f} "
                    f"(10% below ₹{snapshot.current_price:.1f})"
                )
                action_items.append(f"RATCHET {ticker}: update SL to ₹{suggested_trailing_sl:.0f}")

            if abs(snapshot.day_change_pct) > VOLATILE_DAY_MOVE_PCT and not snapshot.is_stop_loss_breached:
                stay_put_reminders.append(
                    f"{ticker}: {snapshot.day_change_pct:+.1f}% today but closed above "
                    f"SL ₹{snapshot.stop_loss_price:.0f} — no action needed"
                )

        portfolio_pnl_pct = (
            (total_market_value - total_book_value) / total_book_value * 100
            if total_book_value > 0 else 0.0
        )
        all_sl_rows  = critical_sl_rows + normal_sl_rows
        action_block = self._format_numbered_action_items(
            action_items, empty_message="CLEAN — Portfolio running clean"
        )

        sections = [
            f"📊 PHASE 3 POST-MARKET AUDIT | {date_label} 15:35 IST",
            (
                "🔍 STOP-LOSS STATUS\n(BREACHED/DANGER rows first)\n"
                + ("\n".join(all_sl_rows) or "No data available")
            ),
            f"📐 TRAILING SL RATCHET\n" + ("\n".join(ratchet_suggestions) or "None — all SLs current"),
            f"💰 PORTFOLIO CLOSE\nTotal: ₹{total_market_value:,.0f} | P&L: {portfolio_pnl_pct:+.1f}%",
            f"🛑 STAY-PUT REMINDERS\n" + ("\n".join(stay_put_reminders) or "None"),
            f"✅ ACTION ITEMS\n{action_block}",
        ]
        return "\n\n".join(sections)

    # ── Private section builders ──────────────────────────────────────────────

    def _build_gtt_status_section(
        self,
        gtt_snapshots: dict[str, Optional[GttOrderSnapshot]],
    ) -> tuple[list[str], list[str]]:
        status_lines: list[str] = []
        action_items: list[str] = []
        for ticker, snapshot in gtt_snapshots.items():
            if snapshot is None:
                trigger_price = GTT_ORDERS[ticker]["gtt"]
                status_lines.append(
                    f"{ticker} | CMP N/A | GTT ₹{trigger_price:.0f} | Dist N/A | ❓ UNAVAILABLE"
                )
                continue
            distance_pct = snapshot.distance_to_trigger_pct
            if snapshot.is_trigger_breached:
                status_label = "🚨 TRIGGERED"
                action_items.append(
                    f"CHECK {ticker}: GTT may have triggered "
                    f"(CMP ₹{snapshot.current_price:.1f} ≤ GTT ₹{snapshot.trigger_price:.0f})"
                )
            elif distance_pct <= GTT_APPROACHING_DISTANCE_PCT:
                status_label = "🔴 APPROACHING"
                action_items.append(
                    f"WATCH {ticker}: only {distance_pct:.1f}% above GTT level "
                    f"₹{snapshot.trigger_price:.0f}"
                )
            else:
                status_label = f"✅ OK ({distance_pct:.1f}% above)"
            status_lines.append(
                f"{ticker} | CMP ₹{snapshot.current_price:.1f} | "
                f"GTT ₹{snapshot.trigger_price:.0f} | Dist {distance_pct:.1f}% | {status_label}"
            )
        return status_lines, action_items

    def _build_capital_allocation_section(
        self,
        holding_snapshots: dict[str, Optional[HoldingSnapshot]],
        action_items: list[str],
    ) -> list[str]:
        allocation_rows: list[str] = []
        for ticker, snapshot in holding_snapshots.items():
            if snapshot is None:
                allocation_rows.append(f"{ticker} | CMP N/A | P&L N/A | ❓ UNAVAILABLE")
                continue
            pnl = snapshot.profit_loss_pct
            if pnl > WINNER_PNL_THRESHOLD_PCT:
                allocation_label = "🌸 Winner — let run"
            elif abs(pnl) <= NEAR_COST_BAND_PCT:
                allocation_label = "⚪ Near cost"
            elif pnl < UNDERPERFORMER_PNL_THRESHOLD_PCT:
                allocation_label = "🔴 Underperformer"
                action_items.append(f"REVIEW {ticker}: P&L {pnl:.1f}% — underperformer flag")
            else:
                allocation_label = f"{'🟢' if pnl > 0 else '🟡'} {pnl:.1f}%"
            allocation_rows.append(
                f"{ticker} | ₹{snapshot.current_price:.1f} | {pnl:+.1f}% | {allocation_label}"
            )
        return allocation_rows

    def _classify_sl_status(
        self,
        snapshot: HoldingSnapshot,
        critical_rows: list[str],
        normal_rows: list[str],
        action_items: list[str],
    ) -> None:
        buffer_pct = snapshot.stop_loss_buffer_pct
        row_prefix = (
            f"{snapshot.ticker} | Close ₹{snapshot.current_price:.1f} | "
            f"SL ₹{snapshot.stop_loss_price:.0f} | Buffer {buffer_pct:.1f}%"
        )
        if snapshot.is_stop_loss_breached:
            critical_rows.insert(0, f"{row_prefix} | 🔴 STOP-LOSS BREACHED")
            action_items.append(
                f"EXIT {snapshot.ticker}: SL breached — "
                f"CMP ₹{snapshot.current_price:.1f} below SL ₹{snapshot.stop_loss_price:.0f}"
            )
        elif buffer_pct < SL_DANGER_BUFFER_PCT:
            critical_rows.append(f"{row_prefix} | 🚨 DANGER ZONE")
            action_items.append(
                f"TIGHT SL {snapshot.ticker}: only {buffer_pct:.1f}% buffer — "
                f"set alert at SL ₹{snapshot.stop_loss_price:.0f}"
            )
        elif buffer_pct < SL_MONITOR_BUFFER_PCT:
            normal_rows.append(f"{row_prefix} | ⚠️ Monitor")
        else:
            normal_rows.append(f"{row_prefix} | ✅ Safe")

    @staticmethod
    def _classify_relative_strength(
        stock_day_change_pct: float,
        nifty_day_change_pct: Optional[float],
        ticker: str,
        action_items: list[str],
    ) -> str:
        if nifty_day_change_pct is None:
            return "⚪ N/A (Nifty unavailable)"
        if stock_day_change_pct > 0 and nifty_day_change_pct <= 0:
            return "🟢 Outperformer"
        if stock_day_change_pct < 0 and nifty_day_change_pct > 0:
            action_items.append(
                f"WATCH {ticker}: lagging Nifty "
                f"(stock {stock_day_change_pct:+.1f}% vs Nifty {nifty_day_change_pct:+.1f}%)"
            )
            return "🔴 Underperformer"
        return "⚪ In-line"

    @staticmethod
    def _format_numbered_action_items(
        items: list[str],
        empty_message: str = "CLEAN — No action required",
    ) -> str:
        if not items:
            return empty_message
        return "\n".join(f"{position}. {item}" for position, item in enumerate(items, start=1))


# ── Cockpit orchestrator ──────────────────────────────────────────────────────

class CockpitRunner:
    """Orchestrates market-data fetching, report building, and Telegram delivery."""

    def __init__(self) -> None:
        self._market_data   = MarketDataService()
        self._web_search    = WebSearchClient()
        self._notifier      = TelegramNotifier()
        self._report_builder = CockpitReportBuilder()

    def run_phase1(self, date_label: str) -> None:
        gift_nifty_summary = self._web_search.search(
            "Gift Nifty pre-open level today vs previous Nifty 50 close. "
            "Give: level, gap%, and whether it is gap-up or gap-down. One paragraph max."
        )
        fii_dii_summary = self._web_search.search(
            "FII DII net activity today India equities. "
            "Give net FII and DII buy/sell in Crores. One line each."
        )
        gtt_snapshots     = self._market_data.fetch_all_gtt_snapshots()
        holding_snapshots = self._market_data.fetch_all_holding_snapshots()

        report = self._report_builder.build_phase1_report(
            date_label=date_label,
            gift_nifty_summary=gift_nifty_summary,
            fii_dii_summary=fii_dii_summary,
            gtt_snapshots=gtt_snapshots,
            holding_snapshots=holding_snapshots,
        )
        self._notifier.send(report)

    def run_phase2(self, date_label: str) -> None:
        holding_snapshots    = self._market_data.fetch_all_holding_snapshots()
        nifty_day_change_pct = self._market_data.fetch_nifty_day_change_pct()
        volume_data_by_ticker = {
            ticker: self._market_data.fetch_volume_data(ticker)
            for ticker in HOLDINGS
        }

        report = self._report_builder.build_phase2_report(
            date_label=date_label,
            nifty_day_change_pct=nifty_day_change_pct,
            holding_snapshots=holding_snapshots,
            volume_data_by_ticker=volume_data_by_ticker,
        )
        self._notifier.send(report)

    def run_phase3(self, date_label: str) -> None:
        holding_snapshots = self._market_data.fetch_all_holding_snapshots()

        report = self._report_builder.build_phase3_report(
            date_label=date_label,
            holding_snapshots=holding_snapshots,
        )
        self._notifier.send(report)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    arg_parser = argparse.ArgumentParser(
        description="Run a portfolio cockpit phase report."
    )
    arg_parser.add_argument(
        "--phase",
        type=int,
        choices=[1, 2, 3],
        required=True,
        help="1 = pre-market  2 = mid-market  3 = post-market",
    )
    args = arg_parser.parse_args()

    ist_timezone     = pytz.timezone("Asia/Kolkata")
    current_ist_time = datetime.now(ist_timezone)
    date_label       = current_ist_time.strftime("%d-%b-%Y")

    runner = CockpitRunner()
    phase_handlers = {
        1: runner.run_phase1,
        2: runner.run_phase2,
        3: runner.run_phase3,
    }
    phase_handlers[args.phase](date_label)


if __name__ == "__main__":
    main()
