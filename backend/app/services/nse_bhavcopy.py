"""NSE's free daily F&O file (the "bhavcopy"): download, keep our underlyings, store in fo_daily.

The file lists every futures and options contract traded on NSE that day, with OHLC, settle
price, open interest (OI), change in OI and volume. From it we can rebuild option-chain history
(IV, PCR, max pain, OI by strike) for past years, which Groww does not keep.

Two formats exist (both confirmed downloadable from our server on 2026-10-18, `tradex.sh sources`):
- new "UDiFF" file: also has the underlying price and lot size;
- old file (exists 2010 to July 2024): no underlying price, so the nearest future's close
  is used to pick strikes. The likelier format for the date is tried first, then the other.

What is kept, per underlying and day (the full file has ~50-90k rows; storing all would fill
the small server):
- every future;
- options of the nearest OPTION_EXPIRIES expiries plus the nearest FUTURE_EXPIRIES monthly
  expiries (the ones that also have a future), with strike within STRIKE_BAND of the
  underlying price, and only if the contract traded or had OI that day (a strike with neither
  has a stale close and no information).
Missing values stay NULL. Volume/OI units (contracts or shares) are NOT_VERIFIED per format.
"""

from __future__ import annotations

import csv
import io
import time
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.market_time import trading_days
from app.db.models import FoDaily
from app.services.source_check import ARCHIVES, HEADERS, MONTHS, NSE_HOME

OPTION_EXPIRIES = 2
FUTURE_EXPIRIES = 2
STRIKE_BAND = 0.15
PAUSE_SECONDS = 1.0
UDIFF_FROM = date(2024, 7, 1)  # server check 2026-10-18: UDiFF 404 on 2023-01-02, old file 404 now


def udiff_url(d: date) -> str:
    return f"{ARCHIVES}/content/fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"


def old_url(d: date) -> str:
    mon = MONTHS[d.month - 1]
    return f"{ARCHIVES}/content/historical/DERIVATIVES/{d.year}/{mon}/fo{d.day:02d}{mon}{d.year}bhav.csv.zip"


@dataclass
class Row:
    symbol: str
    instrument: str  # FUT / CE / PE
    expiry_date: date
    strike: float | None
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    settle: float | None
    underlying: float | None
    open_interest: int | None
    oi_change: int | None
    volume: int | None
    lot_size: int | None


