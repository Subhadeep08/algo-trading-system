"""Tests for scripts/screen_candidates.py — all 4 gates + position sizer + orchestration."""
from __future__ import annotations

import os
import textwrap
from dataclasses import dataclass
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from conftest import make_ohlcv, make_financial_df, make_quarterly_df, make_mock_ticker
from cockpit.config import (
    ANNUAL_PROFIT_GROWTH_MIN,
    DE_RATIO_MAX,
    EBITDA_CFO_MIN_RATIO,
    MAX_POSITION_PCT,
    PE_STRESS_RETRACEMENT_MAX,
    QUARTERLY_EPS_GROWTH_MIN,
    RISK_PER_TRADE_PCT,
    ROCE_MIN_PCT,
    SL_BELOW_ENTRY_PCT,
    STAGE2_MA_PERIOD,
    UD_DISTRIBUTION_THRESHOLD,
    UD_ENTRY_THRESHOLD,
    UD_LOOKBACK_50,
)
from cockpit.models import ScreeningResult
from cockpit.screener import (
    GTT_TRAILING_STOP_SLABS,
    QUARTERLY_REV_GROWTH_MIN,
    CandidateScreener,
    FundamentalScreener,
    PositionSizer,
    ScreeningRunner,
    Stage2Checker,
    UDRatioCalculator,
    ValuationStressTester,
)


# ── Gate 1: Stage2Checker ─────────────────────────────────────────────────────

class TestStage2Checker:
    """Builds synthetic OHLCV histories and patches yf.Ticker."""

    def _check(self, closes):
        hist = make_ohlcv(len(closes), closes)
        mock = make_mock_ticker(history_1y=hist)
        with patch("cockpit.screener.yf.Ticker", return_value=mock):
            return Stage2Checker().check("DUMMY")

    def test_insufficient_data_fails(self):
        result = self._check(closes=[100.0] * 10)
        assert result.passed is False
        assert "INSUFFICIENT" in result.notes

    def test_stage2_advancing_all_criteria_met(self):
        # Build 200 rows where price trends up so CMP > MA150 and MA slopes up
        closes = [100.0 + i * 0.5 for i in range(200)]
        result = self._check(closes)
        assert result.passed is True
        assert "Advancing" in result.stage_label

    def test_stage3_declining_when_price_below_falling_ma(self):
        # Starts high then crashes — CMP below MA, MA declining
        closes = [200.0 - i * 0.6 for i in range(200)]
        result = self._check(closes)
        assert result.passed is False
        assert "Stage 3" in result.stage_label or "Declining" in result.stage_label

    def test_stage2_warning_when_cmp_above_but_ma_flat(self):
        # Flat for 180 days, then slight spike at end: CMP > MA but slope flat/negative
        closes = [100.0] * 180 + [105.0] * 20
        result = self._check(closes)
        assert result.passed is False
        assert "Warning" in result.stage_label or "Stage 1" in result.stage_label

    def test_stage1_basing_when_cmp_below_flat_ma(self):
        # Price trends down at end but slowly — below flat MA
        closes = [100.0] * 170 + [90.0 - i * 0.1 for i in range(30)]
        result = self._check(closes)
        assert result.passed is False

    def test_returns_cmp_and_ma_values(self):
        closes = [100.0 + i * 0.5 for i in range(200)]
        result = self._check(closes)
        assert result.cmp is not None
        assert result.ma_150 is not None

    def test_52w_low_criterion_fails_when_too_close_to_low(self):
        # CMP = 105, 52W low = 100 → only 5% above, need ≥20%
        closes = [100.0] * 199 + [105.0]
        result = self._check(closes)
        assert result.passed is False

    def test_data_error_returns_failed_result(self):
        with patch("cockpit.screener.yf.Ticker", side_effect=Exception("network error")):
            result = Stage2Checker().check("DUMMY")
        assert result.passed is False
        assert "DATA ERROR" in result.notes


# ── Gate 2: UDRatioCalculator ─────────────────────────────────────────────────

