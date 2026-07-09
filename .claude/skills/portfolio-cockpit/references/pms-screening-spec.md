# PMS Quantitative Screening Specification

Last Updated: 2026-07-09

This document is the single source of truth for all gate thresholds, formulas, and the
24-parameter advisory overlay used by both `scripts/screen_candidates.py` (automated)
and the portfolio-cockpit skill (interactive Phase 3D and Phase 5 sessions).

---

## Screening Pipeline Overview

```
UNIVERSE (watchlist.md)
  │
  ├─ Gate 1: Stan Weinstein Stage 2  ─── FAIL → skip to next ticker
  ├─ Gate 2: U/D Volume Ratio         ─── FAIL → skip (DISQUALIFY if distribution)
  ├─ Gate 3: EBITDA-to-CFO (≥ 0.85)  ─── FAIL → skip
  ├─ Gate 4: CANSLIM Fundamentals     ─── FAIL → skip
  ├─ PE Stress Test (52W retracement) ─── FAIL → skip
  └─ 24-Parameter Secondary Overlay   ─── Advisory only (STRONG / ADEQUATE / WEAK)
```

All 4 hard gates must pass before a ticker enters the "Ready to Acquire" list.
The secondary overlay score informs conviction and position sizing — it never
hard-fails the pipeline.

---

## Gate 1: Stan Weinstein Stage 2

**Theory:** Stocks in Stage 2 (advancing) are under active institutional accumulation.
Buying during Stage 1 (basing) or Stage 3 (declining) is a capital-destruction pattern.

**Pass criteria (all three must be true):**

| Criterion | Formula | Threshold |
|-----------|---------|-----------|
| CMP above MA150 | CMP > 150-day simple MA | — |
| MA upsloping | MA150_today > MA150_20_sessions_ago | — |
| Not close to 52W low | CMP > 52W low × 1.20 | ≥ 20% above 52W low |

**MA period:** 150 trading days ≈ 30-week Weinstein MA.
**Slope lookback:** 20 sessions (≈ 1 calendar month).

**Stage labels (for re-qualification table):**
- `Stage 2 (Advancing)` — all three criteria met
- `Stage 2 Warning (MA flattening)` — CMP > MA but MA not upsloping
- `Stage 3/4 (Declining)` — CMP < MA and MA declining
- `Stage 1 (Basing)` — CMP < MA but MA still upsloping

**Holding re-qualification trigger:** If any existing holding moves to Stage 3/4,
flag as **"PMS THESIS VIOLATION — consider exit independent of SL level."**

---

## Gate 2: Up/Down Volume Ratio

**Theory:** Institutional accumulation leaves a volume footprint. Sustained up-day
volume dominance over 50 sessions is the most reliable non-price confirmation signal.

**Definitions:**
- Up day: `close > open` (doji days where `close = open` excluded)
- Down day: `close < open`
- U/D_50 = Σ(volume on up days, last 50 sessions) / Σ(volume on down days, last 50 sessions)
- U/D_21 = same for last 21 sessions (near-term confirmation)

**Thresholds:**

| Condition | Action |
|-----------|--------|
| U/D_50 ≥ 1.25 | PASS — institutional accumulation |
| U/D_50 < 0.75 | IMMEDIATE DISQUALIFY — distribution confirmed |
| U/D_50 between 0.75–1.25 | FAIL — neutral/weak, do not enter |
| U/D_21 < 0.75 | DISTRIBUTION FLAG (⚠️) even if U/D_50 passes — tighten SL on holdings |

**Holding re-qualification:** If a holding shows U/D_21 < 0.75 for 3+ consecutive
sessions, add: "DISTRIBUTION SIGNAL — institutional selling; tighten trailing SL."

---

## Gate 3: EBITDA-to-CFO Cash Conversion

**Threshold:** CFO / EBITDA ≥ 0.85

**Formula:** `EBITDA-to-CFO = Operating Cash Flow (most recent FY) / EBITDA (most recent FY)`

**Rationale:** A ratio < 0.85 indicates aggressive revenue recognition, channel stuffing,
or inflated receivables. AXTEL case: CFO/EBITDA = 0.77 → FAIL.

