"""Backtest SP-1.0 (credit spreads, range-day iron condor, debit spreads) and TP-1.0 (trend-
pullback option buying) on NIFTY options, scored on the pass bar fixed before the run
(backend/app/spreads/__init__.py, backend/app/pullback/__init__.py, docs/01 §26).

Needs no Groww access: load first with `tradex.sh fut-load` and `tradex.sh spread-load`.
Writes reports/spread_backtest.md and every position to reports/spread_trades.csv.

Usage (from the repository root):
    python scripts/spread_backtest.py                         # the 2 development years
    python scripts/spread_backtest.py --holdout "CS-150"      # ONCE, the best passing row
On the server: deploy/tradex.sh spread-backtest [--holdout ROW]
"""

import argparse
import sys
from datetime import date
from pathlib import Path

import _bootstrap  # noqa: F401

from app.core.config import REPO_ROOT
from app.db.session import get_sessionmaker
from app.ema921 import loader as L
from app.ema921 import store as S
from app.pullback import engine as TP
from app.spreads import MIN_TRADES
from app.spreads import engine as E
from app.spreads import report as R
from app.volume import futures as VF

HOLDOUT = (date(2023, 11, 1), date(2024, 10, 31))
REPORTS = Path(REPO_ROOT) / "reports"
MARKER = REPORTS / "spread_holdout_used.txt"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--years", type=int, default=2)
    p.add_argument("--holdout", help='run the held-back year ONCE for this row, e.g. "CS-150"')
    p.add_argument("--slippage-pct", type=float, default=0.5)
    p.add_argument("--slippage-points", type=float, default=0.5)
    a = p.parse_args()
    start, end = L.default_range(L.ist_today(), a.years)
    if a.holdout:
        if MARKER.exists():
            print(f"The held-back year was already used ({MARKER.read_text().strip()}). "
                  "It is run once only.")
            return 1
        start, end = HOLDOUT
    default_slip = a.slippage_pct == 0.5 and a.slippage_points == 0.5
    with get_sessionmaker()() as session:
        spot = S.load_spot(session, start, end, L.WARMUP_CALENDAR_DAYS, "NIFTY")
        if spot.empty:
            print("No 1-minute NIFTY spot data stored. Run deploy/tradex.sh spread-load first.")
            return 1
        # from VF-1.0's history start: the 20-day RVOL average needs the days before `start`
        fut_hist = TP.load_futures(session, VF.history_start(start), end)
        opts = S.DbOptionData(session, "NIFTY")
        base = L.config_for("NIFTY", slippage_pct=a.slippage_pct / 100,
                            slippage_points=a.slippage_points)
        vix = R.vix_closes(session)
        if vix is None or vix.empty:
            print("No India VIX history stored: the range-day condor will have no trades. "
                  "Run deploy/tradex.sh vix-study once to load it.")
        rows, facts = R.build_rows(spot, fut_hist, opts, start, end, base, vix)

    REPORTS.mkdir(exist_ok=True)
    capital = base.capital
    if a.holdout:
        return holdout(a.holdout, rows, start, end, capital)

    md = R.to_markdown(rows, facts, start, end, capital, default_slip,
                       "SP-1.0 spreads and TP-1.0 trend pullback: NIFTY options backtest")
    passed = [r for r in rows if r.judged and default_slip
              and E.passes(r.positions, capital, start, MIN_TRADES)]
    if passed:
        best = max(passed, key=lambda r: E.summarize(r.positions, capital, start).profit_factor or 0)
        md += (f"\nBest passing row: **{best.name}**. Next, once only: "
               f'`deploy/tradex.sh spread-backtest --holdout "{best.name}"`\n')
    elif default_slip:
        md += "\n**No judged row passed.** Nothing goes to the held-back year.\n"
    (REPORTS / "spread_backtest.md").write_text(md, encoding="utf-8")
    n = R.write_csv(rows, REPORTS / "spread_trades.csv")
    print("\n" + md)
    print(f"Saved reports/spread_backtest.md and {n} positions to reports/spread_trades.csv")
    return 0


def holdout(name: str, rows, start, end, capital) -> int:
    row = next((r for r in rows if r.name == name.strip() and r.strategy != "comparison"), None)
    if row is None:
        print(f'Unknown row "{name}". Use the exact name from reports/spread_backtest.md.')
        return 1
    s = E.summarize(row.positions, capital, start)
    ok = bool(row.positions) and s.net_pnl > 0 and (s.profit_factor or 0) >= 1.05
    MARKER.write_text(f"{name} on {date.today()}: {'PASS' if ok else 'FAIL'}\n", encoding="utf-8")
    text = (f"# Held-back year ({start} to {end}), row {name}\n\n"
            f"Trades {s.trades}, net {R.rs(s.net_pnl)}, PF {s.profit_factor}, max DD "
            f"{s.max_drawdown_pct}%. Bar: net > 0 and PF >= 1.05 → **{'PASS' if ok else 'FAIL'}**.\n")
    (REPORTS / "spread_holdout.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
