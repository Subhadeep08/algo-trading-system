"""CockpitRunner — orchestrates all three daily cockpit phases.

Consolidates the phase logic previously spread across scripts/cockpit_runner.py.
Imports everything from the cockpit package; no hardcoded holdings or GTT dicts.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from cockpit.config import (
    GTT_APPROACHING_PCT,
    IST_ZONE,
    NEAR_COST_BAND_PCT,
    RSI_PROXY_MOVE_PCT,
    SL_DANGER_BUFFER_PCT,
    SL_MONITOR_BUFFER_PCT,
    TRAILING_SL_PULLBACK,
    UNDERPERFORMER_PNL_PCT,
    VOLATILE_DAY_MOVE_PCT,
    WINNER_PNL_PCT,
    HIGH_VOLUME_MULTIPLIER,
    ELEVATED_VOLUME_MULTIPLIER,
    NIFTY_50_SYMBOL,
)
from cockpit.market_data import MarketDataService
from cockpit.models import GttOrderSnapshot, HoldingFundamentalsRow, HoldingSnapshot, VolumeSnapshot
from cockpit.portfolio import get_registry
from cockpit.telegram import TelegramNotifier

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# WebSearchClient
# ---------------------------------------------------------------------------


class WebSearchClient:
    """Optional AI-powered web lookup for Gift Nifty and FII-DII data.

    Uses ANTHROPIC_API_KEY when present; returns a graceful N/A string when absent.
    Lazy-imports the ``anthropic`` library so the cockpit package does not require
    it at import time.
    """

    _MODEL = "claude-haiku-4-5-20251001"
    _TOOL = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 2}]
    _MAX_TOKENS = 300
    _FALLBACK = "N/A (set ANTHROPIC_API_KEY for live data)"

    def __init__(self) -> None:
        self._api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def fetch_gift_nifty(self) -> str:
        """Return a one-line Gift Nifty status string."""
        if not self.available:
            return self._FALLBACK
        return self._query(
            "What is the current Gift Nifty level and its change from previous close? "
            "Reply in one short line: e.g. 'Gift Nifty: 24,350 (+0.4%)'. "
            "Use web search to find the latest value."
        )

    def fetch_fii_dii(self) -> str:
        """Return a two-line FII / DII provisional flow summary."""
        if not self.available:
            return self._FALLBACK
        return self._query(
            "What are today's FII and DII provisional net buy/sell figures on Indian equity markets? "
            "Reply in two short lines: 'FII: ₹X Cr (buy/sell)' and 'DII: ₹Y Cr (buy/sell)'. "
            "Use web search to find the latest NSE provisional data."
        )

    def _query(self, prompt: str) -> str:
        try:
            import anthropic  # lazy import
        except ImportError:
            logger.warning("anthropic package not installed — web search unavailable")
            return self._FALLBACK

        try:
            client = anthropic.Anthropic(api_key=self._api_key)
            response = client.messages.create(
                model=self._MODEL,
                max_tokens=self._MAX_TOKENS,
                tools=self._TOOL,
                messages=[{"role": "user", "content": prompt}],
            )
            # Collect all text blocks from the response
            parts = [
                block.text
                for block in response.content
                if hasattr(block, "text") and block.text
            ]
            result = " ".join(parts).strip()
            return result if result else self._FALLBACK
        except Exception as exc:
            logger.warning("WebSearchClient query failed: %s", exc)
            return self._FALLBACK


# ---------------------------------------------------------------------------
# ReportFormatter
# ---------------------------------------------------------------------------


class ReportFormatter:
    """Static methods that format cockpit data structures into Telegram message strings."""

    # ------------------------------------------------------------------
    # Phase 1
    # ------------------------------------------------------------------

    @staticmethod
    def phase1_message(
        date_str: str,
        gift_nifty: str,
        gtt_snapshots: dict[str, Optional[GttOrderSnapshot]],
        fii_dii: str,
        holding_snapshots: dict[str, Optional[HoldingSnapshot]],
    ) -> str:
        lines: list[str] = []

        lines.append(f"📊 <b>PHASE 1 PRE-MARKET | {date_str} 09:05 IST</b>")
        lines.append("")

        # Gift Nifty
        lines.append("🌏 <b>GIFT NIFTY</b>")
        lines.append(gift_nifty)
        lines.append("")

        # GTT Alert Status
        lines.append("⚠️ <b>GTT ALERT STATUS</b>")
        if not gtt_snapshots:
            lines.append("No GTT orders on file.")
        else:
            for ticker, snap in gtt_snapshots.items():
                if snap is None:
                    lines.append(f"{ticker} | price fetch failed")
                    continue
                dist_pct = snap.distance_to_trigger_pct
                if snap.is_trigger_breached:
                    flag = "🔴 TRIGGERED"
                elif dist_pct <= GTT_APPROACHING_PCT:
                    flag = "⚠️ Approaching"
                else:
                    flag = "✅ OK"
                lines.append(
                    f"{ticker} | CMP ₹{snap.current_price:,.2f} | "
                    f"GTT ₹{snap.trigger_price:,.2f} | "
                    f"Dist {dist_pct:+.1f}% | {flag}"
                )
        lines.append("")

        # FII / DII Flows
        lines.append("📉 <b>FII/DII FLOWS</b>")
        lines.append(fii_dii)
        lines.append("")

        # Capital Allocation
        lines.append("💼 <b>CAPITAL ALLOCATION</b>")
        if not holding_snapshots:
            lines.append("No holdings on file.")
        else:
            for ticker, snap in holding_snapshots.items():
                if snap is None:
                    lines.append(f"{ticker} | price fetch failed")
                    continue
                pnl = snap.profit_loss_pct
                status = ReportFormatter._capital_status(pnl)
                lines.append(
                    f"{ticker} | CMP ₹{snap.current_price:,.2f} | "
                    f"P&L {pnl:+.1f}% | {status}"
                )
        lines.append("")

        # Action Items
        lines.append("✅ <b>ACTION ITEMS</b>")
        actions = ReportFormatter._phase1_actions(gtt_snapshots, holding_snapshots)
        if actions:
            for i, action in enumerate(actions, 1):
                lines.append(f"{i}. {action}")
        else:
            lines.append("CLEAN — No action required")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Phase 2
    # ------------------------------------------------------------------

    @staticmethod
    def phase2_message(
        date_str: str,
        nifty_chg: Optional[float],
        holding_snapshots: dict[str, Optional[HoldingSnapshot]],
        volume_snapshots: dict[str, Optional[VolumeSnapshot]],
        nifty_level: Optional[float],
    ) -> str:
        lines: list[str] = []

        nifty_chg_str = f"{nifty_chg:+.2f}%" if nifty_chg is not None else "N/A"
        nifty_level_str = (
            f" @ {nifty_level:,.0f}" if nifty_level is not None else ""
        )
        lines.append(f"📊 <b>PHASE 2 MID-MARKET | {date_str} 13:30 IST</b>")
        lines.append("")

        # Relative Strength
        lines.append(f"📈 <b>RELATIVE STRENGTH vs NIFTY ({nifty_chg_str}{nifty_level_str})</b>")
        if not holding_snapshots:
            lines.append("No holdings on file.")
        else:
            for ticker, snap in holding_snapshots.items():
                if snap is None:
                    lines.append(f"{ticker} | price fetch failed")
                    continue
                day_chg = snap.day_change_pct
                rs_status = ReportFormatter._rs_status(day_chg, nifty_chg)
                lines.append(
                    f"{ticker} | CMP ₹{snap.current_price:,.2f} | "
                    f"Day {day_chg:+.2f}% | RS: {rs_status}"
                )
        lines.append("")

        # Volume Alerts
        lines.append("📦 <b>VOLUME ALERTS</b>")
        vol_alerts = ReportFormatter._volume_alert_lines(volume_snapshots)
        if vol_alerts:
            lines.extend(vol_alerts)
        else:
            lines.append("None")
        lines.append("")

        # RSI Warnings (proxy: large single-day move)
        lines.append("⚡ <b>RSI WARNINGS</b>")
        rsi_warnings = ReportFormatter._rsi_warning_lines(holding_snapshots)
        if rsi_warnings:
            lines.extend(rsi_warnings)
        else:
            lines.append("None")
        lines.append("")

        # Portfolio Snapshot
        lines.append("💰 <b>PORTFOLIO SNAPSHOT</b>")
        invested, current = ReportFormatter._portfolio_totals(holding_snapshots)
        if invested > 0:
            total_pnl = (current - invested) / invested * 100
            lines.append(
                f"Invested: ₹{invested:,.0f} | "
                f"Current: ₹{current:,.0f} | "
                f"P&L: {total_pnl:+.2f}%"
            )
        else:
            lines.append("No live holdings available.")
        lines.append("")

        # Action Items
        lines.append("✅ <b>ACTION ITEMS</b>")
        actions = ReportFormatter._phase2_actions(holding_snapshots, volume_snapshots, nifty_chg)
        if actions:
            for i, action in enumerate(actions, 1):
                lines.append(f"{i}. {action}")
        else:
            lines.append("CLEAN — No action required")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Phase 3
    # ------------------------------------------------------------------

    @staticmethod
    def phase3_message(
        date_str: str,
        holding_snapshots: dict[str, Optional[HoldingSnapshot]],
    ) -> str:
        lines: list[str] = []

        lines.append(f"📊 <b>PHASE 3 POST-MARKET AUDIT | {date_str} 15:35 IST</b>")
        lines.append("")

        # Stop-Loss Status — BREACHED / DANGER first
        lines.append("🔍 <b>STOP-LOSS STATUS</b>")
        sl_rows = ReportFormatter._sl_rows_sorted(holding_snapshots)
        if sl_rows:
            lines.extend(sl_rows)
        else:
            lines.append("No holdings on file.")
        lines.append("")

        # Trailing SL Ratchet
        lines.append("📐 <b>TRAILING SL RATCHET</b>")
        ratchet_lines = ReportFormatter._ratchet_lines(holding_snapshots)
        if ratchet_lines:
            lines.extend(ratchet_lines)
        else:
            lines.append("None — all SLs current")
        lines.append("")

        # Portfolio Close
        lines.append("💰 <b>PORTFOLIO CLOSE</b>")
        invested, current = ReportFormatter._portfolio_totals(holding_snapshots)
        if invested > 0:
            total_pnl = (current - invested) / invested * 100
            lines.append(
                f"Total: ₹{current:,.0f} | P&L: {total_pnl:+.2f}%"
            )
        else:
            lines.append("No live holdings available.")
        lines.append("")

        # Stay-Put Reminders
        lines.append("🛑 <b>STAY-PUT REMINDERS</b>")
        reminders = ReportFormatter._stay_put_reminders(holding_snapshots)
        if reminders:
            lines.extend(reminders)
        else:
            lines.append("None")
        lines.append("")

        # Action Items
        lines.append("✅ <b>ACTION ITEMS</b>")
        actions = ReportFormatter._phase3_actions(holding_snapshots)
        if actions:
            for i, action in enumerate(actions, 1):
                lines.append(f"{i}. {action}")
        else:
            lines.append("CLEAN — No action required")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _capital_status(pnl: float) -> str:
        if pnl > WINNER_PNL_PCT:
            return "🌸 Winner-let run"
        if abs(pnl) <= NEAR_COST_BAND_PCT:
            return "⚪ Near cost"
        if pnl < UNDERPERFORMER_PNL_PCT:
            return "🔴 Underperformer"
        # 5% < pnl <= 15%  →  green
        # UNDERPERFORMER_PNL_PCT <= pnl < -5%  →  yellow
        emoji = "🟢" if pnl > 0 else "🟡"
        return f"{emoji} {pnl:.1f}%"

    @staticmethod
    def _rs_status(stock_chg: float, nifty_chg: Optional[float]) -> str:
        if nifty_chg is None:
            return "⚪ In-line (Nifty N/A)"
        if stock_chg > 0 and nifty_chg <= 0:
            return "🟢 Outperformer"
        if stock_chg < 0 and nifty_chg > 0:
            return "🔴 Underperformer"
        return "⚪ In-line"

    @staticmethod
    def _sl_status(snap: HoldingSnapshot) -> tuple[int, str]:
        """Return (sort_priority, label). Lower priority value sorts first."""
        buf = snap.stop_loss_buffer_pct
        if snap.is_stop_loss_breached:
            return (0, "🔴 STOP-LOSS BREACHED")
        if buf < SL_DANGER_BUFFER_PCT:
            return (1, "🚨 DANGER ZONE")
        if buf < SL_MONITOR_BUFFER_PCT:
            return (2, "⚠️ Monitor")
        return (3, "✅ Safe")

    @staticmethod
    def _sl_rows_sorted(
        holding_snapshots: dict[str, Optional[HoldingSnapshot]],
    ) -> list[str]:
        rows: list[tuple[int, str]] = []
        for ticker, snap in holding_snapshots.items():
            if snap is None:
                rows.append((99, f"{ticker} | price fetch failed"))
                continue
            priority, label = ReportFormatter._sl_status(snap)
            buf = snap.stop_loss_buffer_pct
            row = (
                f"{ticker} | Close ₹{snap.current_price:,.2f} | "
                f"SL ₹{snap.stop_loss_price:,.2f} | "
                f"Buffer {buf:+.1f}% | {label}"
            )
            rows.append((priority, row))
        rows.sort(key=lambda t: t[0])
        return [r for _, r in rows]

    @staticmethod
    def _ratchet_lines(
        holding_snapshots: dict[str, Optional[HoldingSnapshot]],
    ) -> list[str]:
        result: list[str] = []
        for ticker, snap in holding_snapshots.items():
            if snap is None:
                continue
            if snap.profit_loss_pct > WINNER_PNL_PCT:
                suggested_sl = snap.current_price * (1.0 - TRAILING_SL_PULLBACK)
                result.append(
                    f"{ticker}: P&L {snap.profit_loss_pct:+.1f}% — "
                    f"Ratchet SL → ₹{suggested_sl:,.2f} "
                    f"(10% below CMP ₹{snap.current_price:,.2f})"
                )
        return result

    @staticmethod
    def _stay_put_reminders(
        holding_snapshots: dict[str, Optional[HoldingSnapshot]],
    ) -> list[str]:
        result: list[str] = []
        for ticker, snap in holding_snapshots.items():
            if snap is None:
                continue
            if abs(snap.day_change_pct) >= VOLATILE_DAY_MOVE_PCT:
                direction = "up" if snap.day_change_pct > 0 else "down"
                result.append(
                    f"{ticker}: volatile session ({snap.day_change_pct:+.1f}% {direction}) — "
                    "hold discipline; avoid panic/FOMO trades"
                )
        return result

    @staticmethod
    def _volume_alert_lines(
        volume_snapshots: dict[str, Optional[VolumeSnapshot]],
    ) -> list[str]:
        result: list[str] = []
        for ticker, snap in volume_snapshots.items():
            if snap is None:
                continue
            if snap.volume_ratio >= HIGH_VOLUME_MULTIPLIER:
                result.append(
                    f"🔥 {ticker}: {snap.volume_ratio:.1f}x avg volume "
                    f"({snap.today_volume:,.0f} vs avg {snap.average_20d_volume:,.0f}) — HIGH"
                )
            elif snap.volume_ratio >= ELEVATED_VOLUME_MULTIPLIER:
                result.append(
                    f"📈 {ticker}: {snap.volume_ratio:.1f}x avg volume "
                    f"({snap.today_volume:,.0f} vs avg {snap.average_20d_volume:,.0f}) — Elevated"
                )
        return result

    @staticmethod
    def _rsi_warning_lines(
        holding_snapshots: dict[str, Optional[HoldingSnapshot]],
    ) -> list[str]:
        result: list[str] = []
        for ticker, snap in holding_snapshots.items():
            if snap is None:
                continue
            if abs(snap.day_change_pct) >= RSI_PROXY_MOVE_PCT:
                direction = "surge" if snap.day_change_pct > 0 else "drop"
                result.append(
                    f"{ticker}: {snap.day_change_pct:+.1f}% intraday {direction} — "
                    "RSI risk; avoid chasing"
                )
        return result

    @staticmethod
    def _portfolio_totals(
        holding_snapshots: dict[str, Optional[HoldingSnapshot]],
    ) -> tuple[float, float]:
        invested = sum(
            snap.book_value for snap in holding_snapshots.values() if snap is not None
        )
        current = sum(
            snap.market_value for snap in holding_snapshots.values() if snap is not None
        )
        return invested, current

    @staticmethod
    def _format_numbered_action_items(
        items: list[str],
        empty_message: str = "CLEAN — No action required",
    ) -> str:
        if not items:
            return empty_message
        return "\n".join(f"{i}. {item}" for i, item in enumerate(items, 1))

    @staticmethod
    def fundamentals_section(rows: list) -> str:
        """Format a Screener.in holding fundamentals table for Telegram."""
        lines = ["📊 <b>HOLDING FUNDAMENTALS (Screener.in)</b>",
                 "TICKER     | ROCE%  | D/E   | Qtr PAT Gr% | Promo% | Status"]
        status_emoji = {"STRONG": "💚", "WATCH": "🟡", "CONCERN": "🔴", "NO_DATA": "⚪"}
        for row in rows:
            roce  = f"{row.roce_pct:.1f}"  if row.roce_pct  is not None else "N/A"
            de    = f"{row.de_ratio:.2f}"  if row.de_ratio  is not None else "N/A"
            pat   = (f"{row.quarterly_pat_growth_pct:+.0f}%"
                     if row.quarterly_pat_growth_pct is not None else "N/A")
            promo = f"{row.promoter_pct:.1f}" if row.promoter_pct is not None else "N/A"
            emoji = status_emoji.get(row.status, "")
            lines.append(
                f"{row.ticker:<10} | {roce:<7}| {de:<6}| {pat:<12}| {promo:<7}| {emoji} {row.status}"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Action item generators
    # ------------------------------------------------------------------

    @staticmethod
    def _phase1_actions(
        gtt_snapshots: dict[str, Optional[GttOrderSnapshot]],
        holding_snapshots: dict[str, Optional[HoldingSnapshot]],
    ) -> list[str]:
        actions: list[str] = []
        for ticker, snap in gtt_snapshots.items():
            if snap is None:
                continue
            if snap.is_trigger_breached:
                actions.append(f"🔴 {ticker}: GTT triggered — verify order execution")
            elif snap.distance_to_trigger_pct <= GTT_APPROACHING_PCT:
                actions.append(
                    f"⚠️ {ticker}: GTT approaching (dist {snap.distance_to_trigger_pct:.1f}%) — "
                    "confirm order is active"
                )
        for ticker, snap in holding_snapshots.items():
            if snap is None:
                continue
            if snap.profit_loss_pct < UNDERPERFORMER_PNL_PCT:
                actions.append(
                    f"🔴 {ticker}: Underperformer ({snap.profit_loss_pct:+.1f}%) — review thesis"
                )
        return actions

    @staticmethod
    def _phase2_actions(
        holding_snapshots: dict[str, Optional[HoldingSnapshot]],
        volume_snapshots: dict[str, Optional[VolumeSnapshot]],
        nifty_chg: Optional[float],
    ) -> list[str]:
        actions: list[str] = []
        for ticker, snap in holding_snapshots.items():
            if snap is None:
                continue
            rs = ReportFormatter._rs_status(snap.day_change_pct, nifty_chg)
            if "Underperformer" in rs:
                actions.append(
                    f"🔴 {ticker}: underperforming Nifty "
                    f"({snap.day_change_pct:+.1f}% vs market) — monitor closely"
                )
        for ticker, snap in volume_snapshots.items():
            if snap is None:
                continue
            if snap.volume_ratio >= HIGH_VOLUME_MULTIPLIER:
                actions.append(
                    f"🔥 {ticker}: high volume ({snap.volume_ratio:.1f}x) — "
                    "check news/catalyst"
                )
        return actions

    @staticmethod
    def _phase3_actions(
        holding_snapshots: dict[str, Optional[HoldingSnapshot]],
    ) -> list[str]:
        actions: list[str] = []
        for ticker, snap in holding_snapshots.items():
            if snap is None:
                continue
            priority, label = ReportFormatter._sl_status(snap)
            if priority == 0:
                actions.append(f"🔴 {ticker}: SL breached — exit at open tomorrow")
            elif priority == 1:
                actions.append(
                    f"🚨 {ticker}: danger zone (buffer {snap.stop_loss_buffer_pct:.1f}%) — "
                    "tighten SL or prepare exit"
                )
        for ticker, snap in holding_snapshots.items():
            if snap is None:
                continue
            if snap.profit_loss_pct > WINNER_PNL_PCT:
                suggested_sl = snap.current_price * (1.0 - TRAILING_SL_PULLBACK)
                actions.append(
                    f"🌸 {ticker}: winner ({snap.profit_loss_pct:+.1f}%) — "
                    f"ratchet SL to ₹{suggested_sl:,.2f}"
                )
        return actions


# ---------------------------------------------------------------------------
# HoldingRequalifier
# ---------------------------------------------------------------------------


class HoldingRequalifier:
    """Classifies an active holding's requalification status from Gate 1+2 results."""

    @staticmethod
    def _classify(g1, g2, ticker: str) -> tuple[str, str]:
        """Return (status_description, action_flag).

        g1: Stage2CheckResult  (attrs: passed, stage_label, ma_150)
        g2: UDRatioResult      (attrs: passed, disqualified, distribution_flag, ud_50, ud_21)
        """
        if not g1.passed:
            label = g1.stage_label or ""
            if any(k in label for k in ("3", "4", "Declining")):
                return f"THESIS VIOLATION — {label}", f"⚠️ REVIEW {ticker}"
            if "Warning" in label or "flattening" in label.lower():
                return f"Stage 2 Warning — {label}", f"⚠️ Tighten SL {ticker}"
            return f"Stage 1 Basing — {label}", ""

        if g2.disqualified:
            return (
                f"DISTRIBUTION DISQUALIFY — U/D_50={g2.ud_50:.2f}",
                f"⚠️ EXIT CANDIDATE {ticker}",
            )
        if g2.distribution_flag:
            return (
                f"Near-term distribution (U/D_21={g2.ud_21:.2f})",
                f"⚠️ Monitor {ticker}",
            )
        if not g2.passed:
            return "Weak accumulation — U/D below threshold", ""

        return "CONFIRMED ACCUMULATION — Gates 1+2 clear", ""


