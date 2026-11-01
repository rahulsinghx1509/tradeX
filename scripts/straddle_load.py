"""Load the 1-minute option candles for the ST-1.0 09:20 hedged short straddle backtest.

Four legs a day (ATM CE + PE, wings 500 points out). NIFTY spot and expiries are shared with the
9/21 EMA loader, so days already loaded there are skipped. Needs Groww credentials in .env.
Read-only: no orders. Safe to stop (Ctrl+C) and run again.

Usage (from the repository root):
    python scripts/straddle_load.py                # the last 2 years
    python scripts/straddle_load.py --years 3
On the server: deploy/tradex.sh straddle-load [--years 3]
"""

import argparse
import sys
from datetime import date

import _bootstrap  # noqa: F401

from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.ema921 import loader as EL
from app.providers.base import ProviderError
from app.providers.groww import GrowwProvider
from app.straddle import loader as L


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--years", type=int, default=2, help="how many years back (default 2)")
    p.add_argument("--from", dest="start", help="first day, YYYY-MM-DD (overrides --years)")
    p.add_argument("--to", dest="end", help="last day, YYYY-MM-DD (default yesterday)")
    args = p.parse_args()
    start, end = EL.default_range(EL.ist_today(), args.years)
    start = date.fromisoformat(args.start) if args.start else start
    end = date.fromisoformat(args.end) if args.end else end

    provider = GrowwProvider(get_settings())
    try:
        provider._connect()
    except ProviderError as exc:
        print(f"Cannot reach Groww: {exc}")
        return 1
    print(f"Loading straddle legs {start} to {end}. Safe to stop and re-run.")
    with get_sessionmaker()() as session:
        rep = L.load_all(session, provider.client(), start, end)
    print(f"\nSpot days loaded {rep.spot_days} (empty {rep.spot_empty}); expiries {rep.expiries}; "
          f"option contract-days needed {rep.contracts_needed}, loaded {rep.contract_days_fetched} "
          f"(empty {rep.contract_days_empty}).")
    if rep.errors:
        print(f"\n{len(rep.errors)} error(s); run the command again to retry. First few:")
        for e in rep.errors[:10]:
            print("  " + e)
        return 1
    print("Done. Next: deploy/tradex.sh straddle-backtest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
