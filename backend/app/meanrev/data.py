"""MR-4 inputs from the database: spot, futures volume, daily closes, India VIX, event days."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.market_time import IST
from app.db.models import Instrument
from app.ema921 import loader as L
from app.ema921 import store as S
from app.meanrev import FUT_SYMBOLS, VIX_SYMBOL
from app.meanrev import engine as E
from app.meanrev.report import IndexInput, Inputs
from app.pullback.engine import load_futures
from app.services.metrics import daily_frame

EVENTS_FILE = Path(__file__).resolve().parents[1] / "data" / "mr4_event_days.csv"


def load_events(path: Path = EVENTS_FILE) -> frozenset[date]:
    with path.open(encoding="utf-8") as fh:
        rows = csv.DictReader(line for line in fh if not line.startswith("#"))
        return frozenset(date.fromisoformat(r["date"].strip()) for r in rows if r.get("date"))


def stored_daily_frame(session: Session, symbol: str) -> pd.DataFrame:
    """Daily high/low/close of an index from market_candles (the backfilled official candles)."""
    insts = session.scalars(select(Instrument).where(Instrument.symbol == symbol,
                                                     Instrument.instrument_type == "INDEX",
                                                     Instrument.is_sample.is_(False))).all()
    best = pd.DataFrame(columns=["high", "low", "close"], dtype=float)
    for inst in insts:
        f = daily_frame(session, inst.id)
        if len(f) > len(best):
            best = f
    return best


def stored_daily(session: Session, symbol: str) -> pd.Series:
    """Daily closes of an index from market_candles (the backfilled official closes)."""
    return stored_daily_frame(session, symbol)["close"]


def futures_ohlc(session: Session, start: date, end: date, symbol: str) -> pd.DataFrame:
    """Near-month futures 1-minute candles with their open (MR-4N futures fills)."""
    return S.load_minutes(session, symbol, start, end)


def vix_at_open(session: Session, start: date, end: date) -> dict[date, float]:
    """{day: India VIX 09:15 one-minute open (its close if the open is missing)}."""
    df = S.load_minutes(session, VIX_SYMBOL, start, end)
    out: dict[date, float] = {}
    if df.empty:
        return out
    ist = pd.to_datetime(df["time"], utc=True).dt.tz_convert(IST)
    first = df[(ist.dt.hour == 9) & (ist.dt.minute == 15)].assign(day=ist.dt.date)
    for r in first.itertuples(index=False):
        v = r.open if pd.notna(r.open) else r.close
        if pd.notna(v):
            out[r.day] = float(v)
    return out


def load_inputs(session: Session, start: date, end: date, underlyings=("NIFTY", "BANKNIFTY"),
                options_wrap=None) -> Inputs:
    """Everything `report.run_all` needs. `options_wrap` (the loader's recorder) wraps each
    index's DbOptionData."""
    indices = {}
    for u in underlyings:
        spot = S.load_spot(session, start, end, L.WARMUP_CALENDAR_DAYS, u)
        if spot.empty:
            continue
        fut = load_futures(session, start, end, FUT_SYMBOLS[u])
        closes = E.daily_closes(stored_daily(session, u), spot)
        opts = S.DbOptionData(session, u)
        indices[u] = IndexInput(u, spot, fut, closes, options_wrap(u, opts) if options_wrap else opts)
    vix_daily = stored_daily(session, "INDIAVIX")
    market = E.Market(vix_at_open(session, start, end), vix_daily, load_events())
    return Inputs(start, end, indices, market)


def coverage(inp: Inputs) -> list[str]:
    """One line per input: how much of the range is there (printed before the run)."""
    lines = []
    for u, ix in inp.indices.items():
        days = pd.to_datetime(ix.spot["time"], utc=True).dt.tz_convert(IST).dt.date
        spot_days = len({d for d in days if inp.start <= d <= inp.end})
        fdays = 0
        if not ix.fut.empty:
            fd = pd.to_datetime(ix.fut["time"], utc=True).dt.tz_convert(IST).dt.date
            fdays = len({d for d in fd if inp.start <= d <= inp.end})
        daily_before = int((ix.closes.index < pd.Timestamp(inp.start)).sum())
        lines.append(f"{u}: spot {spot_days} days, futures volume {fdays} days, "
                     f"{daily_before} daily closes before {inp.start} (200 needed), "
                     f"{len(ix.options.expiries())} expiries")
    lines.append(f"India VIX: 09:15 one-minute value on {len(inp.market.vix_915)} days, "
                 f"{len(inp.market.vix_daily)} daily closes; event days {len(inp.market.events)}")
    return lines