def _num(v: str | None) -> float | None:
    v = (v or "").strip()
    if not v or v in ("-", "NA"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _int(v: str | None) -> int | None:
    x = _num(v)
    return None if x is None else int(round(x))


def unzip(content: bytes) -> str:
    if content[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            content = z.read(next(n for n in z.namelist() if not n.endswith("/")))
    return content.decode("utf-8-sig", errors="replace")


def parse_udiff(text: str, symbols: set[str]) -> list[Row]:
    kinds = {"IDF": "FUT", "STF": "FUT", "IDO": "OPT", "STO": "OPT"}
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        r = {k.strip(): v for k, v in r.items() if k}
        sym, kind = (r.get("TckrSymb") or "").strip().upper(), kinds.get((r.get("FinInstrmTp") or "").strip())
        if sym not in symbols or kind is None:
            continue
        inst = "FUT" if kind == "FUT" else (r.get("OptnTp") or "").strip().upper()
        if inst not in ("FUT", "CE", "PE"):
            continue
        out.append(Row(
            sym, inst, date.fromisoformat(r["XpryDt"].strip()),
            None if inst == "FUT" else _num(r.get("StrkPric")),
            _num(r.get("OpnPric")), _num(r.get("HghPric")), _num(r.get("LwPric")),
            _num(r.get("ClsPric")), _num(r.get("SttlmPric")), _num(r.get("UndrlygPric")),
            _int(r.get("OpnIntrst")), _int(r.get("ChngInOpnIntrst")), _int(r.get("TtlTradgVol")),
            _int(r.get("NewBrdLotQty")),
        ))
    return out


def parse_old(text: str, symbols: set[str]) -> list[Row]:
    kinds = {"FUTIDX": "FUT", "FUTSTK": "FUT", "OPTIDX": "OPT", "OPTSTK": "OPT"}
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        r = {k.strip(): v for k, v in r.items() if k}
        sym, kind = (r.get("SYMBOL") or "").strip().upper(), kinds.get((r.get("INSTRUMENT") or "").strip())
        if sym not in symbols or kind is None:
            continue
        inst = "FUT" if kind == "FUT" else (r.get("OPTION_TYP") or "").strip().upper()
        if inst not in ("FUT", "CE", "PE"):
            continue
        out.append(Row(
            sym, inst, datetime.strptime(r["EXPIRY_DT"].strip(), "%d-%b-%Y").date(),
            None if inst == "FUT" else _num(r.get("STRIKE_PR")),
            _num(r.get("OPEN")), _num(r.get("HIGH")), _num(r.get("LOW")),
            _num(r.get("CLOSE")), _num(r.get("SETTLE_PR")), None,
            _int(r.get("OPEN_INT")), _int(r.get("CHG_IN_OI")), _int(r.get("CONTRACTS")), None,
        ))
    return out


def select_rows(rows: Iterable[Row], trade_date: date) -> list[Row]:
    """Apply the 'what is kept' rules from the module docstring."""
    by_sym: dict[str, list[Row]] = {}
    for r in rows:
        if r.expiry_date >= trade_date:
            by_sym.setdefault(r.symbol, []).append(r)
    kept: list[Row] = []
    for rs in by_sym.values():
        futs = sorted((r for r in rs if r.instrument == "FUT"), key=lambda r: r.expiry_date)
        kept += futs
        opts = [r for r in rs if r.instrument != "FUT"]
        expiries = sorted({r.expiry_date for r in opts})[:OPTION_EXPIRIES]
        expiries += [f.expiry_date for f in futs[:FUTURE_EXPIRIES]]
        ref = next((r.underlying for r in rs if r.underlying), None)
        if ref is None:
            ref = next((f.close or f.settle for f in futs if f.close or f.settle), None)
        for r in opts:
            if r.expiry_date not in expiries or r.strike is None:
                continue
            if not (r.volume or r.open_interest):
                continue
            if ref is not None and abs(r.strike / ref - 1) > STRIKE_BAND:
                continue
            kept.append(r)
    return kept


@dataclass
class DayResult:
    trade_date: date
    status: str  # LOADED / NO_FILE / SKIPPED / FAILED
    file_format: str | None = None
    rows: int = 0
    error: str | None = None


class Downloader:
    def __init__(self, client: httpx.Client, pause: float = PAUSE_SECONDS):
        self.client, self.pause, self.warmed = client, pause, False

    def get(self, url: str) -> httpx.Response | None:
        """The file, or None if NSE has no file there (404). Raises on blocks and errors."""
        if not self.warmed:
            try:
                self.client.get(NSE_HOME, headers=HEADERS, timeout=30)
            except httpx.HTTPError:
                pass  # the archive often works without the cookie
            self.warmed = True
        if self.pause:
            time.sleep(self.pause)
        r = self.client.get(url, headers=HEADERS, follow_redirects=True, timeout=60)
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise RuntimeError(f"NSE answered HTTP {r.status_code}")
        if r.content[:200].lstrip().lower().startswith((b"<!doctype html", b"<html")):
            raise RuntimeError("NSE answered with a web page instead of the file")
        return r


def fetch_day(dl: Downloader, d: date, symbols: set[str]) -> tuple[str, list[Row]] | None:
    """('udiff' or 'old', kept rows) for a day, or None when neither file exists."""
    order = [("udiff", udiff_url(d), parse_udiff), ("old", old_url(d), parse_old)]
    if d < UDIFF_FROM:
        order.reverse()  # saves one 404 per day on old dates
    for fmt, url, parse in order:
        resp = dl.get(url)
        if resp is not None:
            return fmt, select_rows(parse(unzip(resp.content), symbols), d)
    return None


def store_day(session: Session, d: date, fmt: str, rows: list[Row]) -> None:
    """Replace the day's rows in one transaction, so a re-run never duplicates."""
    session.execute(delete(FoDaily).where(FoDaily.trade_date == d))
    session.add_all(FoDaily(trade_date=d, file_format=fmt, **r.__dict__) for r in rows)
    session.commit()


def loaded_days(session: Session) -> set[date]:
    return set(session.scalars(select(FoDaily.trade_date).distinct()))


def load(session: Session, client: httpx.Client, symbols: list[str], start: date, end: date,
         on_day: Callable[[DayResult], None] | None = None, pause: float | None = None,
         holidays: frozenset[date] | None = None) -> list[DayResult]:
    """Load every trading day in [start, end] not yet in fo_daily. Safe to stop and re-run."""
    dl = Downloader(client, PAUSE_SECONDS if pause is None else pause)
    wanted, done = {s.upper() for s in symbols}, loaded_days(session)
    results = []
    for d in trading_days(start, end, holidays):
        if d in done:
            res = DayResult(d, "SKIPPED")
        else:
            try:
                got = fetch_day(dl, d, wanted)
                if got is None:
                    res = DayResult(d, "NO_FILE")
                elif not got[1]:
                    res = DayResult(d, "FAILED", got[0], error="file had none of our underlyings")
                else:
                    store_day(session, d, *got)
                    res = DayResult(d, "LOADED", got[0], len(got[1]))
            except Exception as exc:  # noqa: BLE001 - one bad day must not stop the loader
                session.rollback()
                res = DayResult(d, "FAILED", error=f"{type(exc).__name__}: {exc}"[:200])
        results.append(res)
        if on_day:
            on_day(res)
    return results


def latest_day(session: Session) -> date | None:
    return session.scalar(select(func.max(FoDaily.trade_date)))
