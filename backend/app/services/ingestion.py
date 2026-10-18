"""Ingestion: provider -> checks -> PostgreSQL.

Idempotent by design: candles are UPSERTed on (instrument_id, timeframe, candle_time),
so running the same backfill twice updates rows in place and never duplicates them.
In SQL terms this is:  INSERT ... ON CONFLICT (instrument_id, timeframe, candle_time) DO UPDATE.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core.market_time import from_db, latest_expected_closed_bar, to_ist, utc_now
from app.db.models import DataQualityLog, IngestionRun, Instrument, MarketCandle, MarketSnapshot
from app.providers.base import Candle, InstrumentInfo, MarketDataProvider, ProviderError
from app.services import data_quality as dq

log = logging.getLogger(__name__)
CHUNK = 1000


@dataclass
class IngestResult:
    run_id: int
    status: str
    rows_received: int = 0
    rows_written: int = 0
    checks: list[dq.CheckResult] = field(default_factory=list)
    error: str | None = None


def _insert(session: Session):
    return pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert


def to_info(inst: Instrument) -> InstrumentInfo:
    return InstrumentInfo(
        symbol=inst.symbol, instrument_type=inst.instrument_type, exchange=inst.exchange,
        segment=inst.segment, provider_symbol=inst.provider_symbol,
        trading_symbol=inst.trading_symbol, expiry_date=inst.expiry_date,
        strike_price=float(inst.strike_price) if inst.strike_price is not None else None,
        option_type=inst.option_type, lot_size=inst.lot_size,
    )


def _start_run(session: Session, provider: MarketDataProvider, job: str,
               instrument_id: int | None = None, timeframe: str | None = None) -> IngestionRun:
    run = IngestionRun(provider=provider.name, job=job, instrument_id=instrument_id,
                       timeframe=timeframe, started_at=utc_now(), status="RUNNING")
    session.add(run)
    session.flush()
    return run


def _finish(session: Session, run: IngestionRun, status: str, error: str | None = None) -> None:
    run.status, run.finished_at, run.error_message = status, utc_now(), error
    session.commit()


def _log_checks(session: Session, run: IngestionRun, instrument_id: int | None,
                timeframe: str | None, checks: list[dq.CheckResult]) -> None:
    now = utc_now()
    for c in checks:
        session.add(DataQualityLog(run_id=run.id, instrument_id=instrument_id, timeframe=timeframe,
                                   check_name=c.check_name, status=c.status, details=c.details,
                                   checked_at=now))


# --------------------------------------------------------------------------- instruments
def sync_instruments(session: Session, provider: MarketDataProvider, underlyings: list[str],
                     futures_expiries: int) -> IngestResult:
    """Refresh the instrument list (indices / stocks + upcoming futures) from the provider."""
    run = _start_run(session, provider, "instruments")
    try:
        infos = provider.list_instruments(underlyings, futures_expiries)
    except ProviderError as exc:
        _finish(session, run, "FAILED", str(exc))
        return IngestResult(run.id, "FAILED", error=str(exc))

    for info in infos:
        stmt = _insert(session)(Instrument).values(
            symbol=info.symbol, instrument_type=info.instrument_type, exchange=info.exchange,
            segment=info.segment, expiry_date=info.expiry_date, strike_price=info.strike_price,
            option_type=info.option_type, lot_size=info.lot_size, provider=provider.name,
            provider_symbol=info.provider_symbol, trading_symbol=info.trading_symbol,
            is_sample=provider.is_sample, is_active=True,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["provider", "provider_symbol"],
            set_={"lot_size": stmt.excluded.lot_size, "trading_symbol": stmt.excluded.trading_symbol,
                  "expiry_date": stmt.excluded.expiry_date, "is_active": True,
                  "updated_at": utc_now()},
        )
        session.execute(stmt)

    # Contracts that have expired are kept (history matters) but marked inactive.
    today = to_ist(utc_now()).date()
    for inst in session.scalars(select(Instrument).where(Instrument.provider == provider.name)):
        if inst.expiry_date and inst.expiry_date < today:
            inst.is_active = False

    run.rows_received = run.rows_written = len(infos)
    _finish(session, run, "SUCCESS")
    return IngestResult(run.id, "SUCCESS", len(infos), len(infos))


# --------------------------------------------------------------------------- candles
def ingest_candles(session: Session, provider: MarketDataProvider, instrument: Instrument,
                   timeframe: str, start: datetime, end: datetime,
                   now: datetime | None = None) -> IngestResult:
    """Fetch, check and upsert candles for one instrument and timeframe."""
    now = now or utc_now()
    run = _start_run(session, provider, "candles", instrument.id, timeframe)
    try:
        raw = provider.get_candles(to_info(instrument), timeframe, start, end)
    except ProviderError as exc:
        _log_checks(session, run, instrument.id, timeframe,
                    [dq.CheckResult("source_availability", dq.FAIL, {"error": str(exc)})])
        _finish(session, run, "FAILED", str(exc))
        log.error("Candle fetch failed for %s %s: %s", instrument.provider_symbol, timeframe, exc)
        return IngestResult(run.id, "FAILED", error=str(exc))

    candles, c1 = dq.drop_incomplete(raw, timeframe, now)
    candles, c2 = dq.dedupe(candles)
    candles, filled = _fill_daily_open(session, instrument, timeframe, candles)
    # Older problems are logged but don't raise a WARN (see dq.RECENT_DAYS).
    recent_from = candles[-1].time - timedelta(days=dq.RECENT_DAYS) if candles else None
    problems, c3 = dq.validate_ohlc(candles, spike_check=instrument.instrument_type != "OPTION",
                                    recent_from=recent_from)
    is_fno = instrument.segment == "FNO"
    expect_volume = is_fno or instrument.instrument_type == "STOCK"
    c4 = dq.check_missing_values(candles, expect_volume=expect_volume, expect_oi=is_fno,
                                 recent_from=recent_from)
    c5 = dq.check_gaps(candles, timeframe,
                       reference_times=_reference_times(session, instrument, timeframe, candles),
                       recent_from=recent_from)
    c6 = dq.check_missing_open(candles, filled, recent_from=recent_from)
    checks = [dq.CheckResult("source_availability", dq.PASS, {"received": len(raw)}),
              c1, c2, c3, c4, c5, c6]

    received_at = utc_now()
    rows = [
        dict(instrument_id=instrument.id, timeframe=timeframe, candle_time=c.time,
             open=c.open, high=c.high, low=c.low, close=c.close, volume=c.volume,
             open_interest=c.open_interest, source=provider.name, is_sample=provider.is_sample,
             received_at=received_at,
             quality_status="INVALID" if c.time in problems else "VALID",
             quality_reason=problems.get(c.time))
        for c in candles
    ]
    insert = _insert(session)
    for i in range(0, len(rows), CHUNK):
        stmt = insert(MarketCandle).values(rows[i : i + CHUNK])
        update_cols = ["open", "high", "low", "close", "volume", "open_interest", "source",
                       "is_sample", "received_at", "quality_status", "quality_reason"]
        stmt = stmt.on_conflict_do_update(
            index_elements=["instrument_id", "timeframe", "candle_time"],
            set_={col: stmt.excluded[col] for col in update_cols},
        )
        session.execute(stmt)

    _log_checks(session, run, instrument.id, timeframe, checks)
    run.rows_received, run.rows_written = len(raw), len(rows)
    status = "SUCCESS"
    _finish(session, run, status)
    return IngestResult(run.id, status, len(raw), len(rows), checks)


# The market reference: every other instrument should have a candle wherever NIFTY has one.
REFERENCE_SYMBOL = "NIFTY"


def _reference_times(session: Session, instrument: Instrument, timeframe: str,
                     candles: list[Candle]) -> list[datetime] | None:
    """NIFTY index candle times covering `candles`, or None (use the holiday calendar)."""
    if not candles or (instrument.symbol == REFERENCE_SYMBOL and instrument.instrument_type == "INDEX"):
        return None
    ref = session.scalar(select(Instrument.id).where(
        Instrument.provider == instrument.provider, Instrument.symbol == REFERENCE_SYMBOL,
        Instrument.instrument_type == "INDEX"))
    if ref is None:
        return None
    times = session.scalars(select(MarketCandle.candle_time).where(
        MarketCandle.instrument_id == ref, MarketCandle.timeframe == timeframe,
        MarketCandle.candle_time >= candles[0].time, MarketCandle.candle_time <= candles[-1].time))
    out = [from_db(t) for t in times]
    return out or None


def _fill_daily_open(session: Session, instrument: Instrument, timeframe: str,
                     candles: list[Candle]) -> tuple[list[Candle], int]:
    """Complete a daily candle's missing open from the stored first 1h candle of that day.

    Both are stamped 09:15 IST and come from the same provider, so this copies a real price;
    nothing is estimated. Days without a valid 1h candle keep open = None.
    """
    missing = [c.time for c in candles if c.open is None]
    if timeframe != "1d" or not missing:
        return candles, 0
    rows = session.execute(
        select(MarketCandle.candle_time, MarketCandle.open)
        .where(MarketCandle.instrument_id == instrument.id, MarketCandle.timeframe == "1h",
               MarketCandle.quality_status == "VALID", MarketCandle.open.is_not(None),
               MarketCandle.candle_time >= min(missing), MarketCandle.candle_time <= max(missing)))
    opens = {from_db(t): float(o) for t, o in rows}
    out = [replace(c, open=opens[c.time]) if c.open is None and c.time in opens else c
           for c in candles]
    return out, sum(1 for c in candles if c.open is None and c.time in opens)


def backfill(session: Session, provider: MarketDataProvider, timeframe: str, days: int,
             symbols: list[str] | None = None, now: datetime | None = None) -> list[IngestResult]:
    """Load `days` of history for every active instrument (optionally filtered by symbol)."""
    now = now or utc_now()
    query = select(Instrument).where(Instrument.provider == provider.name, Instrument.is_active)
    if symbols:
        query = query.where(Instrument.symbol.in_(symbols))
    results = []
    for inst in session.scalars(query).all():
        results.append(ingest_candles(session, provider, inst, timeframe,
                                      now - timedelta(days=days), now, now))
    return results


def refresh_recent(session: Session, provider: MarketDataProvider, timeframe: str,
                   now: datetime | None = None, lookback_days: int = 5) -> list[IngestResult]:
    """Top up the last few days. Used by the collector during market hours."""
    if latest_expected_closed_bar(now or utc_now(), timeframe) is None:
        return []
    return backfill(session, provider, timeframe, lookback_days, now=now)


# --------------------------------------------------------------------------- snapshots
def ingest_snapshot(session: Session, provider: MarketDataProvider,
                    instrument: Instrument, max_age_minutes: int = 15) -> IngestResult:
    """Store the latest quote. Quotes older than `max_age_minutes` are flagged stale."""
    run = _start_run(session, provider, "snapshots", instrument.id)
    try:
        snap = provider.get_snapshot(to_info(instrument))
    except ProviderError as exc:
        _finish(session, run, "FAILED", str(exc))
        return IngestResult(run.id, "FAILED", error=str(exc))
    if snap is None:
        _finish(session, run, "SUCCESS")
        return IngestResult(run.id, "SUCCESS")

    received = utc_now()
    is_stale = (received - snap.observed_at) > timedelta(minutes=max_age_minutes)
    values = dict(instrument_id=instrument.id, observed_at=snap.observed_at, received_at=received,
                  last_price=snap.last_price, bid_price=snap.bid_price, ask_price=snap.ask_price,
                  day_open=snap.day_open, day_high=snap.day_high, day_low=snap.day_low,
                  previous_close=snap.previous_close, volume=snap.volume,
                  open_interest=snap.open_interest, oi_change=snap.oi_change,
                  implied_volatility=snap.implied_volatility, source=provider.name,
                  is_sample=provider.is_sample, is_stale=is_stale)
    stmt = _insert(session)(MarketSnapshot).values(values)
    stmt = stmt.on_conflict_do_nothing(index_elements=["instrument_id", "observed_at"])
    session.execute(stmt)
    run.rows_received = run.rows_written = 1
    _finish(session, run, "SUCCESS")
    return IngestResult(run.id, "SUCCESS", 1, 1)