class TestUDRatioCalculator:
    """Builds 3-month OHLCV with explicit open/close to control U/D classification."""

    def _calculate(self, opens, closes, volume=1000.0):
        n = len(closes)
        hist = make_ohlcv(n, closes, opens=opens, volume=volume)
        mock = make_mock_ticker(history_3mo=hist)
        with patch("cockpit.screener.yf.Ticker", return_value=mock):
            return UDRatioCalculator().calculate("DUMMY")

    def _all_up_days(self, n=60):
        opens  = [100.0] * n
        closes = [102.0] * n
        return opens, closes

    def _all_down_days(self, n=60):
        opens  = [102.0] * n
        closes = [100.0] * n
        return opens, closes

    def test_insufficient_data_fails(self):
        opens  = [100.0] * 10
        closes = [102.0] * 10
        result = self._calculate(opens, closes)
        assert result.passed is False
        assert "INSUFFICIENT" in result.notes

    def test_all_up_days_passes(self):
        # Need at least some down days so down_vol > 0; last 5 are down days with tiny vol
        n = 60
        opens  = [100.0] * 55 + [102.0] * 5
        closes = [102.0] * 55 + [100.0] * 5
        # up days get 2000 vol, down days get 1 vol → U/D ≈ 110,000 / 5 >> 1.25
        volumes = [2000.0 if c > o else 1.0 for o, c in zip(opens, closes)]
        hist = make_ohlcv(n, closes, opens=opens)
        hist["Volume"] = volumes
        mock = make_mock_ticker(history_3mo=hist)
        with patch("cockpit.screener.yf.Ticker", return_value=mock):
            result = UDRatioCalculator().calculate("DUMMY")
        assert result.passed is True
        assert result.ud_50 is not None

    def test_all_down_days_disqualifies(self):
        opens, closes = self._all_down_days(60)
        result = self._calculate(opens, closes)
        assert result.disqualified is True
        assert result.passed is False

    def test_ud_50_below_entry_threshold_is_weak(self):
        # Alternate: every other day is a down day with high volume
        # 30 up days × 1 vol + 30 down days × 1.1 vol → U/D ≈ 0.91 (between 0.75 and 1.25)
        n = 60
        opens  = [100.0 if i % 2 == 0 else 102.0 for i in range(n)]
        closes = [102.0 if i % 2 == 0 else 100.0 for i in range(n)]
        volumes = [1000.0 if c > o else 1100.0 for o, c in zip(opens, closes)]

        hist = make_ohlcv(n, closes, opens=opens)
        hist["Volume"] = volumes
        mock = make_mock_ticker(history_3mo=hist)
        with patch("cockpit.screener.yf.Ticker", return_value=mock):
            result = UDRatioCalculator().calculate("DUMMY")
        # U/D_50 = 30*1000 / 30*1100 = 0.909 → between 0.75 and 1.25 → weak (not pass, not disqualified)
        assert not result.passed
        assert not result.disqualified

    def test_distribution_flag_when_ud_21_below_threshold(self):
        # First 39 days: strong up, last 21 days: strong down
        n = 60
        opens  = [100.0] * 39 + [102.0] * 21
        closes = [102.0] * 39 + [100.0] * 21
        up_vol   = [2000.0 if c > o else 0.0 for o, c in zip(opens, closes)]
        down_vol = [0.0    if c > o else 100.0 for o, c in zip(opens, closes)]
        volumes  = [u + d for u, d in zip(up_vol, down_vol)]

        hist = make_ohlcv(n, closes, opens=opens)
        hist["Volume"] = volumes
        mock = make_mock_ticker(history_3mo=hist)
        with patch("cockpit.screener.yf.Ticker", return_value=mock):
            result = UDRatioCalculator().calculate("DUMMY")
        assert result.distribution_flag is True

    def test_data_error_returns_failed_result(self):
        with patch("cockpit.screener.yf.Ticker", side_effect=Exception("timeout")):
            result = UDRatioCalculator().calculate("DUMMY")
        assert result.passed is False
        assert "DATA ERROR" in result.notes


# ── Gate 3 + 4: FundamentalScreener ──────────────────────────────────────────

