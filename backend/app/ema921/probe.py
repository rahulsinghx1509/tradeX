"""How far back does Groww keep 1-minute candles for NIFTY spot and for EXPIRED NIFTY options?

The 9/21 EMA backtest needs both. For a few ages (3 days ... 3 years ago) this picks the last
trading day at that age and asks Groww for:
- NIFTY spot 1m for that day;
- the ATM call of the weekly expiry the backtest would have traded that day, 1m for that day.
It records counts and first/last candle times only, no prices. Read-only: no orders.
A first candle at 09:15 means Groww stamps a candle with its START minute (what the engine
assumes); 09:16 would mean it stamps the END, and the engine would need a one-minute shift.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from app.core.market_time import is_trading_day, to_ist
from app.ema921 import engine as E
from app.ema921 import loader as L
from app.ema921 import store as S

AGES_DAYS = (3, 30, 90, 180, 365, 545, 730, 913, 1095)


@dataclass
class Row:
    age: int
    day: date
    spot_bars: int | str = "-"
    spot_first: str = "-"
    spot_last: str = "-"
    expiry: str = "-"
    contract: str = "-"
    option_bars: int | str = "-"
    option_first: str = "-"
    option_last: str = "-"
    note: str = ""


def trading_day_at_age(today: date, age: int) -> date:
    d = today - timedelta(days=age)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def _span(candles) -> tuple[str, str]:
    if not candles:
        return "-", "-"
    return f"{to_ist(candles[0].time):%H:%M}", f"{to_ist(candles[-1].time):%H:%M}"


def _step(strikes: list[float]) -> str:
    gaps = sorted({round(b - a, 2) for a, b in zip(strikes, strikes[1:], strict=False)})
    return "/".join(f"{g:g}" for g in gaps[:3]) or "-"


def _expiries_around(client: Any, day: date) -> list[date]:
    out: set[date] = set()
    for y, m in L._months(day, day + timedelta(days=31)):
        raw = (client.get_expiries(exchange="NSE", underlying_symbol=S.UNDERLYING, year=y,
                                   month=m) or {}).get("expiries") or []
        out |= {date.fromisoformat(str(e)[:10]) for e in raw}
    return sorted(out)


def probe_age(client: Any, today: date, age: int) -> Row:
    row = Row(age, trading_day_at_age(today, age))
    try:
        spot = L.fetch_day(client, S.SPOT_SYMBOL, "CASH", row.day)
    except Exception as exc:
        row.spot_bars, row.note = "ERROR", f"spot: {str(exc)[:120]}"
        return row
    row.spot_bars = len(spot)
    row.spot_first, row.spot_last = _span(spot)
    closes = [c.close for c in spot if c.close is not None]
    if not closes:
        row.note = "no spot candles, so no ATM strike to try"
        return row
    try:
        expiry = E.pick_expiry(row.day, _expiries_around(client, row.day), roll=True)
        if expiry is None:
            row.note = "get_expiries returned nothing"
            return row
        row.expiry = expiry.isoformat()
        contracts = (client.get_contracts(exchange="NSE", underlying_symbol=S.UNDERLYING,
                                          expiry_date=expiry.isoformat()) or {}).get("contracts") or []
        strike = E.atm_strike(closes[len(closes) // 2])
        match = [c for c in contracts if L.parse_contract(c) == (strike, "CE")]
        if not match:
            strikes = sorted({k for c in contracts if (p := L.parse_contract(c)) for k in [p[0]]})
            span = (f"strikes {strikes[0]:g}-{strikes[-1]:g}, step {_step(strikes)}"
                    if strikes else "no strikes")
            row.note = (f"no {strike:g} CE among {len(contracts)} contracts "
                        f"({span}, spot {closes[len(closes) // 2]:g})")
            return row
        row.contract = str(match[0])
        opt = L.fetch_day(client, row.contract, "FNO", row.day)
        row.option_bars = len(opt)
        row.option_first, row.option_last = _span(opt)
    except Exception as exc:
        row.option_bars, row.note = "ERROR", f"option: {str(exc)[:120]}"
    return row


def run(client: Any, today: date) -> list[Row]:
    return [probe_age(client, today, a) for a in AGES_DAYS]


def to_markdown(rows: list[Row], today: date) -> str:
    lines = ["# Groww 1-minute history probe (9/21 EMA backtest)", "",
             f"Run {today}. One trading day per age. Counts and times (IST) only, no prices. "
             "A full day has 375 one-minute candles (09:15 to 15:29); Groww also sends a 15:30 one, "
             "which the engine ignores.", "",
             "| Age (days) | Day | Spot 1m | First | Last | Expiry used | ATM call | Option 1m | "
             "First | Last | Note |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r.age} | {r.day} | {r.spot_bars} | {r.spot_first} | {r.spot_last} | "
                     f"{r.expiry} | {r.contract} | {r.option_bars} | {r.option_first} | "
                     f"{r.option_last} | {r.note} |")
    ok_spot = [r.age for r in rows if isinstance(r.spot_bars, int) and r.spot_bars > 0]
    ok_opt = [r.age for r in rows if isinstance(r.option_bars, int) and r.option_bars > 0]
    lines += ["", f"- Oldest age with spot 1m: {max(ok_spot) if ok_spot else 'none'} days",
              f"- Oldest age with expired-option 1m: {max(ok_opt) if ok_opt else 'none'} days",
              "- If the first candle is 09:16 instead of 09:15, tell Claude: the candle stamps are "
              "end times and the engine needs a shift."]
    return "\n".join(lines) + "\n"
