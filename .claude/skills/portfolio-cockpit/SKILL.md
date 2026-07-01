---
name: portfolio-cockpit
description: >
  Daily portfolio momentum cockpit and execution playbook for Groww account holdings.
  Runs a three-phase check (Pre-Market, Mid-Market, Post-Market) plus capital allocation
  rules across all active holdings and pending GTT limit buy orders.
  Use this skill whenever the user says any of: "run my portfolio check", "daily cockpit",
  "portfolio cockpit", "stock check", "run the playbook", "check my holdings",
  "pre-market check", "mid-market check", "post-market check", "stop-loss audit",
  "GTT check", "trailing stop check", "how are my stocks doing", "portfolio review",
  "momentum check", "run the cockpit", or any request to review, audit, or check their
  Groww portfolio holdings against stop-losses, targets, or momentum status.
  Also trigger when the user asks about a specific holding from their portfolio
  (Apar Industries, Cummins India, Aegis Logistics, Aptus Housing, Kirloskar Oil,
  CG Power, Privi Speciality, Syrma SGS) in the context of their portfolio rules.
---

# HNI Portfolio Momentum Cockpit

This skill runs a systematic, rules-based daily execution checklist across three trading
session phases. It eliminates emotional noise and enforces institutional-grade discipline
on position management.

## Step 0 — Load Portfolio Data

Before running any check, read the portfolio reference file:

```
references/portfolio-data.md
```

This file contains the current holdings (tickers, quantities, cost prices, trailing
stop-losses, targets, catalysts) and pending GTT limit buy orders. All price levels
referenced throughout this skill come from that file.

If the user says their holdings or levels have changed, update the reference file first,
then proceed.

## Step 1 — Determine Which Phase to Run

Ask the user which phase they want, or auto-detect based on Indian Standard Time (IST):

| IST Window         | Phase                                      |
|---------------------|--------------------------------------------|
| Before 09:15        | Phase 1: Pre-Market Diagnostic             |
| 09:15 – 14:30       | Phase 2: Mid-Market Relative Strength      |
| After 14:30         | Phase 3: Post-Market Stop-Loss Audit       |
| User says "full"    | Run all three phases sequentially           |

Use `user_time_v0` to get the current time. Convert to IST (UTC+5:30) if needed.
If the user explicitly requests a specific phase or "full cockpit", honour that regardless
of the clock.

---

## Phase 1: Pre-Market Diagnostic (09:00 – 09:15 IST)

### 1A — Global Cue Check

Web-search for: `Gift Nifty pre-open premium discount today`

Report:
- Gift Nifty level and gap vs previous Nifty 50 close
- Flag if gap-down > 0.3% → note that GTT pullback limits on pending buys may trigger
- Flag if gap-up > 0.5% → positive breadth, but warn against chasing extended opens

### 1B — GTT Alert Audit

List all pending GTT limit buy orders from the portfolio data file and remind the user
to verify they are live in the Groww terminal:

For each pending GTT, report:
- Ticker, GTT limit price, current market price (web-search `<TICKER> NSE share price today`)
- Distance from current price to GTT level as a percentage
- If current price is within 3% of GTT level → flag as "APPROACHING — monitor closely"
- If current price is below GTT level → flag as "TRIGGERED or near trigger — verify Groww execution"

### 1C — FII/DII Flow Snapshot (bonus)

Web-search: `FII DII activity today India equities`

Report net FII and DII buy/sell figures if available. Sustained FII selling + DII buying
is a classic institutional rotation pattern worth noting.

---

## Phase 2: Mid-Market Relative Strength Evaluation (13:30 – 14:30 IST)

### 2A — Relative Strength Test

For each active holding, web-search: `<TICKER> NSE share price today`
Also search: `Nifty 50 today` and `Nifty 500 today`

Build a comparison table:

| Ticker | Day Change (%) | Nifty 50 Change (%) | RS Status |
|--------|---------------|---------------------|-----------|

