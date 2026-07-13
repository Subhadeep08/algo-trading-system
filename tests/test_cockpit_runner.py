"""Tests for cockpit/runner.py — snapshots, SL classification, RS, allocation, requalifier."""
from __future__ import annotations

import pytest

from cockpit.config import (
    SL_DANGER_BUFFER_PCT,
    SL_MONITOR_BUFFER_PCT,
    UNDERPERFORMER_PNL_PCT,
    WINNER_PNL_PCT,
)
from cockpit.models import GttOrderSnapshot, HoldingSnapshot
from cockpit.runner import HoldingRequalifier, ReportFormatter


# ── HoldingSnapshot computed properties ──────────────────────────────────────

class TestHoldingSnapshot:
    def _snap(self, current=100.0, prev=95.0, qty=10, cost=90.0, sl=80.0, target=120.0):
        return HoldingSnapshot(
            ticker="TEST",
            current_price=current,
            previous_close=prev,
            quantity=qty,
            average_cost=cost,
            stop_loss_price=sl,
            target_price=target,
        )

    def test_day_change_pct_positive(self):
        snap = self._snap(current=105.0, prev=100.0)
        assert abs(snap.day_change_pct - 5.0) < 1e-9

    def test_day_change_pct_negative(self):
        snap = self._snap(current=90.0, prev=100.0)
        assert abs(snap.day_change_pct - (-10.0)) < 1e-9

    def test_profit_loss_pct_gain(self):
        snap = self._snap(current=110.0, cost=100.0)
        assert abs(snap.profit_loss_pct - 10.0) < 1e-9

    def test_profit_loss_pct_loss(self):
        snap = self._snap(current=85.0, cost=100.0)
        assert abs(snap.profit_loss_pct - (-15.0)) < 1e-9

    def test_stop_loss_buffer_above_sl(self):
        # (95 - 80) / 80 * 100 = 18.75
        snap = self._snap(current=95.0, sl=80.0)
        assert abs(snap.stop_loss_buffer_pct - 18.75) < 1e-9

    def test_stop_loss_buffer_below_sl_is_negative(self):
        snap = self._snap(current=75.0, sl=80.0)
        assert snap.stop_loss_buffer_pct < 0

    def test_market_value(self):
        snap = self._snap(current=200.0, qty=5)
        assert snap.market_value == pytest.approx(1000.0)

    def test_book_value(self):
        snap = self._snap(cost=150.0, qty=4)
        assert snap.book_value == pytest.approx(600.0)

    def test_is_stop_loss_breached_when_below_sl(self):
        assert self._snap(current=79.0, sl=80.0).is_stop_loss_breached is True

    def test_is_stop_loss_breached_false_when_above_sl(self):
        assert self._snap(current=81.0, sl=80.0).is_stop_loss_breached is False

    def test_is_stop_loss_breached_false_at_exact_sl(self):
        assert self._snap(current=80.0, sl=80.0).is_stop_loss_breached is False


# ── GttOrderSnapshot computed properties ─────────────────────────────────────

class TestGttOrderSnapshot:
    def _snap(self, current=1050.0, trigger=1000.0, target=1300.0):
        return GttOrderSnapshot(
            ticker="GTT",
            current_price=current,
            trigger_price=trigger,
            target_price=target,
            quantity=0,
        )

    def test_distance_to_trigger_positive(self):
        snap = self._snap(current=1050.0, trigger=1000.0)
        assert abs(snap.distance_to_trigger_pct - 5.0) < 1e-9

    def test_distance_to_trigger_negative_when_below(self):
        snap = self._snap(current=950.0, trigger=1000.0)
        assert snap.distance_to_trigger_pct < 0

    def test_is_trigger_breached_when_at_trigger(self):
        assert self._snap(current=1000.0, trigger=1000.0).is_trigger_breached is True

    def test_is_trigger_breached_when_below_trigger(self):
        assert self._snap(current=990.0, trigger=1000.0).is_trigger_breached is True

    def test_is_trigger_not_breached_when_above(self):
        assert self._snap(current=1010.0, trigger=1000.0).is_trigger_breached is False


