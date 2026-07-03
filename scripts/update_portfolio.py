"""
Portfolio updater — called by the GitHub Actions update-portfolio workflow.
Atomically updates cockpit_runner.py, fetch_nse_prices.py, and portfolio-data.md.

Supported actions:
  buy        Add a new holding or accumulate into an existing one (weighted-average cost)
  sell       Reduce a holding's quantity, or fully exit (full exit when qty >= held)
  update_sl  Raise or lower the trailing stop-loss for an existing holding
  add_gtt    Register a new pending GTT limit-buy order
  remove_gtt Remove a GTT order after execution or cancellation
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from datetime import date
from pathlib import Path
from typing import Optional

REPO_ROOT      = Path(__file__).resolve().parent.parent
PORTFOLIO_MD   = REPO_ROOT / ".claude/skills/portfolio-cockpit/references/portfolio-data.md"
COCKPIT_SCRIPT = REPO_ROOT / "scripts/cockpit_runner.py"
PRICE_SCRIPT   = REPO_ROOT / "scripts/fetch_nse_prices.py"


# ── Source-file patching ──────────────────────────────────────────────────────

def _find_dict_block_bounds(file_content: str, variable_name: str) -> tuple[int, int]:
    """Return the (first_line_index, last_line_index) of a `variable_name = {...}` block."""
    lines = file_content.splitlines()
    start_line: Optional[int] = None
    for line_index, line_text in enumerate(lines):
        if re.match(rf"^{re.escape(variable_name)}\s*=\s*\{{", line_text):
            start_line = line_index
            break
    if start_line is None:
        raise ValueError(f"Variable '{variable_name}' not found in source file")
    brace_depth = 0
    end_line: Optional[int] = None
    for line_index in range(start_line, len(lines)):
        brace_depth += lines[line_index].count("{") - lines[line_index].count("}")
        if brace_depth == 0:
            end_line = line_index
            break
    if end_line is None:
        raise ValueError(f"Unmatched braces for '{variable_name}' — malformed source block")
    return start_line, end_line


def _parse_dict_from_source(source_file: Path, variable_name: str) -> dict:
    """Read and safely evaluate a dict literal assigned to `variable_name` in a Python file."""
    file_content = source_file.read_text(encoding="utf-8")
    start_line, end_line = _find_dict_block_bounds(file_content, variable_name)
    block_lines  = file_content.splitlines()[start_line:end_line + 1]
    block_source = "\n".join(block_lines)
    raw_dict_source = re.sub(rf"^{re.escape(variable_name)}\s*=\s*", "", block_source)
    return ast.literal_eval(raw_dict_source)


def _patch_dict_in_source(
    source_file: Path, variable_name: str, replacement_block: str
) -> None:
    """Replace the `variable_name = {...}` block in source_file with replacement_block."""
    file_content = source_file.read_text(encoding="utf-8")
    all_lines = file_content.splitlines()
    start_line, end_line = _find_dict_block_bounds(file_content, variable_name)
    updated_lines = (
        all_lines[:start_line]
        + replacement_block.splitlines()
        + all_lines[end_line + 1:]
    )
    source_file.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")
    print(f"  [OK] {source_file.name} — {variable_name} updated")


# ── Python source formatters ──────────────────────────────────────────────────

def _format_holdings_as_source_block(holdings: dict) -> str:
    """Render the holdings dict as a right-aligned Python dict literal."""
    ticker_column_width = max((len(ticker) for ticker in holdings), default=8) + 1
    rows = ["HOLDINGS = {"]
    for ticker_symbol, position_data in holdings.items():
        column_padding = " " * (ticker_column_width - len(ticker_symbol))
        target_value   = (
            f"{position_data['target']:.2f}"
            if position_data["target"] is not None
            else "None"
        )
        rows.append(
            f'    "{ticker_symbol}":{column_padding}'
            f'{{"qty": {position_data["qty"]}, "cost": {position_data["cost"]:.2f}, '
            f'"sl": {position_data["sl"]:.2f}, "target": {target_value}}},'
        )
    rows.append("}")
    return "\n".join(rows)


def _format_gtt_orders_as_source_block(gtt_orders: dict) -> str:
    """Render the GTT orders dict as a right-aligned Python dict literal."""
    ticker_column_width = max((len(ticker) for ticker in gtt_orders), default=8) + 1
    rows = ["GTT_ORDERS = {"]
    for ticker_symbol, order_data in gtt_orders.items():
        column_padding = " " * (ticker_column_width - len(ticker_symbol))
        rows.append(
            f'    "{ticker_symbol}":{column_padding}'
            f'{{"gtt": {order_data["gtt"]:.2f}, "target": {order_data["target"]:.2f}}},'
        )
    rows.append("}")
    return "\n".join(rows)


# ── Markdown writer ───────────────────────────────────────────────────────────

def _write_portfolio_markdown(
    holdings: dict,
    gtt_orders: dict,
    catalyst_notes: dict[str, str],
) -> None:
    """Regenerate portfolio-data.md from the current holdings and GTT state."""
    today_label = date.today().strftime("%B %d, %Y")
    lines = [
        f"# Portfolio Data — Last Updated: {today_label}",
        "",
        "Update this file whenever holdings, stop-losses, GTT levels, or targets change.",
        "",
        "## Active Holdings",
        "",
        "| Ticker (NSE)       | Qty | Cost Price (₹) | Trailing Stop-Loss (₹) | Target Price (₹) | Key Catalyst |",
        "|---------------------|-----|-----------------|-------------------------|-------------------|--------------|",
    ]
    for ticker_symbol, position_data in holdings.items():
        catalyst_text = catalyst_notes.get(ticker_symbol, "")
        target_cell   = (
            f"{position_data['target']:>17,.2f}"
            if position_data["target"] is not None
            else f"{'TBD':>17}"
        )
        lines.append(
            f"| {ticker_symbol:<20}| {position_data['qty']:<4}"
            f"| {position_data['cost']:>15,.2f} | {position_data['sl']:>23,.2f} "
            f"| {target_cell} | {catalyst_text} |"
        )
    lines += [
        "",
        "## Pending GTT Limit Buys",
        "",
        "| Ticker (NSE)       | GTT Limit (₹) | Target Price (₹) | Key Catalyst |",
        "|---------------------|----------------|-------------------|--------------|",
    ]
    for ticker_symbol, order_data in gtt_orders.items():
        catalyst_text = catalyst_notes.get(f"GTT_{ticker_symbol}", "")
        lines.append(
            f"| {ticker_symbol:<20}| {order_data['gtt']:>14,.2f} "
            f"| {order_data['target']:>17,.2f} | {catalyst_text} |"
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
    print("  [OK] portfolio-data.md updated")


# ── Catalyst notes extractor ──────────────────────────────────────────────────

def _load_catalyst_notes_from_markdown() -> dict[str, str]:
    """Parse the Key Catalyst column from portfolio-data.md, keyed by ticker symbol."""
    catalyst_notes: dict[str, str] = {}
    if not PORTFOLIO_MD.exists():
        return catalyst_notes
    for raw_line in PORTFOLIO_MD.read_text(encoding="utf-8").splitlines():
        if not raw_line.startswith("|") or "Ticker" in raw_line or "---" in raw_line:
            continue
        table_columns = [col.strip() for col in raw_line.strip().strip("|").split("|")]
        if len(table_columns) >= 5:
            ticker_symbol = table_columns[0].strip()
            catalyst_text = table_columns[-1].strip()
            if catalyst_text:
                catalyst_notes[ticker_symbol] = catalyst_text
    return catalyst_notes


# ── Trade actions ─────────────────────────────────────────────────────────────

def _execute_buy(
    holdings: dict,
    ticker_symbol: str,
    share_count: int,
    purchase_price: float,
    stop_loss_price: float,
    target_price: float,
    catalyst_text: str,
    catalyst_notes: dict[str, str],
) -> None:
    if ticker_symbol in holdings:
        existing_position   = holdings[ticker_symbol]
        existing_total_cost = existing_position["cost"] * existing_position["qty"]
        new_purchase_cost   = purchase_price * share_count
        combined_shares     = existing_position["qty"] + share_count
        weighted_avg_cost   = (existing_total_cost + new_purchase_cost) / combined_shares
        holdings[ticker_symbol] = {
            "qty":    combined_shares,
            "cost":   round(weighted_avg_cost, 2),
            "sl":     stop_loss_price if stop_loss_price > 0 else existing_position["sl"],
            "target": target_price    if target_price    > 0 else existing_position["target"],
        }
        print(
            f"  Accumulated {ticker_symbol}: "
            f"{existing_position['qty']} + {share_count} = {combined_shares} "
            f"@ weighted avg ₹{weighted_avg_cost:.2f}"
        )
    else:
        if stop_loss_price <= 0 or target_price <= 0:
            sys.exit(
                f"ERROR: stop_loss_price and target_price are required for a new buy of {ticker_symbol}"
            )
        holdings[ticker_symbol] = {
            "qty":    share_count,
            "cost":   purchase_price,
            "sl":     stop_loss_price,
            "target": target_price,
        }
        print(
            f"  New holding {ticker_symbol}: {share_count} shares @ ₹{purchase_price} "
            f"| SL ₹{stop_loss_price} | Target ₹{target_price}"
        )
    if catalyst_text:
        catalyst_notes[ticker_symbol] = catalyst_text


def _execute_sell(
    holdings: dict,
    ticker_symbol: str,
    shares_to_sell: int,
    catalyst_notes: dict[str, str],
) -> None:
    if ticker_symbol not in holdings:
        sys.exit(f"ERROR: {ticker_symbol} is not in active holdings")
    currently_held = holdings[ticker_symbol]["qty"]
    if shares_to_sell <= 0 or shares_to_sell >= currently_held:
        del holdings[ticker_symbol]
        catalyst_notes.pop(ticker_symbol, None)
        print(f"  Full exit: removed {ticker_symbol} from holdings")
    else:
        remaining_shares = currently_held - shares_to_sell
        holdings[ticker_symbol]["qty"] = remaining_shares
        print(f"  Partial exit {ticker_symbol}: {currently_held} → {remaining_shares} shares remaining")


def _execute_update_stop_loss(
    holdings: dict,
    ticker_symbol: str,
    new_stop_loss_price: float,
) -> None:
    if ticker_symbol not in holdings:
        sys.exit(f"ERROR: {ticker_symbol} is not in active holdings")
    previous_stop_loss = holdings[ticker_symbol]["sl"]
    holdings[ticker_symbol]["sl"] = new_stop_loss_price
    print(f"  SL updated {ticker_symbol}: ₹{previous_stop_loss} → ₹{new_stop_loss_price}")


def _execute_add_gtt_order(
    gtt_orders: dict,
    ticker_symbol: str,
    trigger_price: float,
    target_price: float,
    catalyst_text: str,
    catalyst_notes: dict[str, str],
) -> None:
    gtt_orders[ticker_symbol] = {"gtt": trigger_price, "target": target_price}
    if catalyst_text:
        catalyst_notes[f"GTT_{ticker_symbol}"] = catalyst_text
    print(f"  GTT added {ticker_symbol}: trigger ₹{trigger_price} | Target ₹{target_price}")


def _execute_remove_gtt_order(
    gtt_orders: dict,
    ticker_symbol: str,
    catalyst_notes: dict[str, str],
) -> None:
    if ticker_symbol not in gtt_orders:
        sys.exit(f"ERROR: {ticker_symbol} is not in GTT orders")
    del gtt_orders[ticker_symbol]
    catalyst_notes.pop(f"GTT_{ticker_symbol}", None)
    print(f"  GTT removed {ticker_symbol}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    arg_parser = argparse.ArgumentParser(
        description="Update portfolio holdings and GTT orders across all tracking files."
    )
    arg_parser.add_argument(
        "--action",
        required=True,
        choices=["buy", "sell", "update_sl", "add_gtt", "remove_gtt"],
    )
    arg_parser.add_argument("--ticker",   required=True, type=str.upper)
    arg_parser.add_argument("--qty",      type=float, default=0)
    arg_parser.add_argument("--price",    type=float, default=0)
    arg_parser.add_argument("--sl",       type=float, default=0)
    arg_parser.add_argument("--target",   type=float, default=0)
    arg_parser.add_argument("--catalyst", type=str,   default="")
    args = arg_parser.parse_args()

    print(f"\n=== Portfolio Update: {args.action.upper()} {args.ticker} ===\n")

    holdings       = _parse_dict_from_source(COCKPIT_SCRIPT, "HOLDINGS")
    gtt_orders     = _parse_dict_from_source(COCKPIT_SCRIPT, "GTT_ORDERS")
    catalyst_notes = _load_catalyst_notes_from_markdown()

    if args.action == "buy":
        _execute_buy(
            holdings, args.ticker, int(args.qty), args.price,
            args.sl, args.target, args.catalyst, catalyst_notes,
        )
    elif args.action == "sell":
        _execute_sell(holdings, args.ticker, int(args.qty), catalyst_notes)
    elif args.action == "update_sl":
        _execute_update_stop_loss(holdings, args.ticker, args.sl)
    elif args.action == "add_gtt":
        _execute_add_gtt_order(
            gtt_orders, args.ticker, args.price, args.target, args.catalyst, catalyst_notes
        )
    elif args.action == "remove_gtt":
        _execute_remove_gtt_order(gtt_orders, args.ticker, catalyst_notes)

    print()
    holdings_block    = _format_holdings_as_source_block(holdings)
    gtt_orders_block  = _format_gtt_orders_as_source_block(gtt_orders)

    _patch_dict_in_source(COCKPIT_SCRIPT, "HOLDINGS",   holdings_block)
    _patch_dict_in_source(COCKPIT_SCRIPT, "GTT_ORDERS", gtt_orders_block)
    _patch_dict_in_source(PRICE_SCRIPT,   "HOLDINGS",   holdings_block)
    _patch_dict_in_source(PRICE_SCRIPT,   "GTT_ORDERS", gtt_orders_block)
    _write_portfolio_markdown(holdings, gtt_orders, catalyst_notes)

    print(f"\nDone. All 3 files updated for {args.action.upper()} {args.ticker}.")


if __name__ == "__main__":
    main()
