"""Load NSE's free daily participant-wise open interest file for past years (FII/DII positioning).

Needs no credentials. One small file per trading day, one request per second: about 30 minutes
for 2020 to today. Safe to stop and run again: days already loaded are skipped. The collector
loads each new day by itself in the evening, so this is only needed once.

Usage (from the repository root):
    python scripts/load_participant_history.py
    python scripts/load_participant_history.py --from 2023-01-01 --to 2023-12-31
On the server: deploy/tradex.sh participant-history
"""

import argparse
import sys
from collections import Counter
from datetime import UTC, date, datetime

import _bootstrap  # noqa: F401
import httpx

from app.core.market_time import to_ist
from app.db.session import get_sessionmaker
from app.services import participant_oi as po
from app.services.nse_bhavcopy import DayResult


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--from", dest="start", default="2020-01-01")
    p.add_argument("--to", dest="end", default=to_ist(datetime.now(UTC)).date().isoformat())
    args = p.parse_args()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    print(f"Loading NSE participant OI files {start} to {end}. "
          "About 30 minutes; it is safe to stop (Ctrl+C) and re-run.")
    failed_shown = 0

    def show(r: DayResult) -> None:
        nonlocal failed_shown
        if r.status == "LOADED" and r.trade_date.day <= 7 and r.trade_date.weekday() == 0:
            print(f"  {r.trade_date} loaded")  # about once a month
        elif r.status == "NO_FILE":
            print(f"  {r.trade_date} NO_FILE")
        elif r.status == "FAILED" and failed_shown < 5:
            failed_shown += 1
            print(f"  {r.trade_date} FAILED {r.error}")

    with httpx.Client() as client, get_sessionmaker()() as session:
        results = po.load(session, client, start, end, show)

    c = Counter(r.status for r in results)
    print("\nSummary (days): " + ", ".join(f"{k} {v}" for k, v in sorted(c.items())))
    print("NO_FILE days are usually holidays missing from our holiday list.")
    failed = c.get("FAILED", 0)
    print("\nDone." if not failed else f"\n{failed} day(s) failed - run the command again to retry them.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
