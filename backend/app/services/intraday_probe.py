"""What does Groww really give for intraday, and can the server get NSE's index member lists?

Before collecting 5-minute data for all NIFTY 50 + NIFTY Next 50 stocks (docs/01 §19) we check,
from the server itself:

1. Candle sizes: do 1, 5 and 15-minute candles come back, for a stock, the index and a future?
2. History depth: how far back each size goes (a short window at 3 days ago, 30 days ago, ...).
3. Request size: the longest window one call accepts for each size.
4. One full day of 5-minute candles: bar count, first/last bar time (IST), missing open/volume/OI.
5. Quote fields: which keys get_quote returns (market depth, circuit limits, ...). Keys only.
6. NSE files: the NIFTY 50 and Next 50 member lists, and the daily delivery file.
7. Every member of both lists: is it in Groww's NSE list, and does it have futures?

The interval names ("1minute", "5minute", "15minute") come from the growwapi SDK docs and are
NOT_VERIFIED until this runs. Read-only: it never places, modifies or cancels orders. The report
holds counts, times and field names, no prices.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from app.core.market_time import is_trading_day, session_bounds, to_ist
from app.providers.base import InstrumentInfo
from app.providers.groww import parse_candles
from app.services import source_check as sc

# From the SDK "Annexures" page (NOT_VERIFIED): our name -> Groww's candle_interval.
INTRADAY_INTERVALS = {"1m": "1minute", "5m": "5minute", "15m": "15minute"}
DEPTH_AGES_DAYS = [3, 30, 60, 90, 120, 180, 270, 365, 545, 730, 1095, 1460, 1825]
DEPTH_WINDOW = timedelta(days=5)  # a few trading days, so one holiday can't look like "no data"
WINDOW_LENGTHS_DAYS = [1, 5, 10, 15, 30, 60, 90, 180]
INDEX_LISTS = {
    "NIFTY 50": ["https://niftyindices.com/IndexConstituent/ind_nifty50list.csv",
                 f"{sc.ARCHIVES}/content/indices/ind_nifty50list.csv"],
    "NIFTY Next 50": ["https://niftyindices.com/IndexConstituent/ind_niftynext50list.csv",
                      f"{sc.ARCHIVES}/content/indices/ind_niftynext50list.csv"],
}
DELIVERY = sc.Source(
    "delivery", "Daily stock file with delivery % (sec_bhavdata_full)", "Delivery %",
    lambda d: f"{sc.ARCHIVES}/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv",
)
DELIVERY_PERIODS = ["recent", "2024-07-01", "2022-01-03", "2020-01-01"]


def _fmt(t: datetime) -> str:
    return to_ist(t).strftime("%Y-%m-%d %H:%M:%S")


def _err(exc: Exception) -> str:
    return f"error: {str(exc)[:100]}"


def fetch(client: Any, inst: InstrumentInfo, interval: str, start: datetime, end: datetime):
    payload = client.get_historical_candles(
        exchange=inst.exchange, segment=inst.segment, groww_symbol=inst.provider_symbol,
        start_time=_fmt(start), end_time=_fmt(end), candle_interval=INTRADAY_INTERVALS[interval])
    return parse_candles(payload or {}, interval)


@dataclass
class DepthRow:
    label: str
    interval: str
    counts: dict[int, int | str] = field(default_factory=dict)  # age in days -> candles or error

    @property
    def oldest_age(self) -> int | None:
        ok = [age for age, n in self.counts.items() if isinstance(n, int) and n > 0]
        return max(ok) if ok else None


def probe_depth(client: Any, inst: InstrumentInfo, label: str, interval: str,
                now: datetime) -> DepthRow:
    row = DepthRow(label, interval)
    for age in DEPTH_AGES_DAYS:
        end = now - timedelta(days=age)
        try:
            row.counts[age] = len(fetch(client, inst, interval, end - DEPTH_WINDOW, end))
        except Exception as exc:  # record and move on: one failure must not hide the rest
            row.counts[age] = _err(exc)
    return row


def probe_window(client: Any, inst: InstrumentInfo, interval: str,
                 end: datetime) -> dict[int, int | str]:
    out: dict[int, int | str] = {}
    for days in WINDOW_LENGTHS_DAYS:
        try:
            out[days] = len(fetch(client, inst, interval, end - timedelta(days=days), end))
        except Exception as exc:
            out[days] = _err(exc)
    return out


@dataclass
class DayRow:
    label: str
    day: date
    bars: int | str = 0
    first: str = "-"
    last: str = "-"
    no_open: int = 0
    no_volume: int = 0
    with_oi: int = 0


def probe_day(client: Any, inst: InstrumentInfo, label: str, day: date) -> DayRow:
    row = DayRow(label, day)
    start, end = session_bounds(day)
    try:
        bars = fetch(client, inst, "5m", start, end)
    except Exception as exc:
        row.bars = _err(exc)
        return row
    row.bars = len(bars)
    if bars:
        row.first, row.last = f"{to_ist(bars[0].time):%H:%M}", f"{to_ist(bars[-1].time):%H:%M}"
    row.no_open = sum(1 for b in bars if b.open is None)
    row.no_volume = sum(1 for b in bars if b.volume is None)
    row.with_oi = sum(1 for b in bars if b.open_interest is not None)
    return row


def key_shape(value: Any, prefix: str = "") -> list[str]:
    """Field names of a quote, nested, with list lengths and whether each value is empty."""
    out = []
    if isinstance(value, dict):
        for k, v in value.items():
            out += key_shape(v, f"{prefix}{k}.") if isinstance(v, dict | list) else [
                f"{prefix}{k}" + (" (empty)" if v in (None, "") else "")]
    elif isinstance(value, list):
        name = prefix.rstrip(".")
        out.append(f"{name} [list of {len(value)}]")
        if value and isinstance(value[0], dict):
            out += key_shape(value[0], f"{name}[0].")
    return out


def probe_quote(client: Any, inst: InstrumentInfo) -> list[str]:
    try:
        payload = client.get_quote(exchange=inst.exchange, segment=inst.segment,
                                   trading_symbol=inst.trading_symbol or inst.symbol)
    except Exception as exc:
        return [_err(exc)]
    return key_shape(payload or {}) or ["(empty answer)"]


@dataclass
class ListCheck:
    name: str
    status: str = "MISSING"
    url: str = ""
    columns: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    note: str = ""


def parse_members(content: bytes) -> tuple[list[str], list[str]]:
    """Columns and the Symbol column of an NSE index member CSV (column name NOT_VERIFIED)."""
    rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig", errors="replace"))))
    cols = [c.strip() for c in (rows[0].keys() if rows else [])]
    key = next((k for k in (rows[0].keys() if rows else []) if k.strip().lower() == "symbol"), None)
    if key is None:
        raise ValueError("no Symbol column")
    return cols, [r[key].strip() for r in rows if (r.get(key) or "").strip()]


def fetch_list(checker: sc.Checker, name: str, urls: list[str]) -> ListCheck:
    chk = ListCheck(name)
    for url in urls:
        chk.url = url
        try:
            resp = checker.get(url)
        except httpx.HTTPError as exc:
            chk.status, chk.note = "ERROR", type(exc).__name__
            continue
        chk.status, chk.note = sc.classify(resp)
        if chk.status != "OK":
            continue
        try:
            chk.columns, chk.symbols = parse_members(resp.content)
            return chk
        except ValueError as exc:
            chk.status, chk.note = "ERROR", f"unreadable file: {exc}"
    return chk


@dataclass
class MemberRow:
    symbol: str
    index: str
    in_groww: bool
    has_futures: bool


def match_members(lists: list[ListCheck], instruments: Any, today: date) -> list[MemberRow]:
    """Is each member in Groww's NSE cash list, and does it have a live futures contract?"""
    import pandas as pd

    df = instruments
    cash = set(df[(df["exchange"] == "NSE") & (df["segment"] == "CASH")]["trading_symbol"])
    expiry = pd.to_datetime(df["expiry_date"], errors="coerce").dt.date
    futs = set(df[(df["exchange"] == "NSE") & (df["segment"] == "FNO")
                  & (df["instrument_type"] == "FUT") & (expiry >= today)]["underlying_symbol"])
    return [MemberRow(s, lc.name, s in cash, s in futs) for lc in lists for s in lc.symbols]


