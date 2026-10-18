"""Load NSE's free daily F&O file ("bhavcopy") for past years into the fo_daily table.

Needs no credentials. Keeps only our underlyings (UNDERLYINGS in .env): every future, and
options of the nearest expiries near the underlying price (see services/nse_bhavcopy.py).
One file per trading day, one request per second: about 1-2 hours for 2020 to today.
Safe to stop and run again: days already loaded are skipped. The collector loads each new
day by itself from 19:00 IST, so this is only needed once.

Usage (from the repository root):
    python scripts/load_fo_history.py
    python scripts/load_fo_history.py --from 2023-01-01 --to 2023-12-31
On the server: deploy/tradex.sh fo-history
"""

import argparse
import sys
from collections import Counter
from datetime import UTC, date, datetime

import _bootstrap  # noqa: F401
import httpx

from app.core.config import get_settings
from app.core.market_time import to_ist
from app.db.session import get_sessionmaker
from app.services import nse_bhavcopy as nb


def main() -> int:
    s = get_settings()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--from", dest="start", default="2020-01-01")
    p.add_argument("--to", dest="end", default=to_ist(datetime.now(UTC)).date().isoformat())
    p.add_argument("--symbols", default=",".join(s.underlying_list))
    args = p.parse_args()
    symbols = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    print(f"Loading NSE F&O files {start} to {end} for {len(symbols)} underlyings. "
          "This can take 1-2 hours; it is safe to stop (Ctrl+C) and re-run.")

    def show(r: nb.DayResult) -> None:
        if r.status == "LOADED" and r.trade_date.day <= 7 and r.trade_date.weekday() == 0:
            print(f"  {r.trade_date} loaded ({r.file_format}, {r.rows} rows)")  # ~ once a month
        elif r.status in ("FAILED", "NO_FILE"):
            print(f"  {r.trade_date} {r.status}{' ' + r.error if r.error else ''}")

    with httpx.Client() as client, get_sessionmaker()() as session:
        results = nb.load(session, client, symbols, start, end, show)

    c = Counter(r.status for r in results)
    print("\nSummary (days): " + ", ".join(f"{k} {v}" for k, v in sorted(c.items())))
    print("NO_FILE days are usually holidays missing from our holiday list.")
    failed = c.get("FAILED", 0)
    print("\nDone." if not failed else f"\n{failed} day(s) failed - run the command again to retry them.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
