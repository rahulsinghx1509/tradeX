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
    instrument_type: Mapped[str] = mapped_column(String(10))  # INDEX / FUTURE / OPTION
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
