"""
PMS quantitative screening pipeline for NSE stocks.

Runs 4 sequential hard gates + PE stress test:
  Gate 1  — Stan Weinstein Stage 2 (150-day MA trend)
  Gate 2  — Up/Down Volume Ratio (institutional accumulation vs distribution)
  Gate 3  — EBITDA-to-CFO cash conversion quality (≥ 0.85)
  Gate 4  — CANSLIM fundamentals (EPS growth, PAT CAGR, ROCE, D/E)
  PE Test — 52-week retracement ≤ 10% (confirms active markup phase)

After all gates pass, runs the 24-parameter secondary valuation overlay
across 6 categories: Valuation, Margin Consistency, Cash Flow Quality,
Asset Allocation, Compound Growth, and Technical Retracement.

Used standalone (GitHub Actions) and imported by cockpit_runner.py for holding re-qualification.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass, field
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

# ── Gate thresholds — single source of truth ─────────────────────────────────
UD_ENTRY_THRESHOLD          = 1.25   # Gate 2: U/D_50 pass threshold
UD_DISTRIBUTION_THRESHOLD   = 0.75   # Gate 2: U/D_50 disqualify / U/D_21 flag
UD_LOOKBACK_50              = 50     # sessions for long-term U/D ratio
UD_LOOKBACK_21              = 21     # sessions for near-term U/D ratio
STAGE2_MA_PERIOD            = 150    # days (≈ 30-week Weinstein MA)
STAGE2_MA_SLOPE_LOOKBACK    = 20     # sessions to measure MA slope direction
STAGE2_CMP_ABOVE_52W_LOW    = 1.20   # CMP must be ≥ 20% above 52W low
EBITDA_CFO_MIN_RATIO        = 0.85   # Gate 3: minimum cash conversion ratio
QUARTERLY_EPS_GROWTH_MIN    = 25.0   # Gate 4-C: min QoQ EPS growth %
QUARTERLY_REV_GROWTH_MIN    = 25.0   # Gate 4-C: min QoQ revenue growth %
ANNUAL_PROFIT_GROWTH_MIN    = 20.0   # Gate 4-A: min 3-year PAT CAGR %
ROCE_MIN_PCT                = 15.0   # Gate 4: min ROCE %
DE_RATIO_MAX                = 0.5    # Gate 4: max Debt/Equity ratio
PE_STRESS_RETRACEMENT_MAX   = 0.10   # PE test: CMP must be within 10% of 52W high
RISK_PER_TRADE_PCT          = 0.02   # 2% of portfolio capital at risk per trade
MAX_POSITION_PCT            = 0.20   # 20% cap per holding
SL_BELOW_ENTRY_PCT          = 0.09   # 9% default SL below entry price

# ── 24-Parameter Secondary Overlay Thresholds ────────────────────────────────
# Category 1: Valuation & Pricing
TRAILING_PE_MAX             = 45.0   # <45x (unless EPS growth >40%)
FORWARD_PE_MAX              = 30.0   # <30x
EV_EBITDA_MAX               = 25.0   # <25x
PB_RATIO_MAX                = 6.0    # <6x
PEG_RATIO_MAX               = 1.2    # ≤1.2

# Category 2: Margin Consistency
GROSS_MARGIN_VAR_MAX_BPS    = 150    # <±150 bps std over 12 quarters
NET_MARGIN_MIN_PCT          = 12.0   # >12%
OTHER_INCOME_PBT_MAX        = 0.10   # ≤10%

# Category 3: Cash Flow Quality
FCF_SALES_MIN_PCT           = 8.0    # ≥8%
FCF_YIELD_MIN_PCT           = 3.0    # ≥3%
CFO_NET_PROFIT_MIN          = 1.0    # ≥1.0x (3-year rolling)
DIVIDEND_PAYOUT_MIN         = 0.15   # ≥15% (mature cash generators)
DIVIDEND_PAYOUT_MAX         = 0.45   # ≤45% (mature cash generators)

# Category 4: Asset Allocation
ROIC_MIN_PCT                = 18.0   # ≥18%
ROIC_WACC_SPREAD_MIN_BPS    = 600    # ≥600 bps (manual verify — needs WACC)
REINVESTMENT_RATE_MIN_PCT   = 50.0   # ≥50% (high-growth names; manual verify)
ASSET_TURNOVER_MIN          = 1.5    # ≥1.5x

# Category 5: Compound Growth
REVENUE_CAGR_3Y_MIN_PCT     = 18.0   # ≥18%
REVENUE_CAGR_5Y_MIN_PCT     = 15.0   # ≥15%
EBITDA_CAGR_3Y_MIN_PCT      = 22.0   # ≥22%

# Category 6: Technical Retracement
DIST_52W_HIGH_MAX           = 0.10   # ≤10% below 52W high (same as PE_STRESS_RETRACEMENT_MAX)
BREAKOUT_VOLUME_MULT        = 1.5    # ≥1.5x 20-day avg vol at ATH breakout

# ── GTT Trailing Stop Slab Table (2026 Groww execution guidelines) ────────────
GTT_TRAILING_STOP_SLABS: list[tuple[float, float, float]] = [
    (0,      50,     0.05),
    (50,     100,    0.10),
    (100,    250,    0.25),
    (250,    500,    0.50),
    (500,    1_000,  1.00),
    (1_000,  2_500,  2.00),
    (2_500,  10_000, 5.00),
]

NSE_SUFFIX                  = ".NS"
NIFTY_SYMBOL                = "^NSEI"
SCREENING_RESULTS_PATH      = ".claude/skills/portfolio-cockpit/references/screening-results.md"
WATCHLIST_PATH              = ".claude/skills/portfolio-cockpit/references/watchlist.md"


# ── Result data-classes ───────────────────────────────────────────────────────

@dataclass
class Stage2Result:
    ticker: str
    passed: bool
    cmp: Optional[float] = None
    ma_150: Optional[float] = None
    ma_150_20d_ago: Optional[float] = None
    low_52w: Optional[float] = None
    stage_label: str = ""
    notes: str = ""


@dataclass
class UDRatioResult:
    ticker: str
    passed: bool
    ud_50: Optional[float] = None
    ud_21: Optional[float] = None
    distribution_flag: bool = False
    disqualified: bool = False
    notes: str = ""


@dataclass
class FundamentalResult:
    ticker: str
    passed: bool
    quarterly_eps_growth_pct: Optional[float] = None
    quarterly_rev_growth_pct: Optional[float] = None
    annual_pat_cagr_pct: Optional[float] = None
    roce_pct: Optional[float] = None
    de_ratio: Optional[float] = None
    ebitda_cfo_ratio: Optional[float] = None
    manual_verify_fields: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class PEStressResult:
    ticker: str
    passed: bool
    entry_pe: Optional[float] = None
    forward_pe: Optional[float] = None
    cmp: Optional[float] = None
    high_52w: Optional[float] = None
    retracement_pct: Optional[float] = None
    notes: str = ""


@dataclass
class PositionSizeResult:
    ticker: str
    entry_price: float
    sl_price: float
    recommended_shares: int
    position_value: float
    effective_risk_pct: float
    capped: bool
    notes: str = ""


@dataclass
class OverlayParamResult:
    name: str
    category: str
    value: Optional[float]
    threshold: str
    status: str   # "PASS" | "FAIL" | "MANUAL_VERIFY" | "N/A"
    notes: str = ""


@dataclass
class SecondaryOverlayResult:
    ticker: str
    params: list = field(default_factory=list)   # list[OverlayParamResult]
    pass_count: int = 0
    fail_count: int = 0
    manual_count: int = 0
    na_count: int = 0
    overall_rating: str = ""   # "STRONG" | "ADEQUATE" | "WEAK"


@dataclass
class ScreeningResult:
    ticker: str
    gate1: Optional[Stage2Result] = None
    gate2: Optional[UDRatioResult] = None
    gate3_gate4: Optional[FundamentalResult] = None
    pe_stress: Optional[PEStressResult] = None
    secondary: Optional[SecondaryOverlayResult] = None
    passed_all: bool = False
    first_failed_gate: str = ""
    recommendation: str = ""


# ── Gate 1: Stan Weinstein Stage 2 ───────────────────────────────────────────

class Stage2Checker:
    """
    Pass criteria:
      (a) CMP > 150-day MA
      (b) 150-day MA today > 150-day MA 20 sessions ago (upslope)
      (c) CMP > 52-week low × 1.20
    """

    def check(self, ticker: str) -> Stage2Result:
        ns_ticker = ticker + NSE_SUFFIX
        try:
            hist = yf.Ticker(ns_ticker).history(period="1y")
        except Exception as exc:
            return Stage2Result(ticker=ticker, passed=False, notes=f"DATA ERROR: {exc}")

        if hist.empty or len(hist) < STAGE2_MA_PERIOD:
            return Stage2Result(
                ticker=ticker, passed=False,
                notes=f"INSUFFICIENT DATA: {len(hist)} rows (need {STAGE2_MA_PERIOD})"
            )

        close = hist["Close"]
        cmp = float(close.iloc[-1])
        ma = close.rolling(STAGE2_MA_PERIOD).mean()
        ma_now = float(ma.iloc[-1])

        if len(ma.dropna()) < STAGE2_MA_SLOPE_LOOKBACK + 1:
            return Stage2Result(ticker=ticker, passed=False, cmp=cmp, ma_150=ma_now,
                                notes="Insufficient MA history for slope check")

        ma_20d_ago = float(ma.iloc[-(STAGE2_MA_SLOPE_LOOKBACK + 1)])
        low_52w = float(close.min())

        crit_a = cmp > ma_now
        crit_b = ma_now > ma_20d_ago
        crit_c = cmp > low_52w * STAGE2_CMP_ABOVE_52W_LOW

        passed = crit_a and crit_b and crit_c

        if passed:
            stage_label = "Stage 2 (Advancing)"
        elif crit_a and not crit_b:
            stage_label = "Stage 2 Warning (MA flattening)"
        elif not crit_a and not crit_b:
            stage_label = "Stage 3/4 (Declining)"
        else:
            stage_label = "Stage 1 (Basing)"

        notes_parts = []
        if not crit_a:
            notes_parts.append(f"CMP ₹{cmp:.1f} < MA150 ₹{ma_now:.1f}")
        if not crit_b:
            notes_parts.append(f"MA slope flat/down ({ma_now:.1f} vs {ma_20d_ago:.1f})")
        if not crit_c:
            notes_parts.append(f"CMP only {(cmp / low_52w - 1) * 100:.1f}% above 52W low")

        return Stage2Result(
            ticker=ticker,
            passed=passed,
            cmp=cmp,
            ma_150=ma_now,
            ma_150_20d_ago=ma_20d_ago,
            low_52w=low_52w,
            stage_label=stage_label,
            notes="; ".join(notes_parts) if notes_parts else "All criteria met",
        )


# ── Gate 2: Up/Down Volume Ratio ──────────────────────────────────────────────

class UDRatioCalculator:
    """
    Up/Down Volume Ratio over 50-day and 21-day rolling windows.
    Up day = close > open; Down day = close < open (doji days excluded).
    """

    def calculate(self, ticker: str) -> UDRatioResult:
        ns_ticker = ticker + NSE_SUFFIX
        try:
            hist = yf.Ticker(ns_ticker).history(period="3mo")
        except Exception as exc:
            return UDRatioResult(ticker=ticker, passed=False, notes=f"DATA ERROR: {exc}")

        needed = UD_LOOKBACK_50 + 5
        if hist.empty or len(hist) < needed:
            return UDRatioResult(
                ticker=ticker, passed=False,
                notes=f"INSUFFICIENT DATA: {len(hist)} rows (need {needed})"
            )

        df = hist.copy()
        df["up_day"]   = df["Close"] > df["Open"]
        df["down_day"] = df["Close"] < df["Open"]

        def _ud_ratio(window: int) -> Optional[float]:
            subset = df.iloc[-window:]
            up_vol   = subset.loc[subset["up_day"],   "Volume"].sum()
            down_vol = subset.loc[subset["down_day"], "Volume"].sum()
            if down_vol == 0:
                return None
            return float(up_vol / down_vol)

        ud_50 = _ud_ratio(UD_LOOKBACK_50)
        ud_21 = _ud_ratio(UD_LOOKBACK_21)

        if ud_50 is None:
            return UDRatioResult(ticker=ticker, passed=False, notes="No down-day volume data")

        disqualified     = ud_50 < UD_DISTRIBUTION_THRESHOLD
        distribution_flag = (ud_21 is not None) and (ud_21 < UD_DISTRIBUTION_THRESHOLD)
        passed = ud_50 >= UD_ENTRY_THRESHOLD and not disqualified

        notes_parts = []
        if disqualified:
            notes_parts.append(f"DISQUALIFY: U/D_50={ud_50:.2f} < {UD_DISTRIBUTION_THRESHOLD}")
        elif not passed:
            notes_parts.append(f"WEAK: U/D_50={ud_50:.2f} (need ≥{UD_ENTRY_THRESHOLD})")
        if distribution_flag:
            notes_parts.append(f"DISTRIBUTION: U/D_21={ud_21:.2f} < {UD_DISTRIBUTION_THRESHOLD}")

        return UDRatioResult(
            ticker=ticker,
            passed=passed,
            ud_50=ud_50,
            ud_21=ud_21,
            distribution_flag=distribution_flag,
            disqualified=disqualified,
            notes="; ".join(notes_parts) if notes_parts else "Accumulation confirmed",
        )


# ── Gate 3 + 4: Fundamental Screener ─────────────────────────────────────────

class FundamentalScreener:
    """
    Gate 3: EBITDA-to-CFO ≥ 0.85 (cash conversion quality)
    Gate 4: CANSLIM-derived fundamentals
      4-A: 3-year annual net profit CAGR ≥ 20%
      4-B: D/E ≤ 0.5, ROCE ≥ 15%
      4-C: Current quarter EPS/revenue growth ≥ 25% YoY
    """

    def screen(self, ticker: str) -> FundamentalResult:
        ns_ticker = ticker + NSE_SUFFIX
        t = yf.Ticker(ns_ticker)
        manual_verify: list[str] = []
        fail_reasons: list[str] = []

        # ── Gate 3: EBITDA-to-CFO ────────────────────────────────────────────
        ebitda_cfo_ratio = self._compute_ebitda_cfo(t, manual_verify)

        gate3_pass = True
        if ebitda_cfo_ratio is None:
            manual_verify.append("EBITDA-CFO ratio")
        elif ebitda_cfo_ratio < EBITDA_CFO_MIN_RATIO:
            gate3_pass = False
            fail_reasons.append(f"EBITDA-CFO={ebitda_cfo_ratio:.2f} < {EBITDA_CFO_MIN_RATIO}")

        # ── Gate 4-A: Annual PAT CAGR ────────────────────────────────────────
        annual_cagr = self._compute_annual_pat_cagr(t, manual_verify)
        gate4a_pass = True
        if annual_cagr is None:
            manual_verify.append("Annual PAT CAGR")
        elif annual_cagr < ANNUAL_PROFIT_GROWTH_MIN:
            gate4a_pass = False
            fail_reasons.append(f"PAT CAGR={annual_cagr:.1f}% < {ANNUAL_PROFIT_GROWTH_MIN}%")

        # ── Gate 4-B: Balance sheet ratios ───────────────────────────────────
        de_ratio, roce_pct = self._compute_balance_sheet_ratios(t, manual_verify)
        gate4b_pass = True
        if de_ratio is None:
            manual_verify.append("D/E ratio")
        elif de_ratio > DE_RATIO_MAX:
            gate4b_pass = False
            fail_reasons.append(f"D/E={de_ratio:.2f} > {DE_RATIO_MAX}")
        if roce_pct is None:
            manual_verify.append("ROCE")
        elif roce_pct < ROCE_MIN_PCT:
            gate4b_pass = False
            fail_reasons.append(f"ROCE={roce_pct:.1f}% < {ROCE_MIN_PCT}%")

        # ── Gate 4-C: Quarterly growth ───────────────────────────────────────
        eps_growth, rev_growth = self._compute_quarterly_growth(t, manual_verify)
        gate4c_pass = True
        if eps_growth is None:
            manual_verify.append("Quarterly EPS growth")
        elif eps_growth < QUARTERLY_EPS_GROWTH_MIN:
            gate4c_pass = False
            fail_reasons.append(f"QtrEPS={eps_growth:.1f}% < {QUARTERLY_EPS_GROWTH_MIN}%")
        if rev_growth is None:
            manual_verify.append("Quarterly revenue growth")
        elif rev_growth < QUARTERLY_REV_GROWTH_MIN:
            gate4c_pass = False
            fail_reasons.append(f"QtrRev={rev_growth:.1f}% < {QUARTERLY_REV_GROWTH_MIN}%")

        passed = gate3_pass and gate4a_pass and gate4b_pass and gate4c_pass
        return FundamentalResult(
            ticker=ticker,
            passed=passed,
            quarterly_eps_growth_pct=eps_growth,
            quarterly_rev_growth_pct=rev_growth,
            annual_pat_cagr_pct=annual_cagr,
            roce_pct=roce_pct,
            de_ratio=de_ratio,
            ebitda_cfo_ratio=ebitda_cfo_ratio,
            manual_verify_fields=manual_verify,
            notes="; ".join(fail_reasons) if fail_reasons else "All fundamental criteria met",
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    def _compute_ebitda_cfo(self, t: yf.Ticker, manual_verify: list[str]) -> Optional[float]:
        try:
            cf = t.cashflow
            if cf is None or cf.empty:
                return None
            cfo = self._get_row(cf, ["Operating Cash Flow", "Total Cash From Operating Activities"])
            if cfo is None:
                return None
            cfo_val = float(cfo.iloc[0])

            ebitda_val = None
            info_ebitda = t.info.get("ebitda")
            if info_ebitda and info_ebitda > 0:
                ebitda_val = float(info_ebitda)
            else:
                fin = t.financials
                if fin is not None and not fin.empty:
                    ebitda_row = self._get_row(fin, ["EBITDA", "Normalized EBITDA"])
                    if ebitda_row is not None:
                        ebitda_val = float(ebitda_row.iloc[0])

            if ebitda_val is None or ebitda_val <= 0:
                return None
            return cfo_val / ebitda_val
        except Exception:
            return None

    def _compute_annual_pat_cagr(self, t: yf.Ticker, manual_verify: list[str]) -> Optional[float]:
        try:
            fin = t.financials
            if fin is None or fin.empty:
                return None
            pat_row = self._get_row(fin, ["Net Income", "Net Income Common Stockholders"])
            if pat_row is None or len(pat_row) < 4:
                return None
            pat_values = pat_row.dropna()
            if len(pat_values) < 4:
                return None
            pat_latest = float(pat_values.iloc[0])
            pat_3y_ago = float(pat_values.iloc[3])
            if pat_3y_ago <= 0:
                return None
            cagr = ((pat_latest / pat_3y_ago) ** (1 / 3) - 1) * 100
            return cagr
        except Exception:
            return None

    def _compute_balance_sheet_ratios(
        self, t: yf.Ticker, manual_verify: list[str]
    ) -> tuple[Optional[float], Optional[float]]:
        de_ratio = None
        roce_pct = None
        try:
            bs = t.balance_sheet
            if bs is None or bs.empty:
                return None, None

            equity = self._get_row(bs, [
                "Stockholders Equity", "Total Stockholders Equity",
                "Common Stock Equity", "Total Equity Gross Minority Interest",
            ])
            debt = self._get_row(bs, [
                "Total Debt", "Long Term Debt And Capital Lease Obligation",
                "Total Long Term Debt",
            ])
            total_assets = self._get_row(bs, ["Total Assets"])
            current_liab = self._get_row(bs, ["Current Liabilities", "Total Current Liabilities"])

            if equity is not None and debt is not None:
                equity_val = float(equity.iloc[0])
                debt_val   = float(debt.iloc[0])
                if equity_val > 0:
                    de_ratio = debt_val / equity_val

            if total_assets is not None and current_liab is not None:
                fin = t.financials
                if fin is not None and not fin.empty:
                    ebit_row = self._get_row(fin, ["EBIT", "Operating Income"])
                    if ebit_row is not None:
                        ebit_val     = float(ebit_row.iloc[0])
                        ta_val       = float(total_assets.iloc[0])
                        cl_val       = float(current_liab.iloc[0])
                        capital_emp  = ta_val - cl_val
                        if capital_emp > 0:
                            roce_pct = (ebit_val / capital_emp) * 100
        except Exception:
            pass
        return de_ratio, roce_pct

    def _compute_quarterly_growth(
        self, t: yf.Ticker, manual_verify: list[str]
    ) -> tuple[Optional[float], Optional[float]]:
        eps_growth = None
        rev_growth = None
        try:
            qfin = t.quarterly_financials
            if qfin is None or qfin.empty or qfin.shape[1] < 5:
                return None, None

            eps_row = self._get_row(qfin, ["Basic EPS", "Diluted EPS", "Net Income"])
            rev_row = self._get_row(qfin, [
                "Total Revenue", "Revenue", "Operating Revenue",
            ])

            if eps_row is not None and len(eps_row.dropna()) >= 5:
                eps_vals = eps_row.dropna()
                curr_eps = float(eps_vals.iloc[0])
                prev_eps = float(eps_vals.iloc[4])
                if prev_eps != 0:
                    eps_growth = ((curr_eps - prev_eps) / abs(prev_eps)) * 100

            if rev_row is not None and len(rev_row.dropna()) >= 5:
                rev_vals = rev_row.dropna()
                curr_rev = float(rev_vals.iloc[0])
                prev_rev = float(rev_vals.iloc[4])
                if prev_rev != 0:
                    rev_growth = ((curr_rev - prev_rev) / abs(prev_rev)) * 100
        except Exception:
            pass
        return eps_growth, rev_growth

    @staticmethod
    def _get_row(df, keys: list[str]):
        for key in keys:
            if key in df.index:
                return df.loc[key]
        return None


# ── Forward PE Stress Test ────────────────────────────────────────────────────

class ValuationStressTester:
    """
    Checks:
      (a) Entry PE and Forward PE (informational)
      (b) 52-week retracement: CMP ≥ 52W high × (1 - PE_STRESS_RETRACEMENT_MAX)
    Pass = within 10% of 52-week high (confirms active markup phase; no trapped overhead sellers).
    Full PE compression stress test (target PE vs 5-year median) flagged for manual verify.
    """

    def stress_test(self, ticker: str) -> PEStressResult:
        ns_ticker = ticker + NSE_SUFFIX
        try:
            info = yf.Ticker(ns_ticker).info
        except Exception as exc:
            return PEStressResult(ticker=ticker, passed=False, notes=f"DATA ERROR: {exc}")

        cmp        = info.get("currentPrice") or info.get("regularMarketPrice")
        high_52w   = info.get("fiftyTwoWeekHigh")
        ttm_eps    = info.get("trailingEps")
        fwd_eps    = info.get("forwardEps")

        if cmp is None or high_52w is None:
            return PEStressResult(ticker=ticker, passed=False,
                                  notes="CMP or 52W high unavailable")

        retracement_pct = (high_52w - cmp) / high_52w
        passed = retracement_pct <= PE_STRESS_RETRACEMENT_MAX

        entry_pe  = (cmp / ttm_eps) if ttm_eps and ttm_eps > 0 else None
        forward_pe = (cmp / fwd_eps) if fwd_eps and fwd_eps > 0 else None

        notes = (
            f"CMP ₹{cmp:.1f} is {retracement_pct * 100:.1f}% below 52W high ₹{high_52w:.1f}"
            + (f"; Entry PE={entry_pe:.1f}" if entry_pe else "; TTM EPS unavailable")
            + (f"; Fwd PE={forward_pe:.1f}" if forward_pe else "")
        )

        return PEStressResult(
            ticker=ticker,
            passed=passed,
            entry_pe=entry_pe,
            forward_pe=forward_pe,
            cmp=cmp,
            high_52w=high_52w,
            retracement_pct=retracement_pct,
            notes=notes,
        )


# ── Position Sizer ────────────────────────────────────────────────────────────

class PositionSizer:
    """
    Risk-budgeted position sizing:
      Risk amount   = portfolio_value × RISK_PER_TRADE_PCT
      Raw shares    = risk_amount / (entry_price − sl_price)
      Position val  = shares × entry_price
      Cap at        = portfolio_value × MAX_POSITION_PCT
    """

    def size(
        self,
        ticker: str,
        portfolio_value: float,
        entry_price: float,
        sl_price: Optional[float] = None,
    ) -> PositionSizeResult:
        if sl_price is None:
            sl_price = entry_price * (1 - SL_BELOW_ENTRY_PCT)

        risk_amount  = portfolio_value * RISK_PER_TRADE_PCT
        gap          = entry_price - sl_price
        if gap <= 0:
            return PositionSizeResult(
                ticker=ticker, entry_price=entry_price, sl_price=sl_price,
                recommended_shares=0, position_value=0, effective_risk_pct=0,
                capped=False, notes="SL must be below entry price",
            )

        raw_shares      = risk_amount / gap
        raw_position    = raw_shares * entry_price
        max_position    = portfolio_value * MAX_POSITION_PCT
        capped          = raw_position > max_position
        final_shares    = int(max_position / entry_price) if capped else int(raw_shares)
        position_value  = final_shares * entry_price
        eff_risk_pct    = (final_shares * gap / portfolio_value) * 100

        notes = (
            f"Risk ₹{risk_amount:.0f} / gap ₹{gap:.1f} = {raw_shares:.0f} raw shares"
            + (f"; CAPPED at {MAX_POSITION_PCT * 100:.0f}% (₹{max_position:.0f})" if capped else "")
        )
        return PositionSizeResult(
            ticker=ticker,
            entry_price=entry_price,
            sl_price=sl_price,
            recommended_shares=final_shares,
            position_value=position_value,
            effective_risk_pct=eff_risk_pct,
            capped=capped,
            notes=notes,
        )

    @staticmethod
    def compute_trailing_stop_min(price: float) -> float:
        """Return the minimum trailing-stop increment (₹) for a given price slab."""
        for lo, hi, tick in GTT_TRAILING_STOP_SLABS:
            if lo <= price < hi:
                return tick
        return GTT_TRAILING_STOP_SLABS[-1][2]


# ── 24-Parameter Secondary Valuation Overlay ─────────────────────────────────

class SecondaryOverlayScreener:
    """
    Evaluates all 24 institutional parameters across 6 categories.
    Advisory only — does not hard-fail the pipeline.
    Flags items as PASS / FAIL / MANUAL_VERIFY / N/A.
    """

    def run(self, ticker: str) -> SecondaryOverlayResult:
        ns_ticker = ticker + NSE_SUFFIX
        t = yf.Ticker(ns_ticker)
        try:
            info = t.info
        except Exception:
            info = {}

        params: list[OverlayParamResult] = []

        # ── Category 1: Valuation & Pricing ──────────────────────────────────
        params.append(self._check_scalar(
            info.get("trailingPE"), "Trailing P/E", "Valuation",
            f"< {TRAILING_PE_MAX:.0f}x (unless EPS growth >40%)",
            lambda v: v < TRAILING_PE_MAX,
        ))
        params.append(self._check_scalar(
            info.get("forwardPE"), "Forward P/E", "Valuation",
            f"< {FORWARD_PE_MAX:.0f}x",
            lambda v: v < FORWARD_PE_MAX,
        ))
        params.append(self._check_scalar(
            info.get("enterpriseToEbitda"), "EV/EBITDA", "Valuation",
            f"< {EV_EBITDA_MAX:.0f}x",
            lambda v: v < EV_EBITDA_MAX,
        ))
        params.append(self._check_scalar(
            info.get("priceToBook"), "P/B Ratio", "Valuation",
            f"< {PB_RATIO_MAX:.0f}x",
            lambda v: v < PB_RATIO_MAX,
        ))
        params.append(self._check_scalar(
            info.get("pegRatio"), "PEG Ratio", "Valuation",
            f"≤ {PEG_RATIO_MAX}",
            lambda v: v <= PEG_RATIO_MAX,
        ))

        # ── Category 2: Margin Consistency ───────────────────────────────────
        gm_var = self._compute_gross_margin_variance(t)
        params.append(self._check_scalar(
            gm_var, "Gross Margin Variance (12Q)", "Margins",
            f"< ±{GROSS_MARGIN_VAR_MAX_BPS} bps std over 12 quarters",
            lambda v: v < GROSS_MARGIN_VAR_MAX_BPS,
        ))
        opm_rising = self._compute_opm_trend(t)
        params.append(OverlayParamResult(
            name="OPM Trend (3 FY)", category="Margins",
            value=None,
            threshold="Rising over 3 fiscal years",
            status="PASS" if opm_rising is True else ("FAIL" if opm_rising is False else "MANUAL_VERIFY"),
            notes="OPM slope positive" if opm_rising else ("OPM declining" if opm_rising is False else "Insufficient data"),
        ))
        npm = (info.get("profitMargins") or 0) * 100
        params.append(self._check_scalar(
            npm if npm > 0 else None, "Net Margin", "Margins",
            f"> {NET_MARGIN_MIN_PCT:.0f}%",
            lambda v: v > NET_MARGIN_MIN_PCT,
        ))
        other_pbt = self._compute_other_income_pbt(t)
        params.append(self._check_scalar(
            other_pbt, "Other Income / PBT", "Margins",
            f"≤ {OTHER_INCOME_PBT_MAX * 100:.0f}%",
            lambda v: v <= OTHER_INCOME_PBT_MAX,
        ))

        # ── Category 3: Cash Flow Quality ────────────────────────────────────
        fcf_sales = self._compute_fcf_sales(t, info)
        params.append(self._check_scalar(
            fcf_sales, "FCF/Sales", "Cash Flow",
            f"≥ {FCF_SALES_MIN_PCT:.0f}%",
            lambda v: v >= FCF_SALES_MIN_PCT,
        ))
        mkt_cap  = info.get("marketCap")
        free_cf  = info.get("freeCashflow")
        fcf_yield = (free_cf / mkt_cap * 100) if (free_cf and mkt_cap and mkt_cap > 0) else None
        params.append(self._check_scalar(
            fcf_yield, "FCF Yield", "Cash Flow",
            f"≥ {FCF_YIELD_MIN_PCT:.0f}%",
            lambda v: v >= FCF_YIELD_MIN_PCT,
        ))
        cfo_np = self._compute_cfo_net_profit_3y(t)
        params.append(self._check_scalar(
            cfo_np, "CFO/Net Profit (3Y avg)", "Cash Flow",
            f"≥ {CFO_NET_PROFIT_MIN}x",
            lambda v: v >= CFO_NET_PROFIT_MIN,
        ))
        payout = info.get("payoutRatio")
        if payout is not None and payout > 0:
            in_range = DIVIDEND_PAYOUT_MIN <= payout <= DIVIDEND_PAYOUT_MAX
            params.append(OverlayParamResult(
                name="Dividend Payout Ratio", category="Cash Flow",
                value=round(payout * 100, 1),
                threshold=f"{DIVIDEND_PAYOUT_MIN * 100:.0f}–{DIVIDEND_PAYOUT_MAX * 100:.0f}% (mature cash generators)",
                status="PASS" if in_range else "FAIL",
                notes=f"{payout * 100:.1f}% — {'in range' if in_range else 'out of range'}",
            ))
        else:
            params.append(OverlayParamResult(
                name="Dividend Payout Ratio", category="Cash Flow",
                value=None, threshold="15–45% (mature cash generators)",
                status="N/A", notes="No dividend or data unavailable — conditional metric",
            ))

        # ── Category 4: Asset Allocation ─────────────────────────────────────
        roic = self._compute_roic(t, info)
        params.append(self._check_scalar(
            roic, "ROIC", "Asset Alloc",
            f"≥ {ROIC_MIN_PCT:.0f}%",
            lambda v: v >= ROIC_MIN_PCT,
        ))
        params.append(OverlayParamResult(
            name="ROIC-WACC Spread", category="Asset Alloc",
            value=None, threshold=f"≥ {ROIC_WACC_SPREAD_MIN_BPS} bps",
            status="MANUAL_VERIFY", notes="WACC requires manual CAPM calculation",
        ))
        params.append(OverlayParamResult(
            name="Reinvestment Rate", category="Asset Alloc",
            value=None, threshold=f"≥ {REINVESTMENT_RATE_MIN_PCT:.0f}% (high-growth names)",
            status="MANUAL_VERIFY", notes="(CapEx + ΔNWC) / NOPAT — verify on Screener.in",
        ))
        at = self._compute_asset_turnover(t)
        params.append(self._check_scalar(
            at, "Asset Turnover", "Asset Alloc",
            f"≥ {ASSET_TURNOVER_MIN}x",
            lambda v: v >= ASSET_TURNOVER_MIN,
        ))

        # ── Category 5: Compound Growth ───────────────────────────────────────
        rev3, rev5, ebitda3 = self._compute_cagrs(t)
        params.append(self._check_scalar(
            rev3, "3Y Revenue CAGR", "Growth",
            f"≥ {REVENUE_CAGR_3Y_MIN_PCT:.0f}%",
            lambda v: v >= REVENUE_CAGR_3Y_MIN_PCT,
        ))
        params.append(self._check_scalar(
            rev5, "5Y Revenue CAGR", "Growth",
            f"≥ {REVENUE_CAGR_5Y_MIN_PCT:.0f}%",
            lambda v: v >= REVENUE_CAGR_5Y_MIN_PCT,
        ))
        params.append(self._check_scalar(
            ebitda3, "3Y EBITDA CAGR", "Growth",
            f"≥ {EBITDA_CAGR_3Y_MIN_PCT:.0f}%",
            lambda v: v >= EBITDA_CAGR_3Y_MIN_PCT,
        ))

        # ── Category 6: Technical Retracement ────────────────────────────────
        cmp      = info.get("currentPrice") or info.get("regularMarketPrice")
        high_52w = info.get("fiftyTwoWeekHigh")
        if cmp and high_52w:
            dist = (high_52w - cmp) / high_52w
            params.append(OverlayParamResult(
                name="Distance from 52W High", category="Technical",
                value=round(dist * 100, 1),
                threshold=f"≤ {DIST_52W_HIGH_MAX * 100:.0f}%",
                status="PASS" if dist <= DIST_52W_HIGH_MAX else "FAIL",
                notes=f"{dist * 100:.1f}% below 52W high ₹{high_52w:.1f}",
            ))
        else:
            params.append(OverlayParamResult(
                name="Distance from 52W High", category="Technical",
                value=None, threshold="≤ 10%", status="MANUAL_VERIFY", notes="Price data unavailable",
            ))

        ema50_pass = self._check_50d_ema(ticker, cmp)
        params.append(OverlayParamResult(
            name="Above 50-day EMA", category="Technical",
            value=None, threshold="CMP > 50-day EMA",
            status="PASS" if ema50_pass is True else ("FAIL" if ema50_pass is False else "MANUAL_VERIFY"),
            notes="Price above 50-day EMA" if ema50_pass else ("Price below 50-day EMA" if ema50_pass is False else "Data error"),
        ))

        breakout_ok = self._check_breakout_volume(ticker, cmp, high_52w)
        params.append(OverlayParamResult(
            name="ATH Breakout Volume", category="Technical",
            value=None, threshold=f"≥ {BREAKOUT_VOLUME_MULT}x 20-day avg (if at ATH)",
            status="PASS" if breakout_ok is True else ("FAIL" if breakout_ok is False else "N/A"),
            notes="N/A — not at ATH" if breakout_ok is None else ("Breakout volume confirmed" if breakout_ok else "Low-conviction breakout"),
        ))

        rs_ok = self._check_rs_vs_nifty(ticker)
        params.append(OverlayParamResult(
            name="RS vs Nifty (6M)", category="Technical",
            value=None, threshold="RS line sloping upward over 6 months",
            status="PASS" if rs_ok is True else ("FAIL" if rs_ok is False else "MANUAL_VERIFY"),
            notes="Outperforming Nifty 6M" if rs_ok is True else ("Underperforming Nifty 6M" if rs_ok is False else "Data unavailable"),
        ))

        # ── Tally scores ──────────────────────────────────────────────────────
        pass_c   = sum(1 for p in params if p.status == "PASS")
        fail_c   = sum(1 for p in params if p.status == "FAIL")
        manual_c = sum(1 for p in params if p.status == "MANUAL_VERIFY")
        na_c     = sum(1 for p in params if p.status == "N/A")
        scorable = pass_c + fail_c
        pct      = (pass_c / scorable * 100) if scorable > 0 else 0
        rating   = "STRONG" if pct >= 80 else ("ADEQUATE" if pct >= 60 else "WEAK")

        return SecondaryOverlayResult(
            ticker=ticker, params=params,
            pass_count=pass_c, fail_count=fail_c,
            manual_count=manual_c, na_count=na_c,
            overall_rating=rating,
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _check_scalar(
        value: Optional[float],
        name: str,
        category: str,
        threshold: str,
        test_fn,
    ) -> OverlayParamResult:
        if value is None:
            return OverlayParamResult(name=name, category=category, value=None,
                                     threshold=threshold, status="MANUAL_VERIFY",
                                     notes="Data unavailable — verify on Screener.in")
        passed = test_fn(value)
        return OverlayParamResult(
            name=name, category=category,
            value=round(value, 2),
            threshold=threshold,
            status="PASS" if passed else "FAIL",
            notes=f"{value:.2f}",
        )

    def _compute_gross_margin_variance(self, t: yf.Ticker) -> Optional[float]:
        try:
            qfin = t.quarterly_financials
            if qfin is None or qfin.empty:
                return None
            rev_row = self._get_row(qfin, ["Total Revenue", "Revenue"])
            gp_row  = self._get_row(qfin, ["Gross Profit"])
            if rev_row is None or gp_row is None:
                return None
            rev = rev_row.dropna()
            gp  = gp_row.dropna()
            n   = min(len(rev), len(gp), 12)
            if n < 4:
                return None
            margins = [(float(gp.iloc[i]) / float(rev.iloc[i]) * 10_000)
                       for i in range(n) if float(rev.iloc[i]) > 0]
            if len(margins) < 4:
                return None
            mean = sum(margins) / len(margins)
            variance = sum((m - mean) ** 2 for m in margins) / len(margins)
            return variance ** 0.5   # std in bps
        except Exception:
            return None

    def _compute_opm_trend(self, t: yf.Ticker) -> Optional[bool]:
        try:
            fin = t.financials
            if fin is None or fin.empty:
                return None
            rev_row = self._get_row(fin, ["Total Revenue", "Revenue"])
            ebit_row = self._get_row(fin, ["EBIT", "Operating Income"])
            if rev_row is None or ebit_row is None:
                return None
            rev  = rev_row.dropna()
            ebit = ebit_row.dropna()
            n = min(len(rev), len(ebit), 3)
            if n < 2:
                return None
            margins = [float(ebit.iloc[i]) / float(rev.iloc[i]) * 100
                       for i in range(n) if float(rev.iloc[i]) > 0]
            return margins[0] > margins[-1]   # most recent > oldest → rising
        except Exception:
            return None

    def _compute_other_income_pbt(self, t: yf.Ticker) -> Optional[float]:
        try:
            fin = t.financials
            if fin is None or fin.empty:
                return None
            pbt_row = self._get_row(fin, ["Pretax Income", "Income Before Tax"])
            oi_row  = self._get_row(fin, ["Other Income Expense", "Non Operating Income Total Other"])
            if pbt_row is None or oi_row is None:
                return None
            pbt = float(pbt_row.dropna().iloc[0])
            oi  = abs(float(oi_row.dropna().iloc[0]))
            if pbt <= 0:
                return None
            return oi / pbt
        except Exception:
            return None

    def _compute_fcf_sales(self, t: yf.Ticker, info: dict) -> Optional[float]:
        try:
            cf = t.cashflow
            fin = t.financials
            if cf is None or cf.empty or fin is None or fin.empty:
                return None
            cfo_row  = self._get_row(cf, ["Operating Cash Flow", "Total Cash From Operating Activities"])
            capex_row = self._get_row(cf, ["Capital Expenditure", "Purchase Of Property Plant And Equipment"])
            rev_row  = self._get_row(fin, ["Total Revenue", "Revenue"])
            if cfo_row is None or rev_row is None:
                return None
            cfo = float(cfo_row.dropna().iloc[0])
            rev = float(rev_row.dropna().iloc[0])
            capex = abs(float(capex_row.dropna().iloc[0])) if capex_row is not None else 0
            if rev <= 0:
                return None
            return ((cfo - capex) / rev) * 100
        except Exception:
            return None

    def _compute_cfo_net_profit_3y(self, t: yf.Ticker) -> Optional[float]:
        try:
            cf  = t.cashflow
            fin = t.financials
            if cf is None or cf.empty or fin is None or fin.empty:
                return None
            cfo_row = self._get_row(cf, ["Operating Cash Flow", "Total Cash From Operating Activities"])
            ni_row  = self._get_row(fin, ["Net Income", "Net Income Common Stockholders"])
            if cfo_row is None or ni_row is None:
                return None
            cfos = cfo_row.dropna()
            nis  = ni_row.dropna()
            n = min(len(cfos), len(nis), 3)
            if n < 1:
                return None
            ratios = [float(cfos.iloc[i]) / float(nis.iloc[i])
                      for i in range(n) if float(nis.iloc[i]) > 0]
            return sum(ratios) / len(ratios) if ratios else None
        except Exception:
            return None

    def _compute_roic(self, t: yf.Ticker, info: dict) -> Optional[float]:
        try:
            fin = t.financials
            bs  = t.balance_sheet
            if fin is None or fin.empty or bs is None or bs.empty:
                return None
            ebit_row = self._get_row(fin, ["EBIT", "Operating Income"])
            tax_prov = self._get_row(fin, ["Tax Provision", "Income Tax Expense"])
            pbt_row  = self._get_row(fin, ["Pretax Income", "Income Before Tax"])
            eq_row   = self._get_row(bs, ["Stockholders Equity", "Total Stockholders Equity",
                                          "Common Stock Equity"])
            debt_row = self._get_row(bs, ["Total Debt", "Long Term Debt And Capital Lease Obligation"])
            cash_row = self._get_row(bs, ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"])

            if ebit_row is None or eq_row is None:
                return None

            ebit  = float(ebit_row.dropna().iloc[0])
            eq    = float(eq_row.dropna().iloc[0])
            debt  = float(debt_row.dropna().iloc[0]) if debt_row is not None else 0
            cash  = float(cash_row.dropna().iloc[0]) if cash_row is not None else 0

            tax_rate = 0.25   # default
            if tax_prov is not None and pbt_row is not None:
                tp  = float(tax_prov.dropna().iloc[0])
                pbt = float(pbt_row.dropna().iloc[0])
                if pbt > 0 and tp > 0:
                    tax_rate = min(tp / pbt, 0.40)

            nopat = ebit * (1 - tax_rate)
            ic    = eq + debt - cash
            if ic <= 0:
                return None
            return (nopat / ic) * 100
        except Exception:
            return None

    def _compute_asset_turnover(self, t: yf.Ticker) -> Optional[float]:
        try:
            fin = t.financials
            bs  = t.balance_sheet
            if fin is None or fin.empty or bs is None or bs.empty:
                return None
            rev_row = self._get_row(fin, ["Total Revenue", "Revenue"])
            ta_row  = self._get_row(bs, ["Total Assets"])
            if rev_row is None or ta_row is None:
                return None
            return float(rev_row.dropna().iloc[0]) / float(ta_row.dropna().iloc[0])
        except Exception:
            return None

    def _compute_cagrs(self, t: yf.Ticker) -> tuple[Optional[float], Optional[float], Optional[float]]:
        rev3 = rev5 = ebitda3 = None
        try:
            fin = t.financials
            if fin is None or fin.empty:
                return None, None, None
            rev_row    = self._get_row(fin, ["Total Revenue", "Revenue"])
            ebitda_row = self._get_row(fin, ["EBITDA", "Normalized EBITDA"])

            if rev_row is not None:
                revs = rev_row.dropna()
                if len(revs) >= 4:
                    rev3 = ((float(revs.iloc[0]) / float(revs.iloc[3])) ** (1/3) - 1) * 100
                if len(revs) >= 6:
                    rev5 = ((float(revs.iloc[0]) / float(revs.iloc[5])) ** (1/5) - 1) * 100

            if ebitda_row is not None:
                ebs = ebitda_row.dropna()
                if len(ebs) >= 4:
                    ebitda3 = ((float(ebs.iloc[0]) / float(ebs.iloc[3])) ** (1/3) - 1) * 100
        except Exception:
            pass
        return rev3, rev5, ebitda3

    def _check_50d_ema(self, ticker: str, cmp: Optional[float]) -> Optional[bool]:
        if cmp is None:
            return None
        try:
            hist = yf.Ticker(ticker + NSE_SUFFIX).history(period="3mo")
            if hist.empty or len(hist) < 50:
                return None
            ema50 = float(hist["Close"].ewm(span=50, adjust=False).mean().iloc[-1])
            return cmp > ema50
        except Exception:
            return None

    def _check_breakout_volume(
        self, ticker: str, cmp: Optional[float], high_52w: Optional[float]
    ) -> Optional[bool]:
        if cmp is None or high_52w is None:
            return None
        near_ath = (high_52w - cmp) / high_52w <= 0.02   # within 2% of 52W high
        if not near_ath:
            return None   # not at ATH — N/A
        try:
            hist = yf.Ticker(ticker + NSE_SUFFIX).history(period="2mo")
            if hist.empty or len(hist) < 21:
                return None
            avg_vol = float(hist["Volume"].iloc[-21:-1].mean())
            today_vol = float(hist["Volume"].iloc[-1])
            if avg_vol <= 0:
                return None
            return today_vol >= avg_vol * BREAKOUT_VOLUME_MULT
        except Exception:
            return None

    def _check_rs_vs_nifty(self, ticker: str) -> Optional[bool]:
        try:
            stock_hist = yf.Ticker(ticker + NSE_SUFFIX).history(period="6mo")
            nifty_hist = yf.Ticker(NIFTY_SYMBOL).history(period="6mo")
            if stock_hist.empty or nifty_hist.empty or len(stock_hist) < 60:
                return None
            stock_ret = float(stock_hist["Close"].iloc[-1] / stock_hist["Close"].iloc[0] - 1)
            nifty_ret = float(nifty_hist["Close"].iloc[-1] / nifty_hist["Close"].iloc[0] - 1)
            return stock_ret > nifty_ret
        except Exception:
            return None

    @staticmethod
    def _get_row(df, keys: list[str]):
        for key in keys:
            if key in df.index:
                return df.loc[key]
        return None


# ── Orchestrator: CandidateScreener ──────────────────────────────────────────

class CandidateScreener:
    """
    Runs all 4 gates in sequence, short-circuits on hard failure.
    Gate 2 disqualification and Gate 1 failure both hard-stop.
    Gate 3/4 continue even with Manual Verify fields.
    """

    def __init__(self) -> None:
        self._stage2     = Stage2Checker()
        self._ud         = UDRatioCalculator()
        self._funds      = FundamentalScreener()
        self._valuation  = ValuationStressTester()
        self._secondary  = SecondaryOverlayScreener()

    def screen(self, ticker: str) -> ScreeningResult:
        result = ScreeningResult(ticker=ticker)

        # Gate 1
        g1 = self._stage2.check(ticker)
        result.gate1 = g1
        if not g1.passed:
            result.first_failed_gate = "Gate 1 (Stage 2)"
            result.recommendation = f"FAIL: {g1.stage_label} — {g1.notes}"
            return result

        # Gate 2
        g2 = self._ud.calculate(ticker)
        result.gate2 = g2
        if g2.disqualified:
            result.first_failed_gate = "Gate 2 (U/D — Distribution)"
            result.recommendation = f"DISQUALIFY: {g2.notes}"
            return result
        if not g2.passed:
            result.first_failed_gate = "Gate 2 (U/D — Weak)"
            result.recommendation = f"FAIL: {g2.notes}"
            return result

        # Gates 3 + 4
        g34 = self._funds.screen(ticker)
        result.gate3_gate4 = g34
        if not g34.passed:
            result.first_failed_gate = "Gate 3/4 (Fundamentals)"
            result.recommendation = f"FAIL: {g34.notes}"
            return result

        # PE Stress Test
        pe = self._valuation.stress_test(ticker)
        result.pe_stress = pe
        if not pe.passed:
            result.first_failed_gate = "PE Stress Test"
            result.recommendation = f"FAIL: {pe.notes}"
            return result

        # 24-Parameter Secondary Overlay (advisory, never hard-fails)
        result.secondary = self._secondary.run(ticker)

        manual_items = g34.manual_verify_fields
        result.passed_all    = True
        overlay_rating = result.secondary.overall_rating if result.secondary else "N/A"
        result.recommendation = (
            f"ALL-CLEAR — Overlay: {overlay_rating}"
            if not manual_items
            else f"ALL-CLEAR (manual verify: {', '.join(manual_items)}) — Overlay: {overlay_rating}"
        )
        return result


# ── Entry-point: ScreeningRunner ──────────────────────────────────────────────

class ScreeningRunner:
    """
    Loads watchlist tickers, runs CandidateScreener on each,
    writes screening-results.md, and sends a Telegram summary.
    """

    def __init__(self, portfolio_value: float = 0.0) -> None:
        self._screener       = CandidateScreener()
        self._sizer          = PositionSizer()
        self._portfolio_value = portfolio_value
        self._bot_token      = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self._chat_id        = os.environ.get("TELEGRAM_CHAT_ID", "")

    def run(self, tickers: Optional[list[str]] = None) -> list[ScreeningResult]:
        if tickers is None:
            tickers = self._load_watchlist()

        if not tickers:
            logger.warning("No tickers to screen — check watchlist.md")
            return []

        results: list[ScreeningResult] = []
        for ticker in tickers:
            logger.info("Screening %s …", ticker)
            try:
                r = self._screener.screen(ticker)
            except Exception as exc:
                logger.error("Error screening %s: %s", ticker, exc)
                r = ScreeningResult(
                    ticker=ticker,
                    recommendation=f"ERROR: {exc}",
                )
            results.append(r)

        self._write_results_md(results)
        self._send_telegram(results)
        return results

    # ── I/O helpers ──────────────────────────────────────────────────────────

    def _load_watchlist(self) -> list[str]:
        try:
            with open(WATCHLIST_PATH, "r") as f:
                lines = f.readlines()
        except FileNotFoundError:
            logger.error("watchlist.md not found at %s", WATCHLIST_PATH)
            return []

        tickers: list[str] = []
        in_table = False
        for line in lines:
            line = line.strip()
            if line.startswith("| Ticker") or line.startswith("|Ticker"):
                in_table = True
                continue
            if in_table and line.startswith("|---"):
                continue
            if in_table and line.startswith("|"):
                parts = [p.strip() for p in line.split("|")]
                parts = [p for p in parts if p]
                if parts and parts[0] and not parts[0].startswith("-"):
                    tickers.append(parts[0])
            elif in_table and not line.startswith("|"):
                in_table = False
        return tickers

    def _write_results_md(self, results: list[ScreeningResult]) -> None:
        ist = pytz.timezone("Asia/Kolkata")
        now = datetime.now(ist).strftime("%Y-%m-%d %H:%M IST")
        passed   = [r for r in results if r.passed_all]
        failed   = [r for r in results if not r.passed_all]

        lines = [
            f"# PMS Screening Results — {now}",
            "",
            f"Screened: {len(results)} | Passed all gates: {len(passed)} | Failed: {len(failed)}",
            "",
            "## Gate-by-Gate Results",
            "",
            "| Ticker | Gate 1 (Stage 2) | Gate 2 (U/D) | Gate 3/4 (Fundas) | PE Stress | Status |",
            "|--------|-----------------|-------------|-------------------|-----------|--------|",
        ]
        for r in results:
            g1  = self._fmt_gate(r.gate1)
            g2  = self._fmt_ud(r.gate2)
            g34 = self._fmt_gate(r.gate3_gate4)
            pe  = self._fmt_gate(r.pe_stress)
            status = "✅ PASS" if r.passed_all else f"❌ {r.first_failed_gate}"
            lines.append(f"| {r.ticker} | {g1} | {g2} | {g34} | {pe} | {status} |")

        lines += ["", "## Ready to Acquire (All Gates Passed)", ""]
        if passed:
            lines += [
                "| Ticker | Entry (CMP) | SL (−9%) | Rec. Shares | Position Value | Risk % | Overlay | Notes |",
                "|--------|-------------|---------|-------------|---------------|--------|---------|-------|",
            ]
            for r in passed:
                cmp = r.gate1.cmp if r.gate1 else None
                overlay_rating = r.secondary.overall_rating if r.secondary else "N/A"
                if cmp and self._portfolio_value > 0:
                    size = self._sizer.size(r.ticker, self._portfolio_value, cmp)
                    lines.append(
                        f"| {r.ticker} | ₹{cmp:.1f} | ₹{size.sl_price:.1f} | "
                        f"{size.recommended_shares} | ₹{size.position_value:,.0f} | "
                        f"{size.effective_risk_pct:.2f}% | {overlay_rating} | {size.notes[:55]} |"
                    )
                else:
                    lines.append(
                        f"| {r.ticker} | ₹{cmp:.1f if cmp else '?'} | — | — | — | — | "
                        f"{overlay_rating} | Set PORTFOLIO_VALUE_INR env var |"
                    )

            # Detailed 24-parameter overlay for each passer
            lines += ["", "### 24-Parameter Secondary Overlay Detail", ""]
            for r in passed:
                if not r.secondary:
                    continue
                s = r.secondary
                lines.append(f"#### {r.ticker} — {s.overall_rating} "
                             f"({s.pass_count}✅ {s.fail_count}❌ {s.manual_count}⚠ {s.na_count} N/A)")
                lines.append("")
                lines.append("| # | Parameter | Category | Value | Threshold | Status |")
                lines.append("|---|-----------|----------|-------|-----------|--------|")
                for i, p in enumerate(s.params, 1):
                    val_str = f"{p.value}" if p.value is not None else "—"
                    status_icon = {"PASS": "✅", "FAIL": "❌", "MANUAL_VERIFY": "⚠️", "N/A": "—"}.get(p.status, p.status)
                    lines.append(
                        f"| {i} | {p.name} | {p.category} | {val_str} | {p.threshold} | {status_icon} |"
                    )
                lines.append("")
        else:
            lines.append("None — no candidates passed all gates today.")

        lines += ["", "## Failed / Flagged", ""]
        for r in failed:
            manual = ""
            if r.gate3_gate4 and r.gate3_gate4.manual_verify_fields:
                manual = f" [Manual Verify: {', '.join(r.gate3_gate4.manual_verify_fields)}]"
            lines.append(f"- **{r.ticker}**: {r.recommendation}{manual}")

        os.makedirs(os.path.dirname(SCREENING_RESULTS_PATH), exist_ok=True)
        with open(SCREENING_RESULTS_PATH, "w") as f:
            f.write("\n".join(lines) + "\n")
        logger.info("Wrote %s", SCREENING_RESULTS_PATH)

    def _send_telegram(self, results: list[ScreeningResult]) -> None:
        if not self._bot_token or not self._chat_id:
            logger.warning("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — skipping notification")
            return

        ist = pytz.timezone("Asia/Kolkata")
        now = datetime.now(ist).strftime("%d %b %Y %H:%M IST")
        passed = [r for r in results if r.passed_all]
        flags  = [r for r in results if not r.passed_all and r.gate2 and r.gate2.distribution_flag]

        msg_parts = [f"PMS SCREENING | {now}", f"Screened: {len(results)}"]
        if passed:
            pass_lines = []
            for r in passed:
                overlay = r.secondary.overall_rating if r.secondary else "N/A"
                pass_lines.append(f"  {r.ticker} [{overlay}]")
            msg_parts.append(f"\nALL-CLEAR ({len(passed)}):\n" + "\n".join(pass_lines))
        else:
            msg_parts.append("\nNo candidates passed all gates today.")

        if flags:
            msg_parts.append(
                f"\nDISTRIBUTION FLAGS: {', '.join(r.ticker for r in flags)}"
            )

        msg_parts.append("\nFull results: references/screening-results.md")
        message = "\n".join(msg_parts)

        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        try:
            resp = requests.post(
                url,
                json={"chat_id": self._chat_id, "text": message},
                timeout=10,
            )
            data = resp.json()
            if data.get("ok"):
                logger.info("Telegram notification sent")
            else:
                logger.error("Telegram error: %s", data)
        except Exception as exc:
            logger.error("Telegram send failed: %s", exc)

    @staticmethod
    def _fmt_gate(gate) -> str:
        if gate is None:
            return "—"
        return "✅" if gate.passed else "❌"

    @staticmethod
    def _fmt_ud(gate: Optional[UDRatioResult]) -> str:
        if gate is None:
            return "—"
        if gate.disqualified:
            return f"❌ DIST({gate.ud_50:.2f})" if gate.ud_50 else "❌ DIST"
        if not gate.passed:
            return f"❌ WEAK({gate.ud_50:.2f})" if gate.ud_50 else "❌ WEAK"
        flag = " ⚠️" if gate.distribution_flag else ""
        return f"✅({gate.ud_50:.2f}){flag}"


# ── CLI entry-point ───────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="PMS candidate screener")
    parser.add_argument(
        "--tickers",
        type=str,
        default="",
        help="Comma-separated list of NSE tickers to screen (overrides watchlist.md)",
    )
    parser.add_argument(
        "--portfolio-value",
        type=float,
        default=float(os.environ.get("PORTFOLIO_VALUE_INR", "0")),
        help="Current portfolio value in INR for position sizing",
    )
    args = parser.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()] or None
    runner = ScreeningRunner(portfolio_value=args.portfolio_value)
    results = runner.run(tickers=tickers)

    passed = [r for r in results if r.passed_all]
    logger.info(
        "Screening complete: %d/%d passed all gates", len(passed), len(results)
    )


if __name__ == "__main__":
    main()
