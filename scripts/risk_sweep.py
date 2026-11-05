"""RL-1.0: loss and profit limits for a ₹1 lakh account, on the ET ATM sell and TP-1.0 trades
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
            f"{rs(RK.worst_period(ps, True))} | {RK.over(ps, 1000)} / {RK.over(ps, 2000)} | "
            f"{RK.exits(ps)} |")


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
        et_trades = [t for t in et.trades[E.CS_SHORT] if t.exit_time is not None]
        cache: dict = {}

        def mins(t):
            if (t.symbol, t.day) not in cache:
                cache[(t.symbol, t.day)] = EE._Minutes(opts.minutes(t.symbol, t.day))
            return cache[(t.symbol, t.day)]

        rows = []
        for lv in RK.LEVELS:
            trades = [RK.apply_exit_rule(t, mins(t), lv, base) for t in et_trades]
            ps = E.single("ET ATM sell", trades)
            for name, sub in (("ET ATM sell, all days", ps),
                              ("ET ATM sell, no expiry days (info)",
                               [x for x in ps if not x.expiry_day])):
                kept, skipped = RK.apply_day_week(sub, lv)
                rows.append((name, line(name, lv, kept, skipped, base.capital, start)))
        rows.sort(key=lambda r: r[0])  # all-days rows first, then the info rows
        rows = [r for _, r in rows]
        for mode in TP.MODES:
            for lv in RK.LEVELS:
                print(f"TP-1.0 {mode} {lv.name}...")
                # per-trade cap and R target in the engine; day/week limits afterwards (risk.py)
                lim = False if lv.stop is None else TP.Limits(lv.stop, None, None)
                target_r = 1e9 if lv.trail else (lv.reward or TP.TARGET_R)  # trail: no target
                res = TP.run(spot, fut, opts, start, end, base, (mode,), lim, target_r=target_r)
                tp = res.trades[mode]
                if lv.trail:
                    tp = [RK.trailing(t, mins(t), lv.stop, base) for t in tp if t.exit_time]
                ps, skipped = RK.apply_day_week(E.single("TP", tp), lv)
                skipped += sum(1 for s in res.skips if s.reason == "RISK_TOO_BIG")
                rows.append(line(f"TP-1.0 {mode.replace('_', ' ')}", lv, ps, skipped, base.capital,
                                 start))
    head = ["# RL-1.0: loss and profit limits for a ₹1 lakh account", "",
            f"Range {start} to {end} (development years only). Levels fixed before the run "
            "(backend/app/spreads/risk.py). Study only: no PASS verdicts, held-back year unused.", "",
            "Levels: stop a trade / target (R) or 1R trailing stop / day loss / day profit lock / "
            "week loss. ET ATM sell: "
            "stop and target on the option premium. TP-1.0: stop = largest allowed 1-lot risk, "
            "target = R x its index stop. Day/week limits stop NEW entries only.", "",
            "| Strategy | Limits | Trades | Skipped | Net | PF | Year 1 | Year 2 | Max DD | "
            "Worst trade | Worst day | Worst week | Trades losing > ₹1k / > ₹2k | Exits |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    md = "\n".join(head + rows) + "\n"
    out = Path(REPO_ROOT) / "reports"
    out.mkdir(exist_ok=True)
    (out / "risk_sweep.md").write_text(md, encoding="utf-8")
    print("\n" + md)
    print("Saved reports/risk_sweep.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
