"""Database tables (SQLAlchemy ORM).

If you know SQL: each class below is one table, each attribute is one column.
The real CREATE TABLE statements live in `alembic/versions/` (the migrations).

Conventions:
- All timestamps are stored in UTC (`timestamptz` in PostgreSQL).
- Optional values are NULL when unknown. A missing volume or OI is NULL, never 0.
- `is_sample = TRUE` marks synthetic sample rows, so they can never be mistaken
  for real market data.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# SQLite (used by the tests) only auto-increments plain INTEGER primary keys.
BigIntPK = BigInteger().with_variant(Integer, "sqlite")
Price = Numeric(14, 4)


class Base(DeclarativeBase):
    pass


class Instrument(Base):
    """Indices and the actual tradable contracts (one row per futures expiry)."""

    __tablename__ = "instruments"
    __table_args__ = (UniqueConstraint("provider", "provider_symbol", name="uq_instrument"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32))  # underlying, e.g. NIFTY
    instrument_type: Mapped[str] = mapped_column(String(10))  # INDEX / STOCK / FUTURE / OPTION
    exchange: Mapped[str] = mapped_column(String(10))
    segment: Mapped[str] = mapped_column(String(10))  # CASH / FNO
    expiry_date: Mapped[date | None] = mapped_column(Date)
    strike_price: Mapped[float | None] = mapped_column(Price)
    option_type: Mapped[str | None] = mapped_column(String(2))  # CE / PE
    lot_size: Mapped[int | None] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(20))  # groww / sample
    provider_symbol: Mapped[str] = mapped_column(String(64))  # e.g. NSE-NIFTY-30Sep25-FUT
    trading_symbol: Mapped[str | None] = mapped_column(String(64))
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MarketCandle(Base):
    """OHLC candles. One row per (instrument, timeframe, candle start time)."""

    __tablename__ = "market_candles"
    __table_args__ = (
        UniqueConstraint("instrument_id", "timeframe", "candle_time", name="uq_candle"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    timeframe: Mapped[str] = mapped_column(String(5))  # 1d / 1h
    candle_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    open: Mapped[float | None] = mapped_column(Price)
    high: Mapped[float | None] = mapped_column(Price)
    low: Mapped[float | None] = mapped_column(Price)
    close: Mapped[float | None] = mapped_column(Price)
    volume: Mapped[int | None] = mapped_column(BigInteger)
    open_interest: Mapped[int | None] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(String(20))
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    quality_status: Mapped[str] = mapped_column(String(10), default="VALID")  # VALID / INVALID
    quality_reason: Mapped[str | None] = mapped_column(String(200))


class MarketSnapshot(Base):
    """Point-in-time quotes taken during market hours (used later for live alerts)."""

    __tablename__ = "market_snapshots"
    __table_args__ = (
        UniqueConstraint("instrument_id", "observed_at", name="uq_snapshot"),
        Index("ix_snapshot_instrument_time", "instrument_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # provider time
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # our time
    last_price: Mapped[float | None] = mapped_column(Price)
    bid_price: Mapped[float | None] = mapped_column(Price)
    ask_price: Mapped[float | None] = mapped_column(Price)
    day_open: Mapped[float | None] = mapped_column(Price)
    day_high: Mapped[float | None] = mapped_column(Price)
    day_low: Mapped[float | None] = mapped_column(Price)
    previous_close: Mapped[float | None] = mapped_column(Price)
    volume: Mapped[int | None] = mapped_column(BigInteger)
    open_interest: Mapped[int | None] = mapped_column(BigInteger)
    oi_change: Mapped[int | None] = mapped_column(BigInteger)
    implied_volatility: Mapped[float | None] = mapped_column(Numeric(10, 4))
    source: Mapped[str] = mapped_column(String(20))
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False)


class OptionChainSnapshot(Base):
    """The whole option chain of one expiry, once per trading day near the close.

    Groww keeps no option-chain history, so TradeX collects its own. The chain is stored
    exactly as Groww sent it (`payload`), so no field is lost before its meaning is verified.
    """

    __tablename__ = "option_chain_snapshots"
    __table_args__ = (
        UniqueConstraint("provider", "symbol", "expiry_date", "trade_date", name="uq_chain_day"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    provider: Mapped[str] = mapped_column(String(20))
    symbol: Mapped[str] = mapped_column(String(32))  # underlying, e.g. NIFTY
    expiry_date: Mapped[date] = mapped_column(Date)
    trade_date: Mapped[date] = mapped_column(Date)  # IST trading day of the snapshot
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    strikes: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)


class FoDaily(Base):
    """End-of-day row of one F&O contract from NSE's free daily F&O file (the "bhavcopy").

    Kept for our underlyings only: every future, and options of the nearest expiries within a
    band around the underlying price that traded or had open interest that day. Units are
    stored exactly as the file gives them (`file_format` says which file): whether volume
    and OI are contracts or shares is NOT_VERIFIED for each format.
    """

    __tablename__ = "fo_daily"
    __table_args__ = (Index("ix_fo_daily_symbol_date", "symbol", "trade_date"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    symbol: Mapped[str] = mapped_column(String(32))  # underlying, e.g. NIFTY
    instrument: Mapped[str] = mapped_column(String(3))  # FUT / CE / PE
    expiry_date: Mapped[date] = mapped_column(Date)
    strike: Mapped[float | None] = mapped_column(Price)  # NULL for futures
    open: Mapped[float | None] = mapped_column(Price)
    high: Mapped[float | None] = mapped_column(Price)
    low: Mapped[float | None] = mapped_column(Price)
    close: Mapped[float | None] = mapped_column(Price)
    settle: Mapped[float | None] = mapped_column(Price)
    underlying: Mapped[float | None] = mapped_column(Price)  # only in the new (UDiFF) file
    open_interest: Mapped[int | None] = mapped_column(BigInteger)
    oi_change: Mapped[int | None] = mapped_column(BigInteger)
    volume: Mapped[int | None] = mapped_column(BigInteger)
    lot_size: Mapped[int | None] = mapped_column(Integer)  # only in the new (UDiFF) file
    file_format: Mapped[str] = mapped_column(String(6))  # udiff / old


class IngestionRun(Base):
    """One row per collection job, so failures and freshness can be audited."""

    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(20))
    job: Mapped[str] = mapped_column(String(20))  # instruments / candles / snapshots
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id"))
    timeframe: Mapped[str | None] = mapped_column(String(5))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(10), default="RUNNING")  # RUNNING/SUCCESS/FAILED
    rows_received: Mapped[int] = mapped_column(Integer, default=0)
    rows_written: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)


class DataQualityLog(Base):
    """Result of every data-quality check (PASS / WARN / FAIL)."""

    __tablename__ = "data_quality_log"
    __table_args__ = (Index("ix_dq_instrument_time", "instrument_id", "checked_at"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("ingestion_runs.id"))
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id"))
    timeframe: Mapped[str | None] = mapped_column(String(5))
    check_name: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(5))
    details: Mapped[dict | None] = mapped_column(JSON)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
