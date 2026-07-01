import argparse
import os
import sys
from datetime import datetime
import pytz
import requests
import yfinance as yf

HOLDINGS = {
    "APARINDS":   {"qty": 3,   "cost": 13577.33, "sl": 15132.00, "target": 18000.00},
    "CUMMINSIND": {"qty": 9,   "cost": 5660.83,  "sl": 5472.00,  "target": 7300.00},
    "AEGISLOG":   {"qty": 40,  "cost": 1002.55,  "sl": 1060.00,  "target": 1350.00},
    "APTUSVALUE": {"qty": 310, "cost": 294.70,   "sl": 251.46,   "target": 350.00},
    "KIRLOSENG":  {"qty": 10,  "cost": 2364.70,  "sl": 2290.00,  "target": 3100.00},
}

GTT_ORDERS = {
    "CGPOWER":         {"gtt": 930.00,  "target": 1120.00},
    "PRIVISPECIALITY": {"gtt": 3350.00, "target": 4025.00},
    "SYRMA":           {"gtt": 1275.00, "target": 1600.00},
}


def fetch_holding_data():
    results = {}
    for ticker, info in HOLDINGS.items():
        try:
            t = yf.Ticker(f"{ticker}.NS")
            fi = t.fast_info
            cmp = fi.last_price
            prev = fi.previous_close
            if cmp is None or prev is None:
                raise ValueError("No price data")
            day_chg_pct = (cmp - prev) / prev * 100
            pnl_pct = (cmp - info["cost"]) / info["cost"] * 100
            sl_buffer_pct = (cmp - info["sl"]) / info["sl"] * 100
            current_value = cmp * info["qty"]
            results[ticker] = {
                "cmp": cmp,
                "prev": prev,
                "day_chg_pct": day_chg_pct,
                "pnl_pct": pnl_pct,
                "sl_buffer_pct": sl_buffer_pct,
                "current_value": current_value,
                "qty": info["qty"],
                "cost": info["cost"],
                "sl": info["sl"],
                "target": info["target"],
                "ok": True,
            }
        except Exception as e:
            print(f"[WARN] {ticker}: {e}", file=sys.stderr)
            results[ticker] = {"ok": False, "sl": info["sl"], "cost": info["cost"],
                               "qty": info["qty"], "target": info["target"]}
    return results


def fetch_gtt_data():
    results = {}
    for ticker, info in GTT_ORDERS.items():
        try:
            t = yf.Ticker(f"{ticker}.NS")
            cmp = t.fast_info.last_price
            if cmp is None:
                raise ValueError("No price data")
            dist_pct = (cmp - info["gtt"]) / info["gtt"] * 100
            results[ticker] = {"cmp": cmp, "gtt": info["gtt"],
                               "target": info["target"], "dist_pct": dist_pct, "ok": True}
        except Exception as e:
            print(f"[WARN] GTT {ticker}: {e}", file=sys.stderr)
            results[ticker] = {"ok": False, "gtt": info["gtt"], "target": info["target"]}
    return results


def fetch_nifty_change():
    try:
        t = yf.Ticker("^NSEI")
        fi = t.fast_info
        cmp = fi.last_price
        prev = fi.previous_close
        if cmp is None or prev is None:
            raise ValueError("No data")
        return (cmp - prev) / prev * 100, cmp
    except Exception as e:
        print(f"[WARN] Nifty fetch failed: {e}", file=sys.stderr)
        return None, None


def fetch_volume_data(ticker):
    try:
        t = yf.Ticker(f"{ticker}.NS")
        hist = t.history(period="21d")
        if hist.empty or len(hist) < 2:
            raise ValueError("Insufficient history")
        today_vol = int(hist["Volume"].iloc[-1])
        avg_20d = int(hist["Volume"].iloc[:-1].mean())
        ratio = today_vol / avg_20d if avg_20d > 0 else 0
        return today_vol, avg_20d, ratio
    except Exception as e:
        print(f"[WARN] Volume {ticker}: {e}", file=sys.stderr)
        return None, None, None