**Data sources:** `yf.Ticker.cashflow` (CFO) and `yf.Ticker.info["ebitda"]` or
`yf.Ticker.financials` (EBITDA). Marked "Manual Verify" if both sources unavailable.

---

## Gate 4: CANSLIM Fundamentals

Four sub-criteria, all must pass (or be manually verified):

### 4-A: Annual Net Profit CAGR
- **Formula:** `((PAT_latest / PAT_3Y_ago) ^ (1/3) − 1) × 100`
- **Threshold:** ≥ 20%
- **Data:** `yf.Ticker.financials` → "Net Income", 4 annual data points needed

### 4-B: Balance Sheet Quality
| Metric | Threshold | Formula |
|--------|-----------|---------|
| D/E ratio | ≤ 0.5 | Total Debt / Shareholders Equity |
| ROCE | ≥ 15% | EBIT / (Total Assets − Current Liabilities) × 100 |

### 4-C: Quarterly Acceleration
| Metric | Threshold | Formula |
|--------|-----------|---------|
| Quarterly EPS growth | ≥ 25% YoY | (EPS_Q0 − EPS_Q4) / |EPS_Q4| × 100 |
| Quarterly Revenue growth | ≥ 25% YoY | (Rev_Q0 − Rev_Q4) / Rev_Q4 × 100 |

**VEEFIN case study — capital trap failure:** ROCE = 4.2%, quarterly EPS growth = −12%.
Despite Stage 2 setup, fundamental deterioration disqualifies under Gate 4-B and 4-C.

---

## PE Stress Test (52-Week Retracement Gate)

**Purpose:** Confirms the stock is in active markup phase with no trapped overhead sellers.

**Threshold:** `(52W High − CMP) / 52W High ≤ 0.10` (within 10% of 52-week high)

**Formula:**
```
retracement_pct = (52W_high - CMP) / 52W_high
PASS if retracement_pct ≤ 0.10
```

**Informational (not hard gate):**
- Entry PE = CMP / TTM EPS
- Forward PE = CMP / Forward EPS

**PE Compression Stress Test (manual verify):**
```
2Y Forward EPS = TTM EPS × (1 + projected_growth_rate)^2
Target Price at 25% upside = CMP × 1.25
Required Fwd PE at target = Target Price / 2Y Fwd EPS
PASS if Required Fwd PE ≤ 5-year median sector PE
```

This is flagged for manual verification in `screen_candidates.py` because the 5-year
median sector PE requires external data.

---

## 24-Parameter Secondary Valuation Overlay

**Nature:** Advisory scoring layer. Never hard-fails the pipeline. Applied only to
tickers that pass all 4 gates.

**Rating:**
- **STRONG:** ≥ 80% of scorable parameters pass
- **ADEQUATE:** 60–79% pass
- **WEAK:** < 60% pass

`Scorable = PASS + FAIL counts only (MANUAL_VERIFY and N/A excluded from denominator)`

---

### Category 1: Valuation & Pricing (5 parameters)

| # | Parameter | Threshold | Source | Notes |
|---|-----------|-----------|--------|-------|
| 1 | Trailing P/E | < 45x | `info["trailingPE"]` | Exception: EPS growth > 40% allows higher PE |
| 2 | Forward P/E | < 30x | `info["forwardPE"]` | Next-12-month consensus estimate |
| 3 | EV/EBITDA | < 25x | `info["enterpriseToEbitda"]` | Enterprise value multiple |
| 4 | P/B Ratio | < 6x | `info["priceToBook"]` | Useful for NBFC, banks |
| 5 | PEG Ratio | ≤ 1.2 | `info["pegRatio"]` | PE ÷ EPS growth rate |

---

### Category 2: Margin Consistency (4 parameters)

| # | Parameter | Threshold | Formula | Notes |
|---|-----------|-----------|---------|-------|
| 6 | Gross Margin Variance | < ±150 bps std | StdDev of 12Q gross margins in bps | Stability = pricing power |
| 7 | OPM Trend | Rising over 3 FY | OPM_FY0 > OPM_FY3 (most recent > oldest) | Operational leverage signal |
| 8 | Net Margin | > 12% | `info["profitMargins"] × 100` | Excludes financial intermediaries |
| 9 | Other Income / PBT | ≤ 10% | |Other Income| / PBT | High ratio = earnings quality risk |

---

