"""MR-4 mean-reversion backtest on NIFTY + BANKNIFTY options, 3 years, with walk-forward.

Reads only the database (run `mr4-load` first). Writes reports/mr4_backtest.md and
reports/mr4_trades.csv. Backtest only, no orders. Rules: backend/app/meanrev/__init__.py.

Usage (from the repository root):  python scripts/mr4_backtest.py [--from 2023-11-01 --to 2026-10-31]
On the server: deploy/tradex.sh mr4-backtest
"""

import argparse
import sys
from datetime import date
from pathlib import Path

import _bootstrap  # noqa: F401

from app.core.config import REPO_ROOT
from app.db.session import get_sessionmaker
from app.meanrev import FIRST_DAY, LAST_DAY
from app.meanrev import data as D
from app.meanrev import report as R

REPORTS = Path(REPO_ROOT) / "reports"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--from", dest="start", help=f"first day (default {FIRST_DAY})")
    p.add_argument("--to", dest="end", help=f"last day (default {LAST_DAY})")
    args = p.parse_args()
    start = date.fromisoformat(args.start) if args.start else FIRST_DAY
    end = date.fromisoformat(args.end) if args.end else LAST_DAY
    with get_sessionmaker()() as session:
        inp = D.load_inputs(session, start, end)
        if not inp.indices:
            print("No NIFTY or BANKNIFTY spot data stored. Run deploy/tradex.sh mr4-load first.")
            return 1
        for line in D.coverage(inp):
            print(line)
        print("Running MR-4 rows and the walk-forward grid (a few minutes)...")
        res = R.run_all(inp)
    REPORTS.mkdir(exist_ok=True)
    text = R.markdown(res)
    (REPORTS / "mr4_backtest.md").write_text(text, encoding="utf-8")
    n = R.write_csv(res, REPORTS / "mr4_trades.csv")
    print("\n" + text)
    print(f"Trades CSV: reports/mr4_trades.csv ({n} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
