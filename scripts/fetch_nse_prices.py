"""Fetch live NSE prices via yfinance and write references/live-prices.md."""
import yfinance as yf
from datetime import datetime

HOLDINGS = {
    "APTUS":      {"qty": 310, "cost": 294.70,   "sl": 280.00,   "target": 350.00},
    "APARINDS":   {"qty": 4,   "cost": 14838.00, "sl": 14154.00, "target": 18000.00},
    "KIRLOSENG":  {"qty": 25,  "cost": 2349.88,  "sl": 2290.00,  "target": 3100.00},
    "AEGISLOG":   {"qty": 40,  "cost": 1002.55,  "sl": 1190.00,  "target": 1350.00},
    "CUMMINSIND": {"qty": 9,   "cost": 5660.83,  "sl": 5472.00,  "target": 7300.00},
    "NAVINFLUOR": {"qty": 6,   "cost": 7510.00,  "sl": 7103.00,  "target": 9250.00},
    "WELCORP":    {"qty": 30,  "cost": 1491.70,  "sl": 1350.00,  "target": None},
    "CGPOWER":    {"qty": 50,  "cost": 898.46,   "sl": 880.00,   "target": 1120.00},
}

GTT_ORDERS = {
    "PRIVISCL": {"gtt": 3350.00, "target": 4025.00},
    "SYRMA":    {"gtt": 1275.00, "target": 1600.00},
}


def fetch_price(yf_ticker):
    try:
        t = yf.Ticker(yf_ticker)
        info = t.fast_info
        price = info.last_price
        prev_close = info.previous_close
        return round(price, 2), round(prev_close, 2) if prev_close else None
    except Exception:
        return None, None


def pct(a, b):
    if b and b != 0:
        return round((a - b) / b * 100, 2)
    return None


lines = []
lines.append(f"# Live NSE Prices — Fetched: {datetime.now().strftime('%Y-%m-%d %H:%M IST')}\n")
lines.append("## Active Holdings\n")
lines.append("| Ticker | CMP (Rs) | Prev Close (Rs) | Day Chg% | Cost (Rs) | P&L% | SL (Rs) | SL Buffer% | Target (Rs) | Upside% |")
lines.append("|--------|----------|-----------------|----------|-----------|------|---------|------------|-------------|---------|")

total_cost_value = 0
total_current_value = 0

for ticker, h in HOLDINGS.items():
    cmp, prev = fetch_price(f"{ticker}.NS")
    target_str = f"{h['target']:,.2f}" if h["target"] is not None else "TBD"
    if cmp is None:
        lines.append(f"| {ticker} | N/A | N/A | N/A | {h['cost']:,.2f} | N/A | {h['sl']:,.2f} | N/A | {target_str} | N/A |")
        continue
    day_chg = pct(cmp, prev) if prev else None
    pnl = pct(cmp, h["cost"])
    sl_buf = pct(cmp, h["sl"])
    upside = pct(h["target"], cmp) if h["target"] is not None else None
    total_cost_value += h["qty"] * h["cost"]
    total_current_value += h["qty"] * cmp
    day_str    = f"{day_chg:+.2f}%" if day_chg is not None else "N/A"
    prev_str   = f"{prev:,.2f}" if prev else "N/A"
    upside_str = f"{upside:+.2f}%" if upside is not None else "TBD"
    lines.append(
        f"| {ticker} | {cmp:,.2f} | {prev_str} | {day_str} | "
        f"{h['cost']:,.2f} | {pnl:+.2f}% | {h['sl']:,.2f} | {sl_buf:+.2f}% | "
        f"{target_str} | {upside_str} |"
    )

total_pnl_pct = pct(total_current_value, total_cost_value)
lines.append(f"\n**Portfolio Total Cost:** Rs {total_cost_value:,.2f}")
lines.append(f"**Portfolio Current Value:** Rs {total_current_value:,.2f}")
pnl_str = f"{total_pnl_pct:+.2f}%" if total_pnl_pct is not None else "N/A"
lines.append(f"**Unrealised P&L:** {pnl_str}\n")

lines.append("## Pending GTT Orders\n")
lines.append("| Ticker | CMP (Rs) | GTT Level (Rs) | Distance% | Target (Rs) | Upside from GTT% |")
lines.append("|--------|----------|----------------|-----------|-------------|-----------------|")

for ticker, g in GTT_ORDERS.items():
    cmp, prev = fetch_price(f"{ticker}.NS")
    if cmp is None:
        lines.append(f"| {ticker} | N/A | {g['gtt']:,.2f} | N/A | {g['target']:,.2f} | N/A |")
        continue
    dist = pct(cmp, g["gtt"])
    upside_from_gtt = pct(g["target"], g["gtt"])
    flag = ""
    if dist is not None:
        if dist <= 0:
            flag = " [AT/BELOW GTT - CHECK]"
        elif dist <= 3:
            flag = " [APPROACHING]"
    dist_str = f"{dist:+.2f}%{flag}" if dist is not None else "N/A"
    lines.append(
        f"| {ticker} | {cmp:,.2f} | {g['gtt']:,.2f} | {dist_str} | "
        f"{g['target']:,.2f} | {upside_from_gtt:+.2f}% |"
    )

output = "\n".join(lines)
with open("references/live-prices.md", "w") as f:
    f.write(output)
print("Written to references/live-prices.md")
print(output)
