"""AD-1.0: walk-forward backtest of the adaptive agent (picks and re-tunes NIFTY option
strategies every month from 1,300 building-block candidates, after costs).

Rules and pass bar are fixed in backend/app/agent/__init__.py (docs/01 §30).
Read-only: reads NIFTY 1m + option 1m (from `agent-load`), fo_daily, NIFTY and India VIX daily
candles. Writes reports/agent_backtest.md, reports/agent_trades.csv, reports/agent_picks.csv.
No orders, ever.

Usage (from the repository root):
    python scripts/agent_backtest.py
On the server: ~/tradex/deploy/tradex.sh agent-backtest
"""

import sys
import time as _time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import _bootstrap  # noqa: F401
import pandas as pd

from app import agent as A
from app.agent import data as D
from app.agent import intraday as I
from app.agent import regime as R
from app.agent import report as RP
from app.agent import swing as SW
from app.agent import walkforward as W
from app.core.config import REPO_ROOT, get_settings
from app.db.session import get_sessionmaker
from app.ema921.loader import ist_today


def main() -> int:
    t0 = _time.time()
    skipped: dict[str, int] = {}
    end = ist_today() - timedelta(days=1)
    with get_sessionmaker()() as session:
        daily, vix = D.daily(session)
        if daily.empty:
            print("No NIFTY daily candles: run ~/tradex/deploy/tradex.sh backfill first.")
            return 1
        if vix is None:
            print("WARNING: no India VIX daily candles: every day is NO TRADE (missing data).")
        print("Reading NIFTY 1-minute candles ...", flush=True)
        sdays = [s for s in I.spot_days(D.spot(session, A.INTRADAY_FIRST, end), skipped)
                 if s.day >= A.INTRADAY_FIRST]
        info_i = R.labels(daily, vix, [s.day for s in sdays])
        panels = D.Panels(session)
        frames, buf = [], []
        for n, s in enumerate(sdays, 1):
            buf += I.day_trades(s, info_i[s.day], panels.panel(s.day), skipped)
            if n % 100 == 0 or n == len(sdays):
                frames.append(pd.DataFrame(buf))
                buf = []
                print(f"  intraday {n}/{len(sdays)} days ({s.day})", flush=True)
        print("Reading NSE daily option file (fo_daily) ...", flush=True)
        book, s_lo, s_hi = D.fo_book(session)
    swing = []
    if book is not None:
        info_s = R.labels(daily, vix, book.days)
        swing = SW.all_trades(book, daily["close"], info_s, skipped)
        print(f"  swing: {len(swing)} base trades", flush=True)
    frames.append(pd.DataFrame(swing))
    trades = pd.concat([f for f in frames if not f.empty], ignore_index=True) if any(
        not f.empty for f in frames) else pd.DataFrame()
    if trades.empty:
        print("No trades could be built: check `agent-load` and `fo-history` ran.")
        return 1
    info = {**R.labels(daily, vix, sorted(set(trades["entry_day"])))}
    trades = W.add_regimes(trades, info)
    n_cand = len(W.candidates(trades))
    assert n_cand <= A.MAX_CANDIDATES, n_cand
    print(f"  {len(trades):,} base trades, {n_cand} candidates", flush=True)

    def span(track):
        m = trades.loc[trades["track"] == track, "month"]
        return (m.min(), m.max()) if len(m) else (None, None)

    i_lo, i_hi = span("I")
    s_lo_m, s_hi_m = span("S")
    plan = {}
    if i_lo is not None and s_lo_m is not None:
        plan["FULL"] = (max(i_lo, s_lo_m) + A.TRAIN_MONTHS, min(i_hi, s_hi_m), ("I", "S"))
    if i_lo is not None:
        plan["INTRADAY only"] = (i_lo + A.TRAIN_MONTHS, i_hi, ("I",))
    if s_lo_m is not None:
        plan["SWING only (since 2020)"] = (s_lo_m + A.TRAIN_MONTHS, s_hi_m, ("S",))
    rows = {}
    full_res = None
    for name, (first, last, tracks) in plan.items():
        print(f"Walk-forward {name}: {first} to {last} ...", flush=True)
        res = W.run(trades, first, last, "net", tracks)
        res2 = W.run(trades, first, last, "net_2x", tracks)
        rows[name] = W.summarize(res, res2)
        rows[name]["span"] = f"{first} to {last}"
        if name == "FULL":
            full_res = res
    if "FULL" not in rows:
        rows = {"FULL": {"trades": 0, "span": "no intraday or no swing data"}, **rows}
    last_m = plan["FULL"][1] if "FULL" in plan else trades["month"].max()
    upcoming = W.next_picks(trades, last_m + 1)
    meta = {"i_first": sdays[0].day if sdays else None, "i_last": sdays[-1].day if sdays else None,
            "i_days": len(sdays), "s_first": s_lo, "s_last": s_hi, "n_candidates": n_cand,
            "skipped": skipped}
    report = RP.to_markdown(rows, meta, upcoming, datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"))
    if get_settings().data_provider.lower() == "sample":
        report = "**SAMPLE DATA: synthetic prices, results mean nothing.**\n\n" + report
    out = Path(REPO_ROOT) / "reports"
    out.mkdir(exist_ok=True)
    (out / "agent_backtest.md").write_text(report, encoding="utf-8")
    if full_res is not None and not full_res.trades.empty:
        t = full_res.trades.copy()
        t["strategy"] = [RP.describe(b, r) for b, r in zip(t["base"], t["regime"], strict=True)]
        cols = ["entry_time", "exit_time", "strategy", "direction", "legs", "reason", "spot_entry",
                "gross", "costs", "pnl"]
        t[cols].to_csv(out / "agent_trades.csv", index=False)
    if "FULL" in plan:
        pd.DataFrame([{"month": m["month"], "pick": RP.describe(p["base"], p["regime"]),
                       "train_trades": p["train_n"], "train_net": round(p["train_net"]),
                       "train_pf": round(p["train_pf"], 2), "month_trades": m["trades"],
                       "month_net": round(m["net"])}
                      for m in rows["FULL"]["months"] for p in m["picks"]]
                     ).to_csv(out / "agent_picks.csv", index=False)
    print("\n" + report)
    print(f"Saved {out / 'agent_backtest.md'} (+ agent_trades.csv, agent_picks.csv) "
          f"in {(_time.time() - t0) / 60:.1f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
