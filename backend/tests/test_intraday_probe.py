"""Intraday probe: history depth, request size, one day of bars, quote keys, NSE member lists."""

from datetime import UTC, date, datetime, timedelta

import httpx
import pandas as pd

from app.core.market_time import IST
from app.providers.base import InstrumentInfo
from app.services import intraday_probe as ip

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)  # 17:30 IST, Thursday: today's session closed
STOCK = InstrumentInfo("RELIANCE", "STOCK", "NSE", "CASH", "NSE-RELIANCE", "RELIANCE")
INDEX = InstrumentInfo("NIFTY", "INDEX", "NSE", "CASH", "NSE-NIFTY", "NIFTY")


class FakeClient:
    """5-min history back 100 days; a call may span at most 30 days; 1-min always fails."""

    def get_historical_candles(self, **kw):
        if kw["candle_interval"] == "1minute":
            raise RuntimeError("Invalid interval")
        start = datetime.fromisoformat(kw["start_time"]).replace(tzinfo=IST)
        end = datetime.fromisoformat(kw["end_time"]).replace(tzinfo=IST)
        if end - start > timedelta(days=30):
            raise RuntimeError("Interval 5minute can only be queried for a maximum of 30 days")
        oldest = NOW - timedelta(days=100)
        if end < oldest:
            return {"candles": []}
        t, rows = max(start, oldest), []
        while t <= end and len(rows) < 75:
            rows.append([t.strftime("%Y-%m-%d %H:%M:%S"), None if not rows else 1, 2, 1, 1.5, 9,
                         None])
            t += timedelta(minutes=5)
        return {"candles": rows}

    def get_quote(self, **kw):
        return {"last_price": 1, "upper_circuit_limit": None,
                "depth": {"buy": [{"price": 1, "quantity": 2}] * 5, "sell": []}}


def test_depth_finds_the_oldest_age_and_records_errors():
    c = FakeClient()
    five = ip.probe_depth(c, STOCK, "RELIANCE stock", "5m", NOW)
    assert five.oldest_age == 90 and five.counts[120] == 0
    one = ip.probe_depth(c, STOCK, "RELIANCE stock", "1m", NOW)
    assert one.oldest_age is None and one.counts[3] == "ERROR"


def test_window_probe_shows_where_requests_get_too_long():
    w = ip.probe_window(FakeClient(), STOCK, "5m", NOW)
    assert isinstance(w[30], int) and w[60] == "TOO LONG"


def test_one_day_counts_bars_and_missing_values():
    day = ip.probe_day(FakeClient(), STOCK, "RELIANCE stock", date(2026, 10, 28))
    assert (day.bars, day.first, day.last) == (75, "09:15", "15:25")
    assert (day.no_open, day.no_volume, day.with_oi) == (1, 0, 0)


def test_last_full_day_skips_today_until_the_close_and_weekends():
    assert ip.last_full_day(NOW) == date(2026, 10, 29)
    morning = datetime(2026, 10, 29, 5, 0, tzinfo=UTC)  # 10:30 IST
    assert ip.last_full_day(morning) == date(2026, 10, 28)
    monday = datetime(2026, 11, 2, 4, 0, tzinfo=UTC)
    assert ip.last_full_day(monday) == date(2026, 10, 30)


def test_quote_keys_are_listed_without_values():
    keys = ip.probe_quote(FakeClient(), STOCK)
    assert "last_price" in keys and "upper_circuit_limit (empty)" in keys
    assert "depth.buy [list of 5]" in keys and "depth.buy[0].quantity" in keys
    assert "depth.sell [list of 0]" in keys


def _nse(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "ind_nifty50list" in url and "niftyindices" in url:
        return httpx.Response(403)
    if "ind_nifty50list" in url:
        return httpx.Response(200, content=b"Company Name,Industry,Symbol,Series\nA,x,RELIANCE,EQ\n"
                                           b"B,x,TCS,EQ\n")
    if "ind_niftynext50list" in url:
        return httpx.Response(200, content=b"Company Name,Industry,Symbol,Series\nC,x,DMART,EQ\n")
    if "sec_bhavdata_full" in url:
        return httpx.Response(200, content=b"SYMBOL,SERIES,DELIV_PER\nX,EQ,50\n")
    return httpx.Response(200, content=b"<html>home</html>")


def test_nse_lists_fall_back_and_members_are_matched_with_groww():
    instruments = pd.DataFrame([
        {"exchange": "NSE", "segment": "CASH", "instrument_type": "EQ", "trading_symbol": "RELIANCE",
         "underlying_symbol": None, "expiry_date": None},
        {"exchange": "NSE", "segment": "CASH", "instrument_type": "EQ", "trading_symbol": "DMART",
         "underlying_symbol": None, "expiry_date": None},
        {"exchange": "NSE", "segment": "FNO", "instrument_type": "FUT", "trading_symbol": "RELFUT",
         "underlying_symbol": "RELIANCE", "expiry_date": "2026-11-24"},
    ])
    res = ip.ProbeResult(NOW)
    with httpx.Client(transport=httpx.MockTransport(_nse)) as http:
        ip.run_nse(res, http, instruments, pause=0)
    n50, next50 = res.lists
    assert n50.status == "OK" and n50.symbols == ["RELIANCE", "TCS"] and "nsearchives" in n50.url
    assert next50.symbols == ["DMART"]
    assert res.delivery[0].status == "OK"
    by = {m.symbol: m for m in res.members}
    assert by["RELIANCE"].has_futures and not by["TCS"].in_groww
    assert by["DMART"].in_groww and not by["DMART"].has_futures
    md = ip.to_markdown(res)
    assert "Not in Groww: TCS" in md and "No futures (cash only): DMART" in md


def test_full_run_writes_a_report_without_prices():
    targets = ip.target_instruments([INDEX, STOCK], "RELIANCE")
    assert list(targets) == ["RELIANCE stock", "NIFTY index"]
    res = ip.run_groww(FakeClient(), targets, NOW, log=lambda _: None)
    md = ip.to_markdown(res)
    assert "**RELIANCE stock 5m**: candles found back to about 90 days ago" in md
    assert "**NIFTY index 1m**: no candles at any age" in md
    assert "| RELIANCE stock | 2026-10-29 | 75 | 09:15 | 15:25 |" in md
    assert "1.5" not in md  # the close price never reaches the report
    assert "- 34x: Invalid interval" in md  # 1m: 13 ages x 2 instruments + 8 request sizes


class FlakyClient(FakeClient):
    """Answers "no healthy upstream" on every other call, like Groww's gateway on 29 Oct."""

    def __init__(self, always_down=False):
        self.calls, self.always_down = 0, always_down

    def get_historical_candles(self, **kw):
        self.calls += 1
        if self.always_down or self.calls % 2:
            raise RuntimeError("Groww get_historical_candles failed (GrowwAPIException: "
                               "no healthy upstream)")
        return super().get_historical_candles(**kw)


def test_a_down_answer_is_retried_once_then_labelled(monkeypatch):
    monkeypatch.setattr(ip, "RETRY_PAUSE", 0)
    flaky = FlakyClient()
    sizes = ip.probe_window(flaky, STOCK, "5m", NOW)
    assert sizes[5] == 75 and flaky.calls == 2 * len(sizes)  # each size: one DOWN, one retry
    assert ip.probe_window(FlakyClient(always_down=True), STOCK, "5m", NOW)[5] == "DOWN"
