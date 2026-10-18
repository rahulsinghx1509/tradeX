"""Outside data source check: classifies answers correctly without touching the internet."""

import io
import zipfile
from datetime import UTC, date, datetime

import httpx

from app.services import source_check as sc

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)  # a Sunday
NO_HOLIDAYS: frozenset[date] = frozenset()
CSV = b"TckrSymb,StrkPric,OpnIntrst\nNIFTY,25000,100\nNIFTY,25100,50\n"


def zipped(data: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("bhav.csv", data)
    return buf.getvalue()


def checker(handler) -> sc.Checker:
    return sc.Checker(httpx.Client(transport=httpx.MockTransport(handler)), pause=0)


def test_read_table_handles_csv_and_zip():
    assert sc.read_table(CSV) == (["TckrSymb", "StrkPric", "OpnIntrst"], 2)
    assert sc.read_table(zipped(CSV)) == (["TckrSymb", "StrkPric", "OpnIntrst"], 2)


def test_classify_spots_blocks_and_web_pages():
    req = httpx.Request("GET", "https://x")
    assert sc.classify(httpx.Response(404, request=req))[0] == "MISSING"
    assert sc.classify(httpx.Response(403, request=req))[0] == "BLOCKED"
    assert sc.classify(httpx.Response(200, content=b"<!DOCTYPE html><p>", request=req))[0] == "BLOCKED"
    assert sc.classify(httpx.Response(200, content=CSV, request=req))[0] == "OK"


def test_candidate_days_skip_weekends():
    assert sc.candidate_days("recent", date(2026, 10, 18), NO_HOLIDAYS) == [
        date(2026, 10, 16), date(2026, 10, 15), date(2026, 10, 14)
    ]
    assert sc.candidate_days("2020-01-04", date(2026, 10, 18), NO_HOLIDAYS)[0] == date(2020, 1, 6)


def test_fetch_falls_back_to_next_day_on_404():
    src = sc.NSE_SOURCES[0]
    hit = src.url(date(2026, 10, 15))
    days = sc.candidate_days("recent", date(2026, 10, 18), NO_HOLIDAYS)

    def handler(r):
        return httpx.Response(200, content=zipped(CSV)) if str(r.url) == hit else httpx.Response(404)

    chk = checker(handler).fetch(src, days, "recent")
    assert (chk.status, chk.file_date, chk.rows) == ("OK", "2026-10-15", 2)
    assert chk.tried == ["2026-10-16", "2026-10-15"]


def test_block_stops_early_and_report_says_so():
    calls = []

    def handler(r):
        calls.append(str(r.url))
        return httpx.Response(403)

    checks, note = sc.run(httpx.Client(transport=httpx.MockTransport(handler)), NOW, pause=0,
                          holidays=NO_HOLIDAYS, fred=False)
    assert all(c.status == "BLOCKED" for c in checks)
    # home page + latest ban file + one request per source (no retries after a block)
    assert len(calls) == 2 + len(sc.NSE_SOURCES)
    md = sc.to_markdown(checks, note, NOW)
    assert "BLOCKED" in md and "None: no source returned a readable file." in md


def test_fred_span_ignores_missing_days():
    data = b"observation_date,DEXINUS\n1973-01-01,.\n1973-01-02,8.02\n2026-10-09,88.1\n2026-10-12,.\n"
    assert sc.fred_span(data) == ("1973-01-02", "2026-10-09")


def test_fred_check_reports_span():
    body = b"observation_date,X\n2000-01-03,1.0\n2026-10-10,2.0\n"
    checks = checker(lambda r: httpx.Response(200, content=body)).check_fred()
    assert len(checks) == len(sc.FRED_SERIES)
    assert checks[0].status == "OK" and checks[0].file_date == "2000-01-03 to 2026-10-10"