def ai_web_lookup(prompt: str) -> str:
    """Use Claude API with web_search tool for Gift Nifty / FII-DII data."""
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        return 'N/A (set ANTHROPIC_API_KEY secret for live data)'
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=300,
        tools=[{'type': 'web_search_20250305', 'name': 'web_search', 'max_uses': 2}],
        messages=[{'role': 'user', 'content': prompt}]
    )
    for block in response.content:
        if hasattr(block, 'text') and block.text:
            return block.text.strip()
    return 'N/A'


def send_telegram(text: str) -> bool:
    token = os.environ['TELEGRAM_BOT_TOKEN']
    chat_id = os.environ['TELEGRAM_CHAT_ID']
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={'chat_id': chat_id, 'text': text[:4000], 'parse_mode': 'HTML'}
    )
    data = resp.json()
    ok = data.get('ok', False)
    print(f"[{'OK' if ok else 'FAIL'}] Telegram: {data}")
    return ok


def run_phase1(date_str: str):
    gift_nifty_text = ai_web_lookup(
        "Gift Nifty pre-open level today vs previous Nifty 50 close. "
        "Give me: level, gap%, and whether it's gap-up or gap-down. One paragraph max."
    )

    gtt_data = fetch_gtt_data()
    fii_dii_text = ai_web_lookup(
        "FII DII net activity today India equities. "
        "Give net FII and DII buy/sell in Crores. One line each."
    )
    holdings_data = fetch_holding_data()

    gtt_rows = []
    action_items = []

    for ticker, d in gtt_data.items():
        if not d["ok"]:
            gtt_rows.append(f"{ticker} | CMP N/A | GTT ₹{d['gtt']:.0f} | Dist N/A | ❓ UNAVAILABLE")
            continue
        dist = d["dist_pct"]
        cmp = d["cmp"]
        gtt = d["gtt"]
        if cmp <= gtt:
            flag = "🚨 TRIGGERED"
            action_items.append(f"CHECK {ticker}: GTT may have triggered (CMP ₹{cmp:.1f} ≤ GTT ₹{gtt:.0f})")
        elif dist <= 3:
            flag = "🔴 APPROACHING"
            action_items.append(f"WATCH {ticker}: only {dist:.1f}% above GTT level ₹{gtt:.0f}")
        else:
            flag = f"✅ OK ({dist:.1f}% above)"
        gtt_rows.append(f"{ticker} | CMP ₹{cmp:.1f} | GTT ₹{gtt:.0f} | Dist {dist:.1f}% | {flag}")

    alloc_rows = []
    for ticker, d in holdings_data.items():
        if not d["ok"]:
            alloc_rows.append(f"{ticker} | CMP N/A | P&L N/A | ❓ UNAVAILABLE")
            continue
        pnl = d["pnl_pct"]
        cmp = d["cmp"]
        if pnl > 15:
            status = "🌸 Winner-let run"
        elif -5 <= pnl <= 5:
            status = "⚪ Near cost"
        elif pnl < -10:
            status = "🔴 Underperformer"
            action_items.append(f"REVIEW {ticker}: P&L {pnl:.1f}% — underperformer flag")
        else:
            status = f"{'🟢' if pnl > 0 else '🟡'} {pnl:.1f}%"
        alloc_rows.append(f"{ticker} | ₹{cmp:.1f} | {pnl:+.1f}% | {status}")

    action_block = "\n".join(f"{i+1}. {a}" for i, a in enumerate(action_items)) if action_items else "CLEAN — No action required"

    msg = (
        f"📊 PHASE 1 PRE-MARKET | {date_str} 09:05 IST\n\n"
        f"🌏 GIFT NIFTY\n{gift_nifty_text}\n\n"
        f"⚠️ GTT ALERT STATUS\n" + "\n".join(gtt_rows) + "\n\n"
        f"📉 FII/DII FLOWS\n{fii_dii_text}\n\n"
        f"💼 CAPITAL ALLOCATION (Phase 4)\n" + "\n".join(alloc_rows) + "\n\n"
        f"✅ ACTION ITEMS\n{action_block}"
    )

    send_telegram(msg)