class TestFundamentalScreener:

    def _make_ticker_with_financials(
        self,
        *,
        ebitda_info=None,
        cfo_val=1_000.0,
        pat_values=(500.0, 400.0, 350.0, 250.0),
        de_ratio=0.3,
        roce_pct=20.0,
        eps_growth=30.0,
        rev_growth=30.0,
    ):
        """Build a mock ticker with sane financial data for full gate pass."""
        equity_val = 10_000.0
        debt_val   = equity_val * de_ratio
        # EBIT for ROCE: ROCE = EBIT / (TA - CL) * 100 → EBIT = roce * capital_emp / 100
        total_assets = 20_000.0
        current_liab = 5_000.0
        capital_emp  = total_assets - current_liab
        ebit_val     = roce_pct / 100 * capital_emp

        financials = make_financial_df(
            **{
                "Net Income":    list(pat_values),
                "EBITDA":        [1_200.0, 1_100.0, 1_000.0, 900.0],
                "EBIT":          [ebit_val, ebit_val * 0.9, ebit_val * 0.8, ebit_val * 0.7],
                "Total Revenue": [5_000.0,  4_500.0,  4_000.0, 3_500.0],
            }
        )
        # Quarterly: 5 columns for YoY comparison (iloc[0] vs iloc[4])
        base_eps = 10.0
        curr_eps = base_eps * (1 + eps_growth / 100)
        base_rev = 1_000.0
        curr_rev = base_rev * (1 + rev_growth / 100)
        quarterly = make_quarterly_df(
            **{
                "Basic EPS":     [curr_eps, base_eps * 1.05, base_eps * 1.02, base_eps * 1.01, base_eps],
                "Total Revenue": [curr_rev,  base_rev * 1.05, base_rev * 1.02, base_rev * 1.01, base_rev],
            }
        )
        cashflow = make_financial_df(
            **{"Operating Cash Flow": [cfo_val, cfo_val * 0.9, cfo_val * 0.8, cfo_val * 0.7]}
        )
        balance_sheet = make_financial_df(
            **{
                "Stockholders Equity": [equity_val, equity_val * 0.9, equity_val * 0.8, equity_val * 0.7],
                "Total Debt":          [debt_val,   debt_val * 0.9,   debt_val * 0.8,   debt_val * 0.7],
                "Total Assets":        [total_assets] * 4,
                "Current Liabilities": [current_liab] * 4,
            }
        )
        return make_mock_ticker(
            financials=financials,
            quarterly_financials=quarterly,
            cashflow=cashflow,
            balance_sheet=balance_sheet,
            info={"ebitda": ebitda_info or 1_200.0},
        )

    def _screen(self, mock_ticker):
        with patch("cockpit.screener.yf.Ticker", return_value=mock_ticker):
            return FundamentalScreener().screen("DUMMY")

    def test_all_gates_pass(self):
        result = self._screen(self._make_ticker_with_financials(
            cfo_val=1_100.0, ebitda_info=1_200.0
        ))
        assert result.passed is True
        assert "All fundamental criteria met" in result.notes

    def test_gate3_fails_low_ebitda_cfo(self):
        # CFO = 500, EBITDA = 1200 → ratio = 0.42 < 0.85
        result = self._screen(self._make_ticker_with_financials(cfo_val=500.0, ebitda_info=1_200.0))
        assert result.passed is False
        assert "EBITDA-CFO" in result.notes

    def test_gate4a_fails_low_pat_cagr(self):
        # PAT barely grows: 250→260 over 3 years ≈ 1.3% CAGR < 20%
        result = self._screen(self._make_ticker_with_financials(
            cfo_val=1_100.0,
            ebitda_info=1_200.0,
            pat_values=(260.0, 255.0, 252.0, 250.0),
        ))
        assert result.passed is False
        assert "PAT CAGR" in result.notes

    def test_gate4b_fails_high_de_ratio(self):
        # D/E = 1.0 > 0.5
        result = self._screen(self._make_ticker_with_financials(
            cfo_val=1_100.0, ebitda_info=1_200.0, de_ratio=1.0
        ))
        assert result.passed is False
        assert "D/E" in result.notes

    def test_gate4b_fails_low_roce(self):
        # ROCE = 10% < 15%
        result = self._screen(self._make_ticker_with_financials(
            cfo_val=1_100.0, ebitda_info=1_200.0, roce_pct=10.0
        ))
        assert result.passed is False
        assert "ROCE" in result.notes

    def test_gate4c_fails_low_eps_growth(self):
        # EPS growth = 5% < 25%
        result = self._screen(self._make_ticker_with_financials(
            cfo_val=1_100.0, ebitda_info=1_200.0, eps_growth=5.0
        ))
        assert result.passed is False
        assert "QtrEPS" in result.notes

    def test_gate4c_fails_low_rev_growth(self):
        # Rev growth = 5% < 25%
        result = self._screen(self._make_ticker_with_financials(
            cfo_val=1_100.0, ebitda_info=1_200.0, rev_growth=5.0
        ))
        assert result.passed is False
        assert "QtrRev" in result.notes

    def test_empty_financials_adds_manual_verify_fields(self):
        mock = make_mock_ticker()   # all DataFrames empty by default
        result = self._screen(mock)
        assert result.passed is True   # manual verify items don't hard-fail
        assert len(result.manual_verify_fields) > 0


