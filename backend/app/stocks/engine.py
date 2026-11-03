"""IS-1.0 engine: readings, signals, exits and the portfolio pass (rules in rules.py).

The same functions serve the backtest (whole history) and the live PAPER scanner (`as_of`:
only bars that have closed by then are used).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from app.stocks import rules as R

BAR = timedelta(minutes=5)


# ----------------------------------------------------------------------------- readings
def _bar_time(idx: pd.DatetimeIndex) -> np.ndarray:
    return np.array([t.time() for t in idx])


def day_table(df: pd.DataFrame) -> pd.DataFrame:
    """Per-day readings: open (09:15 bar), previous close, gap %, first-bar and first-15-minute
    volume, RVOL of both, opening range and first-bar high/low. Missing stays NaN."""
    t = _bar_time(df.index)
    g = df.groupby("day", sort=True)
    first = df[t == time(9, 15)].groupby("day")
    first3 = df[(t >= time(9, 15)) & (t < time(9, 30))].groupby("day")
    out = pd.DataFrame(index=pd.Index(sorted(df["day"].unique()), name="day"))
    out["open"] = first["open"].first()
    out["close"] = g["close"].last()
    out["prev_close"] = out["close"].shift(1)
    out["gap_pct"] = (out["open"] / out["prev_close"] - 1) * 100
    out["bar1_high"], out["bar1_low"] = first["high"].first(), first["low"].first()
    out["vol1"] = first["volume"].first()
    n3 = first3["volume"].count()
    out["vol15"] = first3["volume"].sum(min_count=1).where(n3 == R.ORB_RANGE_BARS)
    out["range_high"] = first3["high"].max().where(n3 == R.ORB_RANGE_BARS)
    out["range_low"] = first3["low"].min().where(n3 == R.ORB_RANGE_BARS)
    for col, name in (("vol1", "rvol1"), ("vol15", "rvol15")):
        base = out[col].shift(1).rolling(R.RVOL_DAYS, min_periods=R.RVOL_MIN_DAYS).mean()
        out[name] = out[col] / base.where(base > 0)
    return out


def readings(df: pd.DataFrame, nifty: pd.DataFrame) -> pd.DataFrame:
    """Bar-level readings for one stock, joined with NIFTY's bars and day open/previous close."""
    df = df.copy()
    days = day_table(df)
    tp = (df["high"] + df["low"] + df["close"]) / 3
    missing = df["volume"].isna() | tp.isna()
    gday = df.groupby("day")
    cum_pv = (tp * df["volume"]).fillna(0).groupby(df["day"]).cumsum()
    cum_v = df["volume"].fillna(0).groupby(df["day"]).cumsum()
    bad = missing.astype(int).groupby(df["day"]).cumsum() > 0
    df["vwap"] = (cum_pv / cum_v.where(cum_v > 0)).where(~bad)
    df["ema_fast"] = df["close"].ewm(span=R.EMA_FAST, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=R.EMA_SLOW, adjust=False).mean()
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()],
                   axis=1).max(axis=1, skipna=False)
    df["atr"] = tr.ewm(alpha=1 / R.ATR_N, adjust=False).mean()
    df["prev_low"] = gday["low"].shift(1)
    df["low3"] = gday["low"].transform(lambda s: s.rolling(R.VWP_STOP_BARS).min())
    df["high3"] = gday["high"].transform(lambda s: s.rolling(R.VWP_STOP_BARS).max())
    for col in days.columns:
        df[col if col not in ("open", "close") else f"day_{col}"] = df["day"].map(days[col])
    df["day_chg_pct"] = (df["close"] / df["prev_close"] - 1) * 100
    if nifty is not None and len(nifty):
        nd = day_table(nifty)
        df["n_close"] = nifty["close"].reindex(df.index)
        df["n_open"] = df["day"].map(nd["open"])
        df["n_prev"] = df["day"].map(nd["prev_close"])
        df["rs_pct"] = df["day_chg_pct"] - (df["n_close"] / df["n_prev"] - 1) * 100
    else:
        df["n_close"] = df["n_open"] = df["n_prev"] = df["rs_pct"] = np.nan
    return df


# ----------------------------------------------------------------------------- signals
def _window(t: np.ndarray, span: tuple[time, time]) -> np.ndarray:
    return (t >= span[0]) & (t <= span[1])


