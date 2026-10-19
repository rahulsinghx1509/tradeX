"""NSE's free daily participant-wise open interest file: who holds the market's F&O positions.

Each trading day NSE publishes, for four kinds of trader, how many contracts they hold long
and short in index futures, stock futures, index options and stock options:
- Client: retail and other non-institutional accounts
- DII: domestic institutions (mutual funds, insurers)
- FII: foreign institutions
- Pro: brokers trading their own money
plus a TOTAL row. Downloadable from our server back to 2015 (`tradex.sh sources`, 2026-10-18).

The file's column names are matched by meaning (see COLUMNS), not by position. If a needed
column is missing the day fails with the header it found, so a format change is visible.
Column names come from NSE's published file layout and are NOT_VERIFIED until the first
real load on the server (`tradex.sh participant-history` prints the header of the first day).

From the stored rows, `features` builds market-wide readings for the screen (PS-1.0). They use
only shares (long / (long + short)), so lot-size changes and weekly-expiry changes, which move
contract counts, do not move the readings.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Callable
from datetime import date

import httpx
import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.market_time import trading_days
from app.db.models import ParticipantOi
from app.services import nse_bhavcopy as nb
from app.services.nse_bhavcopy import DayResult, Downloader, unzip
from app.services.source_check import ARCHIVES

CLIENT_TYPES = ("Client", "DII", "FII", "Pro", "TOTAL")
# normalised header text -> column in participant_oi
COLUMNS = {
    "futureindexlong": "fut_idx_long", "futureindexshort": "fut_idx_short",
    "futurestocklong": "fut_stk_long", "futurestockshort": "fut_stk_short",
    "optionindexcalllong": "opt_idx_call_long", "optionindexputlong": "opt_idx_put_long",
    "optionindexcallshort": "opt_idx_call_short", "optionindexputshort": "opt_idx_put_short",
    "optionstockcalllong": "opt_stk_call_long", "optionstockputlong": "opt_stk_put_long",
    "optionstockcallshort": "opt_stk_call_short", "optionstockputshort": "opt_stk_put_short",
    "totallongcontracts": "total_long", "totalshortcontracts": "total_short",
}
REQUIRED = ("fut_idx_long", "fut_idx_short", "opt_idx_call_long", "opt_idx_put_long",
            "opt_idx_call_short", "opt_idx_put_short")


def url(d: date) -> str:
    return f"{ARCHIVES}/content/nsccl/fao_participant_oi_{d:%d%m%Y}.csv"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z]", "", s.lower())


def _int(v: str | None) -> int | None:
    v = (v or "").replace(",", "").strip()
    if not v or v in ("-", "NA"):
        return None
    try:
        return int(round(float(v)))
    except ValueError:
        return None


def parse(text: str) -> list[dict]:
    """Rows of the file as dicts of participant_oi columns. Raises ValueError on a format change."""
    rows = list(csv.reader(io.StringIO(text)))
    head_at = next((i for i, r in enumerate(rows) if r and _norm(r[0]) == "clienttype"), None)
    if head_at is None:
        first = ",".join(rows[0] if rows else [])[:120]
        raise ValueError(f"no 'Client Type' header row; first line: {first}")
    header = [COLUMNS.get(_norm(h)) for h in rows[head_at]]
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        raise ValueError(f"columns missing {missing}; header: {','.join(rows[head_at])[:200]}")
    known = {_norm(t): t for t in CLIENT_TYPES}
    out = []
    for r in rows[head_at + 1:]:
        if not r or _norm(r[0]) not in known:
            continue
        rec = {"client_type": known[_norm(r[0])]}
        for col, v in zip(header, r, strict=False):
            if col:
                rec[col] = _int(v)
        out.append(rec)
    if not any(r["client_type"] == "FII" for r in out):
        raise ValueError("file has no FII row")
    return out


def store_day(session: Session, d: date, rows: list[dict]) -> None:
    """Replace the day's rows in one transaction, so a re-run never duplicates."""
    session.execute(delete(ParticipantOi).where(ParticipantOi.trade_date == d))
    session.add_all(ParticipantOi(trade_date=d, **r) for r in rows)
    session.commit()


