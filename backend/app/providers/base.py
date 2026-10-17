"""The provider "adapter" contract.

Every market-data source (Groww, the sample generator, a future backup vendor)
implements the same small interface. The rest of TradeX only talks to this
interface, so switching providers never means rewriting the pipeline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class Capability(StrEnum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    NOT_VERIFIED = "NOT_VERIFIED"
    REQUIRES_PAID_PLAN = "REQUIRES_PAID_PLAN"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"


class ProviderError(RuntimeError):
    """The provider failed or could not be reached. Never hidden: it is logged and reported."""


@dataclass(frozen=True)
class InstrumentInfo:
    symbol: str  # underlying, e.g. NIFTY
    instrument_type: str  # INDEX / STOCK / FUTURE / OPTION
    exchange: str
    segment: str  # CASH / FNO
    provider_symbol: str  # identifier the provider uses for candles
    trading_symbol: str | None = None  # identifier the provider uses for quotes
    expiry_date: date | None = None
    strike_price: float | None = None
    option_type: str | None = None
    lot_size: int | None = None


@dataclass(frozen=True)
class Candle:
    time: datetime  # candle START, timezone-aware UTC
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: int | None = None  # None = not provided (never 0 as a stand-in)
    open_interest: int | None = None


@dataclass(frozen=True)
class Snapshot:
    observed_at: datetime  # provider's timestamp, UTC
    last_price: float | None
    bid_price: float | None = None
    ask_price: float | None = None
    day_open: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    previous_close: float | None = None
    volume: int | None = None
    open_interest: int | None = None
    oi_change: int | None = None
    implied_volatility: float | None = None


class MarketDataProvider(ABC):
    name: str
    is_sample: bool = False

    @abstractmethod
    def list_instruments(self, underlyings: list[str], futures_expiries: int) -> list[InstrumentInfo]:
        """The index or stock itself plus the next `futures_expiries` futures contracts per underlying."""

    @abstractmethod
    def get_candles(
        self, instrument: InstrumentInfo, timeframe: str, start: datetime, end: datetime
    ) -> list[Candle]:
        """Candles with start time in [start, end]. Times are UTC."""

    @abstractmethod
    def get_snapshot(self, instrument: InstrumentInfo) -> Snapshot | None:
        """Latest quote, or None if the provider has none."""

    @abstractmethod
    def capabilities(self) -> dict[str, Capability]:
        """What this provider can do, as far as has been verified."""
