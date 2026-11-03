"""Load 5-minute candles for the NIFTY 50 + Next 50 stocks (and NIFTY) from Groww. Read-only.

1. Members: NSE's NIFTY 50 and NIFTY Next 50 CSVs (same downloads `intraday-probe` checks),
   matched to Groww's instrument list. If NSE can't be reached, the last saved list is reused.
2. Candles: per symbol, fixed 30-day windows from the newest backwards. Groww accepted 30-day
   5-minute requests and refused 60-day ones in the 2026-10-29 probe. A symbol stops after
   2 empty windows in a row (its history has run out). OK/EMPTY windows are never asked again;
   the window holding the latest day is PARTIAL and is fetched again next run.

NOT_VERIFIED until it runs on the server: how far back Groww keeps 5-minute stock candles.
"""

from __future__ import annotations

import time as _time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.market_time import session_bounds, to_ist
from app.providers.groww import parse_candles
from app.stocks import store as S

INTERVAL = "5minute"  # Groww's name (SDK docs; accepted in the 2026-10-29 probe)
WINDOW_DAYS = 30
ANCHOR = date(2020, 1, 1)  # windows are a fixed grid from here, so re-runs ask the same windows
STOP_AFTER_EMPTY = 2
DOWN_TEXT = "no healthy upstream"
NIFTY_GROWW = "NSE-NIFTY"


@dataclass
class LoadReport:
    symbols: int = 0
    windows_fetched: int = 0
    windows_empty: int = 0
    candles: int = 0
    first_day: dict[str, date] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def windows(start: date, end: date) -> list[tuple[date, date, date]]:
    """(start, end, grid key) of the fixed 30-day windows touching [start, end], newest first,
    clipped to the range."""
    k0 = (start - ANCHOR).days // WINDOW_DAYS
    k1 = (end - ANCHOR).days // WINDOW_DAYS
    out = []
    for k in range(k1, k0 - 1, -1):
        ws = ANCHOR + timedelta(days=k * WINDOW_DAYS)
        we = ws + timedelta(days=WINDOW_DAYS - 1)
        out.append((max(ws, start), min(we, end), ws))
    return out


def fetch_window(client: Any, groww_symbol: str, start: date, end: date) -> list:
    kwargs = dict(exchange="NSE", segment="CASH", groww_symbol=groww_symbol,
                  start_time=to_ist(session_bounds(start)[0]).strftime("%Y-%m-%d %H:%M:%S"),
                  end_time=to_ist(session_bounds(end)[1]).strftime("%Y-%m-%d %H:%M:%S"),
                  candle_interval=INTERVAL)
    try:
        payload = client.get_historical_candles(**kwargs)
    except Exception as exc:  # Groww's gateway sometimes says its backend is down: retry once
        if DOWN_TEXT not in str(exc).lower():
            raise
        _time.sleep(3)
        payload = client.get_historical_candles(**kwargs)
    return parse_candles(payload or {}, "5m")


def load_symbol(session: Session, client: Any, symbol: str, groww_symbol: str, start: date,
                end: date, rep: LoadReport, log: Callable[[str], None] = print) -> None:
    done = S.done_windows(session, symbol)
    empty_run = 0
    for ws, we, key in windows(start, end):
        if done.get(key) in S.DONE:
            empty_run = empty_run + 1 if done[key] == "EMPTY" else 0
            if empty_run >= STOP_AFTER_EMPTY:
                return
            continue
        try:
            candles = fetch_window(client, groww_symbol, ws, we)
        except Exception as exc:  # record and move on; ERROR windows are retried next run
            rep.errors.append(f"{symbol} {ws}..{we}: {str(exc)[:200]}")
            S.record_fetch(session, symbol, key, we, 0, "ERROR", str(exc))
            session.commit()
            continue
        n = S.save_candles(session, symbol, candles)
        complete = key + timedelta(days=WINDOW_DAYS - 1) <= end
        status = ("OK" if complete else "PARTIAL") if n else ("EMPTY" if complete else "PARTIAL")
        S.record_fetch(session, symbol, key, we, n, status)
        session.commit()
        rep.windows_fetched += 1
        rep.candles += n
        if n:
            rep.first_day[symbol] = min(to_ist(c.time).date() for c in candles)
            empty_run = 0
        else:
            rep.windows_empty += 1
            empty_run += 1
            if empty_run >= STOP_AFTER_EMPTY:
                log(f"  {symbol}: no candles before {ws + timedelta(days=WINDOW_DAYS * 2)}")
                return


def match_groww(symbols: list[tuple[str, str]], instruments: Any) -> tuple[list, list[str]]:
    """[(symbol, index_name)] -> ([(symbol, index_name, groww_symbol)], [symbols not in Groww])."""
    df = instruments
    cash = df[(df["exchange"] == "NSE") & (df["segment"] == "CASH")]
    by_sym = {str(r["trading_symbol"]): str(r.get("groww_symbol") or f"NSE-{r['trading_symbol']}")
              for _, r in cash.iterrows()}
    found, missing = [], []
    for sym, idx in symbols:
        if sym in by_sym:
            found.append((sym, idx, by_sym[sym]))
        else:
            missing.append(sym)
    return found, missing


def load_all(session: Session, client: Any, start: date, end: date,
             only: list[str] | None = None, log: Callable[[str], None] = print) -> LoadReport:
    rep = LoadReport()
    todo = [(S.INDEX_SYMBOL, NIFTY_GROWW)] + [(s, g) for s, _, g in S.members(session)]
    if only:
        wanted = {o.upper() for o in only}
        todo = [(s, g) for s, g in todo if s in wanted]
    for i, (sym, groww) in enumerate(todo, 1):
        load_symbol(session, client, sym, groww, start, end, rep, log)
        rep.symbols += 1
        log(f"  {i}/{len(todo)} {sym} done ({rep.candles:,} candles so far)")
    return rep
