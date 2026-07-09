"""Tests for scripts/fetch_nse_prices.py — price rows, report renderer, fetcher."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest

from conftest import make_mock_ticker
from fetch_nse_prices import GttPriceRow, HoldingPriceRow, LivePriceReport, NsePriceFetcher


# ── HoldingPriceRow computed properties ──────────────────────────────────────

class TestHoldingPriceRowProperties:
    def _row(self, current=None, prev_close=None, cost=100.0, sl=90.0, target=120.0, qty=10):
        return HoldingPriceRow(
            ticker="TEST",
            current_price=current,
            previous_close=prev_close,
            quantity=qty,
            average_cost=cost,
            stop_loss_price=sl,
            target_price=target,
        )

    def test_day_change_pct_positive(self):
        row = self._row(current=105.0, prev_close=100.0)
        assert abs(row.day_change_pct - 5.0) < 1e-9

    def test_day_change_pct_negative(self):
        row = self._row(current=95.0, prev_close=100.0)
        assert abs(row.day_change_pct - (-5.0)) < 1e-9

    def test_day_change_pct_none_when_no_current(self):
        assert self._row(current=None, prev_close=100.0).day_change_pct is None

    def test_day_change_pct_none_when_no_prev_close(self):
        assert self._row(current=105.0, prev_close=None).day_change_pct is None

    def test_profit_loss_pct_gain(self):
        row = self._row(current=110.0, cost=100.0)
        assert abs(row.profit_loss_pct - 10.0) < 1e-9

    def test_profit_loss_pct_loss(self):
        row = self._row(current=85.0, cost=100.0)
        assert abs(row.profit_loss_pct - (-15.0)) < 1e-9

    def test_profit_loss_pct_none_when_no_current(self):
        assert self._row(current=None).profit_loss_pct is None

    def test_stop_loss_buffer_above_sl(self):
        # (95 - 90) / 90 * 100 = 5.555...
        row = self._row(current=95.0, sl=90.0)
        assert abs(row.stop_loss_buffer_pct - 5.555555) < 1e-4

    def test_stop_loss_buffer_below_sl_is_negative(self):
        row = self._row(current=85.0, sl=90.0)
        assert row.stop_loss_buffer_pct < 0

    def test_stop_loss_buffer_none_when_no_current(self):
        assert self._row(current=None).stop_loss_buffer_pct is None

    def test_upside_to_target(self):
        # (120 - 100) / 100 * 100 = 20%
        row = self._row(current=100.0, target=120.0)
        assert abs(row.upside_to_target_pct - 20.0) < 1e-9

    def test_upside_to_target_none_when_no_target(self):
        row = self._row(current=100.0, target=None)
        assert row.upside_to_target_pct is None

    def test_upside_to_target_none_when_no_current(self):
        assert self._row(current=None, target=120.0).upside_to_target_pct is None

    def test_market_value(self):
        row = self._row(current=150.0, qty=20)
        assert row.market_value == pytest.approx(3000.0)

    def test_market_value_none_when_no_current(self):
        assert self._row(current=None, qty=20).market_value is None

    def test_book_value(self):
        row = self._row(cost=200.0, qty=15)
        assert row.book_value == pytest.approx(3000.0)


# ── GttPriceRow computed properties ──────────────────────────────────────────

class TestGttPriceRowProperties:
    def _row(self, current=None, trigger=1000.0, target=1200.0):
        return GttPriceRow(
            ticker="GTT", current_price=current, trigger_price=trigger, target_price=target
        )

    def test_distance_above_trigger_positive(self):
        # (1050 - 1000) / 1000 * 100 = 5%
        row = self._row(current=1050.0, trigger=1000.0)
        assert abs(row.distance_to_trigger_pct - 5.0) < 1e-9

    def test_distance_below_trigger_negative(self):
        row = self._row(current=950.0, trigger=1000.0)
        assert row.distance_to_trigger_pct < 0

    def test_distance_none_when_no_current(self):
        assert self._row(current=None).distance_to_trigger_pct is None

    def test_upside_from_trigger(self):
        # (1200 - 1000) / 1000 * 100 = 20%
        row = self._row(trigger=1000.0, target=1200.0)
        assert abs(row.upside_from_trigger_pct - 20.0) < 1e-9


# ── NsePriceFetcher ───────────────────────────────────────────────────────────

class TestNsePriceFetcher:
    def test_fetch_raw_price_success(self):
        mock_ticker = make_mock_ticker(fast_info_price=150.25, fast_info_prev_close=148.00)
        with patch("fetch_nse_prices.yf.Ticker", return_value=mock_ticker):
            fetcher = NsePriceFetcher()
            price, prev = fetcher.fetch_raw_price("DUMMY")
        assert price == pytest.approx(150.25)
        assert prev == pytest.approx(148.00)

    def test_fetch_raw_price_returns_none_on_exception(self):
        with patch("fetch_nse_prices.yf.Ticker", side_effect=Exception("network error")):
            fetcher = NsePriceFetcher()
            price, prev = fetcher.fetch_raw_price("DUMMY")
        assert price is None
        assert prev is None

    def test_fetch_raw_price_none_when_last_price_is_none(self):
        mock_ticker = make_mock_ticker(fast_info_price=None, fast_info_prev_close=100.0)
        with patch("fetch_nse_prices.yf.Ticker", return_value=mock_ticker):
            fetcher = NsePriceFetcher()
            price, prev = fetcher.fetch_raw_price("DUMMY")
        assert price is None

    def test_build_holding_price_rows_count(self):
        mock_ticker = make_mock_ticker(fast_info_price=300.0, fast_info_prev_close=295.0)
        with patch("fetch_nse_prices.yf.Ticker", return_value=mock_ticker):
            rows = NsePriceFetcher().build_holding_price_rows()
        from fetch_nse_prices import HOLDINGS
        assert len(rows) == len(HOLDINGS)

    def test_build_holding_price_rows_maps_cost_correctly(self):
        mock_ticker = make_mock_ticker(fast_info_price=300.0, fast_info_prev_close=295.0)
        from fetch_nse_prices import HOLDINGS
        with patch("fetch_nse_prices.yf.Ticker", return_value=mock_ticker):
            rows = NsePriceFetcher().build_holding_price_rows()
        for row in rows:
            expected_cost = HOLDINGS[row.ticker]["cost"]
            assert row.average_cost == expected_cost

    def test_build_gtt_price_rows_count(self):
        mock_ticker = make_mock_ticker(fast_info_price=1300.0, fast_info_prev_close=1280.0)
        with patch("fetch_nse_prices.yf.Ticker", return_value=mock_ticker):
            rows = NsePriceFetcher().build_gtt_price_rows()
        from fetch_nse_prices import GTT_ORDERS
        assert len(rows) == len(GTT_ORDERS)


# ── LivePriceReport ───────────────────────────────────────────────────────────

class TestLivePriceReport:
    def _make_holding_row(self, ticker="TICK", current=100.0, prev=95.0,
                          cost=90.0, sl=80.0, target=120.0, qty=10):
        return HoldingPriceRow(
            ticker=ticker, current_price=current, previous_close=prev,
            quantity=qty, average_cost=cost, stop_loss_price=sl, target_price=target,
        )

    def _make_gtt_row(self, ticker="GTT", current=1100.0, trigger=1000.0, target=1300.0):
        return GttPriceRow(
            ticker=ticker, current_price=current, trigger_price=trigger, target_price=target
        )

    def test_generate_includes_timestamp(self):
        report = LivePriceReport()
        ts = datetime(2026, 7, 9, 10, 30)
        output = report.generate([self._make_holding_row()], [self._make_gtt_row()], ts)
        assert "2026-07-09 10:30 IST" in output

    def test_generate_includes_section_headers(self):
        report = LivePriceReport()
        output = report.generate([self._make_holding_row()], [self._make_gtt_row()], datetime.now())
        assert "## Active Holdings" in output
        assert "## Pending GTT Orders" in output

    def test_format_holding_row_with_price(self):
        report = LivePriceReport()
        row = self._make_holding_row(ticker="POLYCAB", current=5000.0, prev=4900.0,
                                     cost=4500.0, sl=4200.0, target=6000.0)
        output = report._format_holding_row(row)
        assert "POLYCAB" in output
        assert "5,000.00" in output
        assert "N/A" not in output

    def test_format_holding_row_without_price_shows_na(self):
        report = LivePriceReport()
        row = HoldingPriceRow(
            ticker="MISS", current_price=None, previous_close=None,
            quantity=10, average_cost=100.0, stop_loss_price=90.0, target_price=120.0,
        )
        output = report._format_holding_row(row)
        assert "N/A" in output

    def test_format_holding_row_no_target_shows_tbd(self):
        report = LivePriceReport()
        row = self._make_holding_row(target=None)
        output = report._format_holding_row(row)
        assert "TBD" in output

    def test_portfolio_summary_correct_totals(self):
        report = LivePriceReport()
        rows = [
            self._make_holding_row("A", current=100.0, cost=80.0, qty=10),
            self._make_holding_row("B", current=200.0, cost=150.0, qty=5),
        ]
        output = report._render_portfolio_summary(rows)
        # Market value = 100*10 + 200*5 = 2000; Book = 80*10 + 150*5 = 1550
        assert "2,000.00" in output
        assert "1,550.00" in output

    def test_portfolio_summary_excludes_none_prices(self):
        report = LivePriceReport()
        rows = [
            self._make_holding_row("A", current=100.0, cost=90.0, qty=5),
            HoldingPriceRow("B", None, None, 5, 90.0, 80.0, 120.0),
        ]
        output = report._render_portfolio_summary(rows)
        # Should not raise; B's market value is excluded
        assert "Portfolio" in output

    def test_gtt_row_approaching_flag(self):
        report = LivePriceReport()
        row = GttPriceRow("SYRMA", current_price=1025.0, trigger_price=1000.0, target_price=1300.0)
        output = report._format_gtt_row(row)
        assert "APPROACHING" in output

    def test_gtt_row_at_or_below_trigger_flag(self):
        report = LivePriceReport()
        row = GttPriceRow("SYRMA", current_price=990.0, trigger_price=1000.0, target_price=1300.0)
        output = report._format_gtt_row(row)
        assert "AT/BELOW GTT" in output

    def test_gtt_row_safe_no_flag(self):
        report = LivePriceReport()
        row = GttPriceRow("SYRMA", current_price=1100.0, trigger_price=1000.0, target_price=1300.0)
        output = report._format_gtt_row(row)
        assert "APPROACHING" not in output
        assert "AT/BELOW" not in output

    def test_gtt_row_without_current_price(self):
        report = LivePriceReport()
        row = GttPriceRow("MISS", current_price=None, trigger_price=1275.0, target_price=1600.0)
        output = report._format_gtt_row(row)
        assert "N/A" in output
