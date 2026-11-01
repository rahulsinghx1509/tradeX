"""From NIFTY spot 1-minute candles to signal bars, EMAs and crosses.

If you know SQL: `resample` is a GROUP BY (trading day, bucket), where
bucket = minutes since 09:15 // bar length. `crosses` then looks at each bar and the one
before it (like LAG() OVER (ORDER BY bar_start)).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import pandas as pd

from app.core.market_time import IST

SESSION_MINUTES = 375  # 09:15 to 15:30
OPEN = time(9, 15)
FAST, SLOW = 9, 21
TIMEFRAMES = (5, 15, 30)
TIE_DECIMALS = 4  # EMA difference rounded to this before deciding a cross (rules doc #2)


def _ist_open(d: date) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(d, OPEN), tz=IST)


def resample(minutes: pd.DataFrame, tf: int) -> pd.DataFrame:
    """Group 1-minute candles into `tf`-minute bars aligned to the 09:15 open.

    `minutes` has columns time (tz-aware), open, high, low, close. Candles outside
    09:15-15:30 IST are dropped. The result has one row per bar with: day, bar_start and
    bar_close (IST), open, high, low, close, minutes (how many 1m candles it has) and
    expected (how many it should have; the last 30m bar of the day is only 15 minutes).
    """
    if tf not in TIMEFRAMES:
        raise ValueError(f"timeframe must be one of {TIMEFRAMES}, got {tf}")
    cols = ["day", "bar_start", "bar_close", "open", "high", "low", "close", "minutes", "expected"]
    if minutes.empty:
        return pd.DataFrame(columns=cols)
    df = minutes.copy()
    ist = pd.to_datetime(df["time"], utc=True).dt.tz_convert(IST)
    df["day"] = ist.dt.date
    since_open = (ist - ist.dt.normalize()).dt.total_seconds() // 60 - (9 * 60 + 15)
    df = df[(since_open >= 0) & (since_open < SESSION_MINUTES)].copy()
    df["bucket"] = (since_open[df.index] // tf).astype(int)
    df = df.sort_values("time")
    g = df.groupby(["day", "bucket"], sort=True)
    bars = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                 close=("close", "last"), minutes=("close", "size")).reset_index()
    starts = [_ist_open(d) + timedelta(minutes=int(b) * tf) for d, b in zip(bars["day"], bars["bucket"],
                                                                            strict=True)]
    bars["bar_start"] = starts
    bars["expected"] = [min(tf, SESSION_MINUTES - int(b) * tf) for b in bars["bucket"]]
    bars["bar_close"] = [s + timedelta(minutes=int(e)) for s, e in zip(starts, bars["expected"],
                                                                        strict=True)]
    return bars[cols].reset_index(drop=True)


def add_emas(bars: pd.DataFrame) -> pd.DataFrame:
    """EMA 9 and 21 of the closes, running continuously across days (rules doc #3).

    Standard EMA, alpha = 2 / (n + 1), seeded with the first close (pandas adjust=False).
    """
    out = bars.copy()
    out["ema9"] = out["close"].astype(float).ewm(span=FAST, adjust=False).mean()
    out["ema21"] = out["close"].astype(float).ewm(span=SLOW, adjust=False).mean()
    return out


def add_atr(bars: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    """Wilder's ATR(n) of the bars, running continuously across days like the EMAs.

    True range = the largest of high-low, |high - previous close|, |low - previous close|.
    Wilder smoothing is an EMA with alpha = 1/n. Like a SQL running aggregate over the bars.
    """
    out = bars.copy()
    h, lo = out["high"].astype(float), out["low"].astype(float)
    prev = out["close"].astype(float).shift(1)
    tr = pd.concat([h - lo, (h - prev).abs(), (lo - prev).abs()], axis=1).max(axis=1)
    out["atr"] = tr.ewm(alpha=1 / n, adjust=False).mean()
    return out


@dataclass(frozen=True)
class Cross:
    day: date
    bar_start: pd.Timestamp  # IST
    bar_close: pd.Timestamp  # IST: the moment the cross is known
    direction: int  # +1 bullish (EMA9 above EMA21), -1 bearish
    spot: float  # close of the signal bar
    number: int  # 1 = first cross of the day (counted from 09:15)


def crosses(bars: pd.DataFrame) -> list[Cross]:
    """Every EMA cross on a closed bar, after the first 21 warm-up bars.

    Bullish: (EMA9 - EMA21) turns > 0 after being < 0 (or only ever 0). Bearish: the mirror.
    An exact tie is not a cross, and neither is "above, tie, above again": the sign has to
    really flip from the last non-zero side (rules doc #2).
    """
    if len(bars) <= SLOW:
        return []
    b = add_emas(bars) if "ema9" not in bars else bars
    diff = (b["ema9"] - b["ema21"]).round(TIE_DECIMALS).tolist()
    out: list[Cross] = []
    per_day: dict[date, int] = {}
    side = 0  # last non-zero sign of the difference
    for i in range(len(b)):
        cur = diff[i]
        now = 1 if cur > 0 else -1 if cur < 0 else 0
        direction = now if now and now != side else 0
        if now:
            side = now
        if not direction or i < SLOW:
            continue
        row = b.iloc[i]
        per_day[row["day"]] = per_day.get(row["day"], 0) + 1
        out.append(Cross(row["day"], row["bar_start"], row["bar_close"], direction,
                         float(row["close"]), per_day[row["day"]]))
    return out


def complete_days(minutes: pd.DataFrame, max_missing: int = 0) -> dict[date, int]:
    """Minutes missing per trading day (09:15-15:29). A day is tradable if it misses at most
    `max_missing` (rules doc #13: by default any gap makes it a no-trade day)."""
    if minutes.empty:
        return {}
    ist = pd.to_datetime(minutes["time"], utc=True).dt.tz_convert(IST)
    since_open = (ist - ist.dt.normalize()).dt.total_seconds() // 60 - (9 * 60 + 15)
    inside = minutes[(since_open >= 0) & (since_open < SESSION_MINUTES)]
    days = ist[inside.index].dt.date
    counts = pd.Series(1, index=inside.index).groupby(days).size()
    return {d: SESSION_MINUTES - int(n) for d, n in counts.items()}
