"""RT-1.0: re-test the RL-1.0 rule (ET-1.0 ATM sell, no expiry days, 2% trailing stop, ₹3k day
loss / ₹6k day lock / ₹6k week loss) from Nov 2023 to yesterday, with a stress grid fixed before
the run (backend/app/spreads/retest.py, docs/01 §28). A re-check, not a new held-back test.

Read-only, no orders, no Groww calls: it uses the 1-minute data already stored. Run
`spread-load` first so the newest days are in. Writes reports/et_retest.md and
reports/et_retest_trades.csv (the rule's trades).

Usage (from the repository root):  python scripts/et_retest.py [--to YYYY-MM-DD]
On the server: deploy/tradex.sh et-retest
"""

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

import _bootstrap  # noqa: F401
import pandas as pd

from app.core.config import REPO_ROOT
from app.core.market_time import IST
from app.db.session import get_sessionmaker
from app.ema921 import loader as L
from app.ema921 import store as S
from app.spreads import retest as RT
from app.spreads import retest_report as RR
from app.spreads import risk as RK

REPORTS = Path(REPO_ROOT) / "reports"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--to", dest="end", help="last day, YYYY-MM-DD (default yesterday)")
    a = p.parse_args()
    start = RT.FIRST_DAY
    end = date.fromisoformat(a.end) if a.end else L.default_range(L.ist_today(), 1)[1]
    assert RT.level(2000.0).stop == RK.HOLDOUT_LEVEL.stop and RT.level(2000.0).trail
    with get_sessionmaker()() as session:
        spot = S.load_spot(session, start, end, L.WARMUP_CALENDAR_DAYS, "NIFTY")
        if spot.empty:
            print("No 1-minute NIFTY spot data stored. Run deploy/tradex.sh spread-load first.")
            return 1
        last_spot = pd.Timestamp(spot["time"].max()).tz_convert(IST).date()
        opts = S.DbOptionData(session, "NIFTY")
        text, kept = RR.build(spot, opts, start, end, L.config_for("NIFTY"), last_spot)
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "et_retest.md").write_text(text, encoding="utf-8")
    with open(REPORTS / "et_retest_trades.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["day", "entry_time", "option", "strike", "expiry", "entry_price", "exit_time",
                    "exit_price", "exit_reason", "gross", "slippage", "charges", "net"])
        for p in kept:
            t = p.legs[0]
            w.writerow([t.day, t.entry_time, t.option_type, t.strike, t.expiry, t.entry_price,
                        t.exit_time, t.exit_price, t.exit_reason, t.gross_pnl, t.slippage,
                        t.charges.get("total", 0.0), t.net_pnl])
    print("\n" + text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
