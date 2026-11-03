"""IS-1.0 intraday stock engine: readings, signals, exits, sizing, costs, portfolio, scoring."""

from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest

from app.core.market_time import IST, trading_days
from app.stocks import engine as E

DAYS = trading_days(date(2026, 8, 3), date(2026, 9, 30))
DAY = DAYS[-1]


def frame(days, price_fn, vol_fn=lambda d, i: 1000.0):
    rows = []
    for d in days:
        for i in range(75):
            t = datetime.combine(d, time(9, 15), tzinfo=IST) + timedelta(minutes=5 * i)
            o, c = price_fn(d, i)
            rows.append((t, o, max(o, c) + 0.5, min(o, c) - 0.5, c, vol_fn(d, i)))
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"]).set_index("time")
    df.index = pd.DatetimeIndex(df.index)
    df["day"] = df.index.date
    return df


def flat(d, i):
    return 1000.0, 1000.0


def nifty_up(d, i):
    p = 24000 + (i * 2 if d == DAY else 0)
    return p, p + 1


def orb_day(d, i):
    """Quiet range 995-1005 in the first 15 minutes, breakout up at the 09:30 bar, then trend."""
    if d != DAY:
        return 1000.0, 1000.0
    if i < 3:
        return 1000.0, 1004.0 if i == 1 else 1000.0
    if i == 3:
        return 1004.0, 1008.0  # closes above the 1004.5 range high
    p = 1008 + (i - 3) * 0.5
    return p, p + 0.5


def big_vol(d, i):
    return 4000.0 if d == DAY and i < 3 else 1000.0


@pytest.fixture
def nifty():
    return frame(DAYS, nifty_up)


def test_readings_vwap_rvol_and_missing_volume_stay_missing(nifty):
    df = frame(DAYS, orb_day, big_vol)
    r = E.readings(df, nifty)
    d0 = r[r["day"] == DAY]
    assert d0["rvol15"].iloc[0] == pytest.approx(4.0)
    assert d0["range_high"].iloc[0] == pytest.approx(1004.5)
    df.loc[df.index[-70], "volume"] = np.nan  # one missing volume on the last day
    r2 = E.readings(df, nifty)
    last = r2[r2["day"] == DAY]
    assert last["vwap"].iloc[:5].notna().all() and last["vwap"].iloc[5:].isna().all()


def test_orb_long_signal_entry_next_open_stop_and_target(nifty):
    r = E.readings(frame(DAYS, orb_day, big_vol), nifty)
    (c,) = E.candidates("TEST", r, "ORB", 1)
    assert c.signal_time.time() == time(9, 30) and c.entry_time.time() == time(9, 35)
    assert c.entry == pytest.approx(1008.5) and c.stop == pytest.approx(999.5)
    assert c.target == pytest.approx(1008.5 + 2 * 9.0)
    assert c.exit_reason == "TARGET" and c.exit_price == pytest.approx(c.target)
    assert E.candidates("TEST", r, "ORB", -1) == []  # no short on an up day


def test_market_filter_blocks_longs_when_nifty_is_down():
    down = frame(DAYS, lambda d, i: (24000 - (i * 2 if d == DAY else 0),) * 2)
    r = E.readings(frame(DAYS, orb_day, big_vol), down)
    assert E.candidates("TEST", r, "ORB", 1) == []


def test_low_rvol_is_no_trade(nifty):
    r = E.readings(frame(DAYS, orb_day), nifty)  # normal volume: RVOL 1.0
    assert E.candidates("TEST", r, "ORB", 1) == []


def test_live_as_of_sees_only_closed_bars_and_keeps_trade_open(nifty):
    r = E.readings(frame(DAYS, orb_day, big_vol), nifty)
    t0 = datetime.combine(DAY, time(9, 34), tzinfo=IST)
    assert E.candidates("TEST", r, "ORB", 1, as_of=t0) == []  # 09:30 bar not closed yet
    (c,) = E.candidates("TEST", r, "ORB", 1, as_of=datetime.combine(DAY, time(9, 41), tzinfo=IST))
    assert c.exit_reason == "OPEN" and c.exit_time is None