RS Status rules:
- If stock is green while Nifty is flat/red → **"Relative Outperformer ✅"** (institutional accumulation signal)
- If stock is red while Nifty is green → **"Relative Underperformer ⚠️"** (watch for weakness)
- If both move together → **"In-line"**

### 2B — Volume Breakout Check

For any holding that is near or at a 52-week high or all-time high (check via web search),
verify the breakout quality:

- Search: `<TICKER> NSE volume today vs average`
- Valid breakout = today's volume > 1.5x the 20-day average volume
- Strong breakout = today's volume > 2x the 20-day average volume
- If volume is below 1.5x on a new high → flag as **"Low-conviction breakout — watch for reversal"**

### 2C — RSI Overbought Warning

For any holding where price has rallied significantly (5%+ intraday or 15%+ over 5 sessions),
note that daily RSI may be above 70. This is not a sell signal for existing holdings, but it
means no new capital should be added at these levels. Pending GTT orders at lower levels
remain valid.

---

## Phase 3: Post-Market Stop-Loss Audit (15:30 – 16:00 IST)

### 3A — Closing Price vs Stop-Loss Comparison

For each active holding, web-search the closing price and compare against the trailing
stop-loss from the portfolio data file.

Build the audit table:

| Ticker | Close (₹) | Trailing SL (₹) | Buffer (%) | SL Status |
|--------|-----------|------------------|------------|-----------|

SL Status rules:
- Buffer > 5% → **"Safe ✅"**
- Buffer 2–5% → **"Monitor closely ⚠️"**
- Buffer < 2% → **"Danger zone 🔴 — prepare for potential exit"**
- Close below SL → **"STOP-LOSS BREACHED 🚨 — exit on next open per rules"**

### 3B — Trailing Stop-Loss Ratchet Check

For any stock that has moved significantly higher since the last stop-loss update:
- If the stock has made a new 52-week or all-time high, suggest raising the trailing SL
  to the most recent swing low or 8–10% below the new high, whichever is tighter
- Present the suggested new SL level and ask the user to confirm before updating the
  reference file

### 3C — The "Stay Put" Rule Reminder

If any holding experienced significant intraday volatility (2%+ range) but closed
comfortably above its stop-loss, explicitly state:

> "This stock showed intraday noise but closed safely above your trailing stop.
> Per your rules: do not touch it. Whipsawing out on minor wiggles is a retail
> error that degrades compounding."

---

## Phase 4: Capital Allocation Rules Check

Run these checks regardless of which phase was selected:

### 4A — Water Your Flowers, Pull Your Weeds

For each holding, calculate unrealised P&L:
- Unrealised P&L (%) = (Current Price − Cost Price) / Cost Price × 100

Flag:
- Holdings with P&L > +15% → **"Winner — let it run, trail SL upward"**
- Holdings with P&L between −5% and +5% → **"Near cost — watch for catalyst confirmation"**
- Holdings with P&L < −10% → **"Underperformer — re-evaluate thesis"**

For Aptus Housing specifically, if it is recovering toward cost price on institutional
catalyst, note: "Hold firmly. Let it run toward ₹350 target. SL at ₹251.46 protects capital."

### 4B — Pending GTT Deployment Check

For each pending GTT order, report:
- How far current price is from the GTT limit level
- Whether deploying at the GTT level gives at least a 15% upside to target
- If upside-to-target from GTT < 15%, flag the GTT as potentially stale and suggest review

### 4C — Exited Positions Discipline

Remind the user:
> "Do not track daily ticks on exited positions (JSW Energy or others). Asset-heavy
> power producers lack the operating leverage of your current electrical equipment,
> high-horsepower backup, and specialty chemicals plays. Move forward."

---

## Output Format

Present results as a clean, scannable cockpit report. Use the phase structure above.
End every report with a **"Today's Action Items"** section that lists only the things
requiring the user's attention (GTT verifications, SL breaches, approaching triggers,
suggested SL ratchets). If there are no action items, explicitly say: "No action required.
Portfolio is running clean."

---

## Updating Portfolio Data

If the user reports a trade (new buy, exit, or SL change), update
`references/portfolio-data.md` accordingly and confirm the change back to the user.