### Category 3: Cash Flow Quality (4 parameters)

| # | Parameter | Threshold | Formula | Notes |
|---|-----------|-----------|---------|-------|
| 10 | FCF / Sales | ≥ 8% | (CFO − CapEx) / Revenue × 100 | FCF yield quality |
| 11 | FCF Yield | ≥ 3% | Free Cash Flow / Market Cap × 100 | `info["freeCashflow"] / info["marketCap"]` |
| 12 | CFO / Net Profit (3Y avg) | ≥ 1.0x | 3-year rolling avg of CFO / Net Income | Accrual quality test |
| 13 | Dividend Payout | 15–45% | `info["payoutRatio"]` | Conditional: N/A if no dividend (growth names) |

---

### Category 4: Asset Allocation Efficiency (4 parameters)

| # | Parameter | Threshold | Formula | Notes |
|---|-----------|-----------|---------|-------|
| 14 | ROIC | ≥ 18% | NOPAT / Invested Capital × 100 | NOPAT = EBIT × (1 − tax_rate); IC = Equity + Debt − Cash |
| 15 | ROIC-WACC Spread | ≥ 600 bps | ROIC − WACC | **MANUAL VERIFY** — WACC requires CAPM calculation |
| 16 | Reinvestment Rate | ≥ 50% | (CapEx + ΔNWC) / NOPAT | **MANUAL VERIFY** — verify on Screener.in / annual report |
| 17 | Asset Turnover | ≥ 1.5x | Revenue / Total Assets | Capital efficiency |

**Tax rate default:** 25% if not computable from `Tax Provision / Pretax Income`.
Capped at 40%.

---

### Category 5: Compound Growth Trajectory (3 parameters)

| # | Parameter | Threshold | Formula | Notes |
|---|-----------|-----------|---------|-------|
| 18 | 3Y Revenue CAGR | ≥ 18% | `(Rev_FY0 / Rev_FY3)^(1/3) − 1` × 100 | 4 annual data points needed |
| 19 | 5Y Revenue CAGR | ≥ 15% | `(Rev_FY0 / Rev_FY5)^(1/5) − 1` × 100 | 6 annual data points needed |
| 20 | 3Y EBITDA CAGR | ≥ 22% | `(EBITDA_FY0 / EBITDA_FY3)^(1/3) − 1` × 100 | Higher than revenue = margin expansion |

---

### Category 6: Technical Retracement (4 parameters)

| # | Parameter | Threshold | Formula | Notes |
|---|-----------|-----------|---------|-------|
| 21 | Distance from 52W High | ≤ 10% | `(52W_high − CMP) / 52W_high` | Same as PE Stress Test gate |
| 22 | Above 50-day EMA | CMP > 50d EMA | 50-period exponential MA of close | Near-term momentum |
| 23 | ATH Breakout Volume | ≥ 1.5x 20d avg | Today volume / 20-day avg volume | N/A if not within 2% of 52W high |
| 24 | RS vs Nifty 50 (6M) | Outperforming | 6M stock return > 6M Nifty 50 return | Relative strength confirmation |

---

## GTT Trailing Stop Slab Table (Groww 2026 Execution Guidelines)

Minimum trailing-stop increment by price slab. A trailing stop cannot be set tighter
than these increments on Groww's platform.

| Price Slab (₹) | Minimum Trailing Stop (₹) |
|----------------|--------------------------|
| 0 – 50 | 0.05 |
| 50 – 100 | 0.10 |
| 100 – 250 | 0.25 |
| 250 – 500 | 0.50 |
| 500 – 1,000 | 1.00 |
| 1,000 – 2,500 | 2.00 |
| 2,500 – 10,000 | 5.00 |

**Usage:** `PositionSizer.compute_trailing_stop_min(price)` returns the minimum increment.
When ratcheting a trailing SL upward, always use increments ≥ the slab minimum.

---

## Position Sizing Formula

```
Risk Amount     = Portfolio Value × 2%
SL Gap          = Entry Price − SL Price  (default SL = Entry × 0.91, i.e., −9%)
Raw Shares      = Risk Amount / SL Gap
Position Value  = Raw Shares × Entry Price
Cap Check       = if Position Value > Portfolio Value × 20% → use capped shares
Final Shares    = min(Raw Shares, Portfolio Value × 20% / Entry Price)  [integer]
Effective Risk  = Final Shares × SL Gap / Portfolio Value × 100%
```

