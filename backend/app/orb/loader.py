"""Fetch the 1-minute option candles ORB-1.0 needs: at most one breakout a day, three modes.

Reuses the 9/21 EMA tables and helpers. Spot and the expiry list are loaded the same way as
`ema-load` (days already stored are skipped), then only the contracts a breakout could trade.
Read-only: no orders. Safe to stop and re-run.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.ema921 import engine as EE
from app.ema921 import loader as L
from app.ema921 import store as S
from app.orb import engine as E


def needed_legs(spot: pd.DataFrame, start: date, end: date, expiries: list[date],
                cfg: EE.Config) -> list[tuple[date, date, float, str]]:
    """(day, expiry, strike, CE/PE) for every mode of every breakout day in [start, end]."""
    out = []
    for d, (_, c) in E.signals(spot).items():
        if not (start <= d <= end):
            continue
        e = EE.pick_expiry(d, expiries, cfg.roll_on_expiry_day)
        if e is None:
            continue
        for mode in EE.MODES:
            _, opt, strike = EE.leg_for(mode, c.direction, c.spot, cfg)
            out.append((d, e, strike, opt))
    return out


def load_all(session: Session, client: Any, start: date, end: date, underlying: str = "NIFTY",
             log: Callable[[str], None] = print) -> L.LoadReport:
    cfg = L.config_for(underlying)
    rep = L.LoadReport()
    L.load_spot(session, client, start, end, rep, log, underlying)
    L.load_expiries(session, client, start, end, rep, log, underlying)
    spot = S.load_spot(session, start, end, 0, underlying)
    legs = needed_legs(spot, start, end, S.expiries(session, underlying), cfg)
    L.fetch_legs(session, client, legs, rep, underlying, f"{underlying} ORB legs", log)
    return rep
