"""Fetch 1-minute history from Groww for the 9/21 EMA backtest. Read-only, resumable.

Order of work (each step skips whatever an earlier run already stored):
1. NIFTY spot 1m, one call per trading day (plus a few warm-up days before the range).
2. Weekly expiry dates, one get_expiries call per month.
3. Which option contracts the backtest can ever need: for every EMA cross on 5, 15 and 30-minute
   bars closing between the earliest window start (09:30) and 15:00, the deep-ITM and ATM
   contracts each mode would trade. Only those are fetched, never the whole chain.
4. get_contracts for each needed expiry, then each needed option's 1m candles for that day.

Groww facts this relies on, all NOT_VERIFIED until `tradex.sh ema-probe` runs on the server:
- candle_interval "1minute" is accepted and returns candles stamped with their START minute;
- expired NIFTY options still return 1m candles (segment FNO, their groww_symbol);
- get_contracts lists expired contracts as NSE-NIFTY-<ddMonyy>-<strike>-<CE|PE>.
"""

from __future__ import annotations

import time as _time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.core.market_time import IST, session_bounds, to_ist, trading_days
from app.ema921 import bars as B
from app.ema921 import engine as E
from app.ema921 import store as S
from app.providers.groww import parse_candles

INTERVAL = "1minute"  # Groww's name, from the SDK docs (NOT_VERIFIED)
DOWN_TEXT = "no healthy upstream"
EARLIEST_START = min(E.START_CHOICES)
WARMUP_CALENDAR_DAYS = 10


@dataclass
class LoadReport:
    spot_days: int = 0
    spot_empty: int = 0
    expiries: int = 0
    contracts_needed: int = 0
    contract_days_fetched: int = 0
    contract_days_empty: int = 0
    errors: list[str] = field(default_factory=list)


def parse_contract(symbol: str) -> tuple[float, str] | None:
    """(strike, CE/PE) from 'NSE-NIFTY-25Nov25-24500-CE', or None if it isn't an option."""
    parts = str(symbol).split("-")
    if len(parts) < 3 or parts[-1] not in ("CE", "PE"):
        return None
    try:
        return float(parts[-2]), parts[-1]
    except ValueError:
        return None


def fetch_day(client: Any, symbol: str, segment: str, day: date) -> list:
    start, end = session_bounds(day)
    kwargs = dict(exchange="NSE", segment=segment, groww_symbol=symbol,
                  start_time=to_ist(start).strftime("%Y-%m-%d %H:%M:%S"),
                  end_time=to_ist(end).strftime("%Y-%m-%d %H:%M:%S"), candle_interval=INTERVAL)
    try:
        payload = client.get_historical_candles(**kwargs)
    except Exception as exc:  # Groww's gateway sometimes says its backend is down: retry once
        if DOWN_TEXT not in str(exc).lower():
            raise
        _time.sleep(3)
        payload = client.get_historical_candles(**kwargs)
    return parse_candles(payload or {}, "1m")


def _fetch_and_store(session: Session, client: Any, symbol: str, segment: str, day: date,
                     rep: LoadReport) -> int | None:
    try:
        candles = fetch_day(client, symbol, segment, day)
    except Exception as exc:  # record and move on; ERROR rows are retried on the next run
        msg = f"{symbol} {day}: {str(exc)[:200]}"
        rep.errors.append(msg)
        S.record_fetch(session, symbol, day, 0, "ERROR", str(exc))
        session.commit()
        return None
    n = S.save_minutes(session, symbol, day, candles)
    S.record_fetch(session, symbol, day, n, "OK" if n else "EMPTY")
    session.commit()
    return n


