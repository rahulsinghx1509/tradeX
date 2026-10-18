"""How far back does Groww keep futures and options history, and does it carry open interest?

Before designing a study on open interest (OI) we need to know what history exists. For each
underlying and year this asks Groww for the last June expiry, lists its contracts, and fetches
daily candles for:
- the futures contract, over the 90 days before expiry (a monthly future trades about 3 months);
- one call option near the middle strike, over the 30 days before expiry.
It records counts only (candles, and candles that have OI), never prices, so the report is safe
to share. Read-only: it never places, modifies or cancels orders.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.market_time import to_ist
from app.providers.groww import INTERVALS, parse_candles

PROBE_MONTH = 6
FUT_DAYS, OPT_DAYS = 90, 30


@dataclass
class ProbeRow:
    symbol: str
    year: int
    expiry: str | None = None
    futures: int | None = None
    options: int | None = None
    fut_candles: int | None = None
    fut_with_oi: int | None = None
    opt_candles: int | None = None
    opt_with_oi: int | None = None
    note: str = ""


def _strike(contract: str) -> float:
    try:
        return float(contract.split("-")[-2])
    except (IndexError, ValueError):
        return float("nan")


def _counts(client: Any, symbol: str, expiry: str, days: int) -> tuple[int, int]:
    end = datetime.fromisoformat(expiry).replace(tzinfo=UTC)
    payload = client.get_historical_candles(
        exchange="NSE", segment="FNO", groww_symbol=symbol,
        start_time=(end - timedelta(days=days)).strftime("%Y-%m-%d 09:15:00"),
        end_time=end.strftime("%Y-%m-%d 15:30:00"), candle_interval=INTERVALS["1d"])
    candles = parse_candles(payload or {}, "1d")
    return len(candles), sum(1 for c in candles if c.open_interest is not None)


def probe_one(client: Any, symbol: str, year: int) -> ProbeRow:
    row = ProbeRow(symbol, year)
    try:
        exp = (client.get_expiries(exchange="NSE", underlying_symbol=symbol, year=year,
                                   month=PROBE_MONTH) or {}).get("expiries") or []
        if not exp:
            row.note = "no expiries returned"
            return row
        row.expiry = str(exp[-1])[:10]
        contracts = (client.get_contracts(exchange="NSE", underlying_symbol=symbol,
                                          expiry_date=row.expiry) or {}).get("contracts") or []
        futures = [c for c in contracts if c.endswith("FUT")]
        calls = sorted((c for c in contracts if c.endswith("CE")), key=_strike)
        row.futures, row.options = len(futures), sum(1 for c in contracts if c.endswith(("CE", "PE")))
        if futures:
            row.fut_candles, row.fut_with_oi = _counts(client, futures[0], row.expiry, FUT_DAYS)
        if calls:
            row.opt_candles, row.opt_with_oi = _counts(client, calls[len(calls) // 2], row.expiry,
                                                       OPT_DAYS)
    except Exception as exc:  # record and move on: one failure must not hide the rest
        row.note = f"error: {str(exc)[:120]}"
    return row


def probe(client: Any, symbols: list[str], years: list[int]) -> list[ProbeRow]:
    return [probe_one(client, s, y) for s in symbols for y in years]


def default_years(now: datetime, first: int = 2020) -> list[int]:
    """Every full year from `first` up to last year, plus this year if June has passed."""
    ist = to_ist(now)
    last = ist.year if ist.month > PROBE_MONTH else ist.year - 1
    return list(range(first, last + 1))


def to_markdown(rows: list[ProbeRow], now: datetime) -> str:
    def c(x):
        return "–" if x is None else str(x)

    lines = ["# Derivatives history probe (Groww)", "",
             f"Generated {to_ist(now):%Y-%m-%d %H:%M} IST. Last June expiry of each year. "
             f"Futures: daily candles in the {FUT_DAYS} days before expiry. Option: one call near "
             f"the middle strike, {OPT_DAYS} days before expiry. Counts only, no prices.", "",
             "| Underlying | Year | Expiry | Futures | Options | Future candles | with OI | "
             "Option candles | with OI | Note |", "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r.symbol} | {r.year} | {c(r.expiry)} | {c(r.futures)} | {c(r.options)} | "
                     f"{c(r.fut_candles)} | {c(r.fut_with_oi)} | {c(r.opt_candles)} | "
                     f"{c(r.opt_with_oi)} | {r.note} |")
    lines += ["", "## Earliest year with futures open interest", "",
              "| Underlying | Earliest year | Years with OI |", "|---|---|---|"]
    for sym in dict.fromkeys(r.symbol for r in rows):
        ok = [r.year for r in rows if r.symbol == sym and (r.fut_with_oi or 0) > 0]
        lines.append(f"| {sym} | {min(ok) if ok else '–'} | {len(ok)} |")
    return "\n".join(lines) + "\n"
