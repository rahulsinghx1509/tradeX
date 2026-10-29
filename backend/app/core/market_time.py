"""Indian market calendar helpers.

Rules used everywhere in TradeX:
- Store every timestamp in UTC. Show times in Asia/Kolkata (IST).
- NSE equity / F&O session: 09:15 to 15:30 IST, Monday to Friday, except holidays.
- A candle's `candle_time` is the START of the candle.
    * 1h candles follow the clock, as Groww serves them: 09:15-10:00 (only 45 minutes),
      then 10:00, 11:00, ... 15:00 (the last one is only 30 minutes long).
    * 1d candles are stored at 09:15 IST of the trading day.
    * 5m and 15m candles run from 09:15 in fixed steps (09:15, 09:20, ... 15:25 for 5m: 75 bars;
      09:15 ... 15:15 for 15m: 25 bars). Groww's minute stamps are NOT_VERIFIED
      (`tradex.sh intraday-probe` shows the first and last bar of a day).
- A candle is "complete" only after its end time has passed. Incomplete candles
  are never stored, so historical analysis cannot peek into the future.
"""

from __future__ import annotations

import csv
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)
SUPPORTED_TIMEFRAMES = ("1d", "1h", "15m", "5m")
MINUTE_BARS = {"5m": timedelta(minutes=5), "15m": timedelta(minutes=15)}

HOLIDAYS_FILE = Path(__file__).resolve().parents[1] / "data" / "nse_holidays.csv"


@lru_cache
def load_holidays(path: Path = HOLIDAYS_FILE) -> frozenset[date]:
    """Read trading holidays from `app/data/nse_holidays.csv` (columns: date, description)."""
    if not path.exists():
        return frozenset()
    days: set[date] = set()
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(line for line in fh if not line.startswith("#")):
            value = (row.get("date") or "").strip()
            if value:
                days.add(date.fromisoformat(value))
    return frozenset(days)


def to_utc(dt: datetime) -> datetime:
    """Convert an aware datetime to UTC. Naive datetimes are treated as IST."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(UTC)


def from_db(dt: datetime | None) -> datetime | None:
    """Datetimes read from the database are UTC. SQLite drops the timezone, so add it back."""
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=UTC)


def to_ist(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(IST)


def is_trading_day(d: date, holidays: frozenset[date] | None = None) -> bool:
    holidays = load_holidays() if holidays is None else holidays
    return d.weekday() < 5 and d not in holidays


def trading_days(start: date, end: date, holidays: frozenset[date] | None = None) -> list[date]:
    out, d = [], start
    while d <= end:
        if is_trading_day(d, holidays):
            out.append(d)
        d += timedelta(days=1)
    return out


def session_bounds(d: date) -> tuple[datetime, datetime]:
    """(open, close) of a trading day, in UTC."""
    return (
        datetime.combine(d, SESSION_OPEN, tzinfo=IST).astimezone(UTC),
        datetime.combine(d, SESSION_CLOSE, tzinfo=IST).astimezone(UTC),
    )


def bar_starts_for_day(d: date, timeframe: str) -> list[datetime]:
    """All candle start times (UTC) for one trading day."""
    open_utc, close_utc = session_bounds(d)
    if timeframe == "1d":
        return [open_utc]
    if timeframe == "1h":
        starts, t = [open_utc], _next_clock_hour(open_utc)
        while t < close_utc:
            starts.append(t)
            t += timedelta(hours=1)
        return starts
    if timeframe in MINUTE_BARS:
        step, starts, t = MINUTE_BARS[timeframe], [], open_utc
        while t < close_utc:
            starts.append(t)
            t += step
        return starts
    raise ValueError(f"Unsupported timeframe: {timeframe}")


def _next_clock_hour(dt: datetime) -> datetime:
    """The next full IST hour after `dt` (09:15 -> 10:00), in UTC."""
    ist = to_ist(dt).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return ist.astimezone(UTC)


def bar_end(bar_start: datetime, timeframe: str) -> datetime:
    """When a candle closes. Hourly candles end on the next full hour, or at the 15:30 close."""
    _, close_utc = session_bounds(to_ist(bar_start).date())
    if timeframe == "1d":
        return close_utc
    if timeframe == "1h":
        return min(_next_clock_hour(bar_start), close_utc)
    if timeframe in MINUTE_BARS:
        return min(bar_start + MINUTE_BARS[timeframe], close_utc)
    raise ValueError(f"Unsupported timeframe: {timeframe}")


def is_bar_complete(bar_start: datetime, timeframe: str, now: datetime) -> bool:
    return bar_end(bar_start, timeframe) <= now


def expected_bar_starts(
    start: datetime, end: datetime, timeframe: str, holidays: frozenset[date] | None = None
) -> list[datetime]:
    """Every candle we expect to exist with start time in [start, end]."""
    out: list[datetime] = []
    for d in trading_days(to_ist(start).date(), to_ist(end).date(), holidays):
        out.extend(t for t in bar_starts_for_day(d, timeframe) if start <= t <= end)
    return out


def latest_expected_closed_bar(
    now: datetime, timeframe: str, holidays: frozenset[date] | None = None
) -> datetime | None:
    """Start time of the most recent candle that should be complete at `now`."""
    d = to_ist(now).date()
    for _ in range(15):  # look back up to ~2 weeks (covers long holiday breaks)
        if is_trading_day(d, holidays):
            closed = [
                s for s in bar_starts_for_day(d, timeframe) if is_bar_complete(s, timeframe, now)
            ]
            if closed:
                return closed[-1]
        d -= timedelta(days=1)
    return None


def is_market_open(now: datetime, holidays: frozenset[date] | None = None) -> bool:
    d = to_ist(now).date()
    if not is_trading_day(d, holidays):
        return False
    open_utc, close_utc = session_bounds(d)
    return open_utc <= now < close_utc


def utc_now() -> datetime:
    return datetime.now(UTC)
