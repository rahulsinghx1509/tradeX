"""Intraday chart levels for one instrument: VWAP per bar, the opening range, and the levels known
before the latest session opened (previous-day high/low/close, pivots, CPR, gap).

Readings only, never a buy/sell signal. Calculations live in services/intraday_metrics.py.
"""

import math

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.serialize import num, ts
from app.core.market_time import from_db
from app.db.models import Instrument, MarketCandle
from app.db.session import get_session
from app.services import intraday_metrics as im

router = APIRouter(prefix="/api")
INTRADAY_TIMEFRAMES = ("5m", "15m", "1h")
DAILY_LOOKBACK = 30  # enough daily candles for the ATR 14 behind the levels
LEVEL_KEYS = ("prev_high", "prev_low", "prev_close", "pivot", "r1", "r2", "s1", "s2",
              "cpr_bottom", "cpr_top", "cpr_width_pct", "gap_pct", "prev_atr14")


def _clean(v):
    """NaN -> None, so missing values stay missing in JSON."""
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


def _frame(rows: list[MarketCandle]) -> pd.DataFrame:
    df = pd.DataFrame([{"time": from_db(r.candle_time), "open": num(r.open), "high": num(r.high),
                        "low": num(r.low), "close": num(r.close), "volume": r.volume}
                       for r in rows], columns=["time", "open", "high", "low", "close", "volume"])
    for col in ("open", "high", "low", "close", "volume"):  # None -> NaN: missing stays missing
        df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
    return df


def _candles(session: Session, instrument_id: int, timeframe: str, limit: int) -> list[MarketCandle]:
    q = (select(MarketCandle)
         .where(MarketCandle.instrument_id == instrument_id, MarketCandle.timeframe == timeframe,
                MarketCandle.quality_status == "VALID")
         .order_by(MarketCandle.candle_time.desc()).limit(limit))
    return list(reversed(list(session.scalars(q))))


@router.get("/intraday")
def intraday(
    instrument_id: int = Query(..., ge=1),
    timeframe: str = Query("5m", pattern="^(" + "|".join(INTRADAY_TIMEFRAMES) + ")$"),
    limit: int = Query(500, ge=1, le=5000, description="most recent N candles"),
    session: Session = Depends(get_session),
) -> dict:
    if session.get(Instrument, instrument_id) is None:
        raise HTTPException(404, "Instrument not found")
    rows = _candles(session, instrument_id, timeframe, limit)
    if not rows:
        return {"timeframe": timeframe, "session": None, "bars": [], "levels": None}
    bars = im.bar_metrics(_frame(rows))
    # A 1h opening range would be 45 minutes long (09:15-10:00), so it is only shown for 5m/15m.
    show_or = timeframe != "1h"
    latest = bars["session"].iloc[-1]

    # Levels for the latest session: the daily candles before it, plus a row for that session
    # whose open is its first intraday bar (so the gap uses today's real open).
    daily = _frame(_candles(session, instrument_id, "1d", DAILY_LOOKBACK))
    levels = None
    if len(daily):
        daily = daily[im.session_dates(daily["time"]) < latest]
        today = bars[bars["session"] == latest]
        first_open = num(rows[len(rows) - len(today)].open)  # Groww stocks: often missing
        nxt = pd.DataFrame([{"time": today["time"].iloc[0],
                             "open": float("nan") if first_open is None else first_open,
                             "high": float("nan"), "low": float("nan"), "close": float("nan"),
                             "volume": float("nan")}])
        lv = im.daily_levels(pd.concat([daily, nxt], ignore_index=True)).iloc[-1]
        levels = {k: _clean(lv[k]) for k in LEVEL_KEYS}

    return {
        "timeframe": timeframe,
        "session": latest.isoformat(),
        "levels": levels,
        "bars": [
            {"time": ts(t), "vwap": _clean(v),
             "or_high": _clean(oh) if show_or else None, "or_low": _clean(ol) if show_or else None,
             "rvol": _clean(rv)}
            for t, v, oh, ol, rv in zip(bars["time"], bars["vwap"], bars["or_high"],
                                        bars["or_low"], bars["rvol"], strict=True)
        ],
    }
