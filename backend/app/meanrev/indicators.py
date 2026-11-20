"""MR-4 building blocks: daily bias, VWAP bands, Bollinger bands, candle patterns, option delta.

Each function is small and pure (data in, numbers out) so the tests can check them one by one.
If you know SQL: the running VWAP is SUM(tp * vol) OVER (PARTITION BY day ORDER BY minute) /
SUM(vol) OVER (...), and the Bollinger band is AVG / STDDEV_POP OVER (ROWS 19 PRECEDING).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from app.meanrev import (
    BB_PERIOD,
    BB_STD,
    RSI_DAYS,
    RSI_LONG_BELOW,
    RSI_SHORT_ABOVE,
    SMA_DAYS,
    VWAP_STD,
)


# ----------------------------------------------------------------------------- daily bias (R7-R9)
def rsi(closes: pd.Series, n: int = RSI_DAYS) -> pd.Series:
    """Wilder's RSI(n): average gain / average loss smoothed with alpha = 1/n."""
    diff = closes.astype(float).diff()
    gain = diff.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-diff.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    out = 100 - 100 / (1 + gain / loss)
    out = out.where(loss > 0, 100.0)  # no losses at all = RSI 100
    return out.where(gain.notna())


def daily_bias(closes: pd.Series) -> dict[date, int | None]:
    """{trading day D: bias known before D's open}, from closes up to D-1.

    +1 LONG (close > SMA200, RSI2 < 30), -1 SHORT (close < SMA200, RSI2 > 70), 0 none,
    None = not enough history (no trade). `closes` is indexed by date, one row per trading day;
    the bias of the day AFTER each row is returned, keyed by the next row's date.
    """
    c = closes.dropna().sort_index().astype(float)
    sma = c.rolling(SMA_DAYS, min_periods=SMA_DAYS).mean()
    r = rsi(c)
    out: dict[date, int | None] = {}
    days = list(c.index)
    for i in range(1, len(days)):
        prev = days[i - 1]
        s, x, v = sma.loc[prev], c.loc[prev], r.loc[prev]
        key = pd.Timestamp(days[i]).date()
        if pd.isna(s) or pd.isna(v):
            out[key] = None
        elif x > s and v < RSI_LONG_BELOW:
            out[key] = 1
        elif x < s and v > RSI_SHORT_ABOVE:
            out[key] = -1
        else:
            out[key] = 0
    return out


# ----------------------------------------------------------------------------- VWAP bands (R13)
def vwap_bands(spot_day: pd.DataFrame, fut_volume: pd.Series) -> pd.DataFrame:
    """Running session VWAP and its standard deviation, one row per spot minute of the day.

    spot_day: one day's spot 1m candles with columns m (minutes since 09:15), high, low, close.
    fut_volume: futures volume indexed by m. Spot typical price weighted by futures volume.
    A missing price or volume (or a missing minute) makes VWAP unknown from there on.
    """
    d = spot_day.sort_values("m").reset_index(drop=True)
    vol = d["m"].map(fut_volume).astype(float)
    tp = (d["high"] + d["low"] + d["close"]) / 3
    bad = (tp.isna() | vol.isna() | (d["m"] != range(len(d)))).cummax()
    sv = vol.cumsum()
    vwap = (tp * vol).cumsum() / sv.where(sv > 0)
    var = (vol * tp * tp).cumsum() / sv.where(sv > 0) - vwap * vwap
    sd = np.sqrt(var.clip(lower=0))
    d["vwap"] = vwap.where(~bad)
    d["vwap_sd"] = sd.where(~bad)
    d["vwap_lo"] = d["vwap"] - VWAP_STD * d["vwap_sd"]
    d["vwap_hi"] = d["vwap"] + VWAP_STD * d["vwap_sd"]
    return d


# ----------------------------------------------------------------------------- Bollinger (R14)
def bollinger(closes: pd.Series, n: int = BB_PERIOD, k: float = BB_STD) -> pd.DataFrame:
    c = closes.astype(float)
    mid = c.rolling(n, min_periods=n).mean()
    sd = c.rolling(n, min_periods=n).std(ddof=0)
    return pd.DataFrame({"bb_mid": mid, "bb_lo": mid - k * sd, "bb_hi": mid + k * sd})


# ----------------------------------------------------------------------------- patterns (R16, R17)
@dataclass(frozen=True)
class Bar:
    open: float
    high: float
    low: float
    close: float

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low