# ---------------------------------------------------------------------------
# HoldingFundamentalsChecker
# ---------------------------------------------------------------------------


class HoldingFundamentalsChecker:
    """Fetches and classifies fundamental health for a list of tickers via Screener.in."""

    def check_all(self, tickers: list[str], client) -> list[HoldingFundamentalsRow]:
        return [
            HoldingFundamentalsRow.from_screener(ticker, client.fetch(ticker))
            for ticker in tickers
        ]


# ---------------------------------------------------------------------------
# CockpitRunner
# ---------------------------------------------------------------------------


class CockpitRunner:
    """Orchestrates the three daily cockpit phases using injected dependencies."""

    def __init__(
        self,
        market_data: MarketDataService,
        notifier: TelegramNotifier,
        web_search: Optional[WebSearchClient] = None,
        screener_client=None,
    ) -> None:
        self._market_data = market_data
        self._notifier = notifier
        self._web_search = web_search or WebSearchClient()
        self._screener_client = screener_client

    # ------------------------------------------------------------------
    # Phase 1 — Pre-Market
    # ------------------------------------------------------------------

    def run_phase1(self, date_str: str) -> None:
        logger.info("Phase 1 starting for %s", date_str)

        logger.info("Fetching Gift Nifty via web search")
        gift_nifty = self._web_search.fetch_gift_nifty()

        logger.info("Fetching FII/DII data via web search")
        fii_dii = self._web_search.fetch_fii_dii()

        logger.info("Fetching GTT snapshots")
        gtt_snapshots = self._market_data.gtt_snapshots()

        logger.info("Fetching holding snapshots")
        holding_snapshots = self._market_data.holding_snapshots()

        message = ReportFormatter.phase1_message(
            date_str=date_str,
            gift_nifty=gift_nifty,
            gtt_snapshots=gtt_snapshots,
            fii_dii=fii_dii,
            holding_snapshots=holding_snapshots,
        )

        logger.info("Sending Phase 1 report to Telegram")
        self._notifier.send_chunked(message)
        logger.info("Phase 1 complete")

    # ------------------------------------------------------------------
    # Phase 2 — Mid-Market
    # ------------------------------------------------------------------

    def run_phase2(self, date_str: str) -> None:
        logger.info("Phase 2 starting for %s", date_str)

        logger.info("Fetching holding snapshots")
        holding_snapshots = self._market_data.holding_snapshots()

        logger.info("Fetching Nifty change")
        nifty_chg = self._market_data.nifty_day_change_pct()
        nifty_level = self._fetch_nifty_level()

        logger.info("Fetching volume snapshots for all holdings")
        tickers = list(holding_snapshots.keys())
        volume_snapshots: dict[str, Optional[VolumeSnapshot]] = {
            ticker: self._market_data.volume_snapshot(ticker)
            for ticker in tickers
        }

        message = ReportFormatter.phase2_message(
            date_str=date_str,
            nifty_chg=nifty_chg,
            holding_snapshots=holding_snapshots,
            volume_snapshots=volume_snapshots,
            nifty_level=nifty_level,
        )

        logger.info("Sending Phase 2 report to Telegram")
        self._notifier.send_chunked(message)
        logger.info("Phase 2 complete")

    # ------------------------------------------------------------------
    # Phase 3 — Post-Market
    # ------------------------------------------------------------------

    def run_phase3(self, date_str: str) -> None:
        logger.info("Phase 3 starting for %s", date_str)

        logger.info("Fetching holding snapshots")
        holding_snapshots = self._market_data.holding_snapshots()

        message = ReportFormatter.phase3_message(
            date_str=date_str,
            holding_snapshots=holding_snapshots,
        )

        if self._screener_client is not None:
            logger.info("Fetching holding fundamentals from Screener.in")
            tickers = list(holding_snapshots.keys())
            checker = HoldingFundamentalsChecker()
            fund_rows = checker.check_all(tickers, self._screener_client)
            message += "\n\n" + ReportFormatter.fundamentals_section(fund_rows)

        logger.info("Sending Phase 3 report to Telegram")
        self._notifier.send_chunked(message)
        logger.info("Phase 3 complete")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_nifty_level(self) -> Optional[float]:
        """Fetch the current Nifty 50 index level. Returns None on failure."""
        try:
            import yfinance as yf  # already a project dependency
            fi = yf.Ticker(NIFTY_50_SYMBOL).fast_info
            return float(fi.last_price) if fi.last_price is not None else None
        except Exception as exc:
            logger.warning("Nifty level fetch failed: %s", exc)
            return None