def run_phase2(date_str: str):
    holdings_data = fetch_holding_data()
    nifty_chg, nifty_level = fetch_nifty_change()
    nifty_str = f"{nifty_chg:+.2f}%" if nifty_chg is not None else "N/A"

    rs_rows = []
    volume_alerts = []
    rsi_warnings = []
    action_items = []
    total_invested = 0
    total_current = 0

    for ticker, d in holdings_data.items():
        if not d["ok"]:
            rs_rows.append(f"{ticker} | CMP N/A | Day N/A | RS: ❓ UNAVAILABLE")
            continue

        cmp = d["cmp"]
        day_chg = d["day_chg_pct"]
        current_val = d["current_value"]
        total_invested += d["cost"] * d["qty"]
        total_current += current_val

        if nifty_chg is not None:
            if day_chg > 0 and nifty_chg <= 0:
                rs_status = "🟢 Outperformer"
            elif day_chg < 0 and nifty_chg > 0:
                rs_status = "🔴 Underperformer"
                action_items.append(f"WATCH {ticker}: lagging Nifty (stock {day_chg:+.1f}% vs Nifty {nifty_chg:+.1f}%)")
            else:
                rs_status = "⚪ In-line"
        else:
            rs_status = "⚪ N/A (Nifty unavailable)"

        rs_rows.append(f"{ticker} | CMP ₹{cmp:.1f} | Day {day_chg:+.1f}% | RS: {rs_status}")

        # Volume check
        today_vol, avg_vol, ratio = fetch_volume_data(ticker)
        if ratio is not None:
            if ratio >= 2.0:
                volume_alerts.append(f"{ticker}: {ratio:.1f}x avg volume 🔥")
                action_items.append(f"HIGH VOLUME {ticker}: {ratio:.1f}x average — confirm direction")
            elif ratio >= 1.5:
                volume_alerts.append(f"{ticker}: {ratio:.1f}x avg volume ⬆️")

        # RSI proxy: large single-day move
        if abs(day_chg) > 5:
            rsi_warnings.append(f"{ticker}: {day_chg:+.1f}% single-day move — check overbought/oversold")
            action_items.append(f"RSI CHECK {ticker}: {day_chg:+.1f}% today — verify momentum sustainability")

    portfolio_pnl = (total_current - total_invested) / total_invested * 100 if total_invested > 0 else 0

    vol_block = "\n".join(volume_alerts) if volume_alerts else "None"
    rsi_block = "\n".join(rsi_warnings) if rsi_warnings else "None"
    action_block = "\n".join(f"{i+1}. {a}" for i, a in enumerate(action_items)) if action_items else "CLEAN — No action required"

    msg = (
        f"📊 PHASE 2 MID-MARKET | {date_str} 13:30 IST\n\n"
        f"📈 RELATIVE STRENGTH vs NIFTY ({nifty_str})\n" + "\n".join(rs_rows) + "\n\n"
        f"📦 VOLUME ALERTS\n{vol_block}\n\n"
        f"⚡ RSI WARNINGS\n{rsi_block}\n\n"
        f"💰 PORTFOLIO SNAPSHOT\n"
        f"Invested: ₹{total_invested:,.0f} | Current: ₹{total_current:,.0f} | P&L: {portfolio_pnl:+.1f}%\n\n"
        f"✅ ACTION ITEMS\n{action_block}"
    )

    send_telegram(msg)


