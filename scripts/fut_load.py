"""Load NIFTY near-month FUTURES 1-minute candles from Groww (for their volume, VF-1.0).

Needed by `volume-backtest` and by the volume line in the live PAPER entry alerts. Stores one
continuous near-month series (backend/app/volume/__init__.py, rule 1). Needs Groww credentials in
.env. Read-only: no orders. Safe to stop (Ctrl+C) and re-run: stored days are skipped, failed
days are retried. 3 years is about 740 days, one Groww call each: expect 30-60 minutes.

Whether Groww still serves 1-minute candles of EXPIRED futures is NOT_VERIFIED: the summary
below says how many days came back with candles. Many EMPTY days = not served that far back.

Usage (from the repository root):
    python scripts/fut_load.py               # last 3 years (2 tested years + the held-back year)
    python scripts/fut_load.py --years 1
On the server: deploy/tradex.sh fut-load [--years 1]
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
from app.volume import futures as VF


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--years", type=int, default=3, help="how many years back (default 3)")
    p.add_argument("--from", dest="start", help="first day, YYYY-MM-DD (overrides --years)")
    p.add_argument("--to", dest="end", help="last day, YYYY-MM-DD (default yesterday)")
    a = p.parse_args()
    start, end = L.default_range(L.ist_today(), a.years)
    start = date.fromisoformat(a.start) if a.start else start
    start = VF.history_start(start)  # the first days need 20 earlier days for their average
    end = date.fromisoformat(a.end) if a.end else end
    provider = GrowwProvider(get_settings())
    try:
        provider._connect()
    except ProviderError as exc:
        print(f"Cannot reach Groww: {exc}")
        return 1
    print(f"Loading NIFTY near-month futures 1-minute candles {start} to {end}. Safe to stop and re-run.")
    with get_sessionmaker()() as session:
        rep = VF.load(session, provider.client(), start, end)
    print(f"\nDays with candles {rep.days}, empty {rep.empty}, already stored {rep.skipped}, "
          f"no contract found {len(rep.no_contract)}.")
    if rep.no_contract:
        print(f"  no contract for e.g. {', '.join(str(d) for d in rep.no_contract[:5])}")
    if rep.errors:
        print(f"\n{len(rep.errors)} error(s); run the command again to retry. First few:")
        for e in rep.errors[:10]:
            print("  " + e)
        return 1
    print("Done. Next: deploy/tradex.sh volume-backtest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
