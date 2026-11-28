"""AD-1.0 inputs from the database (read-only)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import agent as A
from app.agent import intraday as I
from app.core.market_time import IST, from_db
from app.db.models import FoDaily, MinuteCandle
from app.ema921 import store as S
from app.ema921.engine import pick_expiry
from app.meanrev.data import stored_daily, stored_daily_frame
from app.premium import engine as PE
from app.services import option_metrics as om


def daily(session: Session) -> tuple[pd.DataFrame, pd.Series | None]:
    """NIFTY daily high/low/close and India VIX daily close (stored official candles)."""
    vix = stored_daily(session, "INDIAVIX")
    return stored_daily_frame(session, A.SYMBOL), (vix if len(vix) else None)


def spot(session: Session, start: date, end: date) -> pd.DataFrame:
    return S.load_spot(session, start, end, 10, A.SYMBOL)


class Panels:
    """Each day's option 1-minute opens for the expiry intraday trades use."""

    def __init__(self, session: Session):
        self.session = session
        self.data = S.DbOptionData(session, A.SYMBOL)
        self.by_expiry: dict[date, dict[str, tuple[str, float]]] = {}
        for (e, k, t), sym in self.data._contracts.items():
            self.by_expiry.setdefault(e, {})[sym] = (t, k)

    def expiry(self, day: date) -> date | None:
        return pick_expiry(day, self.data.expiries(), True)

    def panel(self, day: date) -> I.Panel:
        ex = self.expiry(day)
        syms = self.by_expiry.get(ex, {}) if ex else {}
        arrays: dict[tuple[str, float], np.ndarray] = {}
        if syms:
            rows = self.session.execute(
                select(MinuteCandle.groww_symbol, MinuteCandle.candle_time, MinuteCandle.open)
                .where(MinuteCandle.trade_date == day,
                       MinuteCandle.groww_symbol.in_(list(syms)))).all()
            for sym, t, o in rows:
                if o is None:
                    continue  # missing stays missing
                ist = pd.Timestamp(from_db(t)).tz_convert(IST)
                i = ist.hour * 60 + ist.minute - (9 * 60 + 15)
                if 0 <= i < I.N_MIN:
                    key = syms[sym]
                    if key not in arrays:
                        arrays[key] = np.full(I.N_MIN, np.nan)
                    arrays[key][i] = float(o)
        return lambda kind, strike: arrays.get((kind, float(strike)))


def fo_book(session: Session, first: date = A.SWING_FIRST) -> tuple[PE.Book | None, date | None, date | None]:
    from sqlalchemy import func
    lo, hi = session.execute(select(func.min(FoDaily.trade_date), func.max(FoDaily.trade_date))
                             .where(FoDaily.symbol == A.SYMBOL)).one()
    if lo is None:
        return None, None, None
    frames = []
    for year in range(max(lo.year, first.year), hi.year + 1):
        frames.append(PE.trim(om.load_frame(session, A.SYMBOL, date(year, 1, 1), date(year, 12, 31))))
    return PE.Book(pd.concat(frames, ignore_index=True)), lo, hi


def days_of(spot_days: list[I.SpotDay], start: date, end: date) -> Iterator[I.SpotDay]:
    return (s for s in spot_days if start <= s.day <= end)