def test_stop_wins_when_bar_hits_both_and_square_off():
    c = E.Candidate("X", "ORB", 1, DAY, None, None, 100.0, 99.0, 102.0, 2.0)
    idx = pd.DatetimeIndex([datetime.combine(DAY, time(15, 5), tzinfo=IST),
                            datetime.combine(DAY, time(15, 10), tzinfo=IST),
                            datetime.combine(DAY, time(15, 15), tzinfo=IST)])
    r = pd.DataFrame({"open": [100, 100, 100.5], "high": [100.5, 103, 101], "low": [99.5, 98, 100],
                      "close": [100, 100, 100.6], "day": [DAY] * 3}, index=idx)
    E._exit(r, 0, c, None)
    assert c.exit_reason == "STOP" and c.exit_price == 99.0
    c2 = E.Candidate("X", "ORB", 1, DAY, None, None, 100.0, 90.0, 120.0, 2.0)
    E._exit(r, 0, c2, None)
    assert c2.exit_reason == "SQUARE_OFF" and c2.exit_price == 100.5


def test_sizing_costs_and_portfolio_limits():
    assert E.quantity(1000, 990) == 100  # ₹1,000 risk / ₹10
    assert E.quantity(1000, 999) == 250  # capped at ₹2.5L of stock
    # buy ₹1L, sell ₹1L: brokerage 20+20, STT 25, txn 5.94, SEBI 0.2, stamp 3, GST on 46.14
    assert E.charges(100_000, 100_000) == pytest.approx(40 + 25 + 5.94 + 0.2 + 3 + 0.18 * 46.14, rel=1e-3)

    def cand(minute, exit_minute, exit_px, rvol=2.0):
        t = datetime.combine(DAY, time(10, 0), tzinfo=IST)
        c = E.Candidate("S", "ORB", 1, DAY, t, t + timedelta(minutes=minute), 100.0, 99.0, 102.0, rvol)
        c.exit_time, c.exit_price, c.exit_reason = t + timedelta(minutes=exit_minute), exit_px, "X"
        return c

    trades = E.portfolio([cand(0, 60, 102), cand(0, 60, 102, 3), cand(5, 30, 102)])
    assert len(trades) == 2 and trades[0].c.rvol == 3  # third blocked: 2 already open
    losses = [cand(5 * k, 5 * k + 1, 96.0) for k in range(6)]
    assert len(E.portfolio(losses)) < 6  # daily loss stop / 5-a-day cap


def test_score_and_pass_bar():
    t = []
    for k in range(120):
        d = DAYS[k % len(DAYS)]
        t0 = datetime.combine(d, time(10), tzinfo=IST) + timedelta(seconds=k)
        c = E.Candidate("S", "ORB", 1, d, None, t0, 100, 99, 102, 2)
        t.append(E.Trade(c, 100, 100, 101, 100, 0, 10, 90 if k % 3 else -100))
    s = E.score(t)
    assert s.trades == 120 and s.net > 0 and s.pf > 1.1
    ok, why = E.passes(s)
    assert ok, why
    assert not E.passes(E.score(t[:50]))[0]
    assert E.split_days(DAYS) == DAYS[len(DAYS) // 3]


def gap_day(d, i):
    if d != DAY:
        return 1000.0, 1000.0
    if i == 0:
        return 1020.0, 1021.0  # gap up 2%, 09:15 bar high 1021.5, low 1019.5
    p = 1021 + i * 0.6
    return p, p + 0.6


def test_gap_and_go_long(nifty):
    r = E.readings(frame(DAYS, gap_day, lambda d, i: 3000.0 if d == DAY and i == 0 else 1000.0), nifty)
    (c,) = E.candidates("TEST", r, "GAP", 1)
    assert c.signal_time.time() == time(9, 20) and c.stop == pytest.approx(1019.5)
    assert E.candidates("TEST", r, "GAP", -1) == []


def pullback_day(d, i):
    if d != DAY:
        return 1000.0, 1000.0
    if i <= 20:
        p = 1000 + i
        return p, p + 1
    if i == 21:
        return 1021.0, 1012.0  # the pullback bar: low 1011.5 reaches the slow EMA
    if i == 22:
        return 1012.0, 1024.0  # green, back above the fast EMA and VWAP
    p = 1024 + (i - 22) * 0.2
    return p, p + 0.2


def test_vwap_pullback_long(nifty):
    r = E.readings(frame(DAYS, pullback_day), nifty)
    cs = E.candidates("TEST", r, "VWP", 1)
    assert len(cs) == 1 and cs[0].signal_time.time() == time(11, 5)
    assert cs[0].stop == pytest.approx(r.loc[r.index[-75 + 20]:r.index[-75 + 22], "low"].min())
