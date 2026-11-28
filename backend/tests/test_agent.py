"""AD-1.0 adaptive agent: building blocks, signals, exits, pricing, regimes, walk-forward."""

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from app import agent as A
from app.agent import intraday as I
from app.agent import regime as R
from app.agent import report as RP
from app.agent import swing as SW
from app.agent import walkforward as W
from app.core.market_time import IST
from app.premium import engine as PE
from tests.test_premium import EXP, FRI, MON, frame
from tests.test_premium import spot as daily_spot

D0 = date(2024, 3, 5)


def info(prev_close=22000.0, high=22100.0, low=21900.0, adx=30.0, vix=15.0, med=14.0, vj=False):
    return R.Day(D0, prev_close, high, low, adx, vix, med, 1, 50.0, True, vj)


def spot_day(path: np.ndarray, d: date = D0) -> I.SpotDay:
    """A day whose minute closes follow `path` (375 values); highs/lows +-2 points."""
    t0 = pd.Timestamp(datetime.combine(d, datetime.min.time()), tz=IST) + timedelta(hours=9, minutes=15)
    df = pd.DataFrame({"time": [t0 + timedelta(minutes=i) for i in range(375)],
                       "open": np.concatenate([[path[0]], path[:-1]]), "high": path + 2,
                       "low": path - 2, "close": path})
    days = I.spot_days(df)
    return days[0]


def test_candidate_count_is_the_cap():
    n = len(I.base_keys()) * len(A.I_REGIMES) + len(SW.base_keys()) * len(A.REGIMES)
    assert n == A.MAX_CANDIDATES == 4212


def test_day_with_missing_minute_is_dropped():
    path = np.full(375, 22000.0)
    t0 = pd.Timestamp(datetime.combine(D0, datetime.min.time()), tz=IST) + timedelta(hours=9, minutes=15)
    df = pd.DataFrame({"time": [t0 + timedelta(minutes=i) for i in range(375) if i != 100],
                       "open": 22000.0, "high": 22001.0, "low": 21999.0, "close": 22000.0})
    sk = {}
    assert I.spot_days(df, sk) == [] and sum(sk.values()) == 1
    assert len(I.spot_days(df.assign(time=df.time)[:0])) == 0
    assert path.shape == (375,)


def test_orb_breakout_long():
    path = np.full(375, 22000.0)
    path[40:] = 22050.0  # breaks the 09:15-09:29 high (22002) during bar 8
    s = spot_day(path)
    assert I.signal("ORB", s, info()) == (45, 1)


def test_gap_fade_and_go():
    path = np.full(375, 22100.0)  # 0.45% gap up over 22000
    path[0:5] = [22100, 22095, 22090, 22085, 22080]  # first bar closes lower: back toward close
    s = spot_day(path)
    assert I.signal("GAPFADE", s, info()) == (5, -1)
    assert I.signal("GAPGO", s, info()) is None
    big = spot_day(np.full(375, 22400.0))  # 1.8% gap: outside the band
    assert I.signal("GAPFADE", big, info()) is None


def test_pdhl_and_cpr_crosses():
    path = np.full(375, 22050.0)
    path[60:] = 22150.0  # above previous high 22100 from minute 60
    s = spot_day(path)
    assert I.signal("PDHL", s, info()) == (65, 1)
    # CPR from H 22100 L 21900 C 22000: pivot 22000, BC 22000, TC 22000 -> crosses at 22050 open?
    s2 = spot_day(np.concatenate([np.full(30, 21990.0), np.full(345, 22010.0)]))
    assert I.signal("CPR", s2, info()) == (35, 1)


def test_exit_stop_target_and_trail():
    path = np.full(375, 22000.0)
    path[50:] = 22200.0  # +0.9%
    s = spot_day(path)
    # long from minute 10, stop 0.25% (55 pts): target 2R = 110 pts -> hit at minute 50
    assert I.exit_minute(s, 10, 1, 0.0025, "2R", None) == (51, "TARGET")
    assert I.exit_minute(s, 10, 1, 0.0025, "NONE", None) == (I.EOD, "EOD")
    assert I.exit_minute(s, 10, 1, 0.0025, "NONE", 60) == (70, "TIME")
    # short: stopped when price jumps
    assert I.exit_minute(s, 10, -1, 0.0025, "NONE", None)[1] == "STOP"
    # trail: up 200, then falls back 100 -> stop trails 55 behind the best -> exits in profit
    p2 = path.copy()
    p2[100:] = 22100.0
    s2 = spot_day(p2)
    x, why = I.exit_minute(s2, 10, 1, 0.0025, "TRAIL", None)
    assert why == "TRAIL" and x == 101


