"""VIX-jump volatility warning (rule VJ-1.0), the first rule to pass a final test.

Rule: when India VIX's close is more than JUMP_PCT above its close 5 trading days earlier, NIFTY's
next month has tended to be more volatile than its last 20 days (study FS-1.1; one-time final test
on held-back data PASSED on 2026-10-18, see docs/01 §15). On those days the answer for **option
selling is NO TRADE**. It says nothing about direction: never a buy/sell signal, and no orders.

JUMP_PCT is the development-period 80th percentile of the 5-day VIX change, as printed by the
final test (7.8, rounded to one decimal). It is fixed: changing it means a new version and a new
test, never a quiet edit.

Missing or stale VIX data gives status NO_DATA, which also means NO TRADE for option selling
(the project rule: stale or invalid data => NO TRADE).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.market_time import IST, trading_days
from app.db.models import Instrument
from app.providers.base import MarketDataProvider
from app.services.data_quality import VIX_SYMBOL
from app.services.ingestion import IngestResult, ingest_candles
from app.services.metrics import daily_frame, last_complete_day

VERSION = "VJ-1.0"
JUMP_PCT = 7.8
LOOKBACK = 5
MAX_LAG_TRADING_DAYS = 1  # the day's VIX close is loaded after the close, so allow one day
REFRESH_DAYS = 20


@dataclass
class VixWarning:
    version: str
    status: str  # "WARNING" | "CLEAR" | "NO_DATA"
    as_of: str | None
    vix: float | None
    change_5d_pct: float | None
    jump_pct: float
    started_today: bool
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


def _change(closes: pd.Series) -> pd.Series:
    return (closes / closes.shift(LOOKBACK) - 1) * 100


def evaluate(vix: pd.DataFrame, now: datetime) -> VixWarning:
    """The warning for the latest complete VIX close (daily frame indexed by IST date)."""
    def no_data(msg: str, as_of=None) -> VixWarning:
        return VixWarning(VERSION, "NO_DATA", as_of, None, None, JUMP_PCT, False,
                          f"{msg} Option selling: NO TRADE until VIX data is back.")

    if vix is None or len(vix) <= LOOKBACK:
        return no_data("No India VIX history stored.")
    closes = vix["close"].sort_index()
    last_day = last_complete_day(now)
    closes = closes[closes.index <= pd.Timestamp(last_day)]
    expected = trading_days(last_day - timedelta(days=15), last_day)
    if len(closes) <= LOOKBACK or not expected:
        return no_data("No India VIX history stored.")
    as_of = closes.index[-1]
    oldest_ok = pd.Timestamp(expected[-1 - MAX_LAG_TRADING_DAYS]) if len(expected) > 1 else \
        pd.Timestamp(expected[-1])
    if as_of < oldest_ok:
        return no_data(f"India VIX is stale (last close {as_of:%Y-%m-%d}).", f"{as_of:%Y-%m-%d}")
    chg = _change(closes)
    now_chg, prev_chg = chg.iloc[-1], chg.iloc[-2]
    if pd.isna(now_chg):
        return no_data("India VIX history has a gap.", f"{as_of:%Y-%m-%d}")
    active = bool(now_chg > JUMP_PCT)
    started = active and not (pd.notna(prev_chg) and prev_chg > JUMP_PCT)
    if active:
        msg = (f"India VIX jumped {now_chg:.1f}% in 5 days (above {JUMP_PCT}%). NIFTY's next "
               "month is likely to be more volatile than its last 20 days. Option selling: NO TRADE. "
               "Not a buy/sell signal.")
    else:
        msg = f"India VIX 5-day change {now_chg:+.1f}% (warning above {JUMP_PCT}%)."
    return VixWarning(VERSION, "WARNING" if active else "CLEAR", f"{as_of:%Y-%m-%d}",
                      float(closes.iloc[-1]), float(now_chg), JUMP_PCT, started, msg)


def vix_instrument(session: Session, provider_name: str) -> Instrument | None:
    return session.scalar(select(Instrument).where(Instrument.symbol == VIX_SYMBOL,
                                                   Instrument.provider == provider_name))


def current(session: Session, provider_name: str, now: datetime) -> VixWarning:
    inst = vix_instrument(session, provider_name)
    return evaluate(daily_frame(session, inst.id) if inst else None, now)


def refresh(session: Session, provider: MarketDataProvider, now: datetime) -> IngestResult:
    """Top up the last few weeks of India VIX daily closes (VIX is an inactive instrument, so the
    normal refresh skips it)."""
    from app.services import vix_study  # local import: vix_study imports the study helpers

    inst = vix_instrument(session, provider.name)
    if inst is None:
        return vix_study.load_vix(session, provider, now)
    start = datetime.combine((now.astimezone(IST) - timedelta(days=REFRESH_DAYS)).date(),
                             datetime.min.time(), IST)
    return ingest_candles(session, provider, inst, "1d", start, now, now)


def alert_text(w: VixWarning) -> str:
    return (f"TradeX volatility warning ({w.version}), {w.as_of}: {w.message} "
            "This rule passed one held-back test; about 6 in 10 past jumps were followed by a "
            "more volatile month.")
