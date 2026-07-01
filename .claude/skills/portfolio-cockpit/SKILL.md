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

---

## Step 0 — Load All Data (REQUIRED before any phase)

### 0A — Fetch Live NSE Prices

Run the price fetcher script using the Bash tool:

```bash
python scripts/fetch_nse_prices.py
```

This writes `references/live-prices.md` with tick-accurate CMPs for all holdings and
GTT tickers via yfinance. **Do NOT web-search for individual stock prices anywhere in
this skill — always use the values in live-prices.md.**

If the Bash tool is unavailable, fall back to web-searching
`<TICKER> NSE share price site:nseindia.com` for each ticker.

### 0B — Read Portfolio and Price Data

Read both files:
- `references/portfolio-data.md` — holdings, SL levels, targets, GTT orders
- `references/live-prices.md` — live CMPs, P&L percentages, SL buffers, GTT distances

All price levels and CMP values used throughout the rest of this skill come from these
two files. Do not use stale or assumed prices.

### 0C — Read Telegram Config

Read `references/telegram-config.md` to get the bot token and chat ID.
You will use the sendMessage endpoint at the end of each phase to push the report
directly to the user's Telegram.

---

## Step 1 — Determine Which Phase to Run

Ask the user which phase they want, or auto-detect based on Indian Standard Time (IST):

| IST Window         | Phase                                      |
|--------------------|--------------------------------------------|
| Before 09:15       | Phase 1: Pre-Market Diagnostic             |
| 09:15 – 14:30      | Phase 2: Mid-Market Relative Strength      |
| After 14:30        | Phase 3: Post-Market Stop-Loss Audit       |
| User says "full"   | Run all three phases sequentially          |

If the user explicitly requests a specific phase or "full cockpit", honour that regardless
of the clock.

---

## Phase 1: Pre-Market Diagnostic (09:00 – 09:15 IST)

### 1A — Global Cue Check

Web-search: `Gift Nifty pre-open premium discount today`

Report:
- Gift Nifty level and gap vs previous Nifty 50 close
- Flag if gap-down > 0.3% → GTT pullback limits on pending buys may trigger
- Flag if gap-up > 0.5% → positive breadth, but warn against chasing extended opens

### 1B — GTT Alert Audit

Using CMP values from `references/live-prices.md` (do not web-search individual prices):

For each pending GTT order report:
- Ticker, GTT limit price, CMP from live-prices.md
- Distance from CMP to GTT level (already computed in the file)
- If within 3% of GTT level → flag as "APPROACHING — monitor closely"
- If CMP is below GTT level → flag as "TRIGGERED or near trigger — verify Groww execution"

### 1C — FII/DII Flow Snapshot

Web-search: `FII DII activity today India equities`

Report net FII and DII buy/sell figures if available.

---

### Phase 1 Telegram Notification

After completing Phase 1, build the following message and send it via WebFetch.

**Message format (plain text, keep under 3800 characters):**

```
PHASE 1 PRE-MARKET | {DATE} 09:10 IST

GIFT NIFTY
Level: {level} | Gap vs prev close: {gap}%
{gap flag if applicable}

GTT ALERT STATUS
{For each GTT ticker: TICKER | CMP Rs X | GTT Rs Y | Dist Z% | FLAG}

FII/DII
FII: Rs {X} Cr | DII: Rs {Y} Cr

ACTION ITEMS
{numbered list, or "CLEAN - No action required"}
```

**How to send:**
1. Build the message text above with actual values substituted.
2. URL-encode the message text (replace spaces with %20, newlines with %0A,
   special chars like Rs with Rs, pipe | with %7C).
3. Call WebFetch on this URL:
   `https://api.telegram.org/bot{BOT_TOKEN}/sendMessage?chat_id={CHAT_ID}&parse_mode=HTML&text={URL_ENCODED_MESSAGE}`
   Use the token and chat_id from `references/telegram-config.md`.
4. Confirm the WebFetch returned `"ok":true`.

---

## Phase 2: Mid-Market Relative Strength Evaluation (13:30 – 14:30 IST)

### 2A — Relative Strength Test

Using CMP values from `references/live-prices.md` for each holding.

Also web-search: `Nifty 50 today change percent` and `Nifty 500 today change percent`
to get today's index move.

Build the RS comparison table:

| Ticker | CMP (Rs) | Day Change (%) | Nifty 50 (%) | RS Status |
|--------|----------|----------------|--------------|-----------|

RS Status rules:
- Stock green, Nifty flat/red → "Relative Outperformer" (institutional accumulation signal)
- Stock red, Nifty green → "Relative Underperformer" (watch for weakness)
- Both move together → "In-line"

Note: Day change % for each holding = (CMP - previous close) / previous close * 100.
Use web-search `<TICKER> NSE day change percent today` only if live-prices.md does not
include intraday change (the file includes CMP and cost but not same-day open/prev-close).

### 2B — Volume Breakout Check

For any holding near or at a 52-week high (search if needed):
- Web-search: `<TICKER> NSE volume today vs 20-day average`
- Valid breakout: today volume > 1.5x 20-day average
- Strong breakout: volume > 2x 20-day average
- Below 1.5x on a new high → "Low-conviction breakout — watch for reversal"

