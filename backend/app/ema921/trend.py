"""ET-1.0: the 9/21 EMA cross, taken only when it goes with the day's trend (backtest only).

Rules, fixed on 2026-11-02 before any result was seen (docs/01 §22). Everything else is the
normal 9/21 EMA rule set (rules doc https://claude.ai/artifact/PiDc6FGqdqS6xnc1z6sZq4):

1. Signal bars: 5 minutes. Entry window 09:30 to 14:00 (signal bar close). Square-off 15:00.
2. A cross opens a trade only if BOTH hold at the signal bar's close:
   a. Higher timeframe: on the last 15-minute bar that has closed by then, EMA 9 is above EMA 21
      for a long (below for a short). The 15-minute EMAs run across days like the 5-minute ones;
      before 21 15-minute bars exist nothing is allowed.
   b. Day side: the signal bar's close is above the day's 09:15 open for a long (below for a
      short). This stands in for VWAP, which NIFTY spot cannot have (an index has no volume).
   An exact tie fails both checks.
3. Every opposite cross still closes the open trade, filter or not. It reverses only if the new
   direction passes the filter too.
4. No trailing stop (the 2 x ATR stop cut the trend-day winners this filter is meant to keep).
5. Same three modes, 65 lot, slippage 0.5% + 0.5 point a side, full cost stack.
6. Pass bar on the 2 development years at default slippage, per mode: net > 0, profit factor
   >= 1.10, each year net > 0, max drawdown < 50% of ₹1L. Only the best passing mode of ET-1.0
   or ORB-1.0 (app/orb) gets one run on the held-back year (Nov 2023 - Oct 2024): net > 0 and
   profit factor >= 1.05. Nothing is re-tuned on the same data.

If you know SQL: `blocked` is a join of every 5-minute cross to the latest 15-minute bar at or
before it (an "as-of" join), plus the day's first 1-minute open.
"""

from __future__ import annotations

import bisect
from datetime import time

import pandas as pd

from app.core.market_time import IST
from app.ema921 import bars as B

VERSION = "ET-1.0"
TREND_TIMEFRAME = 15
SIGNAL_TIMEFRAME = 5
ENTRY_START = time(9, 30)
ENTRY_CUTOFF = time(14, 0)


def day_opens(spot: pd.DataFrame) -> dict:
    """{day: open of its 09:15 one-minute candle}. A day without that candle has no entry."""
    if spot.empty:
        return {}
    ist = pd.to_datetime(spot["time"], utc=True).dt.tz_convert(IST)
    first = spot[(ist.dt.hour == 9) & (ist.dt.minute == 15)]
    days = ist[first.index].dt.date
    return {d: float(o) for d, o in zip(days, first["open"], strict=True) if pd.notna(o)}


def trend_at(trend_bars: pd.DataFrame):
    """A function t -> +1 / -1 / 0: the sign of EMA9 - EMA21 on the last 15m bar closed by t."""
    closes = list(trend_bars["bar_close"])
    diff = (trend_bars["ema9"] - trend_bars["ema21"]).round(B.TIE_DECIMALS).tolist()

    def sign(t: pd.Timestamp) -> int:
        i = bisect.bisect_right(closes, t) - 1
        if i < B.SLOW:  # warm-up: not enough 15m bars yet
            return 0
        return 1 if diff[i] > 0 else -1 if diff[i] < 0 else 0

    return sign


def allowed(c: B.Cross, trend_sign, opens: dict) -> bool:
    if trend_sign(c.bar_close) != c.direction:
        return False
    o = opens.get(c.day)
    if o is None:
        return False
    return c.spot > o if c.direction > 0 else c.spot < o


def blocked(spot: pd.DataFrame, crosses: list[B.Cross]) -> frozenset:
    """bar_close of every cross that fails rule 2 (its entry is not taken; its exit still is)."""
    trend_bars = B.add_emas(B.resample(spot, TREND_TIMEFRAME))
    sign = trend_at(trend_bars)
    opens = day_opens(spot)
    return frozenset(c.bar_close for c in crosses if not allowed(c, sign, opens))