def test_live_state():
    path = np.full(375, 22000.0)
    path[100:] = 22100.0  # +0.45%
    s = spot_day(path)
    assert I.live_state(s, 50) == "F" and I.live_state(s, 150) == "T"


def test_legs_for_structures():
    assert I.legs_for("BUY_ATM", 1, 22012) == [("CE", 22000.0, 1)]
    assert I.legs_for("BUY_ITM", -1, 22012) == [("PE", 22050.0, 1)]
    assert I.legs_for("DEBIT", 1, 22000) == [("CE", 22000.0, 1), ("CE", 22100.0, -1)]
    assert I.legs_for("CREDIT", 1, 22000) == [("PE", 22000.0, -1), ("PE", 21800.0, 1)]


def test_price_trade_never_guesses_and_charges_costs():
    arr = np.full(375, np.nan)
    arr[10:] = 100.0
    arr[200:] = 150.0
    panel = lambda k, s: arr if (k, s) == ("CE", 22000.0) else None  # noqa: E731
    p = I.price_trade([("CE", 22000.0, 1)], panel, 10, 200, D0)
    assert p["gross"] == pytest.approx(50 * A.LOT)
    assert p[1.0] < p["gross"] and p[2.0] < p[1.0]
    assert I.price_trade([("CE", 22000.0, 1), ("CE", 22100.0, -1)], panel, 10, 200, D0) is None
    assert I.price_trade([("CE", 22000.0, 1)], panel, 2, 200, D0) is None  # no price within grace


def test_day_trades_skip_selling_on_vj_days_and_missing_data():
    path = np.full(375, 22000.0)
    path[40:] = 22060.0
    s = spot_day(path)
    arr = np.full(375, 100.0)
    panel = lambda k, st: arr  # noqa: E731
    sk = {}
    t = I.day_trades(s, info(vj=True), panel, sk)
    assert t and not any(x["structure"] == "CREDIT" for x in t)
    assert any(x["structure"] == "CREDIT" for x in I.day_trades(s, info(), panel, sk))
    assert I.day_trades(s, info(vix=None), panel, sk) == []
    assert I.needed_legs(s, info())


def test_regime_uses_previous_day_only():
    days = pd.bdate_range("2023-01-02", "2024-03-29")
    rng = np.random.default_rng(3)
    c = pd.Series(22000 * np.exp(rng.normal(0, 0.008, len(days)).cumsum()), index=days)
    daily = pd.DataFrame({"high": c * 1.004, "low": c * 0.996, "close": c})
    vix = pd.Series(14 + rng.normal(0, 1, len(days)), index=days)
    d = date(2024, 3, 5)
    lab = R.labels(daily, vix, [d])[d]
    prev = pd.Timestamp("2024-03-04")
    assert lab.prev_close == pytest.approx(c[prev]) and lab.ok
    changed = daily.copy()
    changed.loc[pd.Timestamp(d), "close"] = 1.0  # the day itself must not matter
    assert R.labels(changed, vix, [d])[d].adx == pytest.approx(lab.adx)
    assert not R.labels(daily, None, [d])[d].ok


def test_swing_trades_from_daily_file():
    book = PE.Book(frame())
    days = [FRI, MON]
    lab = {dd: R.Day(dd, 21700.0, 21800.0, 21600.0, 15.0, 15.0, 14.0, 1, 5.0, True, False)
           for dd in book.days}
    sk = {}
    t = SW.all_trades(book, daily_spot(), lab, sk, first=date(2024, 1, 1))
    assert t and {x["family"] for x in t} <= set(A.HOLDINGS)
    wk = [x for x in t if x["family"] == "WEEKEND"]
    assert wk and all(x["entry_day"] == FRI and x["exit_day"] == MON for x in wk)
    assert any(x["family"] == "WEEKLY" and x["exit_day"] == EXP for x in t)
    assert {x["structure"] for x in t if x["direction"] == 1} >= {"CREDIT", "DEBIT", "LONG"}
    assert days


def _trades(rows):
    df = pd.DataFrame(rows)
    df["entry_time"] = [pd.Timestamp(datetime.combine(d, datetime.min.time()), tz=IST) + timedelta(hours=10)
                        for d in df["entry_day"]]
    df["exit_time"] = df["entry_time"] + timedelta(hours=1)
    df["exit_day"] = df["entry_day"]
    for c in ("gross", "costs"):
        df[c] = 0.0
    df["net_2x"] = df["net"]
    df["live"] = "N"
    df["track"] = "I"
    lab = {d: R.Day(d, 1.0, 1.0, 1.0, 30.0, 15.0, 14.0, 1, 50.0, True, False) for d in df["entry_day"]}
    return W.add_regimes(df, lab)