# ── PE Stress Test ────────────────────────────────────────────────────────────

class TestValuationStressTester:
    def _test(self, cmp, high_52w, ttm_eps=50.0, fwd_eps=60.0):
        info = {
            "currentPrice": cmp,
            "fiftyTwoWeekHigh": high_52w,
            "trailingEps": ttm_eps,
            "forwardEps": fwd_eps,
        }
        mock = make_mock_ticker(info=info)
        with patch("cockpit.screener.yf.Ticker", return_value=mock):
            return ValuationStressTester().stress_test("DUMMY")

    def test_passes_within_10_pct_of_high(self):
        # CMP = 950, high = 1000 → 5% below → pass
        result = self._test(cmp=950.0, high_52w=1_000.0)
        assert result.passed is True
        assert result.retracement_pct == pytest.approx(0.05)

    def test_fails_beyond_10_pct_of_high(self):
        # CMP = 850, high = 1000 → 15% below → fail
        result = self._test(cmp=850.0, high_52w=1_000.0)
        assert result.passed is False
        assert result.retracement_pct == pytest.approx(0.15)

    def test_exactly_at_10_pct_boundary_passes(self):
        # CMP = 900, high = 1000 → exactly 10% below
        result = self._test(cmp=900.0, high_52w=1_000.0)
        assert result.passed is True

    def test_computes_entry_pe(self):
        result = self._test(cmp=1_000.0, high_52w=1_050.0, ttm_eps=50.0)
        assert result.entry_pe == pytest.approx(20.0)

    def test_computes_forward_pe(self):
        result = self._test(cmp=1_000.0, high_52w=1_050.0, fwd_eps=40.0)
        assert result.forward_pe == pytest.approx(25.0)

    def test_missing_cmp_fails_gracefully(self):
        mock = make_mock_ticker(info={})
        with patch("cockpit.screener.yf.Ticker", return_value=mock):
            result = ValuationStressTester().stress_test("DUMMY")
        assert result.passed is False

    def test_data_error_fails_gracefully(self):
        with patch("cockpit.screener.yf.Ticker", side_effect=Exception("err")):
            result = ValuationStressTester().stress_test("DUMMY")
        assert result.passed is False
        assert "DATA ERROR" in result.notes


# ── PositionSizer ─────────────────────────────────────────────────────────────

class TestPositionSizer:
    """Tests the risk-budgeted position sizing calculation."""

    def test_basic_sizing(self):
        # Risk = 100_000 * 0.02 = 2_000; gap = 1_000 - 900 = 100
        # Raw shares = 2_000 / 100 = 20; position = 20 * 1_000 = 20_000 < 20% (20_000)
        result = PositionSizer().size("T", portfolio_value=100_000, entry_price=1_000, sl_price=900)
        assert result.recommended_shares == 20
        assert result.position_value == pytest.approx(20_000.0)
        assert result.capped is False

    def test_position_capped_at_max_pct(self):
        # Risk = 100_000 * 0.02 = 2_000; gap = 1_000 - 990 = 10
        # Raw shares = 200; raw position = 200_000 > 20_000 (20%) → capped
        result = PositionSizer().size("T", portfolio_value=100_000, entry_price=1_000, sl_price=990)
        assert result.capped is True
        assert result.recommended_shares == 20   # int(20_000 / 1_000)
        assert result.position_value == pytest.approx(20_000.0)

    def test_default_sl_applied_when_none(self):
        # Default SL = entry * (1 - 0.09) = 910
        result = PositionSizer().size("T", portfolio_value=100_000, entry_price=1_000, sl_price=None)
        assert result.sl_price == pytest.approx(910.0)

    def test_invalid_sl_above_entry_returns_zero_shares(self):
        result = PositionSizer().size("T", portfolio_value=100_000, entry_price=1_000, sl_price=1_100)
        assert result.recommended_shares == 0
        assert "SL must be below entry" in result.notes

    def test_sl_equal_to_entry_returns_zero_shares(self):
        result = PositionSizer().size("T", portfolio_value=100_000, entry_price=1_000, sl_price=1_000)
        assert result.recommended_shares == 0

    def test_effective_risk_pct(self):
        result = PositionSizer().size("T", portfolio_value=100_000, entry_price=1_000, sl_price=900)
        # 20 shares × ₹100 gap / ₹100_000 portfolio = 2.0%
        assert result.effective_risk_pct == pytest.approx(2.0)


