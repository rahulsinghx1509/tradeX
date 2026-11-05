"""RL-1.0: loss limits for a ₹1 lakh account, on the ET ATM sell and TP-1.0 trades
(rules and levels in backend/app/spreads/risk.py, fixed before the run; docs/01 §26).

A study: no PASS verdicts, the held-back year is not used. Needs the data from `spread-load`.
Writes reports/risk_sweep.md.

Usage (from the repository root):  python scripts/risk_sweep.py
On the server: deploy/tradex.sh risk-sweep
"""

import argparse
import sys
from pathlib import Path

import _bootstrap  # noqa: F401

from app.core.config import REPO_ROOT
from app.db.session import get_sessionmaker
from app.ema921 import engine as EE
from app.ema921 import loader as L
from app.ema921 import store as S
from app.pullback import engine as TP
from app.spreads import engine as E
from app.spreads import risk as RK
from app.spreads.report import nv, rs
from app.volume import futures as VF


def line(name, level, ps, skipped, capital, start):
    s = E.summarize(ps, capital, start)
    return (f"| {name} | {level.name} | {s.trades} | {skipped} | {rs(s.net_pnl)} | "
            f"{nv(s.profit_factor)} | {rs(s.year1)} | {rs(s.year2)} | {nv(s.max_drawdown_pct)}% | "
            f"{rs(s.worst_trade)} | {rs(RK.worst_period(ps, False))} | "
            f"{rs(RK.worst_period(ps, True))} | {RK.over(ps, 1000)} / {RK.over(ps, 2000)} |")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--years", type=int, default=2)
    a = p.parse_args()
    start, end = L.default_range(L.ist_today(), a.years)
    with get_sessionmaker()() as session:
        spot = S.load_spot(session, start, end, L.WARMUP_CALENDAR_DAYS, "NIFTY")
        if spot.empty:
            print("No 1-minute NIFTY spot data stored. Run deploy/tradex.sh spread-load first.")
            return 1
        opts = S.DbOptionData(session, "NIFTY")
        fut = TP.load_futures(session, VF.history_start(start), end)
        base = L.config_for("NIFTY")
        E.set_expiry_days(opts.expiries())
        print("ET ATM sell...")
        et = EE.run(spot, opts, start, end, E.et_config(base), (E.CS_SHORT,))
        et_ps = E.single("ET ATM sell", et.trades[E.CS_SHORT])
        rows = []
        for name, ps in (("ET ATM sell, all days", et_ps),
                         ("ET ATM sell, no expiry days (info)", [x for x in et_ps if not x.expiry_day])):
            for lv in RK.LEVELS:
                kept, skipped = RK.apply_day_week(ps, lv)
                rows.append(line(name, lv, kept, skipped, base.capital, start))
        for mode in TP.MODES:
            for lv in RK.LEVELS:
                print(f"TP-1.0 {mode} {lv.name}...")
                lim = False if lv.per_trade is None else TP.Limits(lv.per_trade, lv.day, lv.week)
                res = TP.run(spot, fut, opts, start, end, base, (mode,), lim)
                ps = E.single("TP", res.trades[mode])
                skipped = sum(1 for s in res.skips if s.reason in ("RISK_TOO_BIG", "LOSS_LIMIT"))
                rows.append(line(f"TP-1.0 {mode.replace('_', ' ')}", lv, ps, skipped, base.capital,
                                 start))
    head = ["# RL-1.0: loss limits for a ₹1 lakh account", "",
            f"Range {start} to {end} (development years only). Levels fixed before the run "
            "(backend/app/spreads/risk.py). Study only: no PASS verdicts, held-back year unused.", "",
            "Day / week limits stop NEW entries once the day's or week's realised net loss reaches "
            "the limit. The per-trade limit applies to TP-1.0 only (ET ATM sell has no stop).", "",
            "| Strategy | Limits | Trades | Skipped | Net | PF | Year 1 | Year 2 | Max DD | "
            "Worst trade | Worst day | Worst week | Trades losing > ₹1k / > ₹2k |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    md = "\n".join(head + rows) + "\n"
    out = Path(REPO_ROOT) / "reports"
    out.mkdir(exist_ok=True)
    (out / "risk_sweep.md").write_text(md, encoding="utf-8")
    print("\n" + md)
    print("Saved reports/risk_sweep.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
