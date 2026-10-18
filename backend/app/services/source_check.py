"""Which free outside data sources can our server actually download, and how far back do they go?

Before writing any loader for NSE archive files (F&O bhavcopy, participant OI, ban list, MWPL) or
FRED global series, we check from the server itself: the research found the file names, but
none could be downloaded from the build sandbox, and NSE is reported to block some cloud IPs
(NOT_VERIFIED). For each source and a few sample dates (recent, mid-2024, 2023, 2020, 2015,
2010) this downloads one file, reads only its header row and row count, and throws it away.
Nothing is stored in the database. Requests are spaced out so NSE is not hammered.

Report statuses:
- OK          the file came back and looks like data (CSV or a zip holding a CSV)
- MISSING     404, or no file for any of the tried days in that period
- BLOCKED     403/401, or NSE answered with a web page instead of a file
- ERROR       network error or anything else unexpected
"""

from __future__ import annotations

import csv
import io
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import httpx

from app.core.market_time import is_trading_day, to_ist

NSE_HOME = "https://www.nseindia.com/"
ARCHIVES = "https://nsearchives.nseindia.com"
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": NSE_HOME,
}
PAUSE_SECONDS = 1.0
DAYS_PER_PERIOD = 3  # a holiday or missing day shouldn't make a whole period look missing
PERIODS = ["recent", "2024-07-01", "2023-01-02", "2020-01-01", "2015-01-01", "2010-01-04"]
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def _ddmmyyyy(d: date) -> str:
    return d.strftime("%d%m%Y")


@dataclass(frozen=True)
class Source:
    key: str
    name: str
    metrics: str  # which catalog metrics it would feed
    url: Callable[[date], str]


NSE_SOURCES = [
    Source(
        "fo_bhav_udiff", "F&O bhavcopy (new UDiFF format)",
        "IV per strike, PCR, max pain, strike OI, IV history",
        lambda d: f"{ARCHIVES}/content/fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip",
    ),
    Source(
        "fo_bhav_old", "F&O bhavcopy (old format, before Jul 2024)",
        "Same, needs the underlying close joined in",
        lambda d: (
            f"{ARCHIVES}/content/historical/DERIVATIVES/{d.year}/{MONTHS[d.month - 1]}/"
            f"fo{d.day:02d}{MONTHS[d.month - 1]}{d.year}bhav.csv.zip"
        ),
    ),
    Source(
        "participant_oi", "Participant-wise open interest",
        "FII/DII futures and options positioning",
        lambda d: f"{ARCHIVES}/content/nsccl/fao_participant_oi_{_ddmmyyyy(d)}.csv",
    ),
    Source(
        "participant_vol", "Participant-wise volume",
        "FII/DII trading activity",
        lambda d: f"{ARCHIVES}/content/nsccl/fao_participant_vol_{_ddmmyyyy(d)}.csv",
    ),
    Source(
        "secban", "F&O ban list (daily archive)",
        "F&O Ban List",
        lambda d: f"{ARCHIVES}/archives/fo/sec_ban/fo_secban_{_ddmmyyyy(d)}.csv",
    ),
    Source(
        "mwpl", "Market-wide position limit usage",
        "Ban-risk early warning, OI concentration",
        lambda d: f"{ARCHIVES}/archives/nsccl/mwpl/combineoi_{_ddmmyyyy(d)}.zip",
    ),
]
NSE_LATEST = Source(
    "secban_latest", "F&O ban list (today's file)", "F&O Ban List",
    lambda d: f"{ARCHIVES}/content/fo/fo_secban.csv",
)
FRED_SERIES = {
    "DEXINUS": "USD/INR",
    "DCOILBRENTEU": "Brent crude",
    "DGS10": "US 10-year yield",
    "NASDAQCOM": "Nasdaq Composite",
    "SP500": "S&P 500",
    "NIKKEI225": "Nikkei 225",
    "DTWEXBGS": "US dollar index (Fed broad, not ICE DXY)",
}


@dataclass
class Check:
    source: str
    name: str
    period: str
    status: str
    tried: list[str] = field(default_factory=list)
    file_date: str | None = None
    rows: int | None = None
    columns: list[str] = field(default_factory=list)
    note: str = ""