def load_spot(session: Session, client: Any, start: date, end: date, rep: LoadReport,
              log: Callable[[str], None] = print) -> None:
    days = trading_days(start - timedelta(days=WARMUP_CALENDAR_DAYS), end)
    done = S.done_set(session, S.SPOT_SYMBOL)
    todo = [d for d in days if (S.SPOT_SYMBOL, d) not in done]
    log(f"NIFTY spot: {len(days) - len(todo)} days already stored, {len(todo)} to fetch")
    for i, d in enumerate(todo, 1):
        n = _fetch_and_store(session, client, S.SPOT_SYMBOL, "CASH", d, rep)
        if n:
            rep.spot_days += 1
        elif n == 0:
            rep.spot_empty += 1
        if i % 25 == 0:
            log(f"  spot {i}/{len(todo)} ({d})")


def _months(start: date, end: date) -> list[tuple[int, int]]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def load_expiries(session: Session, client: Any, start: date, end: date, rep: LoadReport,
                  log: Callable[[str], None] = print) -> None:
    """Expiry dates for every month of the range, plus the next month (trades near month-end
    can use next month's first expiry). Months already stored are skipped, except the last two."""
    key = f"EXPIRIES:{S.UNDERLYING}"
    months = _months(start, end + timedelta(days=31))
    for k, (y, m) in enumerate(months):
        first = date(y, m, 1)
        if k < len(months) - 2 and S.fetched(session, key, first):
            continue
        try:
            raw = (client.get_expiries(exchange="NSE", underlying_symbol=S.UNDERLYING, year=y,
                                       month=m) or {}).get("expiries") or []
        except Exception as exc:
            rep.errors.append(f"expiries {y}-{m:02d}: {str(exc)[:200]}")
            continue
        days = sorted({date.fromisoformat(str(e)[:10]) for e in raw})
        S.save_expiries(session, S.UNDERLYING, days)
        S.record_fetch(session, key, first, len(days), "OK" if days else "EMPTY")
        session.commit()
    rep.expiries = len(S.expiries(session))
    log(f"Expiries stored: {rep.expiries}")


def needed_legs(spot: pd.DataFrame, start: date, end: date,
                cfg: E.Config | None = None) -> set[tuple[date, float, str]]:
    """Every (day, strike, CE/PE) any timeframe, window or mode could enter on."""
    cfg = cfg or E.Config()
    need: set[tuple[date, float, str]] = set()
    for tf in B.TIMEFRAMES:
        for c in B.crosses(B.add_emas(B.resample(spot, tf))):
            t = c.bar_close.time()
            if not (start <= c.day <= end) or t < EARLIEST_START or t >= cfg.square_off:
                continue
            for mode in E.MODES:
                _, opt, strike = E.leg_for(mode, c.direction, c.spot, cfg)
                need.add((c.day, strike, opt))
    return need


def load_contracts(session: Session, client: Any, expiry_list: list[date], rep: LoadReport) -> None:
    key = f"CONTRACTS:{S.UNDERLYING}"
    for e in expiry_list:
        if S.fetched(session, key, e):
            continue
        try:
            raw = (client.get_contracts(exchange="NSE", underlying_symbol=S.UNDERLYING,
                                        expiry_date=e.isoformat()) or {}).get("contracts") or []
        except Exception as exc:
            rep.errors.append(f"contracts {e}: {str(exc)[:200]}")
            continue
        parsed = [(str(s), *p) for s in raw if (p := parse_contract(s))]
        S.save_contracts(session, S.UNDERLYING, e, parsed)
        S.record_fetch(session, key, e, len(parsed), "OK" if parsed else "EMPTY")
        session.commit()


def guess_symbol(session: Session, expiry: date, strike: float, option_type: str) -> str:
    """Groww's symbol for a contract its list left out, built like the listed ones of the same
    expiry (e.g. NSE-NIFTY-03Nov26-22850-CE). Only used if Groww then returns candles for it."""
    from sqlalchemy import select

    from app.db.models import OptionContract

    listed = session.scalar(select(OptionContract.groww_symbol).where(
        OptionContract.underlying == S.UNDERLYING, OptionContract.expiry_date == expiry,
        OptionContract.option_type.is_not(None)).limit(1))
    prefix = listed.rsplit("-", 2)[0] if listed else f"NSE-{S.UNDERLYING}-{expiry:%d%b%y}"
    return f"{prefix}-{strike:g}-{option_type}"


