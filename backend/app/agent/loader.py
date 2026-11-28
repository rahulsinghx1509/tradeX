"""Fetch what AD-1.0's intraday candidates need from Groww. Read-only, resumable.

1. NIFTY spot 1m and expiry lists (shared with the other loaders; stored days are skipped).
2. For each day: the option contracts any intraday signal of that day could trade (every entry
   type x every structure), found from NIFTY's 1-minute candles. No backtest passes are needed:
   which legs a signal trades does not depend on earlier trades.
Swing candidates use NSE's daily file (fo_daily): top it up with `tradex.sh fo-history`.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app import agent as A
from app.agent import data as D
from app.agent import intraday as I
from app.agent import regime as R
from app.ema921 import loader as L
from app.ema921 import store as S
from app.ema921.engine import pick_expiry


def legs_to_fetch(session: Session, start: date, end: date) -> list[tuple[date, date, float, str]]:
    spot = D.spot(session, start, end)
    days = [s for s in I.spot_days(spot) if start <= s.day <= end]
    daily, vix = D.daily(session)
    info = R.labels(daily, vix, [s.day for s in days])
    expiries = S.expiries(session, A.SYMBOL)
    out = []
    for s in days:
        ex = pick_expiry(s.day, expiries, True)
        if ex is None:
            continue
        out += [(s.day, ex, k, t) for t, k in sorted(I.needed_legs(s, info[s.day]))]
    return out


def load_all(session: Session, client: Any, start: date, end: date,
             log: Callable[[str], None] = print) -> L.LoadReport:
    rep = L.LoadReport()
    L.load_spot(session, client, start, end, rep, log, A.SYMBOL)
    L.load_expiries(session, client, start, end, rep, log, A.SYMBOL)
    legs = legs_to_fetch(session, start, end)
    log(f"AD-1.0: {len(legs)} option contract-days could be traded by intraday signals")
    L.fetch_legs(session, client, legs, rep, A.SYMBOL, "AD-1.0 legs", log)
    return rep