def read_table(content: bytes) -> tuple[list[str], int]:
    """Header and data-row count of a CSV, or of the first CSV inside a zip."""
    if content[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            if not names:
                raise ValueError("empty zip")
            content = z.read(names[0])
    text = content.decode("utf-8-sig", errors="replace")
    rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if not rows:
        raise ValueError("empty file")
    return [c.strip() for c in rows[0]], len(rows) - 1


def classify(resp: httpx.Response) -> tuple[str, str]:
    """(status, note) for one HTTP answer, before looking inside the file."""
    if resp.status_code == 404:
        return "MISSING", "404"
    if resp.status_code in (401, 403):
        return "BLOCKED", str(resp.status_code)
    if resp.status_code != 200:
        return "ERROR", f"HTTP {resp.status_code}"
    head = resp.content[:200].lstrip().lower()
    if head.startswith(b"<!doctype html") or head.startswith(b"<html"):
        return "BLOCKED", "got a web page, not a file"
    return "OK", ""


def candidate_days(period: str, today: date, holidays: frozenset[date] | None = None) -> list[date]:
    """Up to DAYS_PER_PERIOD trading days: walking back from yesterday, or forward from a date."""
    out: list[date] = []
    if period == "recent":
        d, step = today - timedelta(days=1), -1
    else:
        d, step = date.fromisoformat(period), 1
    while len(out) < DAYS_PER_PERIOD:
        if is_trading_day(d, holidays):
            out.append(d)
        d += timedelta(days=step)
    return out


class Checker:
    def __init__(self, client: httpx.Client, pause: float = PAUSE_SECONDS):
        self.client = client
        self.pause = pause

    def get(self, url: str) -> httpx.Response:
        if self.pause:
            time.sleep(self.pause)
        return self.client.get(url, headers=HEADERS, follow_redirects=True, timeout=30)

    def warm_up(self) -> str:
        """NSE wants a cookie from its home page first. Returns a note for the report."""
        try:
            r = self.get(NSE_HOME)
            return f"NSE home page answered HTTP {r.status_code}"
        except httpx.HTTPError as exc:
            return f"NSE home page failed: {type(exc).__name__}"

    def fetch(self, source: Source, days: list[date], period: str) -> Check:
        chk = Check(source.key, source.name, period, "MISSING")
        for d in days:
            chk.tried.append(d.isoformat())
            try:
                resp = self.get(source.url(d))
            except httpx.HTTPError as exc:
                chk.status, chk.note = "ERROR", type(exc).__name__
                continue
            status, note = classify(resp)
            chk.status, chk.note = status, note
            if status == "BLOCKED":
                return chk  # a block won't change on the next day; stop asking
            if status != "OK":
                continue
            try:
                chk.columns, chk.rows = read_table(resp.content)
            except (ValueError, zipfile.BadZipFile) as exc:
                chk.status, chk.note = "ERROR", f"unreadable file: {exc}"
                continue
            chk.file_date = d.isoformat()
            return chk
        return chk

    def check_nse(self, today: date, holidays: frozenset[date] | None = None) -> list[Check]:
        out = [self.fetch(NSE_LATEST, [today], "latest")]
        out[0].tried = ["today's file"]
        for src in NSE_SOURCES:
            for period in PERIODS:
                chk = self.fetch(src, candidate_days(period, today, holidays), period)
                out.append(chk)
                if chk.status == "BLOCKED":
                    break  # same server, same answer for older dates
        return out

    def check_fred(self) -> list[Check]:
        out = []
        for sid, label in FRED_SERIES.items():
            chk = Check(f"fred_{sid}", f"FRED {sid} ({label})", "all", "MISSING", tried=["full series"])
            try:
                resp = self.get(FRED_CSV.format(sid))
                chk.status, chk.note = classify(resp)
                if chk.status == "OK":
                    chk.columns, chk.rows = read_table(resp.content)
                    first, last = fred_span(resp.content)
                    chk.file_date = f"{first} to {last}"
            except httpx.HTTPError as exc:
                chk.status, chk.note = "ERROR", type(exc).__name__
            except ValueError as exc:
                chk.status, chk.note = "ERROR", f"unreadable file: {exc}"
            out.append(chk)
        return out


def fred_span(content: bytes) -> tuple[str, str]:
    """First and last date in a FRED CSV that has a real value ('.' marks a missing day)."""
    rows = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))[1:]
    dated = [r[0] for r in rows if len(r) > 1 and r[1].strip() not in ("", ".")]
    if not dated:
        raise ValueError("no values")
    return dated[0], dated[-1]


def to_markdown(checks: list[Check], warm_note: str, now: datetime) -> str:
    lines = [
        "# Outside data source check",
        "",
        f"Run on the server at {to_ist(now):%Y-%m-%d %H:%M} IST. {warm_note}.",
        "One file per source and period was downloaded, its header read, then discarded.",
        "",
        "| Source | Period | Status | File date | Rows | Note |",
        "|---|---|---|---|---|---|",
    ]
    for c in checks:
        lines.append(
            f"| {c.name} | {c.period} | {c.status} | {c.file_date or '-'} | "
            f"{'-' if c.rows is None else c.rows} | {c.note or ', '.join(c.tried)} |"
        )
    lines += ["", "## Columns seen (first OK file of each source)", ""]
    seen: set[str] = set()
    for c in checks:
        if c.status == "OK" and c.source not in seen:
            seen.add(c.source)
            lines.append(f"- **{c.name}**: {', '.join(c.columns) or '(none)'}")
    if not seen:
        lines.append("- None: no source returned a readable file.")
    return "\n".join(lines) + "\n"


def run(client: httpx.Client, now: datetime | None = None, pause: float = PAUSE_SECONDS,
        holidays: frozenset[date] | None = None, fred: bool = True) -> tuple[list[Check], str]:
    now = now or datetime.now(UTC)
    checker = Checker(client, pause)
    note = checker.warm_up()
    checks = checker.check_nse(to_ist(now).date(), holidays)
    if fred:
        checks += checker.check_fred()
    return checks, note
