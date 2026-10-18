"""Core metrics for each tracked index / stock (Milestone 2).

Everything is computed from VALID, completed daily candles of the underlying itself (index value
for indices, cash price for stocks), as the strategy spec (§5) requires. Nothing here is a trade
signal: the four direction readings are shown for information, and signals come only after the
rules are backtested.

A metric that cannot be computed is returned as None together with a reason, never as 0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.market_time import SESSION_CLOSE, from_db, is_trading_day, to_ist
from app.db.models import Instrument, MarketCandle
from app.services import indicators as ind

METRICS_VERSION = "M2-1.0"
# Warm-up from the strategy spec (G2): EMA and ADX need this much history to settle.
MIN_DAILY_BARS = 250
MIN_WEEKLY_BARS = 60
RS_DAYS = 20
ADX_TREND_MIN = 20  # D3 threshold from the spec


@dataclass
class Metrics:
    as_of: date | None = None  # date of the last daily candle used
    bars: int = 0
    status: str = "OK"  # OK / INSUFFICIENT_HISTORY / NO_DATA
    values: dict[str, float | None] = field(default_factory=dict)
    readings: dict[str, str] = field(default_factory=dict)  # D1-D4: UP / DOWN / NEUTRAL / MISSING
    notes: list[str] = field(default_factory=list)


def daily_frame(session: Session, instrument_id: int) -> pd.DataFrame:
    """VALID daily candles with high, low and close, indexed by IST trading date."""
    rows = session.execute(
        select(MarketCandle.candle_time, MarketCandle.high, MarketCandle.low, MarketCandle.close)
        .where(MarketCandle.instrument_id == instrument_id, MarketCandle.timeframe == "1d",
               MarketCandle.quality_status == "VALID", MarketCandle.high.is_not(None),
               MarketCandle.low.is_not(None), MarketCandle.close.is_not(None))
        .order_by(MarketCandle.candle_time)).all()
    if not rows:
        return pd.DataFrame(columns=["high", "low", "close"], dtype=float)
    idx = pd.DatetimeIndex([pd.Timestamp(to_ist(from_db(r[0])).date()) for r in rows])
    return pd.DataFrame({"high": [float(r[1]) for r in rows], "low": [float(r[2]) for r in rows],
                         "close": [float(r[3]) for r in rows]}, index=idx)


def last_complete_day(now: datetime) -> date:
    """Latest date whose daily candle is final: today after 15:30 IST, else the day before."""
    ist = to_ist(now)
    d = ist.date()
    if not (is_trading_day(d) and ist.time() >= SESSION_CLOSE):
        d = d - timedelta(days=1)
    return d


def _num(x) -> float | None:
    return None if x is None or pd.isna(x) else round(float(x), 2)


def _reading(up: bool | None, down: bool | None) -> str:
    if up is None or down is None:
        return "MISSING"
    return "UP" if up else ("DOWN" if down else "NEUTRAL")


def compute(df: pd.DataFrame, now: datetime, reference: pd.DataFrame | None = None) -> Metrics:
    """Metrics for one instrument. `reference` = NIFTY's frame, for relative strength."""
    m = Metrics(bars=len(df))
    if df.empty:
        m.status = "NO_DATA"
        return m
    m.as_of = df.index[-1].date()
    close, high, low = df["close"], df["high"], df["low"]

    ema20, ema50, ema200 = (ind.ema(close, n).iloc[-1] for n in (20, 50, 200))
    adx, plus_di, minus_di = (s.iloc[-1] for s in ind.adx(high, low, close, 14))
    rsi14 = ind.rsi(close, 14).iloc[-1]
    atr14 = ind.atr(high, low, close, 14).iloc[-1]
    weekly = ind.weekly_closes(close, pd.Timestamp(last_complete_day(now)))
    w_ema20 = ind.ema(weekly, 20).iloc[-1] if len(weekly) else None
    last = close.iloc[-1]

    enough_daily = len(df) >= MIN_DAILY_BARS
    enough_weekly = len(weekly) >= MIN_WEEKLY_BARS
    if not enough_daily:
        m.status = "INSUFFICIENT_HISTORY"
        m.notes.append(f"{len(df)} daily candles; {MIN_DAILY_BARS} needed before EMA/ADX are reliable")
        ema20 = ema50 = ema200 = adx = plus_di = minus_di = rsi14 = atr14 = None
    elif len(df) < 400:
        ema200 = None
        m.notes.append("EMA200 needs 400 daily candles")
    if not enough_weekly:
        w_ema20 = None
        m.notes.append(f"{len(weekly)} weekly candles; {MIN_WEEKLY_BARS} needed for the weekly trend")

    rs = None
    if reference is not None and not reference.empty:
        both = pd.concat([close, reference["close"]], axis=1, join="inner").dropna()
        if len(both) > RS_DAYS:
            mine = both.iloc[-1, 0] / both.iloc[-1 - RS_DAYS, 0]
            ref = both.iloc[-1, 1] / both.iloc[-1 - RS_DAYS, 1]
            rs = mine / ref
            if both.index[-1].date() != m.as_of:
                m.notes.append("relative strength uses the last day both this and NIFTY have data")

    weekly_close = weekly.iloc[-1] if len(weekly) else None
    m.values = {
        "close": _num(last), "ema20": _num(ema20), "ema50": _num(ema50), "ema200": _num(ema200),
        "weekly_close": _num(weekly_close) if w_ema20 is not None else None,
        "weekly_ema20": _num(w_ema20),
        "adx14": _num(adx), "plus_di": _num(plus_di), "minus_di": _num(minus_di),
        "rsi14": _num(rsi14), "atr14": _num(atr14),
        "atr_pct": _num(atr14 / last * 100) if atr14 is not None else None,
        "ema20_distance_atr": _num((last - ema20) / atr14) if atr14 and ema20 is not None else None,
        "rs20_vs_nifty": None if rs is None else round(float(rs), 3),
    }

    v = m.values
    have = lambda *keys: all(v[k] is not None for k in keys)  # noqa: E731
    m.readings = {
        # D1 daily trend: close vs EMA50 and EMA20 vs EMA50
        "D1_daily_trend": _reading(
            *(((v["close"] > v["ema50"] and v["ema20"] > v["ema50"]),
               (v["close"] < v["ema50"] and v["ema20"] < v["ema50"]))
              if have("close", "ema20", "ema50") else (None, None))),
        # D2 weekly trend: weekly close vs weekly EMA20
        "D2_weekly_trend": _reading(
            *(((v["weekly_close"] > v["weekly_ema20"]), (v["weekly_close"] < v["weekly_ema20"]))
              if have("weekly_close", "weekly_ema20") else (None, None))),
        # D3 trend strength: ADX >= 20 and which DI line leads
        "D3_trend_strength": _reading(
            *(((v["adx14"] >= ADX_TREND_MIN and v["plus_di"] > v["minus_di"]),
               (v["adx14"] >= ADX_TREND_MIN and v["minus_di"] > v["plus_di"]))
              if have("adx14", "plus_di", "minus_di") else (None, None))),
        # D4 momentum: RSI 50-70 supports UP, 30-50 supports DOWN; outside = stretched
        "D4_momentum": _reading(
            *(((50 <= v["rsi14"] <= 70), (30 <= v["rsi14"] < 50))
              if have("rsi14") else (None, None))),
    }
    return m


def compute_for_instruments(session: Session, instruments: list[Instrument],
                            now: datetime) -> dict[int, Metrics]:
    """Metrics for each instrument id. NIFTY (the INDEX row) is the relative-strength reference."""
    frames = {i.id: daily_frame(session, i.id) for i in instruments}
    nifty = next((i for i in instruments if i.symbol == "NIFTY" and i.instrument_type == "INDEX"), None)
    reference = frames.get(nifty.id) if nifty else None
    return {i.id: compute(frames[i.id], now, None if i is nifty else reference) for i in instruments}
