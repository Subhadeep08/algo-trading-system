#!/usr/bin/env python3
"""Entry point for the portfolio trade updater. Called by GitHub Actions."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cockpit.updater import update

def main() -> None:
    p = argparse.ArgumentParser(description="Update NSE portfolio data")
    p.add_argument("--action",   required=True,
                   choices=["buy", "sell", "update_sl", "add_gtt", "remove_gtt"])
    p.add_argument("--ticker",   required=True, type=str.upper)
    p.add_argument("--qty",      type=float, default=0)
    p.add_argument("--price",    type=float, default=0)
    p.add_argument("--sl",       type=float, default=None)
    p.add_argument("--target",   type=float, default=None)
    p.add_argument("--catalyst", type=str,   default="")
    args = p.parse_args()

    kwargs = {}
    if args.action in ("buy",):
        kwargs = {"qty": int(args.qty), "price": args.price,
                  "sl": args.sl, "target": args.target, "catalyst": args.catalyst or None}
    elif args.action == "sell":
        kwargs = {"qty": int(args.qty)}
    elif args.action == "update_sl":
        kwargs = {"new_sl": args.sl}
    elif args.action == "add_gtt":
        kwargs = {"qty": int(args.qty), "gtt_price": args.price,
                  "target": args.target, "catalyst": args.catalyst or None}
    elif args.action == "remove_gtt":
        kwargs = {}

    update(args.action, args.ticker, **kwargs)

if __name__ == "__main__":
    main()
