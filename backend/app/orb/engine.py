"""ORB-1.0 engine: opening range -> one breakout trade a day per mode (rules in app/orb/__init__.py).

Reuses the 9/21 EMA engine for everything after the signal: contract choice, expiry, fills,
slippage, costs and the Trade/Result records, so the two strategies are scored the same way.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, time

import pandas as pd

from app.core.market_time import IST
from app.ema921 import bars as B
from app.ema921 import engine as E

VERSION = "ORB-1.0"
RANGE_END = time(9, 30)  # the range is 09:15 to 09:29 (candles starting before 09:30)
FIRST_SIGNAL = time(9, 35)
LAST_SIGNAL = time(13, 0)
TIMEFRAME = 5


def config(base: E.Config | None = None) -> E.Config:
    """The EMA Config with ORB-1.0's fixed window (no trailing stop: the range is the stop)."""
    return replace(base or E.Config(), timeframe=TIMEFRAME, start=FIRST_SIGNAL, cutoff=LAST_SIGNAL,
                   stop_atr_mult=None, cross_numbers=None, trend_filter=False)


@dataclass(frozen=True)
class Range:
    day: date
    high: float
    low: float


def opening_ranges(spot: pd.DataFrame) -> dict[date, Range]:
    """{day: opening range} from the 1-minute candles 09:15-09:29. Needs all 15 candles."""
    if spot.empty:
        return {}
    df = spot.assign(t=pd.to_datetime(spot["time"], utc=True).dt.tz_convert(IST))
    tod = df["t"].dt.hour * 60 + df["t"].dt.minute
    first = df[(tod >= 9 * 60 + 15) & (tod < RANGE_END.hour * 60 + RANGE_END.minute)]
    out = {}
    for d, g in first.groupby(first["t"].dt.date):
        if len(g) < 15 or g["high"].isna().any() or g["low"].isna().any():
            continue
        out[d] = Range(d, float(g["high"].max()), float(g["low"].min()))
    return out


def breakout(day_bars: pd.DataFrame, rng: Range) -> B.Cross | None:
    """The day's first 5-minute bar closing outside the range, between 09:35 and 13:00."""
    for r in day_bars.itertuples(index=False):
        t = r.bar_close.time()
        if t < FIRST_SIGNAL:
            continue
        if t > LAST_SIGNAL:
            break
        c = float(r.close)
        direction = 1 if c > rng.high else -1 if c < rng.low else 0
        if direction:
            return B.Cross(r.day, r.bar_start, r.bar_close, direction, c, 1)
    return None


def signals(spot: pd.DataFrame) -> dict[date, tuple[Range, B.Cross]]:
    """{day: (range, breakout)} for every day that has one."""
    bars = B.resample(spot, TIMEFRAME)
    ranges = opening_ranges(spot)
    out = {}
    for d, g in bars.groupby("day", sort=True):
        rng = ranges.get(d)
        if rng is None:
            continue
        c = breakout(g, rng)
        if c is not None:
            out[d] = (rng, c)
    return out


def _range_stop(day_bars: pd.DataFrame, entry: B.Cross, rng: Range, before: pd.Timestamp):
    """First 5-minute bar after the entry, closing before `before`, back beyond the far side."""
    for r in day_bars.itertuples(index=False):
        if r.bar_close <= entry.bar_close:
            continue
        if r.bar_close >= before:
            break
        c = float(r.close)
        if (entry.direction > 0 and c < rng.low) or (entry.direction < 0 and c > rng.high):
            return r
    return None


def run(spot: pd.DataFrame, options: E.OptionData, start: date, end: date,
        cfg: E.Config | None = None, modes: tuple[str, ...] = E.MODES,
        holidays: frozenset[date] | None = None) -> E.Result:
    cfg = config(cfg)
    bars = B.resample(spot, TIMEFRAME)
    bar_index = {t: i for i, t in enumerate(bars["bar_close"])}
    bars_by_day = {d: g.reset_index(drop=True) for d, g in bars.groupby("day", sort=True)}
    sigs = signals(spot)
    days, why = E.no_trade_days(spot, start, end, cfg, holidays)
    expiries = options.expiries()
    trades: dict[str, list[E.Trade]] = {m: [] for m in modes}
    skips: list[E.Skip] = []
    cache: dict = {}

    def minutes(symbol: str, day: date):
        key = (symbol, day)
        if key not in cache:
            cache[key] = E._Minutes(options.minutes(symbol, day))
        return cache[key]

    spot_px = E._SpotPrices(spot)
    for d in days:
        if d not in sigs:
            why[d] = "NO_BREAK"
            continue
        rng, entry = sigs[d]
        square = E._ist(d, cfg.square_off)
        made = 0
        for mode in modes:
            tr = E._open(mode, d, entry, options, expiries, cfg, minutes, skips)
            if tr is None:
                continue
            hit = _range_stop(bars_by_day[d], entry, rng, square)
            if hit is not None:
                E._close(tr, hit.bar_close, "RANGE_STOP", float(hit.close), cfg, minutes, bar_index)
            else:
                E._close(tr, square, "SQUARE_OFF", spot_px.at(square), cfg, minutes, bar_index)
            trades[mode].append(tr)
            made += 1
        if not made:
            why[d] = "ENTRIES_SKIPPED (no option data)"
    return E.Result(cfg, trades, skips, dict(sorted(why.items())), days)