**Verification example:**
- Portfolio = ₹4,25,000; Entry = ₹1,275; SL = ₹1,148 (gap = ₹127)
- Risk = ₹8,500; Raw shares = 8,500 / 127 ≈ 67; Position = ₹85,425 ≈ 20.1% → capped
- Final shares = int(85,000 / 1,275) = 66; Effective risk = 66 × 127 / 4,25,000 = 1.97%

---

## Case Studies

### TIPSIND — Passes All Gates

| Gate | Result | Key Values |
|------|--------|------------|
| Gate 1 (Stage 2) | ✅ PASS | CMP > MA150; MA upsloping; 48% above 52W low |
| Gate 2 (U/D) | ✅ PASS | U/D_50 = 1.67; U/D_21 = 1.82 |
| Gate 3 (EBITDA-CFO) | ✅ PASS | CFO/EBITDA = 0.91 |
| Gate 4 (Fundamentals) | ✅ PASS | PAT CAGR 34%; ROCE 28%; D/E 0.18; QtrEPS +42% |
| PE Stress Test | ✅ PASS | 6% below 52W high |
| Secondary Overlay | **STRONG** | 19/22 scorable pass (86%) |

---

### AXTEL — Fails Gate 3 (EBITDA-CFO = 0.77)

| Gate | Result | Key Values |
|------|--------|------------|
| Gate 1 (Stage 2) | ✅ PASS | CMP > MA150 |
| Gate 2 (U/D) | ✅ PASS | U/D_50 = 1.31 |
| Gate 3 (EBITDA-CFO) | ❌ FAIL | CFO/EBITDA = 0.77 < 0.85 threshold |
| Gates 4+ | Skipped | Short-circuit on Gate 3 failure |

**Lesson:** Revenue recognition quality failure. Despite technical setup, aggressive
revenue recognition would make this a capital trap.

---

### VEEFIN — Fails Gate 4 (Capital Trap: Negative FCF)

| Gate | Result | Key Values |
|------|--------|------------|
| Gate 1 (Stage 2) | ✅ PASS | CMP > MA150 |
| Gate 2 (U/D) | ✅ PASS | U/D_50 = 1.18 → WEAK (borderline, just passed) |
| Gate 3 (EBITDA-CFO) | ✅ PASS | CFO/EBITDA = 0.88 |
| Gate 4-B (D/E, ROCE) | ❌ FAIL | ROCE = 4.2% < 15% threshold |
| Gate 4-C (Quarterly) | ❌ FAIL | QtrEPS growth = −12% |

**Lesson:** Balance sheet-heavy fintech with poor capital efficiency. Good chart,
bad business — Gate 4 catches what Gate 1 and 2 miss.

---

## Implementation Notes

### `screen_candidates.py` Classes

| Class | Role |
|-------|------|
| `Stage2Checker` | Gate 1 — 1Y OHLCV via yfinance |
| `UDRatioCalculator` | Gate 2 — 3M volume via yfinance |
| `FundamentalScreener` | Gates 3+4 — financials, cashflow, balance sheet |
| `ValuationStressTester` | PE stress — `yf.Ticker.info` |
| `PositionSizer` | Risk-budgeted sizing + GTT slab lookup |
| `SecondaryOverlayScreener` | 24-parameter advisory overlay |
| `CandidateScreener` | Pipeline orchestrator — short-circuits on hard failures |
| `ScreeningRunner` | Entry point — loads watchlist, writes results, sends Telegram |

### `cockpit_runner.py` — Phase 3D

`HoldingRequalifier` imports `Stage2Checker` and `UDRatioCalculator` from
`screen_candidates.py` and re-runs Gates 1 and 2 on all active holdings daily.
Output appears as the "HOLDING RE-QUALIFICATION" table in the Phase 3 Telegram message.

### Data Availability

yfinance works in GitHub Actions (open internet). In Claude interactive sessions
(cloud container with proxy restrictions), yfinance returns 403. The cockpit skill
uses WebSearch as fallback for interactive checks, while `screen_candidates.py` and
`cockpit_runner.py` are intended to run exclusively in GitHub Actions.
