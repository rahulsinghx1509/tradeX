"""Load instruments and historical candles into the database.

Usage (from the repository root, with the virtual environment active):
    python scripts/backfill_candles.py                 # everything in .env defaults
    python scripts/backfill_candles.py --days-1d 30 --days-1h 10
    python scripts/backfill_candles.py --symbols NIFTY

Safe to run again and again: existing candles are updated, never duplicated.
"""

import argparse
import sys

import _bootstrap  # noqa: F401

from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.providers.factory import get_provider
from app.services.ingestion import backfill, sync_instruments


def main() -> int:
    s = get_settings()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--symbols", default=",".join(s.underlying_list))
    p.add_argument("--timeframes", default=",".join(s.timeframe_list))
    p.add_argument("--days-1d", type=int, default=s.backfill_days_1d)
    p.add_argument("--days-1h", type=int, default=s.backfill_days_1h)
    args = p.parse_args()

    provider = get_provider(s)
    symbols = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
    if provider.is_sample:
        print("NOTE: DATA_PROVIDER=sample -> loading SYNTHETIC sample data, not real prices.\n")

    failures = 0
    with get_sessionmaker()() as session:
        r = sync_instruments(session, provider, symbols, s.futures_expiries_to_track)
        print(f"Instruments: {r.status} ({r.rows_written} rows){' - ' + r.error if r.error else ''}")
        if r.status != "SUCCESS":
            return 1
        # 1h first: daily candles missing an open take it from the day's first 1h candle.
        timeframes = sorted({t.strip() for t in args.timeframes.split(",") if t.strip()},
                            key=lambda t: t != "1h")
        for tf in timeframes:
            days = args.days_1d if tf == "1d" else args.days_1h
            for res in backfill(session, provider, tf, days, symbols):
                warn = [c.check_name for c in res.checks if c.status != "PASS"]
                print(f"  {tf:>2} run {res.run_id:>4}: {res.status:<7} received={res.rows_received:<6}"
                      f" stored={res.rows_written:<6} checks_not_pass={warn or '-'}"
                      f"{' ERROR: ' + res.error if res.error else ''}")
                failures += res.status != "SUCCESS"
    print("\nDone." if not failures else f"\n{failures} load(s) failed - see messages above.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
