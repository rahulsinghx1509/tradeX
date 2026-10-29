"""Intraday data points for one stock (catalog groups "Intraday Price Levels", "Trend & Momentum",
"Volume & Liquidity"; docs/01 §19). Readings only: nothing here says buy or sell.

Two inputs, both completed candles, oldest first, with columns time (UTC), open, high, low,
close, volume:
- `daily_levels(daily)`: one row per trading day with the levels known BEFORE that day opens
  (previous day's high/low/close, floor pivots, CPR) plus the gap at the open.
- `bar_metrics(bars)`: one row per 5 or 15-minute bar with what is known at that bar's close
  (VWAP and bands, opening range once it has finished, day high/low so far, relative volume,
  EMA 9/21, RSI 14).

Rules kept from the rest of TradeX:
- Missing stays missing. A bar without volume makes VWAP missing from that bar to the end of
  the day, rather than quietly averaging over the bars that had volume.
- No peeking: every value on a bar uses only that bar and earlier ones. The opening range is
  missing until its window has closed.

Formulas (the common charting-tool versions):
- Typical price = (high + low + close) / 3. VWAP = sum(typical x volume) / sum(volume), per day.
- VWAP bands = VWAP +/- k x volume-weighted standard deviation of typical price around VWAP.
- Floor pivots from the previous day: P = (H + L + C) / 3, R1 = 2P - L, S1 = 2P - H,
  R2 = P + (H - L), S2 = P - (H - L), R3 = H + 2(P - L), S3 = L - 2(H - P).
- CPR: BC = (H + L) / 2, TC = 2P - BC (swapped so TC >= BC); width as % of P.
- Relative volume (RVOL) = the day's volume so far / the average volume up to the same time of
  day over the previous RVOL_DAYS sessions (needs at least RVOL_MIN_DAYS of them).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.market_time import IST
from app.services.indicators import atr, ema, rsi

OPENING_RANGE_MINUTES = 15
RVOL_DAYS = 10
RVOL_MIN_DAYS = 5
BAND_WIDTHS = (1.0, 2.0)


def session_dates(times: pd.Series) -> pd.Series:
    return pd.to_datetime(times, utc=True).dt.tz_convert(IST).dt.date


def daily_levels(daily: pd.DataFrame) -> pd.DataFrame:
    """Levels for each day from the day before it, plus that day's opening gap."""
    d = daily.sort_values("time").reset_index(drop=True)
    out = pd.DataFrame({"session": session_dates(d["time"])})
    h, low, c = d["high"].shift(), d["low"].shift(), d["close"].shift()
    p = (h + low + c) / 3
    out["prev_high"], out["prev_low"], out["prev_close"] = h, low, c
    out["pivot"] = p
    out["r1"], out["s1"] = 2 * p - low, 2 * p - h
    out["r2"], out["s2"] = p + (h - low), p - (h - low)
    out["r3"], out["s3"] = h + 2 * (p - low), low - 2 * (h - p)
    bc, tc = (h + low) / 2, 2 * p - (h + low) / 2
    out["cpr_bottom"], out["cpr_top"] = np.minimum(bc, tc), np.maximum(bc, tc)
    out["cpr_width_pct"] = (out["cpr_top"] - out["cpr_bottom"]) / p * 100
    out["gap_pct"] = (d["open"] - c) / c * 100
    out["prev_atr14"] = atr(d["high"], d["low"], d["close"], 14).shift()
    return out


def bar_metrics(bars: pd.DataFrame, opening_range_minutes: int = OPENING_RANGE_MINUTES
                ) -> pd.DataFrame:
    """Per-bar intraday readings. `bars` holds one size of bar (5m or 15m) over several days."""
    b = bars.sort_values("time").reset_index(drop=True)
    out = pd.DataFrame({"time": b["time"], "session": session_dates(b["time"])})
    day = out["session"]

    # VWAP and bands, reset each day; missing from the first bar without volume.
    tp = (b["high"] + b["low"] + b["close"]) / 3
    vol = b["volume"].astype(float)
    broken = vol.isna().astype(int).groupby(day).cummax().astype(bool)
    cum_v = vol.fillna(0).groupby(day).cumsum()
    cum_pv = (tp * vol).fillna(0).groupby(day).cumsum()
    cum_p2v = (tp * tp * vol).fillna(0).groupby(day).cumsum()
    vwap = (cum_pv / cum_v).where(~broken & (cum_v > 0))
    sd = np.sqrt((cum_p2v / cum_v - vwap ** 2).clip(lower=0))
    out["vwap"] = vwap
    for k in BAND_WIDTHS:
        out[f"vwap_upper_{k:g}"] = vwap + k * sd
        out[f"vwap_lower_{k:g}"] = vwap - k * sd

    # Day high/low so far, and the opening range once its window has closed.
    out["day_high"] = b["high"].groupby(day).cummax()
    out["day_low"] = b["low"].groupby(day).cummin()
    t_ist = pd.to_datetime(b["time"], utc=True).dt.tz_convert(IST)
    minutes = (t_ist.dt.hour * 60 + t_ist.dt.minute) - (9 * 60 + 15)
    gaps = minutes.groupby(day).diff().dropna()
    step = int(gaps.min()) if len(gaps) else 5  # bar size in minutes
    in_range = minutes < opening_range_minutes
    or_high = b["high"].where(in_range).groupby(day).transform("max")
    or_low = b["low"].where(in_range).groupby(day).transform("min")
    done = minutes + step >= opening_range_minutes  # this bar closes at or after the range end
    full = in_range.groupby(day).transform("sum") * step >= opening_range_minutes
    out["or_high"] = or_high.where(done & full)
    out["or_low"] = or_low.where(done & full)

    # Relative volume at the same time of day.
    out["cum_volume"] = cum_v.where(~broken)
    slot = pd.DataFrame({"day": day, "minute": minutes, "cum": out["cum_volume"]})
    wide = slot.pivot_table(index="day", columns="minute", values="cum", aggfunc="last")
    past = wide.shift().rolling(RVOL_DAYS, min_periods=RVOL_MIN_DAYS).mean()
    avg = past.stack().rename("avg").reset_index()
    avg.columns = ["day", "minute", "avg"]
    merged = slot.merge(avg, on=["day", "minute"], how="left")
    out["rvol"] = (merged["cum"] / merged["avg"]).where(merged["avg"] > 0).to_numpy()

    # Momentum on the bar closes (runs across days, like a chart does).
    out["ema9"] = ema(b["close"], 9)
    out["ema21"] = ema(b["close"], 21)
    out["rsi14"] = rsi(b["close"], 14)
    out["close"] = b["close"]
    return out
