#!/usr/bin/env python3
"""Entry point for the PMS candidate screener. Called by GitHub Actions."""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cockpit.screener import screen


def main() -> None:
    parser = argparse.ArgumentParser(description="PMS candidate screener")
    parser.add_argument(
        "--tickers",
        default="",
        help="Comma-separated NSE tickers for one-off screening. Omit to use watchlist.md.",
    )
    args = parser.parse_args()

    portfolio_value = float(os.environ.get("PORTFOLIO_VALUE_INR", "0"))
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()] or None
    screen(portfolio_value_inr=portfolio_value, tickers=tickers)


if __name__ == "__main__":
    main()
