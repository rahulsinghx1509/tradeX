"""NIFTY near-month futures 1-minute candles (for their volume): history loader and live fetch.

Read-only Groww calls, no orders. One continuous series is stored under FUT_SYMBOL in
minute_candles (rule 1 in __init__): each day holds the candles of that day's near-month
contract. Days already fetched are skipped, so the loader can be stopped and re-run.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.market_time import trading_days
from app.ema921 import loader as L
from app.ema921 import store as S
from app.volume import FUT_SYMBOL, LOOKBACK_DAYS, UNDERLYING

# Calendar days of history the live engine makes sure are stored before it computes RVOL:
# LOOKBACK_DAYS trading days need about 1.5x as many calendar days, plus holidays.
LIVE_HISTORY_DAYS = 40


@dataclass
class FutLoad:
    days: int = 0
    empty: int = 0
    skipped: int = 0
    no_contract: list[date] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _month_list(start: date, end: date) -> list[tuple[int, int]]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def month_future(client: Any, year: int, month: int,
                 underlying: str = UNDERLYING) -> tuple[date, str] | None:
    """(expiry, groww_symbol) of the futures contract expiring in this month, or None.

    Same documented calls as GrowwProvider.expired_future: expiries are tried from the month's
    last one backwards, because weekly index expiries list options only."""
    raw = (client.get_expiries(exchange="NSE", underlying_symbol=underlying, year=year,
                               month=month) or {}).get("expiries") or []
    for e in sorted({str(x)[:10] for x in raw}, reverse=True):
        contracts = (client.get_contracts(exchange="NSE", underlying_symbol=underlying,
                                          expiry_date=e) or {}).get("contracts") or []
        futs = [str(c) for c in contracts if str(c).upper().endswith("FUT")]
        if futs:
            return date.fromisoformat(e), futs[0]
    return None


def contracts(client: Any, start: date, end: date, underlying: str = UNDERLYING,
              errors: list[str] | None = None) -> list[tuple[date, str]]:
    """Monthly futures (expiry, symbol) from start's month to two months after end, sorted."""
    out = []
    last = end + timedelta(days=62)
    for y, m in _month_list(start, last):
        try:
            got = month_future(client, y, m, underlying)
        except Exception as exc:  # one month failing must not stop the rest
            if errors is not None:
                errors.append(f"futures {y}-{m:02d}: {str(exc)[:200]}")
            continue
        if got:
            out.append(got)
    return sorted(set(out))


def near_month(day: date, futs: list[tuple[date, str]]) -> str | None:
    """The contract with the earliest expiry on or after `day` (rule 1)."""
    for expiry, symbol in futs:
        if expiry >= day:
            return symbol
    return None


def load(session: Session, client: Any, start: date, end: date,
         log: Callable[[str], None] = print, underlying: str = UNDERLYING) -> FutLoad:
    """Fetch and store every trading day's near-month futures 1m candles in [start, end]."""
    rep = FutLoad()
    days = trading_days(start, end)
    done = S.done_set(session, FUT_SYMBOL)
    todo = [d for d in days if (FUT_SYMBOL, d) not in done]
    rep.skipped = len(days) - len(todo)
    log(f"{underlying} near-month futures: {rep.skipped} days already stored, {len(todo)} to fetch")
    if not todo:
        return rep
    futs = contracts(client, todo[0], todo[-1], underlying, rep.errors)
    log(f"  {len(futs)} monthly futures contracts found")
    for i, d in enumerate(todo, 1):
        sym = near_month(d, futs)
        if sym is None:
            rep.no_contract.append(d)
            continue
        try:
            candles = L.fetch_day(client, sym, "FNO", d)
        except Exception as exc:  # recorded; ERROR days are retried on the next run
            rep.errors.append(f"{sym} {d}: {str(exc)[:200]}")
            S.record_fetch(session, FUT_SYMBOL, d, 0, "ERROR", f"{sym}: {exc}")
            session.commit()
            continue
        n = S.save_minutes(session, FUT_SYMBOL, d, candles)
        S.record_fetch(session, FUT_SYMBOL, d, n, "OK" if n else "EMPTY", None if n else sym)
        session.commit()
        if n:
            rep.days += 1
        else:
            rep.empty += 1
        if i % 25 == 0:
            log(f"  {i}/{len(todo)} ({d}, {sym}, {n} candles)")
    return rep


class LiveFutures:
    """Today's near-month futures candles for the live engine; history warmed up once a day."""

    def __init__(self, session: Session, client: Any, today: date):
        self.session, self.client, self.today = session, client, today
        self.symbol: str | None = None
        self.warmed = False

    def warm_up(self, log: Callable[[str], None] = print) -> None:
        """Make sure the last LIVE_HISTORY_DAYS calendar days are stored (yesterday back)."""
        self.warmed = True
        load(self.session, self.client, self.today - timedelta(days=LIVE_HISTORY_DAYS),
             self.today - timedelta(days=1), log=log)
        futs = contracts(self.client, self.today, self.today)
        self.symbol = near_month(self.today, futs)

    def fetch_today(self, closed: Callable[[list], list]) -> int:
        """Store today's closed candles so far; returns how many."""
        if self.symbol is None:
            return 0
        candles = closed(L.fetch_day(self.client, self.symbol, "FNO", self.today))
        n = S.save_minutes(self.session, FUT_SYMBOL, self.today, candles)
        self.session.commit()
        return n


def history_start(day: date) -> date:
    """First calendar day whose candles RVOL for `day` can need (LOOKBACK_DAYS trading days)."""
    return day - timedelta(days=int(LOOKBACK_DAYS * 1.6) + 10)
