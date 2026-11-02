"""Backtest ORB-1.0 (opening-range breakout) and ET-1.0 (9/21 EMA + trend filter) on NIFTY and
BANKNIFTY options, and score each against the pass bar fixed before the run (docs/01 §22).

Rules: backend/app/orb/__init__.py and backend/app/ema921/trend.py. Writes
reports/orb_et_backtest.md and every trade to reports/orb_et_trades.csv. Needs no Groww access:
load first with `tradex.sh orb-load` and `tradex.sh ema-load` (add --underlying BANKNIFTY).

Usage (from the repository root):
    python scripts/orb_et_backtest.py                       # both underlyings, last 2 years
    python scripts/orb_et_backtest.py --underlying NIFTY
    python scripts/orb_et_backtest.py --slippage-pct 0 --slippage-points 0   # charges only
On the server: deploy/tradex.sh orb-et-backtest [options]
"""

import argparse
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import _bootstrap  # noqa: F401

from app.core.config import REPO_ROOT
from app.db.session import get_sessionmaker
from app.ema921 import engine as E
from app.ema921 import loader as L
from app.ema921 import report as R
from app.ema921 import store as S
from app.ema921 import trend as T
from app.ema921.metrics import passes_dev_bar
from app.orb import engine as O

HOLDOUT = (date(2023, 11, 1), date(2024, 10, 31))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--underlying", default="both", choices=["both", *S.UNDERLYINGS])
    p.add_argument("--years", type=int, default=2)
    p.add_argument("--from", dest="start_day")
    p.add_argument("--to", dest="end_day")
    p.add_argument("--slippage-pct", type=float, default=0.5,
                   help="slippage per side as a percent of the price (0.5 means 0.5%%)")
    p.add_argument("--slippage-points", type=float, default=0.5, help="plus these points per side")
    a = p.parse_args()
    start, end = L.default_range(L.ist_today(), a.years)
    start = date.fromisoformat(a.start_day) if a.start_day else start
    end = date.fromisoformat(a.end_day) if a.end_day else end
    unders = list(S.UNDERLYINGS) if a.underlying == "both" else [a.underlying]
    default_slip = a.slippage_pct == 0.5 and a.slippage_points == 0.5
    holdout = start <= HOLDOUT[1] and end >= HOLDOUT[0]

    parts, csv_results, csv_labels, verdicts = [], [], [], []
    with get_sessionmaker()() as session:
        for u in unders:
            spot = S.load_spot(session, start, end, L.WARMUP_CALENDAR_DAYS, u)
            if spot.empty:
                print(f"{u}: no 1-minute spot data stored. Run deploy/tradex.sh ema-load "
                      f"--underlying {u} and orb-load --underlying {u} first.")
                continue
            opts = S.DbOptionData(session, u)
            base = L.config_for(u, slippage_pct=a.slippage_pct / 100,
                                slippage_points=a.slippage_points)
            print(f"{u}: ORB-1.0...")
            orb = O.run(spot, opts, start, end, base)
            print(f"{u}: ET-1.0...")
            et_cfg = replace(base, timeframe=T.SIGNAL_TIMEFRAME, start=T.ENTRY_START,
                             cutoff=T.ENTRY_CUTOFF, stop_atr_mult=None, trend_filter=True)
            et = E.run(spot, opts, start, end, et_cfg)
            labels = [f"{u} ORB", f"{u} ET"]
            parts.append(R.to_markdown([orb, et], start, end,
                                       title=f"{u}: ORB-1.0 and ET-1.0 options backtest",
                                       labels=labels,
                                       rules="Rules: backend/app/orb/__init__.py (ORB-1.0), "
                                             "backend/app/ema921/trend.py (ET-1.0)"))
            csv_results += [orb, et]
            csv_labels += labels
            for name, res in zip(labels, (orb, et), strict=True):
                for mode, trades in res.trades.items():
                    ok, why = passes_dev_bar(trades, res.config.capital, start)
                    verdicts.append((name, E.MODE_LABELS[mode], ok, why))

    if not parts:
        return 1
    head = ["# ORB-1.0 and ET-1.0: pass bar", "",
            "Bar fixed 2026-11-02 before any run: net > 0, profit factor >= 1.10, each year net > 0, "
            "max drawdown < 50% of ₹1L, at the default slippage (0.5% + 0.5 pt a side) on the 2 "
            "development years.", ""]
    if not default_slip:
        head += ["**This run does not use the default slippage, so the pass bar does not apply.**", ""]
    if holdout:
        head += ["**This range touches the held-back year (Nov 2023 - Oct 2024).** Use it once, "
                 "only for the single best mode that passed on the development years.", ""]
    head += ["| Strategy | Mode | Pass | Numbers |", "|---|---|---|---|"]
    head += [f"| {n} | {m} | {'PASS' if ok and default_slip else 'fail' if default_slip else 'n/a'} | "
             f"{why} |" for n, m, ok, why in verdicts]
    head += ["", "12 strategy x underlying x mode rows are scored, so one PASS can be luck: that is "
             "why only the best one gets the single held-back-year check.", ""]
    md = "\n".join(head) + "\n" + "\n".join(parts)
    out = Path(REPO_ROOT) / "reports"
    out.mkdir(exist_ok=True)
    (out / "orb_et_backtest.md").write_text(md, encoding="utf-8")
    n = R.write_csv(csv_results, out / "orb_et_trades.csv", csv_labels)
    print("\n" + md)
    print(f"Saved reports/orb_et_backtest.md and {n} trades to reports/orb_et_trades.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
