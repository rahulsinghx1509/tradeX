"""Fetch the 1-minute option candles the ST-1.0 straddle needs: four legs per day.

Reuses the 9/21 EMA tables and helpers: NIFTY spot and the expiry list come from `ema-load`
(run that first, or this runs the same spot/expiry steps itself). For each day the ATM strike
comes from NIFTY's 09:19 close; then the ATM CE/PE and the two wings are fetched. Strikes that
Groww's contract list leaves out are tried directly (only kept if Groww returns candles).
Read-only: no orders. Safe to stop and re-run.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.core.market_time import IST
from app.ema921 import loader as L
from app.ema921 import store as S
from app.ema921.engine import atm_strike, pick_expiry
from app.straddle import engine as E


def needed_legs(spot: pd.DataFrame, start: date, end: date, expiries: list[date],
                cfg: E.Config) -> list[tuple[date, date, float, str]]:
    """(day, expiry, strike, CE/PE) for every leg of every day in [start, end]."""
    if spot.empty:
        return []
    times = pd.to_datetime(spot["time"], utc=True).dt.tz_convert(IST)
    out = []
    for d, day_df in spot.groupby(times.dt.date):
        if not (start <= d <= end):
            continue
        px = E.spot_at_entry(day_df, d, cfg)
        e = pick_expiry(d, expiries, cfg.roll_on_expiry_day)
        if px is None or e is None:
            continue
        for _, _, opt, strike in E.legs_for(atm_strike(px, cfg.strike_step), cfg):
            out.append((d, e, strike, opt))
    return out


def load_all(session: Session, client: Any, start: date, end: date, cfg: E.Config | None = None,
             log: Callable[[str], None] = print) -> L.LoadReport:
    cfg = cfg or E.Config()
    rep = L.LoadReport()
    L.load_spot(session, client, start, end, rep, log)
    L.load_expiries(session, client, start, end, rep, log)
    spot = S.load_spot(session, start, end, 0)
    legs = needed_legs(spot, start, end, S.expiries(session), cfg)
    L.load_contracts(session, client, sorted({e for _, e, _, _ in legs}), rep)
    data = S.DbOptionData(session)
    todo, missing = [], 0
    for d, e, k, t in legs:
        sym = data.contract(e, k, t) or L.try_unlisted(session, client, e, k, t, d, rep)
        if sym is None:
            missing += 1
        else:
            todo.append((sym, d))
    todo = sorted(set(todo), key=lambda x: (x[1], x[0]))
    rep.contracts_needed = len(todo)
    done = S.done_set(session, f"NSE-{S.UNDERLYING}-")
    todo = [x for x in todo if x not in done]
    log(f"Straddle legs: {rep.contracts_needed} contract-days needed, {len(todo)} to fetch"
        + (f", {missing} legs with no contract" if missing else ""))
    for i, (sym, d) in enumerate(todo, 1):
        n = L._fetch_and_store(session, client, sym, "FNO", d, rep)
        if n:
            rep.contract_days_fetched += 1
        elif n == 0:
            rep.contract_days_empty += 1
        if i % 100 == 0:
            log(f"  legs {i}/{len(todo)} ({d})")
    return rep