def loaded_days(session: Session) -> set[date]:
    return set(session.scalars(select(ParticipantOi.trade_date).distinct()))


def latest_day(session: Session) -> date | None:
    return session.scalar(select(func.max(ParticipantOi.trade_date)))


def load(session: Session, client: httpx.Client, start: date, end: date,
         on_day: Callable[[DayResult], None] | None = None, pause: float | None = None,
         holidays: frozenset[date] | None = None) -> list[DayResult]:
    """Load every trading day in [start, end] not yet stored. Safe to stop and re-run."""
    dl = Downloader(client, nb.PAUSE_SECONDS if pause is None else pause)
    done, results = loaded_days(session), []
    for d in trading_days(start, end, holidays):
        if d in done:
            res = DayResult(d, "SKIPPED")
        else:
            try:
                resp = dl.get(url(d))
                if resp is None:
                    res = DayResult(d, "NO_FILE")
                else:
                    rows = parse(unzip(resp.content))
                    store_day(session, d, rows)
                    res = DayResult(d, "LOADED", "csv", len(rows))
            except Exception as exc:  # noqa: BLE001 - one bad day must not stop the loader
                session.rollback()
                res = DayResult(d, "FAILED", error=f"{type(exc).__name__}: {exc}"[:300])
        results.append(res)
        if on_day:
            on_day(res)
    return results


# --------------------------------------------------------------------------- readings
FEATURES = {
    "fii_fut_long_share": "FII index futures: long / (long + short)",
    "fii_fut_long_share_chg_5d": "change in the FII long share over 5 days",
    "client_fut_long_share": "Client (retail) index futures: long / (long + short)",
    "pro_fut_long_share": "Pro index futures: long / (long + short)",
    "fii_opt_bias": "FII index options: (calls long − short − puts long + short) / all four",
    "client_opt_bias": "Client index options: same as FII's",
}


def load_frame(session: Session, start: date | None = None, end: date | None = None) -> pd.DataFrame:
    q = select(ParticipantOi)
    if start:
        q = q.where(ParticipantOi.trade_date >= start)
    if end:
        q = q.where(ParticipantOi.trade_date <= end)
    rows = session.scalars(q).all()
    cols = ["trade_date", "client_type", *COLUMNS.values()]
    return pd.DataFrame([{c: getattr(r, c) for c in cols} for r in rows], columns=cols)


def _share(long: pd.Series, short: pd.Series) -> pd.Series:
    tot = long + short
    return (long / tot).where(tot > 0)


def _opt_bias(p: pd.DataFrame) -> pd.Series:
    cl, cs, pl, ps = (p["opt_idx_call_long"], p["opt_idx_call_short"],
                      p["opt_idx_put_long"], p["opt_idx_put_short"])
    tot = cl + cs + pl + ps
    return ((cl - cs) - (pl - ps)) / tot.where(tot > 0)


def features(df: pd.DataFrame) -> pd.DataFrame:
    """One row per day with the FEATURES readings (missing stays missing)."""
    if df.empty:
        return pd.DataFrame(columns=list(FEATURES))
    d = df.copy()
    d["trade_date"] = pd.to_datetime(d["trade_date"])
    num = list(COLUMNS.values())
    d[num] = d[num].astype(float)
    by = {k: g.set_index("trade_date").sort_index() for k, g in d.groupby("client_type")}
    empty = pd.DataFrame(columns=num, dtype=float)
    fii, client, pro = (by.get(k, empty) for k in ("FII", "Client", "Pro"))
    out = pd.DataFrame(index=sorted(d["trade_date"].unique()))
    out["fii_fut_long_share"] = _share(fii["fut_idx_long"], fii["fut_idx_short"])
    out["fii_fut_long_share_chg_5d"] = out["fii_fut_long_share"] - out["fii_fut_long_share"].shift(5)
    out["client_fut_long_share"] = _share(client["fut_idx_long"], client["fut_idx_short"])
    out["pro_fut_long_share"] = _share(pro["fut_idx_long"], pro["fut_idx_short"])
    out["fii_opt_bias"] = _opt_bias(fii)
    out["client_opt_bias"] = _opt_bias(client)
    return out[list(FEATURES)]
