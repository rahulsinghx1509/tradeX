"""Load 1-minute NIFTY spot and NIFTY option history from Groww for the 9/21 EMA backtest.

Fetches only the option contracts the backtest can actually trade (deep ITM and ATM at each
EMA cross), not whole option chains. Needs Groww credentials in .env. Read-only: no orders.
Safe to stop (Ctrl+C) and run again: everything already stored is skipped, failures are retried.
2 years is roughly 500 spot days plus several thousand option-days: expect a few hours.

Usage (from the repository root):
    python scripts/ema921_load.py                 # the last 2 years
    python scripts/ema921_load.py --years 3
    python scripts/ema921_load.py --underlying BANKNIFTY
    python scripts/ema921_load.py --from 2025-01-01 --to 2025-03-31
On the server: deploy/tradex.sh ema-load [--years 3] [--underlying BANKNIFTY]
"""

import argparse
import sys
from datetime import date

import _bootstrap  # noqa: F401

from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.ema921 import loader as L
from app.providers.base import ProviderError
from app.providers.groww import GrowwProvider


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--underlying", default="NIFTY", choices=["NIFTY", "BANKNIFTY"],
                   help="index whose options to load (BANKNIFTY: monthly expiries, lot 30)")
    p.add_argument("--years", type=int, default=2, help="how many years back (default 2)")
    p.add_argument("--from", dest="start", help="first day, YYYY-MM-DD (overrides --years)")
    p.add_argument("--to", dest="end", help="last day, YYYY-MM-DD (default yesterday)")
    args = p.parse_args()
    start, end = L.default_range(L.ist_today(), args.years)
    start = date.fromisoformat(args.start) if args.start else start
    end = date.fromisoformat(args.end) if args.end else end

    provider = GrowwProvider(get_settings())
    try:
        provider._connect()
    except ProviderError as exc:
        print(f"Cannot reach Groww: {exc}")
        return 1
    print(f"Loading {args.underlying} 1-minute history {start} to {end}. Safe to stop and re-run.")
    with get_sessionmaker()() as session:
        rep = L.load_all(session, provider.client(), start, end, underlying=args.underlying)
    print(f"\nSpot days loaded {rep.spot_days} (empty {rep.spot_empty}); expiries {rep.expiries}; "
          f"option contract-days needed {rep.contracts_needed}, loaded {rep.contract_days_fetched} "
          f"(empty {rep.contract_days_empty}).")
    if rep.errors:
        print(f"\n{len(rep.errors)} error(s); run the command again to retry. First few:")
        for e in rep.errors[:10]:
            print("  " + e)
        return 1
    print("Done. Next: deploy/tradex.sh ema-backtest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
