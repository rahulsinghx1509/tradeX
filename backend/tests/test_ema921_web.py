"""9/21 EMA Flask trade viewer: read-only routes, filters, trade detail, empty and error states."""

from datetime import date

import pytest
from sqlalchemy.orm import sessionmaker

from app.ema921 import loader as L
from app.ema921 import web
from tests.test_ema921_loader import END, START, FakeGroww


@pytest.fixture
def factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def loaded(factory):
    with factory() as s:
        L.load_all(s, FakeGroww(), START, END, log=lambda _: None)
    return factory


def _client(factory):
    return web.create_app(web.Backtests(factory, START, END)).test_client()


def test_only_get_routes(factory):
    app = web.create_app(web.Backtests(factory, START, END))
    for rule in app.url_map.iter_rules():
        assert rule.methods - {"GET", "HEAD", "OPTIONS"} == set(), rule


def test_page_and_chart_library_are_served(factory):
    c = _client(factory)
    page = c.get("/")
    assert page.status_code == 200 and b"9/21 EMA" in page.data
    lib = c.get("/lightweight-charts.js")
    assert lib.status_code == 200 and b"Lightweight Charts" in lib.data
    lib.close()
    page.close()


def test_empty_state_before_any_data(factory):
    c = _client(factory)
    assert c.get("/api/meta").get_json()["has_data"] is False
    body = c.get("/api/trades").get_json()
    assert body["has_data"] is False and body["trades"] == []


def test_trades_kpis_and_mode_comparison(loaded):
    c = _client(loaded)
    body = c.get("/api/trades?tf=5&mode=ATM_SELL&start=09:30&cutoff=14:30").get_json()
    assert body["has_data"] and body["trades"]
    assert {m["mode"] for m in body["modes"]} == {"ITM_BUY", "ATM_BUY", "ATM_SELL"}
    assert body["summary"]["trades"] == len(body["trades"])
    assert all(t["side"] == "SELL" for t in body["trades"])
    wins = c.get("/api/trades?tf=5&mode=ATM_SELL&outcome=win").get_json()["trades"]
    assert all(t["net_pnl"] > 0 for t in wins)


def test_window_and_cross_filters_change_the_result(loaded):
    c = _client(loaded)
    wide = c.get("/api/trades?tf=5&start=09:30&cutoff=14:30").get_json()["summary"]["trades"]
    narrow = c.get("/api/trades?tf=5&start=10:30&cutoff=11:30").get_json()["summary"]["trades"]
    second = c.get("/api/trades?tf=5&cross=2").get_json()["trades"]
    assert narrow < wide
    assert second and all(t["cross_number"] == 2 for t in second)


def test_trade_detail_has_chart_and_costs(loaded):
    c = _client(loaded)
    body = c.get("/api/trade/0?tf=15&mode=ITM_BUY").get_json()
    t = body["trade"]
    assert t["side"] == "BUY" and set(t["charges"]) >= {"brokerage", "stt", "gst", "total"}
    assert len(body["spot"]) == 25 and len(body["ema9"]) == 25  # one day of 15m bars
    assert body["option"] and {m["kind"] for m in body["markers"]} == {"entry", "exit"}
    times = [x["time"] for x in body["spot"]]
    assert times == sorted(times)


def test_bad_filters_are_rejected(loaded):
    c = _client(loaded)
    assert c.get("/api/trades?tf=7").status_code == 400
    assert c.get("/api/trades?start=09:45").status_code == 400
    assert c.get("/api/trades?mode=YOLO").status_code == 400
    r = c.get("/api/trade/9999?tf=5")
    assert r.status_code == 404 and "error" in r.get_json()


def test_results_are_cached(loaded):
    bt = web.Backtests(loaded, START, END)
    c = web.create_app(bt).test_client()
    c.get("/api/trades?tf=5")
    first = bt._results[(5, *[v for v in list(bt._results)[0][1:]])]
    c.get("/api/trades?tf=5&mode=ITM_BUY")
    assert len(bt._results) == 1 and list(bt._results.values())[0] is first
    assert date(2026, 10, 12) == bt.start
