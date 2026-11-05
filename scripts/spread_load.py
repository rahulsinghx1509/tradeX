"""Load the 1-minute option candles for SP-1.0 (spreads, condor) and TP-1.0 (trend pullback).

NIFTY only. Spot and expiries are shared with `ema-load` (days already stored are skipped).
TP-1.0 needs the futures series from `fut-load` first. Needs Groww credentials in .env.
Read-only: no orders. Safe to stop (Ctrl+C) and run again.

Usage (from the repository root):
    python scripts/spread_load.py                 # Nov 2023 (held-back year) to yesterday
    python scripts/spread_load.py --from 2024-11-01
On the server: deploy/tradex.sh spread-load
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
from app.pullback import loader as PL
from app.spreads import loader as SL

FIRST_DAY = date(2023, 11, 1)  # start of the held-back year


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--from", dest="start", help="first day, YYYY-MM-DD (default 2023-11-01)")
    p.add_argument("--to", dest="end", help="last day, YYYY-MM-DD (default yesterday)")
    args = p.parse_args()
    start = date.fromisoformat(args.start) if args.start else FIRST_DAY
    end = date.fromisoformat(args.end) if args.end else EL.default_range(EL.ist_today(), 1)[1]

    provider = GrowwProvider(get_settings())
    try:
        provider._connect()
    except ProviderError as exc:
        print(f"Cannot reach Groww: {exc}")
        return 1
    print(f"Loading NIFTY spread + pullback legs {start} to {end}. Safe to stop and re-run.")
    errors = []
    with get_sessionmaker()() as session:
        for name, mod in (("SP-1.0 spreads", SL), ("TP-1.0 pullback", PL)):
            print(f"\n== {name} ==")
            rep = mod.load_all(session, provider.client(), start, end)
            print(f"{name}: option contract-days needed {rep.contracts_needed}, loaded "
                  f"{rep.contract_days_fetched} (empty {rep.contract_days_empty}).")
            errors += rep.errors
    if errors:
        print(f"\n{len(errors)} error(s); run the command again to retry. First few:")
        for e in errors[:10]:
            print("  " + e)
        return 1
    print("Done. Next: deploy/tradex.sh spread-backtest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
