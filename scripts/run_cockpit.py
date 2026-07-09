#!/usr/bin/env python3
"""Entry point for the daily portfolio cockpit. Called by GitHub Actions."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cockpit.runner import run

def main() -> None:
    parser = argparse.ArgumentParser(description="NSE Portfolio Cockpit")
    parser.add_argument("--phase", type=int, choices=[1, 2, 3], required=True,
                        help="1=Pre-Market, 2=Mid-Market, 3=Post-Market")
    args = parser.parse_args()
    run(args.phase)

if __name__ == "__main__":
    main()