class TestPositionSizerTrailingStopMin:
    """Tests the GTT trailing-stop slab lookup."""

    @pytest.mark.parametrize("price,expected", [
        (25.0,   0.05),
        (75.0,   0.10),
        (150.0,  0.25),
        (300.0,  0.50),
        (750.0,  1.00),
        (1_500.0, 2.00),
        (5_000.0, 5.00),
    ])
    def test_slab_values(self, price, expected):
        assert PositionSizer.compute_trailing_stop_min(price) == expected

    def test_above_max_slab_returns_last_tick(self):
        # Price beyond last slab hi=10_000 → returns last tick 5.00
        assert PositionSizer.compute_trailing_stop_min(15_000.0) == 5.00


# ── CandidateScreener (orchestration) ────────────────────────────────────────

class TestCandidateScreener:
    """Tests orchestration — all gates run regardless of individual failures."""

    def _mock_gate(self, passed, **kwargs):
        m = MagicMock()
        m.passed = passed
        m.disqualified = kwargs.get("disqualified", False)
        m.distribution_flag = kwargs.get("distribution_flag", False)
        m.manual_verify_fields = kwargs.get("manual_verify_fields", [])
        m.stage_label = kwargs.get("stage_label", "Stage 2 (Advancing)")
        m.notes = kwargs.get("notes", "ok")
        m.cmp = kwargs.get("cmp", 100.0)
        m.overall_rating = kwargs.get("overall_rating", "STRONG")
        return m

    def test_all_gates_run_even_when_gate1_fails(self):
        screener = CandidateScreener()
        g1_fail = self._mock_gate(passed=False, stage_label="Stage 3/4 (Declining)")
        g2_pass = self._mock_gate(passed=True)
        g34_pass = self._mock_gate(passed=True, manual_verify_fields=[])
        pe_pass = self._mock_gate(passed=True)

        with patch.object(screener._stage2, "check", return_value=g1_fail), \
             patch.object(screener._ud, "calculate", return_value=g2_pass), \
             patch.object(screener._funds, "screen", return_value=g34_pass), \
             patch.object(screener._valuation, "stress_test", return_value=pe_pass):
            result = screener.screen("DUMMY")

        assert result.gate1 is g1_fail
        assert result.gate2 is g2_pass
        assert result.gate3_gate4 is g34_pass
        assert result.pe_stress is pe_pass
        assert result.passed_all is False

    def test_passed_all_true_when_all_gates_pass(self):
        screener = CandidateScreener()
        g1_pass  = self._mock_gate(passed=True)
        g2_pass  = self._mock_gate(passed=True)
        g34_pass = self._mock_gate(passed=True, manual_verify_fields=[])
        pe_pass  = self._mock_gate(passed=True)
        overlay  = MagicMock(overall_rating="STRONG")

        with patch.object(screener._stage2, "check", return_value=g1_pass), \
             patch.object(screener._ud, "calculate", return_value=g2_pass), \
             patch.object(screener._funds, "screen", return_value=g34_pass), \
             patch.object(screener._valuation, "stress_test", return_value=pe_pass), \
             patch.object(screener._secondary, "run", return_value=overlay):
            result = screener.screen("DUMMY")

        assert result.passed_all is True
        assert "ALL-CLEAR" in result.recommendation

    def test_secondary_overlay_not_run_when_gates_fail(self):
        screener = CandidateScreener()
        g1_fail = self._mock_gate(passed=False, stage_label="Stage 3/4 (Declining)")
        g2_pass = self._mock_gate(passed=True)
        g34_pass = self._mock_gate(passed=True, manual_verify_fields=[])
        pe_pass = self._mock_gate(passed=True)

        with patch.object(screener._stage2, "check", return_value=g1_fail), \
             patch.object(screener._ud, "calculate", return_value=g2_pass), \
             patch.object(screener._funds, "screen", return_value=g34_pass), \
             patch.object(screener._valuation, "stress_test", return_value=pe_pass):
            with patch.object(screener._secondary, "run") as mock_secondary:
                result = screener.screen("DUMMY")
                mock_secondary.assert_not_called()

    def test_multiple_gate_failures_all_in_recommendation(self):
        screener = CandidateScreener()
        g1_fail  = self._mock_gate(passed=False, stage_label="Stage 3/4 (Declining)", notes="below MA")
        g2_fail  = self._mock_gate(passed=False, disqualified=True, notes="U/D_50=0.5")
        g34_fail = self._mock_gate(passed=False, notes="PAT CAGR low")
        pe_fail  = self._mock_gate(passed=False, notes="25% below 52W high")

        with patch.object(screener._stage2, "check", return_value=g1_fail), \
             patch.object(screener._ud, "calculate", return_value=g2_fail), \
             patch.object(screener._funds, "screen", return_value=g34_fail), \
             patch.object(screener._valuation, "stress_test", return_value=pe_fail):
            result = screener.screen("DUMMY")

        assert "Gate 1" in result.recommendation
        assert "Gate 2" in result.recommendation
        assert "Gate 3/4" in result.recommendation
        assert "PE Test" in result.recommendation


