"""Run the 9/21 EMA NIFTY options backtest on the stored 1-minute data.

Runs 5, 15 and 30-minute bars x the three modes (deep ITM buy, ATM buy, ATM sell) with the
chosen entry window, and writes reports/ema921_backtest.md plus every trade to
reports/ema921_trades.csv. Needs no Groww access (load the data first with ema921_load.py).

Usage (from the repository root):
    python scripts/ema921_backtest.py
    python scripts/ema921_backtest.py --start 10:00 --cutoff 13:00 --cross 1
    python scripts/ema921_backtest.py --years 3 --slippage-points 1
On the server: deploy/tradex.sh ema-backtest [options]
"""

import argparse
import sys
from datetime import date, time
from pathlib import Path

import _bootstrap  # noqa: F401

from app.core.config import REPO_ROOT
from app.db.session import get_sessionmaker
from app.ema921 import engine as E
from app.ema921 import loader as L
from app.ema921 import report as R
from app.ema921 import store as S


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--years", type=int, default=2)
    p.add_argument("--from", dest="start_day")
    p.add_argument("--to", dest="end_day")
    p.add_argument("--start", default="09:30", help="entry window start: 09:30, 10:00 or 10:30")
    p.add_argument("--cutoff", default="14:30", help="entry cutoff: 11:30 ... 14:30")
    p.add_argument("--cross", default="", help="only enter on these crosses of the day, e.g. 1 or 1,2")
    p.add_argument("--slippage-pct", type=float, default=0.5,
                   help="slippage per side as a percent of the price (0.5 means 0.5%%)")
    p.add_argument("--slippage-points", type=float, default=0.5, help="plus these points per side")
    p.add_argument("--capital", type=float, default=100_000)
    p.add_argument("--no-roll", action="store_true", help="trade the same-day expiry on expiry day")
    a = p.parse_args()
    start, end = L.default_range(L.ist_today(), a.years)
    start = date.fromisoformat(a.start_day) if a.start_day else start
    end = date.fromisoformat(a.end_day) if a.end_day else end
    crosses = frozenset(int(x) for x in a.cross.split(",") if x.strip()) or None
    base = dict(start=time.fromisoformat(a.start), cutoff=time.fromisoformat(a.cutoff),
                cross_numbers=crosses, slippage_pct=a.slippage_pct / 100,
                slippage_points=a.slippage_points, capital=a.capital,
                roll_on_expiry_day=not a.no_roll)

    with get_sessionmaker()() as session:
        spot = S.load_spot(session, start, end, L.WARMUP_CALENDAR_DAYS)
        if spot.empty:
            print("No NIFTY 1-minute data stored yet. Run deploy/tradex.sh ema-load first.")
            return 1
        options = S.DbOptionData(session)
        results = []
        for tf in (5, 15, 30):
            print(f"Backtesting {tf}-minute bars...")
            results.append(E.run(spot, options, start, end, E.Config(timeframe=tf, **base)))

    out = Path(REPO_ROOT) / "reports"
    out.mkdir(exist_ok=True)
    md = R.to_markdown(results, start, end)
    (out / "ema921_backtest.md").write_text(md, encoding="utf-8")
    n = R.write_csv(results, out / "ema921_trades.csv")
    print("\n" + md)
    print(f"Saved reports/ema921_backtest.md and {n} trades to reports/ema921_trades.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
