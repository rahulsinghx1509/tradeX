"""Data freshness (metric catalog #78).

FRESH   = we have the most recent candle that should exist by now.
STALE   = the latest stored candle is older than that. Signals must say NO TRADE.
NO_DATA = nothing stored yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.market_time import from_db, latest_expected_closed_bar
from app.db.models import MarketCandle


@dataclass
class Freshness:
    status: str
    last_candle_time: datetime | None
    expected_latest: datetime | None
    lag_minutes: float | None


def evaluate(last: datetime | None, expected: datetime | None) -> Freshness:
    if last is None:
        return Freshness("NO_DATA", None, expected, None)
    if expected is None or last >= expected:
        return Freshness("FRESH", last, expected, 0.0)
    return Freshness("STALE", last, expected, round((expected - last).total_seconds() / 60, 1))


def candle_freshness(session: Session, instrument_id: int, timeframe: str, now: datetime) -> Freshness:
    last = session.scalar(
        select(func.max(MarketCandle.candle_time)).where(
            MarketCandle.instrument_id == instrument_id, MarketCandle.timeframe == timeframe
        )
    )
    return evaluate(from_db(last), latest_expected_closed_bar(now, timeframe))
