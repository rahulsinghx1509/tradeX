"""RL-1.0 held-back-year test, ONCE: ET-1.0 ATM sell, no expiry-day trades, 2% trailing stop and
its day/week limits (rule fixed in backend/app/spreads/risk.py before this run; docs/01 §26).

Uses the held-back year Nov 2023 - Oct 2024, which no rule has been tuned on. Refused if the
held-back year was already used (shared marker with spread-backtest). Writes
reports/et_holdout.md. Needs the data from `spread-load` (it loads from Nov 2023).

Usage (from the repository root):  python scripts/et_holdout.py
On the server: deploy/tradex.sh et-holdout
"""

import sys
from datetime import date
from pathlib import Path

import _bootstrap  # noqa: F401

from app.core.config import REPO_ROOT
from app.db.session import get_sessionmaker
from app.ema921 import engine as EE
from app.ema921 import loader as L
from app.ema921 import store as S
from app.spreads import engine as E
from app.spreads import risk as RK
from app.spreads.report import rs

HOLDOUT = (date(2023, 11, 1), date(2024, 10, 31))
REPORTS = Path(REPO_ROOT) / "reports"
MARKER = REPORTS / "spread_holdout_used.txt"


def main() -> int:
    if MARKER.exists():
        print(f"The held-back year was already used ({MARKER.read_text().strip()}). It is run once only.")
        return 1
    start, end = HOLDOUT
    lv = RK.HOLDOUT_LEVEL
    with get_sessionmaker()() as session:
        spot = S.load_spot(session, start, end, L.WARMUP_CALENDAR_DAYS, "NIFTY")
        if spot.empty:
            print("No NIFTY spot data for the held-back year. Run deploy/tradex.sh spread-load first.")
            return 1
        opts = S.DbOptionData(session, "NIFTY")
        base = L.config_for("NIFTY")
        E.set_expiry_days(opts.expiries())
        print("ET ATM sell on the held-back year...")
        res = EE.run(spot, opts, start, end, E.et_config(base), (E.CS_SHORT,))
        trades = [t for t in res.trades[E.CS_SHORT] if t.exit_time is not None]
        trades = [RK.apply_exit_rule(t, EE._Minutes(opts.minutes(t.symbol, t.day)), lv, base)
                  for t in trades]
        ps = [p for p in E.single("ET ATM sell", trades) if not p.expiry_day]
        kept, skipped = RK.apply_day_week(ps, lv)
        no_limit = [p for p in E.single("ET", [t for t in res.trades[E.CS_SHORT]
                                               if t.exit_time is not None]) if not p.expiry_day]
    s = E.summarize(kept, base.capital, start)
    ok = s.trades > 0 and s.net_pnl > 0 and (s.profit_factor or 0) >= RK.HOLDOUT_PF
    ref = E.summarize(no_limit, base.capital, start)
    REPORTS.mkdir(exist_ok=True)
    MARKER.write_text(f"RL-1.0 ET ATM sell 2% trail, no expiry days, on {date.today()}: "
                      f"{'PASS' if ok else 'FAIL'}\n", encoding="utf-8")
    text = (f"# RL-1.0 held-back year ({start} to {end})\n\n"
            "Rule: ET-1.0 ATM sell, no trades on weekly expiry days, ₹2,000 1R trailing stop, "
            "₹3,000 day loss, ₹6,000 day profit lock, ₹6,000 week loss (backend/app/spreads/risk.py).\n\n"
            f"Trades {s.trades} (skipped by day/week limits {skipped}), net {rs(s.net_pnl)}, "
            f"PF {s.profit_factor}, max DD {s.max_drawdown_pct}%, worst trade {rs(s.worst_trade)}, "
            f"worst day {rs(RK.worst_period(kept, False))}, worst week {rs(RK.worst_period(kept, True))}.\n"
            f"Exits: {RK.exits(kept)}.\n\n"
            f"Bar: net > 0 and PF >= {RK.HOLDOUT_PF} → **{'PASS' if ok else 'FAIL'}**.\n\n"
            f"For reference, same trades with no stop and no limits: {ref.trades} trades, net "
            f"{rs(ref.net_pnl)}, PF {ref.profit_factor}.\n")
    (REPORTS / "et_holdout.md").write_text(text, encoding="utf-8")
    print("\n" + text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