# ---------------------------------------------------------------------------
# Module-level entry point
# ---------------------------------------------------------------------------


def run(phase: int) -> None:
    """Wire up dependencies and run the given cockpit phase (1, 2, or 3).

    Called by the entry-point script (scripts/cockpit_runner.py --phase N).
    The IST date string is computed here and passed into the phase runner.
    """
    import datetime
    import zoneinfo

    ist = zoneinfo.ZoneInfo(IST_ZONE)
    date_str = datetime.datetime.now(tz=ist).strftime("%Y-%m-%d")

    market_data = MarketDataService()
    notifier = TelegramNotifier()
    web_search = WebSearchClient()

    from cockpit.screener_in import ScreenerInClient
    screener_client = ScreenerInClient() if ScreenerInClient.is_configured() else None
    if screener_client is None and phase == 3:
        logger.warning("SCREENER_IN_SESSION not set — Phase 3 holding fundamentals skipped")

    runner = CockpitRunner(
        market_data=market_data,
        notifier=notifier,
        web_search=web_search,
        screener_client=screener_client,
    )

    if phase == 1:
        runner.run_phase1(date_str)
    elif phase == 2:
        runner.run_phase2(date_str)
    elif phase == 3:
        runner.run_phase3(date_str)
    else:
        raise ValueError(f"Unknown phase: {phase!r}. Expected 1, 2, or 3.")
