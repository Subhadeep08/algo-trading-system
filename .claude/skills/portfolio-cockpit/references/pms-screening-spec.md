# PMS Quantitative Screening Spec

Single source of truth for all gate thresholds, formulas, and the 24-parameter checklist.
Claude reads this file during Phase 3D (holding re-qualification) and Phase 5 (watchlist screening).

Last Updated: 2026-07-08

---

## Overview

Four sequential gates + one valuation stress test filter candidates.
Gates short-circuit: a fail at Gate 1 skips Gates 2–4.
All math below is implemented in `scripts/screen_candidates.py`.

---

## Gate 1 — Stan Weinstein Stage 2 (Technical Trend)

**What it measures:** Is the stock in an institutionally-led advancing phase?

| Criterion | Formula | Threshold | Pass Condition |
|-----------|---------|-----------|---------------|
| 1. CMP above 150-day MA | Rolling 150-day close average | — | CMP > MA150 |
| 2. MA150 upslope | MA150_today vs MA150_20_sessions_ago | — | MA150_today > MA150_20d |
| 3. CMP above 52W low floor | CMP / 52W_low | ≥ 1.20 | CMP ≥ 52W_low × 1.20 |

**Stage Labels:**
- Stage 1 (Basing): CMP near MA150, MA flat
- Stage 2 (Advancing): All 3 criteria met — ENTRY ZONE
- Stage 2 Warning: CMP above MA but MA slope turning flat
- Stage 3 (Top): CMP rolling over, MA still rising — DO NOT ENTER; EXIT EXISTING
- Stage 4 (Declining): CMP below MA150, MA declining — AVOID

**PMS rule:** Never enter Stage 3 or Stage 4. For existing holdings in Stage 3, flag as "PMS THESIS VIOLATION" independent of trailing SL level.

---

## Gate 2 — Up/Down Volume Ratio (Institutional Footprint)

**What it measures:** Is institutional money flowing in (accumulation) or out (distribution)?

| Parameter | Formula | Threshold |
|-----------|---------|-----------|
| U/D_50 | Sum(volume on up-days, last 50 sessions) / Sum(volume on down-days, last 50 sessions) | ≥ 1.25 = Pass; < 0.75 = DISQUALIFY |
| U/D_21 | Same computation, last 21 sessions | < 0.75 = FLAG DISTRIBUTION |

**Day classification:**
- Up day: Close > Open
- Down day: Close < Open
- Doji (Close ≈ Open): Excluded from both numerator and denominator

**Decision matrix:**
| U/D_50 | U/D_21 | Action |
|--------|--------|--------|
| ≥ 1.25 | ≥ 0.75 | ✅ Gate 2 pass |
| ≥ 1.25 | < 0.75 | ⚠️ Pass with near-term distribution flag — monitor closely |
| 0.75–1.25 | any | ❌ Gate 2 fail — insufficient accumulation |
| < 0.75 | any | ❌ IMMEDIATE DISQUALIFY — institutional selling confirmed |

---

## Gate 3 — Cash Conversion Quality (EBITDA-to-CFO)

**What it measures:** Are reported earnings backed by actual cash flows? (Protects against earnings manipulation.)

| Metric | Formula | Threshold |
|--------|---------|-----------|
| EBITDA-to-CFO ratio | CFO / EBITDA | ≥ 0.85 |

Where:
- CFO = Operating Cash Flow (from cash flow statement)
- EBITDA = Earnings Before Interest, Tax, Depreciation & Amortisation

**If data unavailable:** Mark as "Manual Verify" — check Screener.in cash flow statement manually.
Do NOT fail the stock solely on missing data. Fail only on confirmed ratio < 0.85.

---

## Gate 4 — CANSLIM Fundamentals

**Sub-gate 4-A: Annual PAT CAGR (3 years)**

| Metric | Formula | Threshold |
|--------|---------|-----------|
| 3-year PAT CAGR | (PAT_latest / PAT_3y_ago)^(1/3) − 1 | ≥ 20% |

**Sub-gate 4-B: Balance Sheet Quality**

| Metric | Formula | Threshold |
|--------|---------|-----------|
| Debt-to-Equity | Total Debt / Total Shareholders' Equity | ≤ 0.50 |
| ROCE | EBIT / (Total Assets − Current Liabilities) | ≥ 15% |

**Sub-gate 4-C: Recent Quarter Growth (YoY)**

| Metric | Formula | Threshold |
|--------|---------|-----------|
| Quarterly EPS growth | (EPS_current_Q / EPS_same_Q_last_year − 1) × 100 | ≥ 25% |
| Quarterly Revenue growth | (Rev_current_Q / Rev_same_Q_last_year − 1) × 100 | ≥ 25% |

**N (New catalyst):** Not quantified — required qualitative check. At least one of:
- New product launch / entry into new market
- Order book expansion ≥ 20% YoY
- Regulatory or structural tailwind (e.g., PLI scheme, capex cycle)
- Management guidance upgrade