@dataclass
class ProbeResult:
    now: datetime
    depth: list[DepthRow] = field(default_factory=list)
    windows: dict[str, dict[int, int | str]] = field(default_factory=dict)
    days: list[DayRow] = field(default_factory=list)
    quotes: dict[str, list[str]] = field(default_factory=dict)
    lists: list[ListCheck] = field(default_factory=list)
    delivery: list[sc.Check] = field(default_factory=list)
    members: list[MemberRow] = field(default_factory=list)
    nse_note: str = ""


def last_full_day(now: datetime, holidays: frozenset[date] | None = None) -> date:
    """The latest trading day whose session has closed."""
    d = to_ist(now).date()
    if not (is_trading_day(d, holidays) and now >= session_bounds(d)[1]):
        d -= timedelta(days=1)
        while not is_trading_day(d, holidays):
            d -= timedelta(days=1)
    return d


def run_groww(client: Any, targets: dict[str, InstrumentInfo], now: datetime,
              holidays: frozenset[date] | None = None, log=print) -> ProbeResult:
    res = ProbeResult(now)
    day = last_full_day(now, holidays)
    day_end = session_bounds(day)[1]
    for label, inst in targets.items():
        for interval in INTRADAY_INTERVALS:
            res.depth.append(probe_depth(client, inst, label, interval, now))
        res.days.append(probe_day(client, inst, label, day))
        res.quotes[label] = probe_quote(client, inst)
        log(f"  {label} done")
    first = next(iter(targets.values()))
    for interval in INTRADAY_INTERVALS:
        res.windows[interval] = probe_window(client, first, interval, day_end)
    log("  request sizes done")
    return res


