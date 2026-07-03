"""
Portfolio updater — called by the GitHub Actions update-portfolio workflow.
Updates portfolio-data.md, cockpit_runner.py, and fetch_nse_prices.py atomically.

Actions:
  buy        Add new holding or accumulate (weighted-average cost)
  sell       Remove holding or reduce qty (full exit if qty omitted / >= held)
  update_sl  Update trailing stop-loss for an existing holding
  add_gtt    Add a pending GTT limit-buy order
  remove_gtt Remove a GTT order (after execution or cancellation)
"""

import argparse
import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PORTFOLIO_MD = REPO / ".claude/skills/portfolio-cockpit/references/portfolio-data.md"
COCKPIT_PY   = REPO / "scripts/cockpit_runner.py"
FETCH_PY     = REPO / "scripts/fetch_nse_prices.py"


# ── Dict parsing ─────────────────────────────────────────────────────────────

def _extract_block(content: str, var_name: str) -> tuple[int, int]:
    """Return (start_line, end_line) indices of the var_name = {...} block."""
    lines = content.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(rf"^{re.escape(var_name)}\s*=\s*\{{", line):
            start = i
            break
    if start is None:
        raise ValueError(f"{var_name} not found")
    depth, end = 0, None
    for i in range(start, len(lines)):
        depth += lines[i].count("{") - lines[i].count("}")
        if depth == 0:
            end = i
            break
    if end is None:
        raise ValueError(f"Unmatched braces for {var_name}")
    return start, end


def load_dict(filepath: Path, var_name: str) -> dict:
    content = filepath.read_text(encoding="utf-8")
    start, end = _extract_block(content, var_name)
    lines = content.splitlines()[start : end + 1]
    block = "\n".join(lines)
    dict_src = re.sub(rf"^{re.escape(var_name)}\s*=\s*", "", block)
    return ast.literal_eval(dict_src)


def replace_dict(filepath: Path, var_name: str, new_block: str):
    content = filepath.read_text(encoding="utf-8")
    lines = content.splitlines()
    start, end = _extract_block(content, var_name)
    result = lines[:start] + new_block.splitlines() + lines[end + 1:]
    filepath.write_text("\n".join(result) + "\n", encoding="utf-8")
    print(f"  [OK] {filepath.name} — {var_name} updated")


# ── Dict formatters ───────────────────────────────────────────────────────────

def fmt_holdings(holdings: dict) -> str:
    pad = max((len(k) for k in holdings), default=8) + 1
    rows = [f'HOLDINGS = {{']
    for t, d in holdings.items():
        sp = " " * (pad - len(t))
        target_val = f"{d['target']:.2f}" if d["target"] is not None else "None"
        rows.append(
            f'    "{t}":{sp}{{"qty": {d["qty"]}, "cost": {d["cost"]:.2f}, '
            f'"sl": {d["sl"]:.2f}, "target": {target_val}}},'
        )
    rows.append("}")
    return "\n".join(rows)


def fmt_gtt(gtt: dict) -> str:
    pad = max((len(k) for k in gtt), default=8) + 1
    rows = ["GTT_ORDERS = {"]
    for t, d in gtt.items():
        sp = " " * (pad - len(t))
        rows.append(
            f'    "{t}":{sp}{{"gtt": {d["gtt"]:.2f}, "target": {d["target"]:.2f}}},'
        )
    rows.append("}")
    return "\n".join(rows)


# ── Markdown writer ───────────────────────────────────────────────────────────

def write_portfolio_md(holdings: dict, gtt: dict, catalysts: dict):
    today = __import__("datetime").date.today().strftime("%B %d, %Y")
    lines = [
        f"# Portfolio Data — Last Updated: {today}",
        "",
        "Update this file whenever holdings, stop-losses, GTT levels, or targets change.",
        "",
        "## Active Holdings",
        "",
        "| Ticker (NSE)       | Qty | Cost Price (₹) | Trailing Stop-Loss (₹) | Target Price (₹) | Key Catalyst |",
        "|---------------------|-----|-----------------|-------------------------|-------------------|--------------|",
    ]
    for t, d in holdings.items():
        catalyst = catalysts.get(t, "")
        target_str = f"{d['target']:>17,.2f}" if d["target"] is not None else f"{'TBD':>17}"
        lines.append(
            f"| {t:<20}| {d['qty']:<4}| {d['cost']:>15,.2f} | {d['sl']:>23,.2f} "
            f"| {target_str} | {catalyst} |"
        )
    lines += [
        "",
        "## Pending GTT Limit Buys",
        "",
        "| Ticker (NSE)       | GTT Limit (₹) | Target Price (₹) | Key Catalyst |",
        "|---------------------|----------------|-------------------|--------------|",
    ]
    for t, d in gtt.items():
        catalyst = catalysts.get(f"GTT_{t}", "")
        lines.append(
            f"| {t:<20}| {d['gtt']:>14,.2f} | {d['target']:>17,.2f} | {catalyst} |"
        )
    lines += [
        "",
        "## Benchmark Indices",
        "",
        "- Nifty 50",
        "- Nifty 500",
        "",
        "## Risk Parameters",
        "",
        "- Valid breakout volume threshold: 1.5x of 20-day average (ideal: 2x+)",
        "- RSI overbought warning: daily RSI > 70 (avoid new entries)",
        "- Minimum risk-reward for new entry: mathematically optimal only at/below GTT levels",
        "",
    ]
    PORTFOLIO_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"  [OK] portfolio-data.md updated")