### 2C — RSI Overbought Warning

For any holding up 5%+ intraday or 15%+ over 5 sessions, flag that RSI may be above 70.
No new capital at these levels. Existing GTT orders at lower levels remain valid.

---

### Phase 2 Telegram Notification

After completing Phase 2, build and send:

```
PHASE 2 MID-MARKET | {DATE} 13:35 IST

RELATIVE STRENGTH TABLE
TICKER | CMP Rs X | Day +Y% | Nifty +Z% | STATUS
(one row per holding)

VOLUME ALERTS (if any)
{ticker}: {flag}

RSI WARNINGS (if any)
{ticker}: RSI possibly overbought, no new entries

P&L SNAPSHOT
Portfolio: Rs {current_value} | Unrealised: {pnl}%

ACTION ITEMS
{numbered list, or "CLEAN - No action required"}
```

Send via the same WebFetch pattern as Phase 1, using telegram-config.md.

---

## Phase 3: Post-Market Stop-Loss Audit (15:30 – 16:00 IST)

### 3A — Closing Price vs Stop-Loss Comparison

Using closing CMP from `references/live-prices.md` (run the fetch script again at 15:35
to capture closing prices):

Build the SL audit table:

| Ticker | Close (Rs) | Trailing SL (Rs) | Buffer (%) | SL Status |
|--------|------------|------------------|------------|-----------|

SL Status rules:
- Buffer > 5% → "Safe"
- Buffer 2–5% → "Monitor closely"
- Buffer < 2% → "DANGER ZONE — prepare for potential exit"
- Close below SL → "STOP-LOSS BREACHED — exit on next open per rules"

### 3B — Trailing Stop-Loss Ratchet Check

For any stock that has moved significantly higher since the last SL update:
- If at new 52-week or all-time high, suggest raising trailing SL to the most recent
  swing low or 8–10% below the new high, whichever is tighter.
- Present the suggested new SL level and ask the user to confirm before updating
  `references/portfolio-data.md`.

### 3C — The "Stay Put" Rule Reminder

If any holding experienced 2%+ intraday range but closed comfortably above its SL:

> "This stock showed intraday noise but closed safely above your trailing stop.
> Per your rules: do not touch it. Whipsawing out on minor wiggles is a retail
> error that degrades compounding."

---

### Phase 3 Telegram Notification

After completing Phase 3, build and send:

```
PHASE 3 POST-MARKET AUDIT | {DATE} 15:35 IST

STOP-LOSS STATUS
TICKER | Close Rs X | SL Rs Y | Buffer Z% | STATUS
(one row per holding, BREACH rows at the top if any)

TRAILING SL RATCHET SUGGESTIONS
{ticker}: Current SL Rs X -> Suggested Rs Y (confirm before updating)
(or "None — all SLs current")

PORTFOLIO CLOSE
Total Value : Rs {X}
Unrealised  : {pnl}%

STAY-PUT REMINDERS (if applicable)
{ticker}: noise day, closed above SL — hold firm

ACTION ITEMS
{numbered list, or "CLEAN - Portfolio running clean"}
```

Send via the same WebFetch pattern as Phase 1, using telegram-config.md.

---

## Phase 4: Capital Allocation Rules Check

Run these after whichever phase was selected.

### 4A — Water Your Flowers, Pull Your Weeds

Use P&L % from `references/live-prices.md` for each holding:
- P&L > +15% → "Winner — let it run, trail SL upward"
- P&L between −5% and +5% → "Near cost — watch for catalyst confirmation"
- P&L < −10% → "Underperformer — re-evaluate thesis"

For APTUSVALUE: if recovering toward cost on institutional catalyst, note:
"Hold firmly. Let it run toward Rs 350 target. SL at Rs 251.46 protects capital."

### 4B — Pending GTT Deployment Check

For each pending GTT order:
- Distance from CMP to GTT level (from live-prices.md)
- Whether deploying at GTT gives at least 15% upside to target
- If upside-to-target from GTT < 15%, flag as potentially stale — suggest review

### 4C — Exited Positions Discipline

Remind the user:
> "Do not track daily ticks on exited positions (JSW Energy or others). Asset-heavy
> power producers lack the operating leverage of your current electrical equipment,
> high-horsepower backup, and specialty chemicals plays. Move forward."

---

## Output Format

Present results as a clean, scannable cockpit report using the phase structure above.

End every report with a **"Today's Action Items"** section listing only the things
requiring the user's attention (GTT verifications, SL breaches, approaching triggers,
suggested SL ratchets). If there are no action items, explicitly say:
"No action required. Portfolio is running clean."

Then send the phase-appropriate Telegram message as specified above.

---

## Updating Portfolio Data

If the user reports a trade (new buy, exit, or SL change), update
`references/portfolio-data.md` accordingly and confirm the change.

If a trailing SL ratchet was confirmed, update the SL in `references/portfolio-data.md`
**and** update the corresponding entry in `scripts/fetch_nse_prices.py` so the
live-prices.md computation uses the new SL level going forward.
