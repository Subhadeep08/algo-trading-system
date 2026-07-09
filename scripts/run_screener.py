#!/usr/bin/env python3
"""Entry point for the PMS candidate screener. Called by GitHub Actions."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cockpit.screener import screen

def main() -> None:
    portfolio_value = float(os.environ.get("PORTFOLIO_VALUE_INR", "0"))
    screen(portfolio_value_inr=portfolio_value)

if __name__ == "__main__":
    main()