# ── Catalyst loader ───────────────────────────────────────────────────────────

def load_catalysts() -> dict:
    """Extract catalyst text from existing portfolio-data.md."""
    catalysts = {}
    if not PORTFOLIO_MD.exists():
        return catalysts
    for line in PORTFOLIO_MD.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or "Ticker" in line or "---" in line:
            continue
        parts = [p.strip() for p in line.strip().strip("|").split("|")]
        if len(parts) >= 5:
            ticker = parts[0].strip()
            catalyst = parts[-1].strip()
            if catalyst:
                catalysts[ticker] = catalyst
    return catalysts


# ── Actions ───────────────────────────────────────────────────────────────────

def do_buy(holdings, ticker, qty, price, sl, target, catalyst, catalysts):
    if ticker in holdings:
        h = holdings[ticker]
        old_total = h["cost"] * h["qty"]
        new_total = price * qty
        new_qty = h["qty"] + qty
        avg_cost = (old_total + new_total) / new_qty
        holdings[ticker] = {"qty": new_qty, "cost": round(avg_cost, 2),
                            "sl": sl if sl > 0 else h["sl"],
                            "target": target if target > 0 else h["target"]}
        print(f"  Accumulated {ticker}: {h['qty']} + {qty} = {new_qty} @ avg ₹{avg_cost:.2f}")
    else:
        if sl <= 0 or target <= 0:
            sys.exit(f"ERROR: sl and target required for new buy of {ticker}")
        holdings[ticker] = {"qty": qty, "cost": price, "sl": sl, "target": target}
        print(f"  New holding {ticker}: {qty} shares @ ₹{price} | SL ₹{sl} | Target ₹{target}")
    if catalyst:
        catalysts[ticker] = catalyst


def do_sell(holdings, ticker, qty, catalysts):
    if ticker not in holdings:
        sys.exit(f"ERROR: {ticker} not in holdings")
    held = holdings[ticker]["qty"]
    if qty <= 0 or qty >= held:
        del holdings[ticker]
        catalysts.pop(ticker, None)
        print(f"  Full exit: removed {ticker}")
    else:
        holdings[ticker]["qty"] = held - qty
        print(f"  Partial exit {ticker}: {held} -> {held - qty} shares remaining")


def do_update_sl(holdings, ticker, sl):
    if ticker not in holdings:
        sys.exit(f"ERROR: {ticker} not in holdings")
    old_sl = holdings[ticker]["sl"]
    holdings[ticker]["sl"] = sl
    print(f"  SL updated {ticker}: ₹{old_sl} -> ₹{sl}")


def do_add_gtt(gtt, ticker, gtt_level, target, catalyst, catalysts):
    gtt[ticker] = {"gtt": gtt_level, "target": target}
    if catalyst:
        catalysts[f"GTT_{ticker}"] = catalyst
    print(f"  GTT added {ticker}: ₹{gtt_level} | Target ₹{target}")


def do_remove_gtt(gtt, ticker, catalysts):
    if ticker not in gtt:
        sys.exit(f"ERROR: {ticker} not in GTT orders")
    del gtt[ticker]
    catalysts.pop(f"GTT_{ticker}", None)
    print(f"  GTT removed {ticker}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--action",   required=True,
                   choices=["buy", "sell", "update_sl", "add_gtt", "remove_gtt"])
    p.add_argument("--ticker",   required=True, type=str.upper)
    p.add_argument("--qty",      type=float, default=0)
    p.add_argument("--price",    type=float, default=0)
    p.add_argument("--sl",       type=float, default=0)
    p.add_argument("--target",   type=float, default=0)
    p.add_argument("--catalyst", type=str,   default="")
    args = p.parse_args()

    print(f"\n=== Portfolio Update: {args.action.upper()} {args.ticker} ===\n")

    holdings  = load_dict(COCKPIT_PY, "HOLDINGS")
    gtt       = load_dict(COCKPIT_PY, "GTT_ORDERS")
    catalysts = load_catalysts()

    if args.action == "buy":
        do_buy(holdings, args.ticker, int(args.qty), args.price,
               args.sl, args.target, args.catalyst, catalysts)
    elif args.action == "sell":
        do_sell(holdings, args.ticker, int(args.qty), catalysts)
    elif args.action == "update_sl":
        do_update_sl(holdings, args.ticker, args.sl)
    elif args.action == "add_gtt":
        do_add_gtt(gtt, args.ticker, args.price, args.target, args.catalyst, catalysts)
    elif args.action == "remove_gtt":
        do_remove_gtt(gtt, args.ticker, catalysts)

    print()
    replace_dict(COCKPIT_PY,  "HOLDINGS",   fmt_holdings(holdings))
    replace_dict(COCKPIT_PY,  "GTT_ORDERS", fmt_gtt(gtt))
    replace_dict(FETCH_PY,    "HOLDINGS",   fmt_holdings(holdings))
    replace_dict(FETCH_PY,    "GTT_ORDERS", fmt_gtt(gtt))
    write_portfolio_md(holdings, gtt, catalysts)

    print(f"\nDone. All 3 files updated for {args.action.upper()} {args.ticker}.")


if __name__ == "__main__":
    main()