def signal_mask(r: pd.DataFrame, setup: str, direction: int) -> tuple[np.ndarray, np.ndarray]:
    """(bars whose close is a signal, stop level per bar). NaN anywhere => False."""
    t = _bar_time(r.index)
    d = direction
    c = r["close"]
    market = (np.sign(r["n_close"] - r["n_open"]) == d)
    rs = (np.sign(r["rs_pct"]) == d)
    vwap_side = (np.sign(c - r["vwap"]) == d)
    if setup == "ORB":
        level, other = (r["range_high"], r["range_low"]) if d > 0 else (r["range_low"], r["range_high"])
        beyond = (c > level) if d > 0 else (c < level)
        m = _window(t, R.ORB_SIGNAL) & beyond & vwap_side & (r["rvol15"] >= R.ORB_RVOL_MIN)
        stop = other
    elif setup == "VWP":
        trend = (np.sign(r["ema_fast"] - r["ema_slow"]) == d)
        strong = (d * r["day_chg_pct"] >= R.VWP_DAY_CHANGE_PCT) & (d * r["rs_pct"] >= R.VWP_RS_PCT)
        if d > 0:
            pulled = r["prev_low"] <= r["ema_slow"].shift(1)
            bar_ok = (c > r["open"]) & (c > r["ema_fast"])
            stop = r["low3"]
        else:
            prev_high = r.groupby("day")["high"].shift(1)
            pulled = prev_high >= r["ema_slow"].shift(1)
            bar_ok = (c < r["open"]) & (c < r["ema_fast"])
            stop = r["high3"]
        m = _window(t, R.VWP_SIGNAL) & trend & strong & pulled & bar_ok & vwap_side
    elif setup == "GAP":
        gap = d * r["gap_pct"]
        level, other = (r["bar1_high"], r["bar1_low"]) if d > 0 else (r["bar1_low"], r["bar1_high"])
        beyond = (c > level) if d > 0 else (c < level)
        m = (_window(t, R.GAP_SIGNAL) & (gap >= R.GAP_MIN_PCT) & (gap <= R.GAP_MAX_PCT)
             & (r["rvol1"] >= R.GAP_RVOL_MIN) & beyond & vwap_side)
        stop = other
    else:
        raise ValueError(setup)
    m = m & market & rs
    return m.fillna(False).to_numpy(dtype=bool), stop.to_numpy(dtype=float)


@dataclass
class Candidate:
    symbol: str
    setup: str
    direction: int
    day: date
    signal_time: datetime   # bar START of the signal bar (it closes 5 minutes later)
    entry_time: datetime    # bar START of the entry bar (fill at its open)
    entry: float
    stop: float
    target: float
    rvol: float
    exit_time: datetime | None = None
    exit_price: float | None = None
    exit_reason: str | None = None   # STOP / TARGET / SQUARE_OFF / DATA_END / OPEN


def _exit(r: pd.DataFrame, i: int, c: Candidate, as_of: datetime | None) -> None:
    """Walk bars from the entry bar on. Stop wins if a bar touches both."""
    o, h, lo = r["open"].to_numpy(), r["high"].to_numpy(), r["low"].to_numpy()
    cl, days, idx = r["close"].to_numpy(), r["day"].to_numpy(), r.index
    d, last = c.direction, i
    for j in range(i, len(r)):
        if days[j] != c.day:
            break
        if as_of is not None and idx[j] + BAR > as_of:
            c.exit_reason = "OPEN"
            return
        if idx[j].time() >= R.SQUARE_OFF:
            c.exit_time, c.exit_price, c.exit_reason = idx[j], o[j], "SQUARE_OFF"
            return
        last = j
        hit_stop = (lo[j] <= c.stop) if d > 0 else (h[j] >= c.stop)
        if hit_stop:
            px = min(o[j], c.stop) if d > 0 else max(o[j], c.stop)
            c.exit_time, c.exit_price, c.exit_reason = idx[j], px, "STOP"
            return
        if (h[j] >= c.target) if d > 0 else (lo[j] <= c.target):
            c.exit_time, c.exit_price, c.exit_reason = idx[j], c.target, "TARGET"
            return
    if as_of is not None and last == len(r) - 1:
        c.exit_reason = "OPEN"  # the day is still running
        return
    c.exit_time, c.exit_price, c.exit_reason = idx[last], cl[last], "DATA_END"


def candidates(symbol: str, r: pd.DataFrame, setup: str, direction: int,
               as_of: datetime | None = None) -> list[Candidate]:
    """At most one candidate per day: the first bar meeting every condition whose trade can
    actually be entered (next bar exists, same day, starts by 14:30, stop distance in range)."""
    if as_of is not None:
        r = r[r.index + BAR <= as_of]
    if r.empty:
        return []
    mask, stops = signal_mask(r, setup, direction)
    out, done_days = [], set()
    idx, days, opens = r.index, r["day"].to_numpy(), r["open"].to_numpy()
    rv = (r["rvol1"] if setup == "GAP" else r["rvol15"]).to_numpy()
    for i in np.flatnonzero(mask):
        day = days[i]
        if day in done_days:
            continue
        done_days.add(day)  # the first qualifying bar is the day's only chance
        if i + 1 >= len(r) or days[i + 1] != day or idx[i + 1] - idx[i] != BAR:
            continue
        if idx[i + 1].time() > R.LAST_ENTRY:
            continue
        entry, stop = opens[i + 1], stops[i]
        if not (math.isfinite(entry) and math.isfinite(stop)):
            continue
        dist = direction * (entry - stop)
        if not (R.MIN_STOP_PCT <= dist / entry * 100 <= R.MAX_STOP_PCT):
            continue
        c = Candidate(symbol, setup, direction, day, idx[i], idx[i + 1], float(entry), float(stop),
                      float(entry + direction * R.TARGET_R * dist), float(rv[i]))
        _exit(r, i + 1, c, as_of)
        out.append(c)
    return out


