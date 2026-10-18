"""Technical indicators used by the strategy's core direction checks (docs/06_STRATEGY_SPEC.md §5).

Every function takes pandas Series of completed candles (oldest first) and returns a Series of
the same length. Values that cannot be computed yet (warm-up) are NaN, never 0.

Formulas follow J. Welles Wilder's originals, which is what most charting tools show:
- EMA: exponential moving average, smoothing 2 / (n + 1), started from the first value.
- RSI, ATR, ADX: Wilder smoothing (alpha = 1 / n).
Small differences from a broker's chart in the first few hundred bars are normal; they fade
once enough history has gone through the smoothing (see MIN_DAILY_BARS in metrics.py).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, n: int) -> pd.Series:
    out = series.ewm(span=n, adjust=False).mean()
    out.iloc[: n - 1] = np.nan  # not enough data for the first n-1 values
    return out


def _wilder(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(alpha=1 / n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Relative Strength Index, 0-100."""
    change = close.diff()
    gain = _wilder(change.clip(lower=0), n)
    loss = _wilder(-change.clip(upper=0), n)
    out = 100 - 100 / (1 + gain / loss)
    out[(loss == 0) & (gain > 0)] = 100.0  # only gains: RSI is 100, not a division error
    out.iloc[:n] = np.nan
    return out


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    return pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()],
                     axis=1).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    """Average True Range: the typical daily move in price points."""
    out = _wilder(true_range(high, low, close), n)
    out.iloc[:n] = np.nan
    return out


def adx(high: pd.Series, low: pd.Series, close: pd.Series,
        n: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    """(ADX, +DI, -DI). ADX >= 20-25 means a trend is present; DI lines give its direction."""
    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    tr = _wilder(true_range(high, low, close), n)
    plus_di = 100 * _wilder(plus_dm, n) / tr
    minus_di = 100 * _wilder(minus_dm, n) / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    out = _wilder(dx, n)
    warmup = 2 * n
    for s in (out, plus_di, minus_di):
        s.iloc[:warmup] = np.nan
    return out, plus_di, minus_di


def weekly_closes(close: pd.Series, last_complete_day: pd.Timestamp | None = None) -> pd.Series:
    """Last close of each Monday-Friday week. `close` is indexed by trading date.

    The current week counts only once it has ended (its Friday is on or before
    `last_complete_day`), so a half-finished week never moves the weekly trend.
    """
    weekly = close.groupby(close.index.to_period("W-FRI")).last()
    if len(weekly) and last_complete_day is not None:
        if weekly.index[-1].end_time.normalize() > pd.Timestamp(last_complete_day).normalize():
            weekly = weekly.iloc[:-1]
    return weekly
