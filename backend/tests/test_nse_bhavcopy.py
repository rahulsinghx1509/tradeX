"""NSE daily F&O file: parsing both formats, what is kept, loading and the collector step."""

import io
import zipfile
from datetime import UTC, date, datetime

import httpx
from sqlalchemy import func, select

from app.db.models import FoDaily
from app.services import nse_bhavcopy as nb
from app.services.collector import CollectorState, run_cycle

D = date(2026, 10, 16)
NO_HOLIDAYS: frozenset[date] = frozenset()
UDIFF_HEAD = ("TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,"
              "FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,"
              "LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,"
              "TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4")


def udiff_line(tp, sym, expiry, strike="", opt="", close="100", under="25000", oi="10", vol="5"):
    return (f"2026-10-16,2026-10-16,FO,NSE,{tp},1,,{sym},,{expiry},{expiry},{strike},{opt},x,"
            f"99,101,98,{close},{close},97,{under},{close},{oi},2,{vol},1,1,F1,75,,,,,")


def zipped(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("f.csv", text)
    return buf.getvalue()


UDIFF = "\n".join([
    UDIFF_HEAD,
    udiff_line("IDF", "NIFTY", "2026-10-27", close="25050"),
    udiff_line("IDF", "NIFTY", "2026-11-24", close="25150"),
    udiff_line("IDO", "NIFTY", "2026-10-20", "25000", "CE"),          # nearest weekly: kept
    udiff_line("IDO", "NIFTY", "2026-10-20", "25000", "PE"),          # kept
    udiff_line("IDO", "NIFTY", "2026-10-20", "40000", "CE"),          # far from the money: dropped
    udiff_line("IDO", "NIFTY", "2026-10-20", "25100", "CE", oi="0", vol="0"),  # dead strike: dropped
    udiff_line("IDO", "NIFTY", "2026-11-03", "25000", "CE"),          # 3rd expiry, not monthly: dropped
    udiff_line("IDO", "NIFTY", "2026-11-24", "25000", "CE"),          # monthly with a future: kept
    udiff_line("IDO", "NIFTY", "2026-10-23", "25000", "CE"),          # 2nd expiry: kept
    udiff_line("STO", "SBIN", "2026-10-27", "800", "CE", under="800"),  # not our underlying
]) + "\n"

OLD = (
    "INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,CONTRACTS,"
    "VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP,\n"
    "FUTIDX,NIFTY,30-Jan-2020,0,XX,12200,12300,12150,12250,12250,1000,1,500,10,02-JAN-2020,\n"
    "OPTIDX,NIFTY,09-Jan-2020,12200,CE,90,110,80,100,100,50,1,300,5,02-JAN-2020,\n"
    "OPTIDX,NIFTY,09-Jan-2020,20000,CE,1,1,1,1,1,1,1,1,0,02-JAN-2020,\n"
    "OPTIDX,NIFTY,30-Jan-2020,12000,PE,-,-,-,-,40,0,0,0,0,02-JAN-2020,\n"
)


def test_parse_udiff_keeps_the_right_rows():
    rows = nb.select_rows(nb.parse_udiff(UDIFF, {"NIFTY"}), D)
    got = sorted((r.instrument, str(r.expiry_date), r.strike) for r in rows)
    assert got == [
        ("CE", "2026-10-20", 25000.0), ("CE", "2026-10-23", 25000.0),
        ("CE", "2026-11-24", 25000.0), ("FUT", "2026-10-27", None),
        ("FUT", "2026-11-24", None), ("PE", "2026-10-20", 25000.0),
    ]
    ce = next(r for r in rows if r.instrument == "CE")
    assert (ce.underlying, ce.lot_size, ce.open_interest, ce.volume) == (25000.0, 75, 10, 5)


def test_parse_old_uses_future_close_and_keeps_missing_as_none():
    rows = nb.select_rows(nb.parse_old(OLD, {"NIFTY"}), date(2020, 1, 2))
    kept = sorted((r.instrument, r.strike) for r in rows)
    assert kept == [("CE", 12200.0), ("FUT", None)]  # 20000 too far; dead 12000 PE dropped
    fut = next(r for r in rows if r.instrument == "FUT")
    assert (fut.close, fut.underlying, fut.lot_size, fut.volume) == (12250.0, None, None, 1000)
    assert nb.parse_old(OLD, {"NIFTY"})[-1].close is None  # "-" stays missing, never zero


def client_for(files: dict[str, bytes], calls: list[str] | None = None) -> httpx.Client:
    def handler(r):
        if calls is not None:
            calls.append(str(r.url))
        body = files.get(str(r.url))
        return httpx.Response(200, content=body) if body else httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_load_falls_back_to_old_file_and_skips_loaded_days(session):
    d1, d2 = date(2020, 1, 2), date(2020, 1, 3)
    files = {nb.old_url(d1): zipped(OLD)}
    res = nb.load(session, client_for(files), ["NIFTY"], d1, d2, pause=0, holidays=NO_HOLIDAYS)
    assert [(r.status, r.file_format) for r in res] == [("LOADED", "old"), ("NO_FILE", None)]
    assert session.scalar(select(func.count()).select_from(FoDaily)) == 2

    calls: list[str] = []
    res = nb.load(session, client_for(files, calls), ["NIFTY"], d1, d1, pause=0, holidays=NO_HOLIDAYS)
    assert res[0].status == "SKIPPED" and not [c for c in calls if "DERIVATIVES" in c]


def test_blocked_day_is_failed_not_stored(session):
    blocked = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403)))
    res = nb.load(session, blocked, ["NIFTY"], D, D, pause=0, holidays=NO_HOLIDAYS)
    assert res[0].status == "FAILED" and "403" in res[0].error
    assert session.scalar(select(func.count()).select_from(FoDaily)) == 0


class LiveProvider:
    name = "groww"


class Notes:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)


def test_collector_loads_the_evening_file(session, monkeypatch):
    monkeypatch.setattr(nb, "PAUSE_SECONDS", 0)
    files = {nb.udiff_url(D): zipped(UDIFF)}
    state = CollectorState(http_client=lambda: client_for(files))
    notes = Notes()
    evening = datetime(2026, 10, 16, 14, 0, tzinfo=UTC)  # 19:30 IST, a Friday
    state.eod_done_for = "2026-10-16"
    assert run_cycle(session, LiveProvider(), notes, state, evening) == "fo_file"
    assert state.fo_done_for == "2026-10-16" and not notes.sent
    assert nb.latest_day(session) == D
    assert run_cycle(session, LiveProvider(), notes, state, evening) == "idle"


def test_collector_waits_then_gives_up(session, monkeypatch):
    monkeypatch.setattr(nb, "PAUSE_SECONDS", 0)
    state = CollectorState(http_client=lambda: client_for({}), eod_done_for="2026-10-16")
    notes = Notes()
    assert run_cycle(session, LiveProvider(), notes, state,
                     datetime(2026, 10, 16, 14, 0, tzinfo=UTC)) == "fo_file_waiting"
    assert state.fo_done_for is None and not notes.sent
    assert run_cycle(session, LiveProvider(), notes, state,
                     datetime(2026, 10, 16, 17, 45, tzinfo=UTC)) == "fo_file"  # 23:15 IST
    assert "not stored" in notes.sent[0]