def run_nse(res: ProbeResult, http: httpx.Client, instruments: Any,
            holidays: frozenset[date] | None = None, pause: float = sc.PAUSE_SECONDS) -> None:
    checker = sc.Checker(http, pause)
    res.nse_note = checker.warm_up()
    res.lists = [fetch_list(checker, name, urls) for name, urls in INDEX_LISTS.items()]
    today = to_ist(res.now).date()
    for period in DELIVERY_PERIODS:
        res.delivery.append(checker.fetch(DELIVERY, sc.candidate_days(period, today, holidays),
                                          period))
    if instruments is not None:
        res.members = match_members(res.lists, instruments, today)


def _cell(v: int | str) -> str:
    return v if isinstance(v, str) else str(v)


def to_markdown(res: ProbeResult) -> str:
    ages = DEPTH_AGES_DAYS
    lines = ["# Intraday probe (Groww + NSE)", "",
             f"Run on the server at {to_ist(res.now):%Y-%m-%d %H:%M} IST. Counts, times and field "
             "names only, no prices.", "", "## Summary", ""]
    for r in res.depth:
        old = r.oldest_age
        lines.append(f"- **{r.label} {r.interval}**: "
                     + ("no candles at any age" if old is None else
                        f"candles found back to about {old} days ago "
                        f"({to_ist(res.now - timedelta(days=old)):%Y-%m-%d})"))
    lines += ["", "## History depth", "",
              f"Candles in a {DEPTH_WINDOW.days}-day window ending N days ago.", "",
              "| Instrument | Size | " + " | ".join(f"{a}d" for a in ages) + " |",
              "|---|---|" + "---|" * len(ages)]
    for r in res.depth:
        lines.append(f"| {r.label} | {r.interval} | "
                     + " | ".join(_cell(r.counts.get(a, "-")) for a in ages) + " |")
    lens = WINDOW_LENGTHS_DAYS
    lines += ["", "## Longest request", "",
              "Candles from one call covering the last N calendar days (an error = too long).", "",
              "| Size | " + " | ".join(f"{n}d" for n in lens) + " |", "|---|" + "---|" * len(lens)]
    for interval, counts in res.windows.items():
        lines.append(f"| {interval} | " + " | ".join(_cell(counts.get(n, "-")) for n in lens) + " |")
    lines += ["", "## One full day of 5-minute candles", "",
              "A full NSE day should have 75 bars, 09:15 to 15:25.", "",
              "| Instrument | Day | Bars | First | Last | No open | No volume | With OI |",
              "|---|---|---|---|---|---|---|---|"]
    for d in res.days:
        lines.append(f"| {d.label} | {d.day} | {_cell(d.bars)} | {d.first} | {d.last} | "
                     f"{d.no_open} | {d.no_volume} | {d.with_oi} |")
    lines += ["", "## Quote fields (get_quote)", ""]
    for label, keys in res.quotes.items():
        lines.append(f"- **{label}**: " + ", ".join(keys))
    lines += ["", "## NSE files", "", f"{res.nse_note or 'NSE not checked'}.", "",
              "| File | Status | Members | Source / note |", "|---|---|---|---|"]
    for lc in res.lists:
        lines.append(f"| {lc.name} list | {lc.status} | {len(lc.symbols) or '-'} | "
                     f"{lc.note or lc.url} |")
    for c in res.delivery:
        lines.append(f"| Delivery file, {c.period} | {c.status} | "
                     f"{'-' if c.rows is None else str(c.rows) + ' rows'} | "
                     f"{c.file_date or c.note or ', '.join(c.tried)} |")
    cols = [lc for lc in res.lists if lc.columns] + [c for c in res.delivery if c.columns][:1]
    for item in cols:
        lines.append(f"\n- Columns of **{item.name}**: {', '.join(item.columns)}")
    if res.members:
        missing = [m.symbol for m in res.members if not m.in_groww]
        no_fut = [m.symbol for m in res.members if m.in_groww and not m.has_futures]
        lines += ["", "## Index members in Groww", "",
                  f"- {len(res.members)} members, {len(res.members) - len(missing)} found in "
                  f"Groww's NSE list, {sum(m.has_futures for m in res.members)} with futures.",
                  f"- Not in Groww: {', '.join(missing) or 'none'}",
                  f"- No futures (cash only): {', '.join(no_fut) or 'none'}"]
    return "\n".join(lines) + "\n"


def target_instruments(instruments: list[InstrumentInfo], stock: str) -> dict[str, InstrumentInfo]:
    """The labelled instruments to probe: NIFTY index, one stock, and that stock's nearest future."""
    out = {}
    for i in instruments:
        if i.symbol == "NIFTY" and i.instrument_type == "INDEX":
            out["NIFTY index"] = i
        elif i.symbol == stock and i.instrument_type == "STOCK":
            out[f"{stock} stock"] = i
        elif i.symbol == stock and i.instrument_type == "FUTURE" and f"{stock} future" not in out:
            out[f"{stock} future"] = i
    order = [f"{stock} stock", "NIFTY index", f"{stock} future"]
    return {k: out[k] for k in order if k in out}