def try_unlisted(session: Session, client: Any, expiry: date, strike: float, option_type: str,
                 day: date, rep: LoadReport, live: bool = False) -> str | None:
    """get_contracts skips some strikes that do trade (server probe 2026-11-01: 22850 and 24600
    were missing while 50-point neighbours were listed). Ask Groww for the guessed symbol's
    candles; if any come back the contract is real and is recorded. Never invents prices."""
    sym = guess_symbol(session, expiry, strike, option_type)
    if live:  # today is still trading: store what exists, but don't mark the day as fetched
        try:
            candles = fetch_day(client, sym, "FNO", day)
        except Exception:
            candles = []
        n = S.save_minutes(session, sym, day, candles)
        session.commit()
    elif S.fetched(session, sym, day):
        n = 1 if S.load_minutes(session, sym, day, day).shape[0] else 0
    else:
        n = _fetch_and_store(session, client, sym, "FNO", day, rep) or 0
    if n:
        S.save_contracts(session, S.UNDERLYING, expiry, [(sym, strike, option_type)])
        session.commit()
        return sym
    return None


def load_options(session: Session, client: Any, spot: pd.DataFrame, start: date, end: date,
                 rep: LoadReport, cfg: E.Config | None = None,
                 log: Callable[[str], None] = print) -> None:
    cfg = cfg or E.Config()
    legs = needed_legs(spot, start, end, cfg)
    expiry_list = S.expiries(session)
    by_leg = {(d, k, t): E.pick_expiry(d, expiry_list, cfg.roll_on_expiry_day) for d, k, t in legs}
    needed_expiries = sorted({e for e in by_leg.values() if e})
    load_contracts(session, client, needed_expiries, rep)
    data = S.DbOptionData(session)
    todo: list[tuple[str, date]] = []
    missing_contract = 0
    for (d, k, t), e in sorted(by_leg.items()):
        sym = data.contract(e, k, t) if e else None
        if sym is None and e is not None:
            sym = try_unlisted(session, client, e, k, t, d, rep)
        if sym is None:
            missing_contract += 1
        else:
            todo.append((sym, d))
    todo = sorted(set(todo), key=lambda x: (x[1], x[0]))
    rep.contracts_needed = len(todo)
    done = S.done_set(session, f"NSE-{S.UNDERLYING}-")
    todo = [x for x in todo if x not in done]
    log(f"Options: {rep.contracts_needed} contract-days needed, {len(todo)} to fetch"
        + (f", {missing_contract} legs with no listed contract" if missing_contract else ""))
    for i, (sym, d) in enumerate(todo, 1):
        n = _fetch_and_store(session, client, sym, "FNO", d, rep)
        if n:
            rep.contract_days_fetched += 1
        elif n == 0:
            rep.contract_days_empty += 1
        if i % 100 == 0:
            log(f"  options {i}/{len(todo)} ({d})")


def load_all(session: Session, client: Any, start: date, end: date,
             log: Callable[[str], None] = print) -> LoadReport:
    rep = LoadReport()
    load_spot(session, client, start, end, rep, log)
    load_expiries(session, client, start, end, rep, log)
    spot = S.load_spot(session, start, end, WARMUP_CALENDAR_DAYS)
    load_options(session, client, spot, start, end, rep, log=log)
    return rep


def default_range(today: date, years: int) -> tuple[date, date]:
    """[today - years, yesterday]. Holidays and weekends inside are skipped by the loader."""
    try:
        start = today.replace(year=today.year - years)
    except ValueError:  # 29 February
        start = today.replace(year=today.year - years, day=28)
    return start, today - timedelta(days=1)


def ist_today() -> date:
    return pd.Timestamp.now(tz=IST).date()
