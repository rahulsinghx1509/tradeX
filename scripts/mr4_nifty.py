"""MR-4N: NIFTY-only gap fill with bigger gaps, options vs futures, leave-one-year-out test.

Reads only the database (run `mr4-load` first; it also fetches the option legs these rows need).
Writes reports/mr4_nifty.md and prints a one-screen summary. Backtest only, no orders.
Rules: backend/app/meanrev/nifty.py.

Usage (from the repository root):  python scripts/mr4_nifty.py [--from 2023-11-01 --to 2026-10-31]
On the server: ~/tradex/deploy/tradex.sh mr4-nifty
"""

import argparse
import sys
from datetime import date
from pathlib import Path

import _bootstrap  # noqa: F401

from app.core.config import REPO_ROOT
from app.db.session import get_sessionmaker
from app.meanrev import FIRST_DAY, FUT_SYMBOLS, LAST_DAY
from app.meanrev import data as D
from app.meanrev import nifty as N

REPORTS = Path(REPO_ROOT) / "reports"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--from", dest="start", help=f"first day (default {FIRST_DAY})")
    p.add_argument("--to", dest="end", help=f"last day (default {LAST_DAY})")
    args = p.parse_args()
    start = date.fromisoformat(args.start) if args.start else FIRST_DAY
    end = date.fromisoformat(args.end) if args.end else LAST_DAY
    with get_sessionmaker()() as session:
        inp = D.load_inputs(session, start, end, ("NIFTY",))
        if "NIFTY" not in inp.indices:
            print("No NIFTY spot data stored. Run ~/tradex/deploy/tradex.sh mr4-load first.")
            return 1
        ix = inp.indices["NIFTY"]
        atr = N.atr_pct_before(N.daily_hlc(D.stored_daily_frame(session, "NIFTY"), ix.spot))
        fut = N.fut_minutes_by_day(D.futures_ohlc(session, start, end, FUT_SYMBOLS["NIFTY"]))
    print("Running 4 gap variants x options/futures (a minute or two)...")
    rows = N.run_rows(inp, atr, fut)
    REPORTS.mkdir(exist_ok=True)
    days = [d for d in atr if start <= d <= end]
    (REPORTS / "mr4_nifty.md").write_text(N.markdown(inp, rows, len(days), len(fut)),
                                          encoding="utf-8")
    print(N.summary(inp, rows))
    print("\nFull report: ~/tradex/reports/mr4_nifty.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