---

## Forward PE Stress Test

**What it measures:** Is the current valuation justified at the target price?

| Parameter | Formula | Threshold |
|-----------|---------|-----------|
| Entry PE | CMP / TTM EPS | Informational |
| Forward PE | CMP / Forward EPS | Informational |
| 52-week retracement | (52W_high − CMP) / 52W_high | ≤ 20% |

**Pass condition:** CMP ≥ 52W high × 0.80 (stock is not in a terminal decline — within 20% of its recent high)

**Target PE stress test (manual):**
Target PE at 25% upside = (CMP × 1.25) / (Fwd EPS × (1 + projected_growth_rate))
Pass if Target PE ≤ 5-year median PE of the stock (verify on Screener.in)

---

## Position Sizing Formula

| Variable | Formula | Default |
|----------|---------|---------|
| Risk amount | Portfolio value × 0.02 | 2% per trade |
| Raw shares | Risk amount / (Entry price − SL price) | — |
| Position value | Raw shares × Entry price | — |
| Cap | Max 20% of portfolio | — |
| Default SL | Entry price × (1 − 0.09) | 9% below entry |

**Worked example:**
- Portfolio: ₹4,25,000; Entry: ₹1,275; SL: ₹1,148
- Risk: ₹8,500 / Gap: ₹127 = 66.9 raw shares → 66 shares
- Position value: 66 × ₹1,275 = ₹84,150 (19.8% of portfolio — within cap)
- Effective risk: 66 × ₹127 / ₹4,25,000 = 1.97%

---

## 24-Parameter Checklist

Use this for interactive Phase 5 screening and Phase 3D re-qualification.

### Trend (Gate 1)
- [ ] 1. CMP > 150-day MA
- [ ] 2. 150-day MA upsloping (today vs 20 sessions ago)
- [ ] 3. CMP ≥ 52W low × 1.20

### Volume / Accumulation (Gate 2)
- [ ] 4. U/D_50 ≥ 1.25
- [ ] 5. U/D_50 NOT < 0.75 (no distribution disqualify)
- [ ] 6. U/D_21 NOT < 0.75 (no near-term distribution flag)

### Cash Quality (Gate 3)
- [ ] 7. EBITDA-to-CFO ≥ 0.85 (or Manual Verify)

### Fundamentals (Gate 4)
- [ ] 8. Quarterly EPS growth ≥ 25% YoY
- [ ] 9. Quarterly Revenue growth ≥ 25% YoY
- [ ] 10. 3-year PAT CAGR ≥ 20%
- [ ] 11. ROCE ≥ 15%
- [ ] 12. D/E ratio ≤ 0.50
- [ ] 13. New catalyst identified (qualitative)

### Valuation (PE Stress Test)
- [ ] 14. CMP within 20% of 52W high
- [ ] 15. Entry PE computed (TTM EPS available)
- [ ] 16. Forward PE computed (fwd EPS available)
- [ ] 17. Target PE ≤ 5-year median PE (manual check on Screener.in)

### Position Sizing
- [ ] 18. Risk per trade = 2% of portfolio confirmed
- [ ] 19. Position cap ≤ 20% of portfolio
- [ ] 20. SL identified (entry × 0.91 if no specific level)

### Re-Qualification (Existing Holdings)
- [ ] 21. Stage 2 confirmed (not Stage 3/4)
- [ ] 22. U/D_50 not in distribution zone
- [ ] 23. U/D_21 not showing near-term distribution
- [ ] 24. Original thesis catalyst still intact (qualitative)

---

## Holding Re-Qualification Rules

For existing holdings, run Gates 1 and 2 daily (Phase 3D).

| Gate 1 Result | Gate 2 Result | Recommended Action |
|--------------|---------------|--------------------|
| Stage 2 | Accumulation | ✅ HOLD — thesis confirmed |
| Stage 2 Warning | Any | ⚠️ WATCH — tighten trailing SL |
| Stage 3/4 | Any | 🔴 PMS THESIS VIOLATION — consider exit independent of SL |
| Stage 2 | U/D_21 Distribution | ⚠️ DISTRIBUTION SIGNAL — watch for SL approach |
| Stage 2 | U/D_50 Disqualify | 🔴 INSTITUTIONAL EXIT DETECTED — review urgently |

---

## Data Sources for Interactive (Claude) Sessions

When running Phase 5 interactively (no GitHub Actions), Claude should:
1. Web-search `TICKER NSE 150 day moving average site:trendlyne.com` for Gate 1
2. Web-search `TICKER NSE volume analysis accumulation distribution` for Gate 2 proxy
3. Web-search `TICKER screener.in` for Gate 3/4 fundamentals
4. Use live price from live-prices.md or Phase 2 RS search for CMP
5. Apply PositionSizer formula manually using portfolio value from portfolio-data.md context