# ── ReportFormatter._sl_status ───────────────────────────────────────────────

class TestClassifySlStatus:
    def _snap(self, current, sl):
        return HoldingSnapshot(
            ticker="T", current_price=current, previous_close=95.0,
            quantity=10, average_cost=90.0, stop_loss_price=sl, target_price=120.0,
        )

    def _run(self, current, sl):
        return ReportFormatter._sl_status(self._snap(current, sl))

    def test_breached_goes_to_priority_zero(self):
        priority, label = self._run(current=78.0, sl=80.0)
        assert priority == 0
        assert "BREACHED" in label

    def test_danger_zone_buffer_below_danger_threshold(self):
        # buffer = (81 - 80) / 80 * 100 = 1.25% < SL_DANGER_BUFFER_PCT (2%)
        priority, label = self._run(current=81.0, sl=80.0)
        assert priority == 1
        assert "DANGER" in label

    def test_monitor_zone_buffer_between_danger_and_monitor(self):
        # buffer ≈ 3.75% → between 2% and 5%
        priority, label = self._run(current=83.0, sl=80.0)
        assert priority == 2
        assert "Monitor" in label

    def test_safe_buffer_above_monitor_threshold(self):
        # buffer = (90 - 80) / 80 * 100 = 12.5% > SL_MONITOR_BUFFER_PCT (5%)
        priority, label = self._run(current=90.0, sl=80.0)
        assert priority == 3
        assert "Safe" in label


# ── ReportFormatter._rs_status ───────────────────────────────────────────────

class TestClassifyRelativeStrength:
    def _classify(self, stock_pct, nifty_pct):
        return ReportFormatter._rs_status(stock_pct, nifty_pct)

    def test_outperformer_stock_green_nifty_flat(self):
        label = self._classify(stock_pct=1.5, nifty_pct=0.0)
        assert "Outperformer" in label

    def test_outperformer_stock_green_nifty_red(self):
        label = self._classify(stock_pct=0.5, nifty_pct=-0.3)
        assert "Outperformer" in label

    def test_underperformer_stock_red_nifty_green(self):
        label = self._classify(stock_pct=-1.0, nifty_pct=0.5)
        assert "Underperformer" in label

    def test_inline_both_positive(self):
        label = self._classify(stock_pct=0.5, nifty_pct=0.4)
        assert "In-line" in label

    def test_inline_both_negative(self):
        label = self._classify(stock_pct=-0.3, nifty_pct=-0.8)
        assert "In-line" in label

    def test_nifty_unavailable_returns_na(self):
        label = self._classify(stock_pct=1.0, nifty_pct=None)
        assert "N/A" in label


# ── ReportFormatter._capital_status ──────────────────────────────────────────

class TestCapitalAllocationSection:
    def test_winner_label_above_threshold(self):
        # pnl = +20% > WINNER_PNL_PCT (15%)
        label = ReportFormatter._capital_status(20.0)
        assert "Winner" in label

    def test_near_cost_label_within_band(self):
        # pnl = +3% within NEAR_COST_BAND_PCT (5%)
        label = ReportFormatter._capital_status(3.0)
        assert "Near cost" in label

    def test_underperformer_label_below_threshold(self):
        # pnl = -15% < UNDERPERFORMER_PNL_PCT (-10%)
        label = ReportFormatter._capital_status(-15.0)
        assert "Underperformer" in label

    def test_none_snapshot_shows_price_fetch_failed(self):
        msg = ReportFormatter.phase1_message(
            date_str="2026-01-01",
            gift_nifty="N/A",
            gtt_snapshots={},
            fii_dii="N/A",
            holding_snapshots={"A": None},
        )
        assert "price fetch failed" in msg


# ── ReportFormatter._format_numbered_action_items ────────────────────────────