def hammer(b: Bar) -> bool:
    """Lower wick >= 2 x body, upper wick <= 0.3 x body, body in the top third of the range."""
    if b.range <= 0:
        return False
    return (b.lower_wick >= 2 * b.body and b.upper_wick <= 0.3 * b.body
            and min(b.open, b.close) >= b.low + 2 * b.range / 3)


def shooting_star(b: Bar) -> bool:
    """The mirror image of a hammer: long upper wick, body in the bottom third."""
    if b.range <= 0:
        return False
    return (b.upper_wick >= 2 * b.body and b.lower_wick <= 0.3 * b.body
            and max(b.open, b.close) <= b.low + b.range / 3)


def morning_star(b1: Bar, b2: Bar, b3: Bar) -> bool:
    """Red bar with body >= 0.6 x range, small bar (body <= 0.3 x bar 1's), green bar closing
    above bar 1's body midpoint."""
    if b1.range <= 0 or not b1.close < b1.open:
        return False
    return (b1.body >= 0.6 * b1.range and b2.body <= 0.3 * b1.body and b3.close > b3.open
            and b3.close > (b1.open + b1.close) / 2)


def evening_star(b1: Bar, b2: Bar, b3: Bar) -> bool:
    if b1.range <= 0 or not b1.close > b1.open:
        return False
    return (b1.body >= 0.6 * b1.range and b2.body <= 0.3 * b1.body and b3.close < b3.open
            and b3.close < (b1.open + b1.close) / 2)


def reversal(bars: list[Bar], direction: int) -> tuple[str, int] | None:
    """(pattern name, how many bars it spans) if the LAST bar of `bars` completes a reversal
    for `direction` (+1 bullish, -1 bearish). A single-bar pattern is preferred over a star."""
    if not bars:
        return None
    last = bars[-1]
    if direction > 0 and hammer(last):
        return "hammer", 1
    if direction < 0 and shooting_star(last):
        return "shooting star", 1
    if len(bars) >= 3:
        b1, b2, b3 = bars[-3:]
        if direction > 0 and morning_star(b1, b2, b3):
            return "morning star", 3
        if direction < 0 and evening_star(b1, b2, b3):
            return "evening star", 3
    return None


# ----------------------------------------------------------------------------- option delta (R23)
def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(spot: float, strike: float, years: float, vol: float, r: float, call: bool) -> float:
    if years <= 0 or vol <= 0:
        return max(0.0, spot - strike) if call else max(0.0, strike - spot)
    sq = vol * math.sqrt(years)
    d1 = (math.log(spot / strike) + (r + vol * vol / 2) * years) / sq
    d2 = d1 - sq
    disc = math.exp(-r * years)
    if call:
        return spot * _ncdf(d1) - strike * disc * _ncdf(d2)
    return strike * disc * _ncdf(-d2) - spot * _ncdf(-d1)


def bs_delta(spot: float, strike: float, years: float, vol: float, r: float, call: bool) -> float:
    if years <= 0 or vol <= 0:
        itm = spot > strike if call else spot < strike
        return (1.0 if itm else 0.0) * (1 if call else -1)
    d1 = (math.log(spot / strike) + (r + vol * vol / 2) * years) / (vol * math.sqrt(years))
    return _ncdf(d1) if call else _ncdf(d1) - 1


def implied_vol(price: float, spot: float, strike: float, years: float, r: float,
                call: bool) -> float | None:
    """Black-Scholes implied volatility by bisection; None if the price is outside the model's
    range (below intrinsic value, for example)."""
    if price is None or not price > 0 or years <= 0:
        return None
    lo, hi = 0.005, 3.0
    if not bs_price(spot, strike, years, lo, r, call) <= price <= bs_price(spot, strike, years,
                                                                         hi, r, call):
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if bs_price(spot, strike, years, mid, r, call) < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def strike_near_delta(spot: float, atm: float, step: int, years: float, vol: float, r: float,
                      call: bool, target: float, max_steps: int = 15) -> tuple[float, float]:
    """(strike, |delta|) of the in-the-money (or ATM) strike whose |delta| is nearest `target`.
    Calls go down from ATM, puts up. Ties go to the strike nearer ATM."""
    best = None
    for k in range(max_steps + 1):
        strike = atm - k * step if call else atm + k * step
        if strike <= 0:
            break
        dl = abs(bs_delta(spot, strike, years, vol, r, call))
        gap = abs(dl - target)
        if best is None or gap < best[0] - 1e-12:
            best = (gap, strike, dl)
    return best[1], best[2]
