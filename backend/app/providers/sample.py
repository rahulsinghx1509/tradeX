"""SAMPLE data provider: synthetic prices for building and testing the pipeline.

!!! These are NOT real market prices. !!!
Every row it produces is stored with source = "sample" and is_sample = TRUE,
and the dashboard shows a "SAMPLE DATA" banner whenever it is displayed.

How it works: a seeded random walk of hourly candles from 2020-01-01. Because the
seed is fixed, asking for the same time window twice returns identical numbers,
which lets us test that repeated ingestion does not create duplicates.
Daily candles are built from the hourly ones, so both timeframes agree.
"""

from __future__ import annotations

import hashlib
from calendar import monthrange
from datetime import UTC, date, datetime, timedelta

import numpy as np

from app.core.market_time import bar_starts_for_day, to_ist, trading_days
from app.providers.base import Candle, Capability, InstrumentInfo, MarketDataProvider, Snapshot

ANCHOR = date(2020, 1, 1)
# Starting levels and lot sizes are illustrative only. Real lot sizes must come
# from the provider's instrument list (they change about every 6 months).
_START_LEVEL = {"NIFTY": 12000.0, "BANKNIFTY": 31000.0, "FINNIFTY": 13000.0}
_SAMPLE_LOT = {"NIFTY": 65, "BANKNIFTY": 30, "FINNIFTY": 60}
_SAMPLE_INDICES = frozenset({"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"})
_SAMPLE_STOCK_LEVEL = 1000.0
_SAMPLE_STOCK_LOT = 500
_HOURLY_VOL = 0.0035
_HOURLY_DRIFT = 0.00003


def _seed(*parts: str) -> int:
    return int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:8], 16)


def _last_tuesday(year: int, month: int) -> date:
    d = date(year, month, monthrange(year, month)[1])
    while d.weekday() != 1:
        d -= timedelta(days=1)
    return d


def sample_monthly_expiries(today: date, count: int) -> list[date]:
    """Synthetic monthly expiries (last Tuesday of the month). Real ones come from Groww."""
    out, y, m = [], today.year, today.month
    while len(out) < count:
        exp = _last_tuesday(y, m)
        if exp >= today:
            out.append(exp)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