class TestFormatNumberedActionItems:
    def test_empty_returns_default_message(self):
        out = ReportFormatter._format_numbered_action_items([])
        assert out == "CLEAN — No action required"

    def test_custom_empty_message(self):
        out = ReportFormatter._format_numbered_action_items([], empty_message="ALL GOOD")
        assert out == "ALL GOOD"

    def test_single_item_numbered(self):
        out = ReportFormatter._format_numbered_action_items(["Do this"])
        assert out == "1. Do this"

    def test_multiple_items_numbered(self):
        out = ReportFormatter._format_numbered_action_items(["Alpha", "Beta", "Gamma"])
        lines = out.splitlines()
        assert lines[0].startswith("1.")
        assert lines[1].startswith("2.")
        assert lines[2].startswith("3.")


# ── HoldingRequalifier._classify ─────────────────────────────────────────────

class TestHoldingRequalifierClassify:
    class _G1:
        def __init__(self, passed, stage_label, ma_150=None):
            self.passed = passed
            self.stage_label = stage_label
            self.ma_150 = ma_150

    class _G2:
        def __init__(self, passed, disqualified=False, distribution_flag=False,
                     ud_50=1.3, ud_21=1.1):
            self.passed = passed
            self.disqualified = disqualified
            self.distribution_flag = distribution_flag
            self.ud_50 = ud_50
            self.ud_21 = ud_21

    def test_stage3_declining_is_thesis_violation(self):
        g1 = self._G1(passed=False, stage_label="Stage 3/4 (Declining)")
        g2 = self._G2(passed=True)
        status, flag = HoldingRequalifier._classify(g1, g2, "TICK")
        assert "THESIS VIOLATION" in status
        assert "TICK" in flag

    def test_stage2_warning_is_tighten_sl(self):
        g1 = self._G1(passed=False, stage_label="Stage 2 Warning (MA flattening)")
        g2 = self._G2(passed=True)
        status, flag = HoldingRequalifier._classify(g1, g2, "TICK")
        assert "Warning" in status
        assert "TICK" in flag

    def test_stage1_basing_shows_basing_label(self):
        g1 = self._G1(passed=False, stage_label="Stage 1 (Basing)")
        g2 = self._G2(passed=True)
        status, flag = HoldingRequalifier._classify(g1, g2, "TICK")
        assert "Basing" in status
        assert flag == ""

    def test_gate2_disqualified_shows_distribution_disqualify(self):
        g1 = self._G1(passed=True, stage_label="Stage 2 (Advancing)")
        g2 = self._G2(passed=False, disqualified=True, ud_50=0.60)
        status, flag = HoldingRequalifier._classify(g1, g2, "TICK")
        assert "DISTRIBUTION DISQUALIFY" in status
        assert "TICK" in flag

    def test_gate2_distribution_flag_shows_near_term_warning(self):
        g1 = self._G1(passed=True, stage_label="Stage 2 (Advancing)")
        g2 = self._G2(passed=True, distribution_flag=True, ud_21=0.70)
        status, flag = HoldingRequalifier._classify(g1, g2, "TICK")
        assert "distribution" in status.lower()
        assert "TICK" in flag

    def test_gate2_weak_accumulation_shows_weak_label(self):
        g1 = self._G1(passed=True, stage_label="Stage 2 (Advancing)")
        g2 = self._G2(passed=False, disqualified=False, distribution_flag=False)
        status, flag = HoldingRequalifier._classify(g1, g2, "TICK")
        assert "Weak" in status or "weak" in status
        assert flag == ""

    def test_all_clear_confirmed_accumulation(self):
        g1 = self._G1(passed=True, stage_label="Stage 2 (Advancing)")
        g2 = self._G2(passed=True, disqualified=False, distribution_flag=False)
        status, flag = HoldingRequalifier._classify(g1, g2, "TICK")
        assert "CONFIRMED ACCUMULATION" in status
        assert flag == ""