def test_walk_forward_only_uses_past_months():
    rows = []
    days = pd.bdate_range("2024-01-01", "2024-09-30")
    for d in days:
        good = 300.0 if d.day % 4 else -100.0
        rows.append({"base": "I|ORB|0.25%|2R|EOD|BUY_ATM", "family": "ORB", "entry_day": d.date(),
                     "net": good})
        # LATE only makes money from August: must not be picked before September
        late = 500.0 if d.month >= 8 else -200.0
        rows.append({"base": "I|EMA|0.25%|2R|EOD|BUY_ATM", "family": "EMA", "entry_day": d.date(),
                     "net": late})
    t = _trades(rows)
    res = W.run(t, pd.Period("2024-07", "M"), pd.Period("2024-09", "M"))
    picks = {m["month"]: {p["base"] for p in m["picks"]} for m in res.months}
    assert "I|ORB|0.25%|2R|EOD|BUY_ATM" in picks["2024-07"]
    assert "I|EMA|0.25%|2R|EOD|BUY_ATM" not in picks["2024-07"]
    assert "I|EMA|0.25%|2R|EOD|BUY_ATM" not in picks["2024-08"]
    # one position at a time: both candidates trade at 10:00, only one goes through per day
    assert res.trades.groupby("entry_day").size().max() == 1


def test_day_loss_limit_and_kill_switch():
    rows = [{"base": "I|ORB|0.25%|2R|EOD|BUY_ATM", "family": "ORB", "entry_day": d.date(),
             "net": 400.0 + 50 * (d.day % 2)} for d in pd.bdate_range("2024-01-01", "2024-06-28")]
    rows += [{"base": "I|ORB|0.25%|2R|EOD|BUY_ATM", "family": "ORB", "entry_day": d.date(),
              "net": -3500.0} for d in pd.bdate_range("2024-07-01", "2024-07-31")]
    t = _trades(rows)
    res = W.run(t, pd.Period("2024-07", "M"), pd.Period("2024-07", "M"))
    # -3,500 a day: the week limit stops new trades after 2 days each week, the kill switch at -15k
    assert res.kills == 1
    assert len(res.trades) < 10
    assert res.blocked.get("kill switch", 0) > 0


def test_summary_and_report_words():
    rows = [{"base": "S|WEEKLY|VRP|FLY", "family": "WEEKLY", "entry_day": d.date(),
             "net": 500.0 + 100 * (d.day % 3)} for d in pd.bdate_range("2024-01-01", "2024-12-31")]
    t = _trades(rows)
    t["track"] = "S"
    res = W.run(t, pd.Period("2024-07", "M"), pd.Period("2024-12", "M"), tracks=("S",))
    s = W.summarize(res, res)
    assert s["trades"] > 60 and s["verdict"] == "PASS"
    md = RP.to_markdown({"FULL": s}, {"skipped": {}}, [], "now")
    assert "Swing to expiry (weekly), iron fly when VIX > 20-day volatility" in md
    assert "Intraday" in RP.describe("I|PDHL|0.5%|TRAIL|60m|CREDIT", "LIVE_TREND")


def test_panels_read_option_opens_from_the_database(session):
    from app.agent import data as AD
    from app.ema921 import store as S
    from app.providers.base import Candle

    ex = date(2024, 3, 7)
    S.save_expiries(session, "NIFTY", [ex])
    S.save_contracts(session, "NIFTY", ex, [("NSE-NIFTY-07Mar24-22000-CE", 22000.0, "CE")])
    t0 = pd.Timestamp(datetime.combine(D0, datetime.min.time()), tz=IST) + timedelta(hours=9, minutes=15)
    candles = [Candle(time=(t0 + timedelta(minutes=i)).tz_convert("UTC").to_pydatetime(),
                      open=100.0 + i,
                      high=101.0 + i, low=99.0 + i, close=100.5 + i, volume=10, open_interest=None)
               for i in (0, 1, 5)]
    S.save_minutes(session, "NSE-NIFTY-07Mar24-22000-CE", D0, candles)
    session.commit()
    p = AD.Panels(session)
    assert p.expiry(D0) == ex
    arr = p.panel(D0)("CE", 22000)
    assert arr[0] == 100.0 and arr[5] == 105.0 and np.isnan(arr[2])
    assert p.panel(D0)("PE", 22000) is None
