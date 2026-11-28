"""Market condition of each day, from daily data up to the PREVIOUS trading day only.

If you know SQL: `features` is one row per trading day with window functions over the daily
closes (ADX, EMA20, RSI(2), 20-day volatility, India VIX's 60-day median), and `labels` looks up
the row of the day before (like LAG() OVER (ORDER BY day)).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from app import agent as A
from app.premium.engine import vj_blocked
from app.services import indicators as ind


@dataclass(frozen=True)
class Day:
    """What is known before `day` opens. None = missing (that day is NO TRADE)."""
    day: date
    prev_close: float | None
    prev_high: float | None
    prev_low: float | None
    adx: float | None
    vix: float | None
    vix_median: float | None
    trend_side: int          # +1 close > EMA20 with ADX >= 20, -1 below, 0 otherwise
    rsi2: float | None
    vrp: bool                # VIX above NIFTY's 20-day realised volatility
    vj: bool                 # VJ-1.0 warning (or VIX missing): no option selling

    @property
    def ok(self) -> bool:
        return None not in (self.prev_close, self.adx, self.vix, self.vix_median)

    def matches(self, regime: str) -> bool:
        if not self.ok:
            return False
        if regime in ("ANY", "LIVE_TREND", "LIVE_FLAT"):  # live part is checked per trade
            return True
        if regime == "TREND":
            return self.adx >= A.ADX_TREND
        if regime == "RANGE":
            return self.adx < A.ADX_RANGE
        if regime == "HIGHVOL":
            return self.vix > self.vix_median
        if regime == "LOWVOL":
            return self.vix <= self.vix_median
        raise ValueError(regime)


def _f(x) -> float | None:
    return float(x) if x is not None and np.isfinite(x) else None


def features(daily: pd.DataFrame, vix: pd.Series | None) -> pd.DataFrame:
    """daily: NIFTY high/low/close indexed by trading date. One row per day, values AT its close."""
    d = daily.sort_index()
    out = pd.DataFrame(index=d.index)
    out["close"] = d["close"]
    out["high"] = d["high"]
    out["low"] = d["low"]
    out["adx"] = ind.adx(d["high"], d["low"], d["close"], 14)[0]
    out["ema20"] = ind.ema(d["close"], 20)
    out["rsi2"] = ind.rsi(d["close"], 2)
    r = np.log(d["close"]).diff()
    out["hv20"] = r.rolling(20).std(ddof=1) * math.sqrt(252) * 100
    if vix is not None and len(vix):
        v = vix.sort_index().reindex(d.index)
        out["vix"] = v
        out["vix_median"] = v.rolling(A.VIX_MEDIAN_DAYS, min_periods=A.VIX_MEDIAN_DAYS).median()
    else:
        out["vix"] = np.nan
        out["vix_median"] = np.nan
    return out


def labels(daily: pd.DataFrame, vix: pd.Series | None, days: list[date]) -> dict[date, Day]:
    """{day: what was known before it opened}, using the last daily row strictly before it."""
    f = features(daily, vix)
    idx = f.index
    out: dict[date, Day] = {}
    for d in days:
        i = idx.searchsorted(pd.Timestamp(d)) - 1
        if i < 0:
            out[d] = Day(d, None, None, None, None, None, None, 0, None, False, True)
            continue
        row = f.iloc[i]
        if (pd.Timestamp(d) - idx[i]).days > 5:  # stale: more than a long weekend behind
            out[d] = Day(d, None, None, None, None, None, None, 0, None, False, True)
            continue
        adx, close, ema20 = _f(row["adx"]), _f(row["close"]), _f(row["ema20"])
        side = 0
        if adx is not None and ema20 is not None and close is not None and adx >= A.ADX_RANGE:
            side = 1 if close > ema20 else -1 if close < ema20 else 0
        vix_v, hv = _f(row["vix"]), _f(row["hv20"])
        out[d] = Day(d, close, _f(row["high"]), _f(row["low"]), adx, vix_v, _f(row["vix_median"]),
                     side, _f(row["rsi2"]),
                     bool(vix_v is not None and hv is not None and vix_v > hv),
                     vj_blocked(vix, d) if vix is not None else True)
    return out