# ── ScreeningRunner._load_watchlist ───────────────────────────────────────────

class TestLoadWatchlist:
    """Tests _load_watchlist() section restriction and em-dash exclusion."""

    WATCHLIST_CONTENT = textwrap.dedent("""\
        # PMS Watchlist

        ## Candidate Tickers
        | Ticker | Sector | Added Date | Notes |
        |--------|--------|------------|-------|
        | DIXON  | Electronics | 2026-07-01 | Strong |
        | POLYCAB | Wires | 2026-07-01 | Good |

        ## Latest Gate Scores
        | Ticker | Gate 1 (Stage 2) | Status |
        |--------|-----------------|--------|
        | DIXON | ✅ | Pass |

        ## Ready to Acquire (All Gates Passed)
        | Ticker | Suggested GTT Entry |
        |--------|---------------------|
        | —      | —                   |
    """)

    def test_parses_only_candidate_tickers_section(self, tmp_path):
        wl = tmp_path / "watchlist.md"
        wl.write_text(self.WATCHLIST_CONTENT)
        runner = ScreeningRunner.__new__(ScreeningRunner)
        with patch("cockpit.screener.WATCHLIST_PATH", str(wl)):
            tickers = runner._load_watchlist()
        assert "DIXON" in tickers
        assert "POLYCAB" in tickers
        assert len(tickers) == 2

    def test_excludes_em_dash_placeholder_rows(self, tmp_path):
        wl = tmp_path / "watchlist.md"
        wl.write_text(self.WATCHLIST_CONTENT)
        runner = ScreeningRunner.__new__(ScreeningRunner)
        with patch("cockpit.screener.WATCHLIST_PATH", str(wl)):
            tickers = runner._load_watchlist()
        assert "—" not in tickers

    def test_does_not_pick_up_tickers_from_other_sections(self, tmp_path):
        wl = tmp_path / "watchlist.md"
        wl.write_text(self.WATCHLIST_CONTENT)
        runner = ScreeningRunner.__new__(ScreeningRunner)
        with patch("cockpit.screener.WATCHLIST_PATH", str(wl)):
            tickers = runner._load_watchlist()
        # DIXON appears in "Latest Gate Scores" too — should only appear once
        assert tickers.count("DIXON") == 1

    def test_empty_candidate_section_returns_empty_list(self, tmp_path):
        content = "# PMS Watchlist\n\n## Candidate Tickers\n| Ticker | Sector |\n|--------|--------|\n"
        wl = tmp_path / "watchlist.md"
        wl.write_text(content)
        runner = ScreeningRunner.__new__(ScreeningRunner)
        with patch("cockpit.screener.WATCHLIST_PATH", str(wl)):
            tickers = runner._load_watchlist()
        assert tickers == []

    def test_missing_file_returns_empty_list(self, tmp_path):
        runner = ScreeningRunner.__new__(ScreeningRunner)
        with patch("cockpit.screener.WATCHLIST_PATH", str(tmp_path / "nonexistent.md")):
            tickers = runner._load_watchlist()
        assert tickers == []