class SampleProvider(MarketDataProvider):
    name = "sample"
    is_sample = True

    def __init__(self, today: date | None = None):
        self._today = today
        self._cache: dict[tuple[str, date], tuple[list[datetime], np.ndarray]] = {}

    # ---------------------------------------------------------------- internals
    def _hourly_path(self, symbol: str, until: date) -> tuple[list[datetime], np.ndarray]:
        """Hourly OHLC array (n x 4) for an index from ANCHOR to `until`."""
        key = (symbol, until)
        if key not in self._cache:
            times = [t for d in trading_days(ANCHOR, until, frozenset()) for t in bar_starts_for_day(d, "1h")]
            # Separate generators per series, so the first N values are the same
            # whatever `until` is (history never changes when we ask again later).
            ret_rng = np.random.default_rng(_seed("index", symbol))
            hi_rng = np.random.default_rng(_seed("high", symbol))
            lo_rng = np.random.default_rng(_seed("low", symbol))
            returns = ret_rng.normal(_HOURLY_DRIFT, _HOURLY_VOL, size=len(times))
            default = 10000.0 if symbol in _SAMPLE_INDICES else _SAMPLE_STOCK_LEVEL
            closes = _START_LEVEL.get(symbol, default) * np.exp(np.cumsum(returns))
            opens = np.concatenate([[closes[0] / np.exp(returns[0])], closes[:-1]])
            wiggle = np.abs(
                np.column_stack(
                    [
                        hi_rng.normal(0, _HOURLY_VOL / 2, size=len(times)),
                        lo_rng.normal(0, _HOURLY_VOL / 2, size=len(times)),
                    ]
                )
            )
            highs = np.maximum(opens, closes) * (1 + wiggle[:, 0])
            lows = np.minimum(opens, closes) * (1 - wiggle[:, 1])
            ohlc = np.round(np.column_stack([opens, highs, lows, closes]), 2)
            self._cache[key] = (times, ohlc)
        return self._cache[key]

    @staticmethod
    def _parse_future(inst: InstrumentInfo) -> tuple[str, date]:
        assert inst.expiry_date is not None
        return inst.symbol, inst.expiry_date

    def _today_ist(self) -> date:
        return self._today or to_ist(datetime.now(UTC)).date()

    # ---------------------------------------------------------------- interface
    def list_instruments(self, underlyings: list[str], futures_expiries: int) -> list[InstrumentInfo]:
        out: list[InstrumentInfo] = []
        for sym in underlyings:
            kind = "INDEX" if sym in _SAMPLE_INDICES else "STOCK"
            out.append(
                InstrumentInfo(
                    symbol=sym, instrument_type=kind, exchange="NSE", segment="CASH",
                    provider_symbol=f"SAMPLE-NSE-{sym}", trading_symbol=sym,
                )
            )
            for exp in sample_monthly_expiries(self._today_ist(), futures_expiries):
                out.append(
                    InstrumentInfo(
                        symbol=sym, instrument_type="FUTURE", exchange="NSE", segment="FNO",
                        provider_symbol=f"SAMPLE-NSE-{sym}-{exp:%d%b%y}-FUT",
                        trading_symbol=f"{sym}{exp:%y%b}FUT".upper(),
                        expiry_date=exp,
                        lot_size=_SAMPLE_LOT.get(sym, None if kind == "INDEX" else _SAMPLE_STOCK_LOT),
                    )
                )
        return out

    def get_candles(
        self, instrument: InstrumentInfo, timeframe: str, start: datetime, end: datetime
    ) -> list[Candle]:
        until = to_ist(end).date()
        if instrument.expiry_date:
            until = min(until, instrument.expiry_date)
        times, ohlc = self._hourly_path(instrument.symbol, until)
        if not times:
            return []

        is_future = instrument.instrument_type == "FUTURE"
        if is_future:
            # A futures contract trades for ~3 months before expiry at a small premium.
            listed = instrument.expiry_date - timedelta(days=90)
            rng = np.random.default_rng(_seed("future", instrument.provider_symbol))
            premium = 1 + rng.uniform(0.002, 0.006)
            oi_base = rng.integers(8_000_000, 15_000_000)

        hourly: list[Candle] = []
        for i, t in enumerate(times):
            if is_future and to_ist(t).date() < listed:
                continue
            o, h, low, c = (float(x) for x in ohlc[i])
            if is_future:
                o, h, low, c = (round(v * premium, 2) for v in (o, h, low, c))
                vol_rng = np.random.default_rng(_seed("vol", instrument.provider_symbol, t.isoformat()))
                volume = int(vol_rng.integers(50_000, 400_000))
                oi = int(oi_base * (1 + 0.15 * np.sin(i / 40)))
            elif instrument.instrument_type == "STOCK":
                vol_rng = np.random.default_rng(_seed("vol", instrument.provider_symbol, t.isoformat()))
                volume, oi = int(vol_rng.integers(200_000, 2_000_000)), None  # shares trade; no OI
            else:
                volume, oi = None, None  # indices have no traded volume or OI
            hourly.append(Candle(t, o, h, low, c, volume, oi))

        candles = hourly if timeframe == "1h" else _to_daily(hourly)
        return [c for c in candles if start <= c.time <= end]

    def get_snapshot(self, instrument: InstrumentInfo) -> Snapshot | None:
        now = datetime.now(UTC)
        recent = self.get_candles(instrument, "1h", now - timedelta(days=7), now)
        if not recent:
            return None
        last = recent[-1]
        return Snapshot(
            observed_at=last.time, last_price=last.close, day_high=last.high, day_low=last.low,
            volume=last.volume, open_interest=last.open_interest,
        )

    def capabilities(self) -> dict[str, Capability]:
        return {
            "historical_candles_1d": Capability.SUPPORTED,
            "historical_candles_1h": Capability.SUPPORTED,
            "futures_open_interest": Capability.SUPPORTED,
            "quotes": Capability.SUPPORTED,
            "option_chain": Capability.UNSUPPORTED,
        }


def _to_daily(hourly: list[Candle]) -> list[Candle]:
    """Roll hourly candles into one candle per trading day (stored at 09:15 IST)."""
    by_day: dict[date, list[Candle]] = {}
    for c in hourly:
        by_day.setdefault(to_ist(c.time).date(), []).append(c)
    out = []
    for d in sorted(by_day):
        bars = by_day[d]
        vols = [b.volume for b in bars if b.volume is not None]
        out.append(
            Candle(
                time=bar_starts_for_day(d, "1d")[0],
                open=bars[0].open,
                high=max(b.high for b in bars),
                low=min(b.low for b in bars),
                close=bars[-1].close,
                volume=sum(vols) if vols else None,
                open_interest=bars[-1].open_interest,
            )
        )
    return out