# ----------------------------------------------------------------------------- money
def charges(buy_value: float, sell_value: float) -> float:
    def brokerage(v: float) -> float:
        return min(R.BROKERAGE_FLAT, v * R.BROKERAGE_PCT / 100)

    brk = brokerage(buy_value) + brokerage(sell_value)
    txn = (buy_value + sell_value) * R.TXN_PCT / 100
    sebi = (buy_value + sell_value) * R.SEBI_PCT / 100
    stt = sell_value * R.STT_SELL_PCT / 100
    stamp = buy_value * R.STAMP_BUY_PCT / 100
    return brk + txn + sebi + stt + stamp + (brk + txn + sebi) * R.GST_PCT / 100


def quantity(entry: float, stop: float) -> int:
    dist = abs(entry - stop)
    if dist <= 0:
        return 0
    return int(min(R.RISK_PER_TRADE / dist, R.MAX_POSITION_VALUE / entry))


@dataclass
class Trade:
    c: Candidate
    qty: int
    entry_fill: float
    exit_fill: float | None
    gross: float | None
    slippage: float | None
    charges: float | None
    net: float | None


def price_trade(c: Candidate, qty: int) -> Trade:
    d, s = c.direction, R.SLIPPAGE_PCT / 100
    entry_fill = c.entry * (1 + d * s)
    if c.exit_price is None:
        return Trade(c, qty, entry_fill, None, None, None, None, None)
    exit_fill = c.exit_price * (1 - d * s)
    gross = d * (c.exit_price - c.entry) * qty
    after_slip = d * (exit_fill - entry_fill) * qty
    buy, sell = (entry_fill, exit_fill) if d > 0 else (exit_fill, entry_fill)
    ch = charges(buy * qty, sell * qty)
    return Trade(c, qty, entry_fill, exit_fill, gross, gross - after_slip, ch, after_slip - ch)


def portfolio(cands: list[Candidate]) -> list[Trade]:
    """Apply the per-row limits in time order (ties: highest RVOL first)."""
    order = sorted(cands, key=lambda c: (c.entry_time, -(c.rvol if math.isfinite(c.rvol) else 0)))
    taken: list[Trade] = []
    for c in order:
        today = [t for t in taken if t.c.day == c.day]
        open_now = [t for t in today if t.c.exit_time is None or t.c.exit_time >= c.entry_time]
        closed_loss = sum(t.net for t in today if t.c.exit_time is not None
                          and t.c.exit_time < c.entry_time and t.net is not None)
        if len(today) >= R.MAX_TRADES_PER_DAY or len(open_now) >= R.MAX_OPEN:
            continue
        if closed_loss <= -R.DAILY_LOSS_STOP:
            continue
        qty = quantity(c.entry, c.stop)
        if qty < 1:
            continue
        taken.append(price_trade(c, qty))
    return taken


# ----------------------------------------------------------------------------- scoring
@dataclass
class Score:
    trades: int
    win_pct: float
    gross: float
    costs: float
    net: float
    pf: float
    avg: float
    max_dd: float
    max_dd_pct: float
    half1: float
    half2: float


def score(trades: list[Trade], split: date | None = None) -> Score:
    closed = sorted((t for t in trades if t.net is not None), key=lambda t: t.c.entry_time)
    nets = np.array([t.net for t in closed], dtype=float)
    if not len(nets):
        return Score(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    wins, losses = nets[nets > 0].sum(), -nets[nets < 0].sum()
    eq = R.CAPITAL + np.cumsum(nets)
    peak = np.maximum.accumulate(np.concatenate([[R.CAPITAL], eq]))[1:]
    dd = float((peak - eq).max())
    gross = float(sum(t.gross for t in closed))
    if split is None:
        mid = closed[len(closed) // 2].c.day
    else:
        mid = split
    h1 = float(sum(t.net for t in closed if t.c.day < mid))
    h2 = float(sum(t.net for t in closed if t.c.day >= mid))
    return Score(len(nets), float((nets > 0).mean() * 100), gross, gross - float(nets.sum()),
                 float(nets.sum()), float(wins / losses) if losses else math.inf,
                 float(nets.mean()), dd, dd / R.CAPITAL * 100, h1, h2)


def passes(s: Score) -> tuple[bool, str]:
    why = []
    if s.trades < R.PASS_MIN_TRADES:
        why.append(f"only {s.trades} trades")
    if s.net <= 0:
        why.append("net <= 0")
    if s.pf < R.PASS_PF:
        why.append(f"PF {s.pf:.2f} < {R.PASS_PF}")
    if s.half1 <= 0 or s.half2 <= 0:
        why.append("a half lost money")
    if s.max_dd_pct >= R.PASS_MAX_DD_PCT:
        why.append(f"max DD {s.max_dd_pct:.0f}%")
    return (not why, "; ".join(why) or "all conditions met")


def split_days(nifty_days: list[date]) -> date:
    """First development day: everything before it (the oldest third) is held back."""
    days = sorted(nifty_days)
    return days[int(len(days) * R.HOLDOUT_FRACTION)]
