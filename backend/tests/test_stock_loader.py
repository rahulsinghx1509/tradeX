"""Intraday stock loader: 30-day window grid, resume, history-end stop, member matching."""

from datetime import date, datetime, time, timedelta

import pandas as pd

from app.core.market_time import trading_days
from app.stocks import loader as L
from app.stocks import store as S

FIRST = date(2026, 6, 1)  # the fake Groww has no candles before this


class FakeGroww:
    def __init__(self, first=FIRST, fail_once=()):
        self.calls = []
        self.first = first
        self.fail_once = set(fail_once)

    def get_historical_candles(self, exchange, segment, groww_symbol, start_time, end_time,
                               candle_interval):
        assert candle_interval == "5minute" and segment == "CASH"
        s, e = date.fromisoformat(start_time[:10]), date.fromisoformat(end_time[:10])
        assert (e - s).days < L.WINDOW_DAYS
        self.calls.append((groww_symbol, s, e))
        if groww_symbol in self.fail_once:
            self.fail_once.discard(groww_symbol)
            raise RuntimeError("rate limited")
        rows = []
        for d in trading_days(max(s, self.first), e):
            for i in range(75):
                t = datetime.combine(d, time(9, 15)) + timedelta(minutes=5 * i)
                p = 1000 + i
                rows.append([t.strftime("%Y-%m-%d %H:%M:%S"), p, p + 2, p - 2, p + 1, 500, None])
        rows += rows[:3]  # Groww sometimes repeats candles
        return {"candles": rows}


def test_windows_are_a_fixed_grid_newest_first():
    w = L.windows(date(2026, 1, 10), date(2026, 3, 20))
    assert w[0][1] == date(2026, 3, 20) and w[-1][0] == date(2026, 1, 10)
    assert all((we - ws).days < L.WINDOW_DAYS for ws, we, _ in w)
    assert all((k - L.ANCHOR).days % L.WINDOW_DAYS == 0 for _, _, k in w)
    assert [k for *_, k in w] == sorted({k for *_, k in w}, reverse=True)


def test_load_resumes_stops_at_history_end_and_refetches_partial(session):
    S.save_members(session, [("RELIANCE", "NIFTY 50", "NSE-RELIANCE")])
    session.commit()
    g = FakeGroww()
    start, end = date(2025, 1, 1), date(2026, 10, 30)
    rep = L.load_all(session, g, start, end, log=lambda _: None)
    assert not rep.errors and rep.symbols == 2
    df = S.load_candles(session, "RELIANCE")
    days = trading_days(FIRST, end)
    assert len(df) == 75 * len(days)  # repeats dropped, one row per bar
    assert df.index.min().date() == FIRST and str(df.index.tz) == "Asia/Kolkata"
    asked = [c for c in g.calls if c[0] == "NSE-RELIANCE"]
    assert min(c[1] for c in asked) >= FIRST - timedelta(days=3 * L.WINDOW_DAYS)  # stopped early

    g2 = FakeGroww()
    L.load_all(session, g2, start, end, log=lambda _: None)
    # Only the window holding the last day (PARTIAL) is asked again, once per symbol.
    assert len(g2.calls) == 2 and all(c[2] == end for c in g2.calls)


def test_errors_are_recorded_and_retried(session):
    S.save_members(session, [("SBIN", "NIFTY 50", "NSE-SBIN")])
    session.commit()
    g = FakeGroww(fail_once={"NSE-SBIN"})
    end = date(2026, 10, 30)
    rep = L.load_all(session, g, date(2026, 9, 1), end, only=["SBIN"], log=lambda _: None)
    assert len(rep.errors) == 1
    rep2 = L.load_all(session, FakeGroww(), date(2026, 9, 1), end, only=["SBIN"], log=lambda _: None)
    assert not rep2.errors
    assert S.load_candles(session, "SBIN").index.max().date() == end


def test_match_groww_and_missing_stay_missing(session):
    inst = pd.DataFrame([
        dict(exchange="NSE", segment="CASH", trading_symbol="RELIANCE", groww_symbol="NSE-RELIANCE"),
        dict(exchange="NSE", segment="FNO", trading_symbol="SBIN", groww_symbol="NSE-SBIN-FUT"),
    ])
    found, missing = L.match_groww([("RELIANCE", "NIFTY 50"), ("SBIN", "NIFTY 50")], inst)
    assert found == [("RELIANCE", "NIFTY 50", "NSE-RELIANCE")] and missing == ["SBIN"]
    S.save_members(session, found)
    S.save_members(session, found)  # upsert, no duplicate
    assert S.members(session) == found
