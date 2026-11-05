"""TP-1.0 engine: trend-pullback option buying (rules in app/pullback/__init__.py).

Signals are found bar by bar on NIFTY 5-minute bars; fills, slippage, costs and the Trade record
come from the 9/21 EMA engine (`_open` / `_close`), so results compare with every other strategy.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, time, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.market_time import IST, from_db
from app.db.models import MinuteCandle
from app.ema921 import bars as B
from app.ema921 import engine as EE
from app.orb import engine as O
from app.pullback import (
    CHASE_ATR,
    DAY_LOSS,
    DELTA,
    MAX_TRADES_DAY,
    MODES,
    RISK_PER_TRADE,
    STALL_MINUTES,
    STALL_R,
    TARGET_R,
    WEEK_LOSS,
)
from app.volume import FUT_SYMBOL

WINDOWS = ((time(10, 0), time(12, 0)), (time(13, 30), time(14, 45)))
FIRST_BREAK = time(9, 35)
SQUARE_OFF = time(15, 15)

# 1 strike in the money: bullish buys the call 50 lower, bearish the put 50 higher.
EE.EXTRA_LEGS["ITM1_BUY"] = (lambda d, atm, cfg:
                             ("BUY", "CE", atm - cfg.strike_step) if d > 0
                             else ("BUY", "PE", atm + cfg.strike_step))


def config(base: EE.Config | None = None) -> EE.Config:
    return replace(base or EE.Config(), timeframe=5, square_off=SQUARE_OFF, stop_atr_mult=None)


# ----------------------------------------------------------------------------- futures VWAP
def load_futures(session: Session, start: date, end: date, symbol: str = FUT_SYMBOL) -> pd.DataFrame:
    rows = session.execute(
        select(MinuteCandle.candle_time, MinuteCandle.high, MinuteCandle.low, MinuteCandle.close,
               MinuteCandle.volume)
        .where(MinuteCandle.groww_symbol == symbol, MinuteCandle.trade_date >= start,
               MinuteCandle.trade_date <= end)
        .order_by(MinuteCandle.candle_time)).all()
    df = pd.DataFrame(rows, columns=["time", "high", "low", "close", "volume"])
    if not df.empty:
        df["time"] = [from_db(t) for t in df["time"]]
        for c in ("high", "low", "close", "volume"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


@dataclass(frozen=True)
class FutBar:
    close: float
    low: float
    high: float
    vwap: float | None


def futures_bars(fut: pd.DataFrame) -> dict[pd.Timestamp, FutBar]:
    """{5-minute bar close (IST): futures close/low/high and VWAP at that close}.

    VWAP = running sum(typical price x volume) / running sum(volume) from 09:15. Once a minute
    lacks a volume or price (or a minute is missing), VWAP stays unknown for the rest of the day.
    If you know SQL: SUM(tp*vol) OVER (PARTITION BY day ORDER BY minute) / SUM(vol) OVER (...)."""
    if fut.empty:
        return {}
    df = fut.copy()
    ist = pd.to_datetime(df["time"], utc=True).dt.tz_convert(IST)
    df["day"] = ist.dt.date
    df["m"] = (ist.dt.hour * 60 + ist.dt.minute) - (9 * 60 + 15)
    df = df[(df["m"] >= 0) & (df["m"] < B.SESSION_MINUTES)].drop_duplicates(["day", "m"])
    df = df.sort_values(["day", "m"])
    out: dict[pd.Timestamp, FutBar] = {}
    for d, g in df.groupby("day", sort=True):
        g = g.reset_index(drop=True)
        tp = (g["high"] + g["low"] + g["close"]) / 3
        bad = (tp.isna() | g["volume"].isna() | (g["m"] != range(len(g)))).cummax()
        pv, vol = (tp * g["volume"]).cumsum(), g["volume"].cumsum()
        vwap = (pv / vol.where(vol > 0)).where(~bad)
        g = g.assign(vwap=vwap, bucket=g["m"] // 5)
        for b, bg in g.groupby("bucket"):
            if len(bg) < 5:
                continue
            close_t = B._ist_open(d) + timedelta(minutes=int(b) * 5 + 5)
            last = bg.iloc[-1]
            v = last["vwap"]
            out[close_t] = FutBar(float(last["close"]), float(bg["low"].min()),
                                  float(bg["high"].max()), None if pd.isna(v) else float(v))
    return out


# ----------------------------------------------------------------------------- the run
@dataclass(frozen=True)
class Limits:
    """Loss limits in ₹ (per-trade estimated risk, day and week realised net loss)."""
    per_trade: float | None = RISK_PER_TRADE
    day: float | None = DAY_LOSS
    week: float | None = WEEK_LOSS


PLAN_LIMITS = Limits()  # the ChatGPT plan's ₹500 / ₹1,000 / ₹2,500


@dataclass
class Signal:
    cross: B.Cross  # direction, spot (entry close), bar_close: what EE._open needs
    stop: float
    risk_points: float


def _in_window(t: time) -> bool:
    return any(a <= t <= b for a, b in WINDOWS)


class _SpotMinutes:
    def __init__(self, spot: pd.DataFrame):
        d = spot.assign(t=pd.to_datetime(spot["time"], utc=True).dt.tz_convert(IST))
        d = d.sort_values("t").drop_duplicates("t")
        self.by_day = {k: g.reset_index(drop=True) for k, g in d.groupby(d["t"].dt.date)}


def _exit_for(sig: Signal, day_min: pd.DataFrame, day_bars: list, sq: pd.Timestamp,
              target_r: float = TARGET_R):
    """(exit time, reason, nifty) for a trade entered on `sig`, from 1m closes and 5m bars."""
    long = sig.cross.direction > 0
    entry, r = sig.cross.spot, sig.risk_points
    target = entry + target_r * r if long else entry - target_r * r
    stall_lvl = entry + STALL_R * r if long else entry - STALL_R * r
    t0 = sig.cross.bar_close
    stall_t = t0 + timedelta(minutes=STALL_MINUTES)
    reached = False
    ema21_at = {b.bar_close: b.ema21 for b in day_bars}
    close_at = {b.bar_close: b.close for b in day_bars}
    for row in day_min.itertuples(index=False):
        start = row.t
        done = start + timedelta(minutes=1)  # this candle is known when it closes
        if start < t0:
            continue
        if done > sq:
            break
        c = row.close
        if pd.isna(c):
            continue
        if (c <= sig.stop) if long else (c >= sig.stop):
            return done, "STOP", float(c)
        if (c >= target) if long else (c <= target):
            return done, "TARGET", float(c)
        if (c >= stall_lvl) if long else (c <= stall_lvl):
            reached = True
        if done >= stall_t and not reached:
            return done, "STALL", float(c)
        if done in ema21_at:
            e21, bc = ema21_at[done], close_at[done]
            if (bc < e21) if long else (bc > e21):
                return done, "INVALID", float(bc)
    return sq, "SQUARE_OFF", None


def run(spot: pd.DataFrame, fut: pd.DataFrame, options: EE.OptionData, start: date, end: date,
        base: EE.Config | None = None, modes=MODES, limits: bool | Limits = False,
        holidays: frozenset[date] | None = None, target_r: float = TARGET_R) -> EE.Result:
    cfg = config(base)
    limits = PLAN_LIMITS if limits is True else (limits or None)
    bars = B.add_atr(B.add_emas(B.resample(spot, 5)))
    fb = futures_bars(fut)
    ranges = O.opening_ranges(spot)
    days, why = EE.no_trade_days(spot, start, end, cfg, holidays)
    expiries = options.expiries()
    exp_set = set(expiries)
    trades: dict[str, list[EE.Trade]] = {m: [] for m in modes}
    skips: list[EE.Skip] = []
    cache: dict = {}

    def minutes(symbol, day):
        key = (symbol, day)
        if key not in cache:
            cache[key] = EE._Minutes(options.minutes(symbol, day))
        return cache[key]

    spot_min = _SpotMinutes(spot).by_day
    by_day = {d: list(g.itertuples(index=False)) for d, g in bars.groupby("day", sort=True)}
    for d in days:
        if d in exp_set:
            why[d] = "EXPIRY_DAY"
            continue
        rng = ranges.get(d)
        if rng is None:
            why[d] = "NO_RANGE"
            continue
        sq = EE._ist(d, cfg.square_off)
        made = 0
        for mode in modes:
            made += _run_day(mode, d, by_day.get(d, []), rng, fb, spot_min.get(d), sq, cfg,
                             options, expiries, minutes, skips, trades[mode], limits, target_r)
        if not made:
            why.setdefault(d, "NO_SETUP")
    return EE.Result(cfg, trades, skips, dict(sorted(why.items())), days)


def _week_loss(done: list[EE.Trade], d: date) -> float:
    wk = d.isocalendar()[:2]
    return -sum(t.net_pnl for t in done if t.day.isocalendar()[:2] == wk)


def _run_day(mode, d, day_bars, rng, fb, day_min, sq, cfg, options, expiries, minutes, skips,
             done: list[EE.Trade], limits: Limits | None, target_r: float = TARGET_R) -> int:
    armed: dict[int, pd.Timestamp | None] = {1: None, -1: None}  # bar that armed the side
    pull: dict[int, float | None] = {1: None, -1: None}  # extreme since the pullback started
    busy_until: pd.Timestamp | None = None
    n = 0
    for b in day_bars:
        t = b.bar_close
        f = fb.get(t)
        for s in (1, -1):
            # 1. breakout arms the side
            if armed[s] is None and t.time() >= FIRST_BREAK and (
                    (b.close > rng.high) if s > 0 else (b.close < rng.low)):
                armed[s] = t
                continue  # the pullback must come on a LATER bar
            if armed[s] is None or (busy_until is not None and t <= busy_until):
                continue  # not armed, or a trade is open: nothing builds up meanwhile
            # cancel a pullback that closed through EMA 21
            if pull[s] is not None and ((b.close < b.ema21) if s > 0 else (b.close > b.ema21)):
                pull[s] = None
                continue
            if pull[s] is not None:
                pull[s] = min(pull[s], b.low) if s > 0 else max(pull[s], b.high)
                # 3. confirmation
                ok = (f is not None and f.vwap is not None
                      and ((b.close > b.open and b.close > b.ema9 and b.ema9 > b.ema21
                            and f.close > f.vwap) if s > 0 else
                           (b.close < b.open and b.close < b.ema9 and b.ema9 < b.ema21
                            and f.close < f.vwap)))
                if ok and not pd.isna(b.atr) and (b.high - b.low) > CHASE_ATR * b.atr:
                    ok = False  # a chase candle: wait for the next bar
                if ok and _in_window(t.time()) and n < MAX_TRADES_DAY and day_min is not None:
                    stop = pull[s]
                    risk = (b.close - stop) if s > 0 else (stop - b.close)
                    pull[s] = None  # the setup is used up either way
                    if risk <= 0:
                        continue
                    if limits:
                        est = risk * DELTA[mode] * cfg.qty
                        day_net = -sum(x.net_pnl for x in done if x.day == d)
                        if limits.per_trade is not None and est > limits.per_trade:
                            skips.append(EE.Skip(mode, d, t, "RISK_TOO_BIG", f"₹{est:,.0f}"))
                            continue
                        if (limits.day is not None and day_net >= limits.day) or (
                                limits.week is not None and _week_loss(done, d) >= limits.week):
                            skips.append(EE.Skip(mode, d, t, "LOSS_LIMIT"))
                            continue
                    sig = Signal(B.Cross(d, b.bar_start, t, s, float(b.close), n + 1), stop, risk)
                    tr = EE._open(mode, d, sig.cross, options, expiries, cfg, minutes, skips)
                    if tr is None:
                        continue
                    at, reason, nifty = _exit_for(sig, day_min, day_bars, sq, target_r)
                    EE._close(tr, at, reason, nifty, cfg, minutes, {})
                    done.append(tr)
                    busy_until = at
                    n += 1
                    pull = {1: None, -1: None}  # 7. a new pullback is needed
                continue
            # 2. pullback starts (on a bar after the one that armed the side)
            touch = (b.low <= b.ema9) if s > 0 else (b.high >= b.ema9)
            if f is not None and f.vwap is not None:
                touch = touch or ((f.low <= f.vwap) if s > 0 else (f.high >= f.vwap))
            if touch:
                pull[s] = b.low if s > 0 else b.high
    return n
