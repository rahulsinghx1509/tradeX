"""Reading and writing the 1-minute tables (minute_candles, minute_fetches, option_contracts)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core.market_time import from_db, to_ist
from app.db.models import MinuteCandle, MinuteFetch, OptionContract
from app.providers.base import Candle

SPOT_SYMBOL = "NSE-NIFTY"
UNDERLYING = "NIFTY"
DONE = ("OK", "EMPTY")


def _insert(session: Session):
    return pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert


def fetched(session: Session, symbol: str, day: date) -> bool:
    """True if this (symbol, day) was already asked from Groww and answered (even with nothing)."""
    row = session.scalar(select(MinuteFetch.status).where(
        MinuteFetch.groww_symbol == symbol, MinuteFetch.trade_date == day))
    return row in DONE


def done_set(session: Session, symbol_prefix: str) -> set[tuple[str, date]]:
    rows = session.execute(select(MinuteFetch.groww_symbol, MinuteFetch.trade_date).where(
        MinuteFetch.groww_symbol.like(f"{symbol_prefix}%"), MinuteFetch.status.in_(DONE)))
    return {(s, d) for s, d in rows}


def record_fetch(session: Session, symbol: str, day: date, rows: int, status: str,
                 error: str | None = None) -> None:
    ins = _insert(session)(MinuteFetch).values(
        groww_symbol=symbol, trade_date=day, rows=rows, status=status,
        error=(error or "")[:300] or None, fetched_at=datetime.now(UTC))
    session.execute(ins.on_conflict_do_update(
        index_elements=["groww_symbol", "trade_date"],
        set_={"rows": rows, "status": status, "error": (error or "")[:300] or None,
              "fetched_at": datetime.now(UTC)}))


def save_minutes(session: Session, symbol: str, day: date, candles: list[Candle]) -> int:
    """Upsert one day of 1-minute candles. Missing values stay NULL."""
    rows = [dict(groww_symbol=symbol, trade_date=day, candle_time=c.time, open=c.open, high=c.high,
                 low=c.low, close=c.close, volume=c.volume, open_interest=c.open_interest)
            for c in candles if to_ist(c.time).date() == day]
    if rows:
        ins = _insert(session)(MinuteCandle)
        for i in range(0, len(rows), 500):
            stmt = ins.values(rows[i:i + 500])
            session.execute(stmt.on_conflict_do_update(
                index_elements=["groww_symbol", "candle_time"],
                set_={k: getattr(stmt.excluded, k) for k in
                      ("open", "high", "low", "close", "volume", "open_interest")}))
    return len(rows)


def save_expiries(session: Session, underlying: str, expiries: list[date]) -> None:
    for e in expiries:
        _upsert_contract(session, underlying, e, None, None, f"EXPIRY:{underlying}:{e.isoformat()}")


def save_contracts(session: Session, underlying: str, expiry: date,
                   contracts: list[tuple[str, float, str]]) -> None:
    for symbol, strike, opt in contracts:
        _upsert_contract(session, underlying, expiry, strike, opt, symbol)


def _upsert_contract(session, underlying, expiry, strike, opt, symbol) -> None:
    ins = _insert(session)(OptionContract).values(
        underlying=underlying, expiry_date=expiry, strike=strike, option_type=opt, groww_symbol=symbol)
    session.execute(ins.on_conflict_do_nothing(index_elements=["groww_symbol"]))


def expiries(session: Session, underlying: str = UNDERLYING) -> list[date]:
    return sorted(set(session.scalars(select(OptionContract.expiry_date).where(
        OptionContract.underlying == underlying, OptionContract.option_type.is_(None)))))


def frame(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])
    if df.empty:
        return df
    df["time"] = [from_db(t) for t in df["time"]]
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")  # NULL stays NaN, never 0
    return df


def load_minutes(session: Session, symbol: str, start: date, end: date) -> pd.DataFrame:
    rows = session.execute(
        select(MinuteCandle.candle_time, MinuteCandle.open, MinuteCandle.high, MinuteCandle.low,
               MinuteCandle.close)
        .where(MinuteCandle.groww_symbol == symbol, MinuteCandle.trade_date >= start,
               MinuteCandle.trade_date <= end)
        .order_by(MinuteCandle.candle_time)).all()
    return frame(rows)


def load_spot(session: Session, start: date, end: date, warmup_days: int = 10) -> pd.DataFrame:
    """NIFTY spot 1m from `warmup_days` calendar days before `start` (EMA warm-up) to `end`."""
    return load_minutes(session, SPOT_SYMBOL, start - timedelta(days=warmup_days), end)


class DbOptionData:
    """The engine's OptionData, backed by the database."""

    def __init__(self, session: Session, underlying: str = UNDERLYING):
        self.session = session
        self._expiries = expiries(session, underlying)
        rows = session.execute(select(OptionContract.expiry_date, OptionContract.strike,
                                      OptionContract.option_type, OptionContract.groww_symbol)
                               .where(OptionContract.underlying == underlying,
                                      OptionContract.option_type.is_not(None)))
        self._contracts = {(e, float(k), t): s for e, k, t, s in rows}

    def expiries(self) -> list[date]:
        return self._expiries

    def contract(self, expiry: date, strike: float, option_type: str) -> str | None:
        return self._contracts.get((expiry, float(strike), option_type))

    def minutes(self, symbol: str, day: date) -> pd.DataFrame | None:
        df = load_minutes(self.session, symbol, day, day)
        return None if df.empty else df
