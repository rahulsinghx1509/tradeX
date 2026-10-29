
import pytest
from sqlalchemy.orm import sessionmaker

from app.core.market_time import utc_now
from app.providers.sample import SampleProvider
from app.services.ingestion import backfill, sync_instruments


@pytest.fixture
def loaded(engine):
    # The API reads the real clock, so the sample contracts must too: with the fixed test date,
    # its October expiry counts as expired once the real date passes it.
    provider = SampleProvider()
    with sessionmaker(bind=engine, expire_on_commit=False)() as s:
        sync_instruments(s, provider, ["NIFTY", "BANKNIFTY"], 2)
        backfill(s, provider, "1d", 30, now=utc_now())
        backfill(s, provider, "1h", 5, now=utc_now())


def test_health(client):
    body = client.get("/health").json()
    assert body["database"] == "ok" and "DISABLED" in body["order_execution"]


def test_instruments_list_and_filter(client, loaded):
    all_rows = client.get("/api/instruments").json()
    assert len(all_rows) == 6
    futs = client.get("/api/instruments", params={"symbol": "nifty", "instrument_type": "FUTURE"}).json()
    assert len(futs) == 2 and all(f["expiry_date"] for f in futs)


def test_candles_endpoint(client, loaded):
    inst = client.get("/api/instruments", params={"instrument_type": "INDEX"}).json()[0]
    body = client.get("/api/candles", params={"instrument_id": inst["id"], "timeframe": "1h",
                                              "limit": 10}).json()
    assert body["is_sample"] is True and body["count"] == 10
    times = [c["time"] for c in body["candles"]]
    assert times == sorted(times)  # oldest first, ready for charting
    assert body["candles"][0]["volume"] is None  # index: no volume, not zero


def test_candles_validation(client, loaded):
    assert client.get("/api/candles", params={"instrument_id": 999}).status_code == 404
    assert client.get("/api/candles", params={"instrument_id": 1, "timeframe": "7m"}).status_code == 422
    assert client.get("/api/candles", params={"instrument_id": 1, "limit": 999999}).status_code == 422


def test_overview(client, loaded):
    body = client.get("/api/market/overview").json()
    assert body["contains_sample_data"] is True
    assert body["signal"]["status"] == "NOT_BUILT"
    nifty = body["underlyings"][0]
    assert nifty["symbol"] == "NIFTY" and nifty["index"]["last_close"] is not None
    assert nifty["index"]["freshness"]["1d"]["status"] in {"FRESH", "STALE"}
    assert nifty["index"]["change_reference_close"] is not None
    assert nifty["futures"] == []  # F&O view switched off on the dashboard (29 Oct)
    assert body["hourly_version"] and nifty["hourly"]["status"] in {"OK", "INSUFFICIENT_HISTORY"}
    assert set(nifty["hourly"]["readings"]) == {"H1_hourly_trend", "H2_hourly_momentum",
                                                "H3_hourly_pullback"}


def test_data_quality_endpoint(client, loaded):
    body = client.get("/api/data-quality", params={"limit": 20}).json()
    assert body["runs"] and body["checks"]
    assert {c["status"] for c in body["checks"]} <= {"PASS", "WARN", "FAIL"}


def test_no_write_endpoints_exist(client):
    from app.main import app

    methods = {m for r in app.routes for m in getattr(r, "methods", set())}
    assert methods <= {"GET", "HEAD"}


def test_empty_database_overview(client):
    body = client.get("/api/market/overview").json()
    assert body["underlyings"][0]["index"] is None




def test_other_provider_rows_are_hidden(client, engine, loaded):
    from app.db.models import Instrument

    with sessionmaker(bind=engine)() as s:
        s.add(Instrument(symbol="NIFTY", instrument_type="INDEX", exchange="NSE", segment="CASH",
                         provider="groww", provider_symbol="NSE-NIFTY", is_sample=False))
        s.commit()
    assert all(i["provider"] == "sample" for i in client.get("/api/instruments").json())
    assert client.get("/api/market/overview").json()["underlyings"][0]["index"]["provider"] == "sample"


def test_live_quote_on_cards(engine, client, loaded):
    from datetime import UTC, datetime, timedelta

    from app.api.routes.market import _live_quote
    from app.db.models import Instrument, MarketSnapshot

    now = datetime(2026, 10, 19, 6, 0, tzinfo=UTC)  # 11:30 IST
    with sessionmaker(bind=engine, expire_on_commit=False)() as s:
        inst = s.query(Instrument).filter_by(instrument_type="INDEX").first()
        assert _live_quote(s, inst.id, now)["live_status"] == "NO_DATA"

        def add(minutes_ago, price):
            t = now - timedelta(minutes=minutes_ago)
            s.add(MarketSnapshot(instrument_id=inst.id, observed_at=t, received_at=t,
                                 last_price=price, source="sample", is_sample=True))
            s.commit()

        add(60 * 20, 100.0)  # yesterday: never shown as today's price
        assert _live_quote(s, inst.id, now)["live_status"] == "NO_DATA"
        add(40, 101.0)
        assert _live_quote(s, inst.id, now) | {"live_time": None} == {
            "live_price": 101.0, "live_time": None, "live_status": "OLD"}
        add(3, 102.5)
        assert _live_quote(s, inst.id, now)["live_price"] == 102.5
        assert _live_quote(s, inst.id, now)["live_status"] == "LIVE"
    assert "live_status" in client.get("/api/market/overview").json()["underlyings"][0]["index"]


def test_intraday_levels_endpoint(client, loaded):
    inst = client.get("/api/instruments", params={"symbol": "NIFTY", "instrument_type": "INDEX"}).json()[0]
    body = client.get("/api/intraday", params={"instrument_id": inst["id"], "timeframe": "1h"}).json()
    assert body["session"] and len(body["bars"]) > 0
    last = body["bars"][-1]
    assert last["vwap"] is None  # an index has no volume, so no VWAP (missing, not zero)
    assert last["or_high"] is None  # no opening range on 1h bars
    lv = body["levels"]
    assert lv["prev_high"] >= lv["prev_low"] and lv["cpr_top"] >= lv["cpr_bottom"]
    empty = client.get("/api/intraday", params={"instrument_id": inst["id"], "timeframe": "5m"}).json()
    assert empty["bars"] == [] and empty["levels"] is None
    assert client.get("/api/intraday", params={"instrument_id": 999}).status_code == 404
    assert client.get("/api/intraday", params={"instrument_id": inst["id"],
                                               "timeframe": "1d"}).status_code == 422


def test_intraday_vwap_where_there_is_volume(client, loaded):
    fut = client.get("/api/instruments", params={"symbol": "NIFTY", "instrument_type": "FUTURE"}).json()[0]
    body = client.get("/api/intraday", params={"instrument_id": fut["id"], "timeframe": "1h"}).json()
    today = [b for b in body["bars"] if b["vwap"] is not None]
    assert today and all(b["vwap"] > 0 for b in today)
