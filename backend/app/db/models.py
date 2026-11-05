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


class ParticipantOi(Base):
    """NSE's daily participant-wise open interest: how many F&O contracts each kind of trader
    (Client = retail, DII, FII, Pro = brokers' own trading) holds long and short.

    One row per day and participant type (plus NSE's TOTAL row). Counts are contracts as NSE
    reports them, so a lot-size change moves the level; the screen uses ratios only.
    Column meanings are matched by name from the file's header (services/participant_oi.py).
    """

    __tablename__ = "participant_oi"
    __table_args__ = (UniqueConstraint("trade_date", "client_type", name="uq_participant_oi"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    client_type: Mapped[str] = mapped_column(String(10))  # Client / DII / FII / Pro / TOTAL
    fut_idx_long: Mapped[int | None] = mapped_column(BigInteger)
    fut_idx_short: Mapped[int | None] = mapped_column(BigInteger)
    fut_stk_long: Mapped[int | None] = mapped_column(BigInteger)
    fut_stk_short: Mapped[int | None] = mapped_column(BigInteger)
    opt_idx_call_long: Mapped[int | None] = mapped_column(BigInteger)
    opt_idx_put_long: Mapped[int | None] = mapped_column(BigInteger)
    opt_idx_call_short: Mapped[int | None] = mapped_column(BigInteger)
    opt_idx_put_short: Mapped[int | None] = mapped_column(BigInteger)
    opt_stk_call_long: Mapped[int | None] = mapped_column(BigInteger)
    opt_stk_put_long: Mapped[int | None] = mapped_column(BigInteger)
    opt_stk_call_short: Mapped[int | None] = mapped_column(BigInteger)
    opt_stk_put_short: Mapped[int | None] = mapped_column(BigInteger)
    total_long: Mapped[int | None] = mapped_column(BigInteger)
    total_short: Mapped[int | None] = mapped_column(BigInteger)


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


class MinuteCandle(Base):
    """1-minute candles for the 9/21 EMA options backtest: NIFTY spot and individual options.

    Keyed by Groww's own symbol (NSE-NIFTY, NSE-NIFTY-25Nov25-24500-CE ...). Kept apart from
    `market_candles` so the dashboard collector and its quality checks are untouched.
    """

    __tablename__ = "minute_candles"
    __table_args__ = (UniqueConstraint("groww_symbol", "candle_time", name="uq_minute_candle"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    groww_symbol: Mapped[str] = mapped_column(String(64))
    trade_date: Mapped[date] = mapped_column(Date)  # IST trading day
    candle_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # candle START, UTC
    open: Mapped[float | None] = mapped_column(Price)
    high: Mapped[float | None] = mapped_column(Price)
    low: Mapped[float | None] = mapped_column(Price)
    close: Mapped[float | None] = mapped_column(Price)
    volume: Mapped[int | None] = mapped_column(BigInteger)
    open_interest: Mapped[int | None] = mapped_column(BigInteger)


Index("ix_minute_candle_symbol_day", MinuteCandle.groww_symbol, MinuteCandle.trade_date)


class MinuteFetch(Base):
    """One row per (symbol, day) asked from Groww, so the loader can resume and never asks
    twice. rows = 0 means Groww answered with nothing (recorded, not treated as zero prices)."""

    __tablename__ = "minute_fetches"
    __table_args__ = (UniqueConstraint("groww_symbol", "trade_date", name="uq_minute_fetch"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    groww_symbol: Mapped[str] = mapped_column(String(64))
    trade_date: Mapped[date] = mapped_column(Date)
    rows: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10))  # OK / EMPTY / ERROR
    error: Mapped[str | None] = mapped_column(String(300))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OptionContract(Base):
    """Option contracts (live and expired) listed by Groww's get_contracts, plus every expiry
    date get_expiries returned (a row with strike NULL marks "expiry listed")."""

    __tablename__ = "option_contracts"
    __table_args__ = (UniqueConstraint("groww_symbol", name="uq_option_contract"),
                      Index("ix_option_contract_lookup", "underlying", "expiry_date"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    underlying: Mapped[str] = mapped_column(String(32))
    expiry_date: Mapped[date] = mapped_column(Date)
    strike: Mapped[float | None] = mapped_column(Price)
    option_type: Mapped[str | None] = mapped_column(String(2))  # CE / PE; NULL = expiry marker
    groww_symbol: Mapped[str] = mapped_column(String(64))


class PaperTrade(Base):
    """A 9/21 EMA PAPER trade from the live paper engine. Never a real order.

    One row per (day, timeframe, mode, entry signal). status OPEN until the exit is filled.
    """

    __tablename__ = "paper_trades"
    __table_args__ = (UniqueConstraint("trade_date", "timeframe", "mode", "signal_time",
                                       name="uq_paper_trade"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    timeframe: Mapped[int] = mapped_column(Integer)  # minutes
    mode: Mapped[str] = mapped_column(String(10))  # ITM_BUY / ATM_BUY / ATM_SELL
    signal_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    direction: Mapped[int] = mapped_column(Integer)  # +1 long NIFTY, -1 short
    side: Mapped[str] = mapped_column(String(4))  # BUY / SELL
    option_type: Mapped[str] = mapped_column(String(2))
    strike: Mapped[float] = mapped_column(Price)
    expiry_date: Mapped[date] = mapped_column(Date)
    groww_symbol: Mapped[str] = mapped_column(String(64))
    cross_number: Mapped[int] = mapped_column(Integer)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    entry_price: Mapped[float] = mapped_column(Price)
    entry_fill: Mapped[float] = mapped_column(Price)
    nifty_entry: Mapped[float | None] = mapped_column(Price)
    exit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_price: Mapped[float | None] = mapped_column(Price)
    exit_fill: Mapped[float | None] = mapped_column(Price)
    exit_reason: Mapped[str | None] = mapped_column(String(20))
    nifty_exit: Mapped[float | None] = mapped_column(Price)
    stale_exit: Mapped[bool] = mapped_column(Boolean, default=False)
    gross_pnl: Mapped[float | None] = mapped_column(Numeric(14, 2))
    slippage: Mapped[float | None] = mapped_column(Numeric(14, 2))
    charges: Mapped[float | None] = mapped_column(Numeric(14, 2))
    net_pnl: Mapped[float | None] = mapped_column(Numeric(14, 2))
    status: Mapped[str] = mapped_column(String(6))  # OPEN / CLOSED
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SignalTrade(Base):
    """A PAPER trade from the multi-strategy signal engine (app/live). Never a real order.

    `trade_no` is the trade number shown in every Telegram message and the day report:
    YYYYMMDD-NN ("Trade #20261105-01"), NN counting from 01 each IST trading day across all
    strategies (RK 2026-11-05). `id` is only the database key. One row per (strategy, day,
    entry signal)."""

    __tablename__ = "signal_trades"
    __table_args__ = (UniqueConstraint("strategy", "trade_date", "signal_time",
                                       name="uq_signal_trade"),
                      UniqueConstraint("trade_no", name="uq_signal_trade_no"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trade_no: Mapped[str | None] = mapped_column(String(16))  # 20261105-01
    strategy: Mapped[str] = mapped_column(String(16))  # EMA-921 / ORB-1.0 / ET-1.0
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    mode: Mapped[str] = mapped_column(String(10))  # ITM_BUY / ATM_BUY / ATM_SELL
    signal_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    direction: Mapped[int] = mapped_column(Integer)  # +1 long NIFTY, -1 short
    side: Mapped[str] = mapped_column(String(4))
    option_type: Mapped[str] = mapped_column(String(2))
    strike: Mapped[float] = mapped_column(Price)
    expiry_date: Mapped[date] = mapped_column(Date)
    groww_symbol: Mapped[str] = mapped_column(String(64))
    lot_size: Mapped[int] = mapped_column(Integer)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    entry_price: Mapped[float] = mapped_column(Price)
    entry_fill: Mapped[float] = mapped_column(Price)
    nifty_entry: Mapped[float | None] = mapped_column(Price)
    exit_plan: Mapped[str] = mapped_column(String(300))
    exit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_price: Mapped[float | None] = mapped_column(Price)
    exit_fill: Mapped[float | None] = mapped_column(Price)
    exit_reason: Mapped[str | None] = mapped_column(String(20))
    nifty_exit: Mapped[float | None] = mapped_column(Price)
    stale_exit: Mapped[bool] = mapped_column(Boolean, default=False)
    gross_pnl: Mapped[float | None] = mapped_column(Numeric(14, 2))
    slippage: Mapped[float | None] = mapped_column(Numeric(14, 2))
    charges: Mapped[float | None] = mapped_column(Numeric(14, 2))
    net_pnl: Mapped[float | None] = mapped_column(Numeric(14, 2))
    status: Mapped[str] = mapped_column(String(6))  # OPEN / CLOSED
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StockMember(Base):
    """The intraday stock universe: current NIFTY 50 and NIFTY Next 50 members (NSE's own lists,
    downloaded by `tradex.sh stock-load`). Today's members only, so older history has
    survivorship bias (stocks that left the index are missing)."""

    __tablename__ = "stock_members"
    __table_args__ = (UniqueConstraint("symbol", name="uq_stock_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32))  # NSE trading symbol, e.g. RELIANCE
    index_name: Mapped[str] = mapped_column(String(20))  # NIFTY 50 / NIFTY Next 50
    groww_symbol: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StockCandle(Base):
    """5-minute candles for intraday stock work (and NIFTY itself, symbol "NIFTY")."""

    __tablename__ = "stock_candles"
    __table_args__ = (UniqueConstraint("symbol", "candle_time", name="uq_stock_candle"),
                      Index("ix_stock_candles_symbol_date", "symbol", "trade_date"))

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32))
    trade_date: Mapped[date] = mapped_column(Date)  # IST trading day
    candle_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # candle START, UTC
    open: Mapped[float | None] = mapped_column(Price)
    high: Mapped[float | None] = mapped_column(Price)
    low: Mapped[float | None] = mapped_column(Price)
    close: Mapped[float | None] = mapped_column(Price)
    volume: Mapped[int | None] = mapped_column(BigInteger)


class StockFetch(Base):
    """One row per (symbol, 30-day window) asked from Groww, so `stock-load` resumes."""

    __tablename__ = "stock_fetches"
    __table_args__ = (UniqueConstraint("symbol", "window_start", name="uq_stock_fetch"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32))
    window_start: Mapped[date] = mapped_column(Date)
    window_end: Mapped[date] = mapped_column(Date)
    rows: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10))  # OK / EMPTY / PARTIAL / ERROR
    error: Mapped[str | None] = mapped_column(String(300))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
