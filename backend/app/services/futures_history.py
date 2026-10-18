"""Load the daily candles of expired monthly futures, for open-interest studies.

The collector only tracks the next few futures contracts, so open interest (OI) history starts
when TradeX started. Groww also serves expired contracts (checked by the derivatives probe:
futures OI back to 2020 for every underlying). This loads, for each underlying and month, the
futures contract that expired that month: its daily candles from about 100 days before expiry
(a monthly future is listed about three months ahead) to expiry day.

Expired contracts are stored as inactive instruments, so the collector never refreshes them and
the dashboard never shows them. A contract that already has candles is skipped, so the loader
can be stopped and re-run safely.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.market_time import IST, to_ist
from app.db.models import Instrument, MarketCandle
from app.providers.base import MarketDataProvider, ProviderError
from app.services.ingestion import _insert, ingest_candles

log = logging.getLogger(__name__)

LISTED_DAYS_BEFORE_EXPIRY = 100
MIN_STORED_CANDLES = 10  # a contract with at least this many daily candles counts as loaded


@dataclass
class MonthResult:
    symbol: str
    month: str  # YYYY-MM
    status: str  # LOADED / SKIPPED / NONE / FAILED
    contract: str | None = None
    candles: int = 0
    error: str | None = None


def months(first_year: int, today: date) -> list[tuple[int, int]]:
    """Every month from January of `first_year` up to and including the previous month."""
    out, y, m = [], first_year, 1
    while (y, m) < (today.year, today.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _stored(session: Session, provider: str, symbol: str, y: int, m: int) -> Instrument | None:
    """An expired future of this month that already has candles, if any."""
    start = date(y, m, 1)
    end = date(y + (m == 12), m % 12 + 1, 1)
    return session.scalar(
        select(Instrument)
        .join(MarketCandle, MarketCandle.instrument_id == Instrument.id)
        .where(Instrument.provider == provider, Instrument.symbol == symbol,
               Instrument.instrument_type == "FUTURE", Instrument.expiry_date >= start,
               Instrument.expiry_date < end, MarketCandle.timeframe == "1d")
        .group_by(Instrument.id)
        .having(func.count(MarketCandle.id) >= MIN_STORED_CANDLES))


def _upsert_inactive(session: Session, provider: MarketDataProvider, info) -> Instrument:
    stmt = _insert(session)(Instrument).values(
        symbol=info.symbol, instrument_type=info.instrument_type, exchange=info.exchange,
        segment=info.segment, expiry_date=info.expiry_date, provider=provider.name,
        provider_symbol=info.provider_symbol, trading_symbol=info.trading_symbol,
        lot_size=info.lot_size, is_sample=provider.is_sample, is_active=False,
    ).on_conflict_do_nothing(index_elements=["provider", "provider_symbol"])
    session.execute(stmt)
    session.commit()
    return session.scalar(select(Instrument).where(Instrument.provider == provider.name,
                                                   Instrument.provider_symbol == info.provider_symbol))


def load_month(session: Session, provider: MarketDataProvider, symbol: str, y: int, m: int,
               now: datetime) -> MonthResult:
    label = f"{y}-{m:02d}"
    done = _stored(session, provider.name, symbol, y, m)
    if done is not None:
        return MonthResult(symbol, label, "SKIPPED", done.provider_symbol)
    try:
        info = provider.expired_future(symbol, y, m)
    except ProviderError as exc:
        return MonthResult(symbol, label, "FAILED", error=str(exc))
    if info is None:
        return MonthResult(symbol, label, "NONE")
    inst = _upsert_inactive(session, provider, info)
    close = datetime.combine(info.expiry_date, datetime.min.time(), IST) + timedelta(hours=15, minutes=30)
    start = close - timedelta(days=LISTED_DAYS_BEFORE_EXPIRY)
    res = ingest_candles(session, provider, inst, "1d", start, min(close, now), now)
    if res.status != "SUCCESS":
        return MonthResult(symbol, label, "FAILED", info.provider_symbol, error=res.error)
    return MonthResult(symbol, label, "LOADED", info.provider_symbol, res.rows_written)


def load(session: Session, provider: MarketDataProvider, symbols: list[str], first_year: int,
         now: datetime, progress=None) -> list[MonthResult]:
    """Load every month for every symbol; `progress` is called with each result."""
    out = []
    for sym in symbols:
        for y, m in months(first_year, to_ist(now).date()):
            r = load_month(session, provider, sym, y, m, now)
            out.append(r)
            if progress:
                progress(r)
    return out
