"""Reading and writing the intraday stock tables (stock_members, stock_candles, stock_fetches)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core.market_time import IST, from_db, session_bounds, to_ist
from app.db.models import StockCandle, StockFetch, StockMember
from app.providers.base import Candle

INDEX_SYMBOL = "NIFTY"  # NIFTY's own 5-minute candles are stored under this symbol
DONE = ("OK", "EMPTY")
CHUNK = 500


def _insert(session: Session):
    return pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert


def save_members(session: Session, members: list[tuple[str, str, str]]) -> None:
    """Upsert (symbol, index_name, groww_symbol) rows."""
    now = datetime.now(UTC)
    for sym, index_name, groww in members:
        ins = _insert(session)(StockMember).values(symbol=sym, index_name=index_name,
                                                   groww_symbol=groww, updated_at=now)
        session.execute(ins.on_conflict_do_update(
            index_elements=["symbol"],
            set_={"index_name": index_name, "groww_symbol": groww, "updated_at": now}))


def members(session: Session) -> list[tuple[str, str, str]]:
    rows = session.execute(select(StockMember.symbol, StockMember.index_name,
                                  StockMember.groww_symbol).order_by(StockMember.id))
    return [tuple(r) for r in rows]


def done_windows(session: Session, symbol: str) -> dict[date, str]:
    rows = session.execute(select(StockFetch.window_start, StockFetch.status)
                           .where(StockFetch.symbol == symbol))
    return {d: s for d, s in rows}


def record_fetch(session: Session, symbol: str, start: date, end: date, rows: int, status: str,
                 error: str | None = None) -> None:
    now, err = datetime.now(UTC), (error or "")[:300] or None
    ins = _insert(session)(StockFetch).values(symbol=symbol, window_start=start, window_end=end,
                                              rows=rows, status=status, error=err, fetched_at=now)
    session.execute(ins.on_conflict_do_update(
        index_elements=["symbol", "window_start"],
        set_={"window_end": end, "rows": rows, "status": status, "error": err, "fetched_at": now}))


def save_candles(session: Session, symbol: str, candles: list[Candle]) -> int:
    """Upsert 5-minute candles that fall inside a trading session. Missing values stay NULL.
    If Groww sends a minute twice, the last copy is kept."""
    by_time: dict = {}
    for c in candles:
        day = to_ist(c.time).date()
        open_, close = session_bounds(day)
        if open_ <= c.time < close:
            by_time[c.time] = c
    rows = [dict(symbol=symbol, trade_date=to_ist(c.time).date(), candle_time=c.time,
                 open=c.open, high=c.high, low=c.low, close=c.close, volume=c.volume)
            for c in by_time.values()]
    for i in range(0, len(rows), CHUNK):
        part = rows[i:i + CHUNK]
        ins = _insert(session)(StockCandle).values(part)
        session.execute(ins.on_conflict_do_update(
            index_elements=["symbol", "candle_time"],
            set_={k: getattr(ins.excluded, k) for k in ("open", "high", "low", "close", "volume")}))
    return len(rows)


def load_candles(session: Session, symbol: str, start: date | None = None,
                 end: date | None = None) -> pd.DataFrame:
    """One symbol's 5-minute candles as a DataFrame indexed by IST bar start time, with a
    `day` column. Prices are floats; missing values are NaN (never zero)."""
    q = select(StockCandle.candle_time, StockCandle.open, StockCandle.high, StockCandle.low,
               StockCandle.close, StockCandle.volume).where(StockCandle.symbol == symbol)
    if start:
        q = q.where(StockCandle.trade_date >= start)
    if end:
        q = q.where(StockCandle.trade_date <= end)
    rows = session.execute(q.order_by(StockCandle.candle_time)).all()
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
    if df.empty:
        return df.set_index("time").assign(day=pd.Series(dtype=object))
    df["time"] = pd.to_datetime([from_db(t) for t in df["time"]], utc=True).tz_convert(IST)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    df = df.set_index("time")
    df["day"] = df.index.date
    return df


def stored_symbols(session: Session) -> list[str]:
    return sorted(session.scalars(select(StockCandle.symbol).distinct()))