def run_phase3(date_str: str):
    holdings_data = fetch_holding_data()

    sl_rows_critical = []
    sl_rows_normal = []
    ratchet_suggestions = []
    stay_put_reminders = []
    action_items = []
    total_invested = 0
    total_current = 0

    for ticker, d in holdings_data.items():
        if not d["ok"]:
            sl_rows_normal.append(f"{ticker} | Close N/A | SL ₹{d['sl']:.0f} | Buffer N/A | ❓ UNAVAILABLE")
            continue

        cmp = d["cmp"]
        sl = d["sl"]
        current_val = d["current_value"]
        total_invested += d["cost"] * d["qty"]
        total_current += current_val
        buf = d["sl_buffer_pct"]
        day_chg = d["day_chg_pct"]
        pnl = d["pnl_pct"]

        if cmp < sl:
            status = "🔴 STOP-LOSS BREACHED"
            row = f"{ticker} | Close ₹{cmp:.1f} | SL ₹{sl:.0f} | Buffer {buf:.1f}% | {status}"
            sl_rows_critical.insert(0, row)
            action_items.append(f"EXIT {ticker}: SL breached — CMP ₹{cmp:.1f} below SL ₹{sl:.0f}")
        elif buf < 2:
            status = "🚨 DANGER ZONE"
            row = f"{ticker} | Close ₹{cmp:.1f} | SL ₹{sl:.0f} | Buffer {buf:.1f}% | {status}"
            sl_rows_critical.append(row)
            action_items.append(f"TIGHT SL {ticker}: only {buf:.1f}% buffer — set alert at SL ₹{sl:.0f}")
        elif buf < 5:
            status = "⚠️ Monitor"
            sl_rows_normal.append(f"{ticker} | Close ₹{cmp:.1f} | SL ₹{sl:.0f} | Buffer {buf:.1f}% | {status}")
        else:
            status = "✅ Safe"
            sl_rows_normal.append(f"{ticker} | Close ₹{cmp:.1f} | SL ₹{sl:.0f} | Buffer {buf:.1f}% | {status}")

        # Trailing SL ratchet when winner threshold hit
        if pnl > 15:
            trail_sl = cmp * 0.90
            ratchet_suggestions.append(
                f"{ticker}: P&L {pnl:.1f}% — trail SL to ₹{trail_sl:.0f} (10% below ₹{cmp:.1f})"
            )
            action_items.append(f"RATCHET {ticker}: update SL to ₹{trail_sl:.0f}")

        # Stay-put reminder: volatile day but holding above SL
        if abs(day_chg) > 2 and cmp > sl:
            stay_put_reminders.append(
                f"{ticker}: {day_chg:+.1f}% today but closed above SL ₹{sl:.0f} — no action needed"
            )

    portfolio_pnl = (total_current - total_invested) / total_invested * 100 if total_invested > 0 else 0

    all_sl_rows = sl_rows_critical + sl_rows_normal
    sl_block = "\n".join(all_sl_rows) if all_sl_rows else "No data available"
    ratchet_block = "\n".join(ratchet_suggestions) if ratchet_suggestions else "None — all SLs current"
    stay_block = "\n".join(stay_put_reminders) if stay_put_reminders else "None"
    action_block = "\n".join(f"{i+1}. {a}" for i, a in enumerate(action_items)) if action_items else "CLEAN — Portfolio running clean"

    msg = (
        f"📊 PHASE 3 POST-MARKET AUDIT | {date_str} 15:35 IST\n\n"
        f"🔍 STOP-LOSS STATUS\n(BREACHED/DANGER rows first)\n{sl_block}\n\n"
        f"📐 TRAILING SL RATCHET\n{ratchet_block}\n\n"
        f"💰 PORTFOLIO CLOSE\n"
        f"Total: ₹{total_current:,.0f} | P&L: {portfolio_pnl:+.1f}%\n\n"
        f"🛑 STAY-PUT REMINDERS\n{stay_block}\n\n"
        f"✅ ACTION ITEMS\n{action_block}"
    )

    send_telegram(msg)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", type=int, choices=[1, 2, 3], required=True)
    args = parser.parse_args()

    ist = pytz.timezone("Asia/Kolkata")
    now_ist = datetime.now(ist)
    date_str = now_ist.strftime("%d-%b-%Y")

    if args.phase == 1:
        run_phase1(date_str)
    elif args.phase == 2:
        run_phase2(date_str)
    elif args.phase == 3:
        run_phase3(date_str)


if __name__ == "__main__":
    main()
