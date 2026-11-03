"""Bar volume and relative volume (RVOL) from 1-minute futures candles (rules 2-4 in __init__).

If you know SQL: bar_volumes is a GROUP BY (day, 5-minute bucket) with SUM(volume) that turns
NULL when any minute is missing; rvol is AVG(volume) OVER (PARTITION BY slot ORDER BY day ROWS
BETWEEN 20 PRECEDING AND 1 PRECEDING), counted only over bars that have a volume.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.market_time import IST, from_db
from app.db.models import MinuteCandle
from app.ema921 import bars as B
from app.volume import FUT_SYMBOL, LOOKBACK_DAYS, TIMEFRAME

SESSION_MINUTES = 375  # 09:15-15:30


def load_volume(session: Session, start: date, end: date, symbol: str = FUT_SYMBOL) -> pd.DataFrame:
    """Columns time (UTC, tz-aware) and volume (NaN when Groww gave none)."""
    rows = session.execute(
        select(MinuteCandle.candle_time, MinuteCandle.volume)
        .where(MinuteCandle.groww_symbol == symbol, MinuteCandle.trade_date >= start,
               MinuteCandle.trade_date <= end)
        .order_by(MinuteCandle.candle_time)).all()
    df = pd.DataFrame(rows, columns=["time", "volume"])
    if not df.empty:
        df["time"] = [from_db(t) for t in df["time"]]
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    return df


def bar_volumes(minutes: pd.DataFrame, tf: int = TIMEFRAME) -> pd.DataFrame:
    """One row per tf-minute bar: day, slot (0 = 09:15 bar), bar_close (IST), volume.

    volume is NaN unless the bar has all tf one-minute candles, each with a volume."""
    cols = ["day", "slot", "bar_close", "volume"]
    if minutes.empty:
        return pd.DataFrame(columns=cols)
    df = minutes.copy()
    ist = pd.to_datetime(df["time"], utc=True).dt.tz_convert(IST)
    since_open = (ist - ist.dt.normalize()).dt.total_seconds() // 60 - (9 * 60 + 15)
    keep = (since_open >= 0) & (since_open < SESSION_MINUTES)
    df, ist, since_open = df[keep].copy(), ist[keep], since_open[keep]
    df["day"] = ist.dt.date
    df["slot"] = (since_open // tf).astype(int)
    g = df.groupby(["day", "slot"], sort=True)
    out = g.agg(volume=("volume", lambda v: v.sum(min_count=1)),
                have=("volume", "count"), rows=("volume", "size")).reset_index()
    expected = [min(tf, SESSION_MINUTES - s * tf) for s in out["slot"]]
    complete = (out["have"] == expected) & (out["rows"] == expected)
    out.loc[~complete, "volume"] = float("nan")
    out["bar_close"] = [B._ist_open(d) + timedelta(minutes=int(s) * tf + int(e))
                        for d, s, e in zip(out["day"], out["slot"], expected, strict=True)]
    return out[cols]


def rvol(bars: pd.DataFrame, lookback: int = LOOKBACK_DAYS) -> pd.DataFrame:
    """bars + columns avg (same-slot average over the previous `lookback` days with a volume)
    and rvol = volume / avg. Missing stays missing."""
    out = bars.copy()
    out["avg"] = float("nan")
    out["rvol"] = float("nan")
    if out.empty:
        return out
    have = out[out["volume"].notna()].sort_values(["slot", "day"])
    avg = (have.groupby("slot")["volume"]
           .transform(lambda v: v.shift(1).rolling(lookback, min_periods=lookback).mean()))
    out.loc[avg.index, "avg"] = avg
    ok = out["avg"].notna() & (out["avg"] > 0) & out["volume"].notna()
    out.loc[ok, "rvol"] = out.loc[ok, "volume"] / out.loc[ok, "avg"]
    return out


def _key(t) -> pd.Timestamp:
    """UTC instant. A naive time is UTC (how SQLite hands back what TradeX stores)."""
    ts = pd.Timestamp(t)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


class RvolLookup:
    """RVOL by signal-bar close time. `at(t)` -> (rvol, volume, avg); None where missing."""

    def __init__(self, minutes: pd.DataFrame, lookback: int = LOOKBACK_DAYS, tf: int = TIMEFRAME):
        table = rvol(bar_volumes(minutes, tf), lookback)
        self._by_close = {_key(r.bar_close): r for r in table.itertuples(index=False)}

    def at(self, bar_close) -> tuple[float | None, float | None, float | None]:
        r = self._by_close.get(_key(bar_close))
        if r is None:
            return None, None, None

        def num(x):
            return None if pd.isna(x) else float(x)
        return num(r.rvol), num(r.volume), num(r.avg)

    def filter(self, threshold: float) -> Callable[[B.Cross], bool]:
        """Entry filter for the engines: True only if the signal bar's RVOL >= threshold."""
        def allowed(c: B.Cross) -> bool:
            value = self.at(c.bar_close)[0]
            return value is not None and value >= threshold
        return allowed

    def coverage(self) -> tuple[int, int]:
        """(bars with an RVOL, all bars)."""
        rs = [r for r in self._by_close.values() if not pd.isna(r.rvol)]
        return len(rs), len(self._by_close)


def note(lookup: RvolLookup | None, bar_close) -> str:
    """One line for a PAPER entry alert (information only, rule 7)."""
    t = _key(bar_close).tz_convert(IST)
    if lookup is None:
        return "Volume (NIFTY fut): not available"
    value, vol, avg = lookup.at(bar_close)
    if vol is None:
        return f"Volume (NIFTY fut, {t:%H:%M} bar): not available"
    if value is None:
        return f"Volume (NIFTY fut, {t:%H:%M} bar): {vol:,.0f} (no {LOOKBACK_DAYS}-day average yet)"
    word = "above usual" if value >= 1.2 else "below usual" if value <= 0.8 else "about usual"
    return (f"Volume (NIFTY fut, {t:%H:%M} bar): {vol:,.0f} = {value:.2f}x its {LOOKBACK_DAYS}-day "
            f"average for that time ({word}). Info only, not a rule.")
