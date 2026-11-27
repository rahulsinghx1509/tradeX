"""MR-4 mean reversion: indicators, Setup A / B exits, option pricing, the account rules."""

from collections import Counter
from dataclasses import replace
from datetime import date, datetime, time, timedelta

import pandas as pd
import pytest

from app.core.market_time import IST
from app.ema921 import engine as EE
from app.meanrev import PARAMS
from app.meanrev import engine as E
from app.meanrev import indicators as I
from app.meanrev import report as R

DAY = date(2026, 10, 14)  # Wednesday
EXPIRY = date(2026, 10, 20)
PREV = 24500.0
CFG = EE.Config()
NO_EVENTS = frozenset()


def market(vix=14.0, events=NO_EVENTS, days=(DAY,)):
    return E.Market({d: vix for d in days}, pd.Series(dtype=float), events)


def minute_rows(day, price_at, wick=1.0):
    rows = []
    for i in range(375):
        t = pd.Timestamp(datetime.combine(day, time(9, 15)), tz=IST) + timedelta(minutes=i)
        o, c = price_at(i)
        rows.append({"time": t.tz_convert("UTC"), "open": o, "high": max(o, c) + wick,
                     "low": min(o, c) - wick, "close": c})
    return rows


class FakeOptions:
    """Option price = intrinsic value + 60, following the spot minute by minute."""

    def __init__(self, spot, expiries=(EXPIRY,), missing=frozenset()):
        self.spot, self._exp, self.missing = spot, list(expiries), set(missing)

    def expiries(self):
        return self._exp

    def contract(self, expiry, strike, opt):
        return f"NSE-NIFTY-{expiry:%d%b%y}-{strike:g}-{opt}"

    def minutes(self, symbol, day):
        if symbol in self.missing:
            return None
        strike, opt = float(symbol.split("-")[-2]), symbol.split("-")[-1]
        s = self.spot[pd.to_datetime(self.spot["time"], utc=True).dt.tz_convert(IST).dt.date == day]
        val = (s["open"] - strike).clip(lower=0) if opt == "CE" else (strike - s["open"]).clip(lower=0)
        p = val + 60
        return pd.DataFrame({"time": s["time"], "open": p, "high": p + 1, "low": p - 1, "close": p})


def closes_for(day, n=260, last=PREV, trend=1.0):
    idx = pd.bdate_range(end=pd.Timestamp(day) - pd.Timedelta(days=1), periods=n)
    vals = [last - trend * (n - 1 - i) for i in range(n)]
    return pd.Series(vals, index=idx)


# ----------------------------------------------------------------------------- indicators
def test_rsi2_and_bias():
    up = pd.Series([100.0 + i for i in range(10)])
    assert I.rsi(up).iloc[-1] == 100.0
    down = pd.Series([100.0 - i for i in range(10)])
    assert I.rsi(down).iloc[-1] == pytest.approx(0.0)
    # 250 rising closes then two sharp down days: above SMA200, RSI2 low -> LONG bias next day
    c = closes_for(DAY, 252)
    c.iloc[-2] -= 25
    c.iloc[-1] -= 50  # still above the SMA200 (~24,400)
    c = pd.concat([c, pd.Series([PREV], index=[pd.Timestamp(DAY)])])
    assert I.daily_bias(c)[DAY] == 1
    # too little history = no bias at all (None = no trade)
    short = c.iloc[-50:]
    assert I.daily_bias(short)[DAY] is None


def test_patterns():
    assert I.hammer(I.Bar(100, 101, 90, 100.8))  # long lower wick, tiny upper wick
    assert not I.hammer(I.Bar(100, 105, 90, 101))  # upper wick too big
    assert I.shooting_star(I.Bar(100, 110, 99, 99.2))
    red, small, green = I.Bar(110, 111, 99, 100), I.Bar(100, 101, 98, 99.5), I.Bar(100, 107, 99, 106)
    assert I.morning_star(red, small, green)
    assert not I.morning_star(red, small, I.Bar(100, 104, 99, 103))  # below the midpoint 105
    assert I.reversal([red, small, green], 1) == ("morning star", 3)
    assert I.reversal([I.Bar(100, 101, 90, 100.8)], 1) == ("hammer", 1)
    assert I.reversal([I.Bar(100, 101, 90, 100.8)], -1) is None


def test_bollinger_and_vwap_bands():
    bb = I.bollinger(pd.Series([float(x) for x in range(1, 21)]))
    assert bb["bb_mid"].iloc[-1] == pytest.approx(10.5)
    assert bb["bb_lo"].iloc[18] != bb["bb_lo"].iloc[18]  # NaN before 20 bars
    day = pd.DataFrame({"m": [0, 1, 2], "high": [101.0, 103, 105], "low": [99.0, 101, 103],
                        "close": [100.0, 102, 104]})
    vw = I.vwap_bands(day, pd.Series([1.0, 1, 2], index=[0, 1, 2]))
    assert vw["vwap"].iloc[-1] == pytest.approx((100 + 102 + 2 * 104) / 4)
    assert vw["vwap_sd"].iloc[0] == pytest.approx(0.0)
    # a missing volume makes VWAP unknown from there on
    vw2 = I.vwap_bands(day, pd.Series([1.0, float("nan"), 2], index=[0, 1, 2]))
    assert pd.notna(vw2["vwap"].iloc[0]) and vw2["vwap"].iloc[1:].isna().all()


def test_implied_vol_round_trip_and_delta_strike():
    p = I.bs_price(24500, 24500, 7 / 365, 0.13, 0.065, True)
    assert I.implied_vol(p, 24500, 24500, 7 / 365, 0.065, True) == pytest.approx(0.13, abs=1e-4)
    k, d = I.strike_near_delta(24500, 24500, 50, 7 / 365, 0.13, 0.065, True, 0.7)
    assert k < 24500 and abs(d - 0.7) < 0.06
    kp, dp = I.strike_near_delta(24500, 24500, 50, 7 / 365, 0.13, 0.065, False, 0.7)
    assert kp > 24500 and abs(dp - 0.7) < 0.06


# ----------------------------------------------------------------------------- Setup A
def gap_down_fill(i):
    """Opens 0.5% below the previous close, first 5 minutes rise, fills the gap at 10:00."""
    start = PREV * 0.995
    if i < 45:
        p = start + i * (PREV + 5 - start) / 45
        return p, p + 0.5
    return PREV + 5, PREV + 5


def prep(rows, closes, fut=None):
    spot = pd.DataFrame(rows)
    return spot, E.prepare("NIFTY", spot, fut, E.daily_closes(closes, spot))


def test_setup_a_gap_fill_long_hits_target():
    spot, days = prep(minute_rows(DAY, gap_down_fill, wick=0.5), closes_for(DAY))
    info = days[DAY]
    assert info.prev_close == PREV and info.bias == 0  # steady uptrend, RSI2 high -> no bias
    why = Counter()
    cands = E.candidates(info, replace(PARAMS["NIFTY"], setups=("A",)), market(), frozenset(),
                         why=why)
    assert len(cands) == 1
    c = cands[0]
    assert (c.setup, c.direction, c.entry_m, c.size) == ("A", 1, 5, 0.5)
    assert c.exits[0].reason == "TARGET" and c.exits[0].minute < 50
    inst = E.instrument_for("NIFTY", FakeOptions(spot), PARAMS["NIFTY"])
    tr = E.price(c, inst, market(), CFG, check_risk=False)
    assert isinstance(tr, E.Trade)
    assert tr.option_type == "CE" and tr.strike < 24400 and tr.qty == 32.5
    assert tr.gross_pnl > 0 and tr.net_pnl < tr.gross_pnl


def test_day_filters_block_the_day():
    spot, days = prep(minute_rows(DAY, gap_down_fill), closes_for(DAY))
    p = PARAMS["NIFTY"]
    assert E.day_block(days[DAY], p, market(vix=21), frozenset()) == "VIX_ABOVE_20"
    assert E.day_block(days[DAY], p, market(events=frozenset({DAY})), frozenset()) == "EVENT_DAY"
    assert E.day_block(days[DAY], p, market(), frozenset({DAY})) == "EXPIRY_DAY"
    m = E.Market({}, pd.Series([15.0], index=[pd.Timestamp(DAY - timedelta(days=1))]), NO_EVENTS)
    assert m.vix(DAY) == (15.0, True)  # previous daily close when no 1-minute VIX
    assert E.day_block(days[DAY], p, m, frozenset()) is None


def test_monthly_expiry_days():
    ex = [date(2024, 10, 2), date(2024, 10, 9), date(2024, 10, 30), date(2024, 11, 27)]
    assert E.expiry_days("BANKNIFTY", ex, True) == frozenset({date(2024, 10, 30), date(2024, 11, 27)})
    assert E.expiry_days("NIFTY", ex, False) == frozenset(ex)


# ----------------------------------------------------------------------------- Setup B exits
def info_with(lo, hi, bbmid, vwap):
    n = 375
    o = [24500.0] * n
    return E.DayInfo("NIFTY", DAY, True, o, list(hi), list(lo), o, PREV, 0, [vwap] * n, [bbmid] * n)


def test_setup_b_half_at_each_target_then_breakeven():
    n = 375
    lo, hi = [24487.0] * n, [24495.0] * n  # always above the entry 24485
    hi[200] = 24502  # touches the middle band (24500) -> first half
    lo[205] = 24470  # back to the entry 24485: breakeven stop of the other half
    info = info_with(lo, hi, 24500.0, 24520.0)
    ex = E.spot_exits_b(info, 1, 195, 24485.0, 24460.0)
    assert [(e.fraction, e.minute, e.reason) for e in ex] == [(0.5, 201, "MID_BAND"),
                                                              (0.5, 206, "BREAKEVEN")]


def test_setup_b_stop_beats_target_in_same_minute_and_time_stop():
    n = 375
    lo, hi = [24480.0] * n, [24490.0] * n
    lo[200], hi[200] = 24450, 24530
    ex = E.spot_exits_b(info_with(lo, hi, 24500.0, 24520.0), 1, 195, 24485.0, 24460.0)
    assert [(e.fraction, e.reason) for e in ex] == [(1.0, "STOP")]
    flat = E.spot_exits_b(info_with([24480.0] * n, [24490.0] * n, 24500.0, 24520.0), 1, 100,
                          24485.0, 24460.0)
    assert [(e.fraction, e.minute, e.reason) for e in flat] == [(1.0, 190, "TIME_90")]


# ----------------------------------------------------------------------------- account
def cand(u, m, direction, score=1.0, exit_m=None, setup="A"):
    return E.Candidate(u, setup, DAY, direction, m, 24500.0, 24400.0 if direction > 0 else 24600.0,
                       1.0, score, "x", [E.Exit(1.0, exit_m or m + 10, "TARGET")])


def test_account_rules(monkeypatch):
    priced = []

    def fake_price(c, inst, market, cfg, check_risk):
        priced.append((c.underlying, c.entry_m))
        tr = E.Trade(c.underlying, c.setup, c.day, c.direction, "x", 1, 65, "CE", 24300, EXPIRY,
                     "S", 0.7, 0.13, "ATM", c.entry_time.to_pydatetime(), c.entry_spot, c.stop,
                     100, 100.5, [E.Fill(1, E._t(c.day, c.last_exit_m).to_pydatetime(), "T", 90, 89.5)])
        tr.net_pnl = -3500.0
        return tr

    monkeypatch.setattr(E, "price", fake_price)
    cs = [cand("NIFTY", 5, 1, 1.0, 40), cand("BANKNIFTY", 5, 1, 2.0, 40),  # R27: BANKNIFTY wins
          cand("NIFTY", 30, 1),  # position still open -> skipped
          cand("NIFTY", 60, 1),  # day loss limit already hit (-3,500) -> skipped
          ]
    res = E.run(cs, {}, market(), CFG)
    assert priced == [("BANKNIFTY", 5)]
    assert res.skips["R27_OTHER_INDEX"] == 1 and res.skips["POSITION_OPEN"] == 1
    assert res.skips["LOSS_LIMIT"] == 1
    # R28: opposite directions in the same minute = neither
    priced.clear()
    res = E.run([cand("NIFTY", 5, 1), cand("BANKNIFTY", 5, -1)], {}, market(), CFG)
    assert priced == [] and res.skips["OPPOSITE_SIGNALS"] == 2


def test_max_two_trades_a_day(monkeypatch):
    def fake_price(c, inst, market, cfg, check_risk):
        tr = E.Trade(c.underlying, c.setup, c.day, c.direction, "x", 1, 65, "CE", 24300, EXPIRY,
                     "S", 0.7, 0.13, "ATM", c.entry_time.to_pydatetime(), 1, 1, 100, 100,
                     [E.Fill(1, E._t(c.day, c.last_exit_m).to_pydatetime(), "T", 101, 101)])
        tr.net_pnl = 10.0
        return tr

    monkeypatch.setattr(E, "price", fake_price)
    res = E.run([cand("NIFTY", m, 1) for m in (60, 90, 120)], {}, market(), CFG)
    assert len(res.trades) == 2 and res.skips["MAX_2_A_DAY"] == 1


def test_risk_cap_skips_setup_b():
    spot = pd.DataFrame(minute_rows(DAY, lambda i: (PREV, PREV)))
    inst = E.instrument_for("NIFTY", FakeOptions(spot), PARAMS["NIFTY"])
    c = E.Candidate("NIFTY", "B", DAY, 1, 120, PREV, PREV - 60, 1.0, 1.0, "hammer",
                    [E.Exit(1.0, 150, "TIME_90")])  # 60 pts x ~0.7 x 65 ≈ ₹2,700 > ₹1,500
    assert E.price(c, inst, market(), CFG, check_risk=True) == "RISK_TOO_BIG"
    assert isinstance(E.price(replace(c, stop=PREV - 20), inst, market(), CFG, True), E.Trade)


def test_missing_option_data_is_skipped_not_zero():
    spot = pd.DataFrame(minute_rows(DAY, lambda i: (PREV, PREV)))
    opts = FakeOptions(spot)
    opts.minutes = lambda s, d: None
    inst = E.instrument_for("NIFTY", opts, PARAMS["NIFTY"])
    c = cand("NIFTY", 5, 1)
    assert E.price(c, inst, market(), CFG, check_risk=False) == "NO_DATA"


def test_missing_strike_uses_nearest_strike_with_real_candles():
    spot = pd.DataFrame(minute_rows(DAY, lambda i: (PREV, PREV)))
    opts = FakeOptions(spot)
    inst = E.instrument_for("NIFTY", opts, PARAMS["NIFTY"])
    c = cand("NIFTY", 5, 1)
    first = E.price(c, inst, market(), CFG, check_risk=False)
    assert isinstance(first, E.Trade) and first.strike_shift == 0
    opts.missing = {first.symbol}  # the ~0.7 delta strike has no candles
    inst = E.instrument_for("NIFTY", opts, PARAMS["NIFTY"])
    moved = E.price(c, inst, market(), CFG, check_risk=False)
    assert isinstance(moved, E.Trade)
    assert moved.strike_shift == 1 and moved.strike == first.strike - inst.step  # deeper ITM call
    assert moved.delta > first.delta
    assert E.strike_shifts(2) == [0, 1, -1, 2, -2] and E.strike_shifts(0) == [0]


def test_one_index_data_gap_does_not_hide_the_other_verdict():
    res = R.Results(None, [], {}, 0, Counter(), ["BANKNIFTY: 12 of 100"], [], {"BANKNIFTY"})
    assert res.not_judged("BANKNIFTY") and res.not_judged("BOTH")
    assert not res.not_judged("NIFTY")


# ----------------------------------------------------------------------------- whole run
def test_run_all_smoke():
    rows = minute_rows(DAY, gap_down_fill, wick=0.5)
    spot = pd.DataFrame(rows)
    fut = spot.assign(volume=1000.0)[["time", "high", "low", "close", "volume"]]
    closes = closes_for(DAY)
    ix = {u: R.IndexInput(u, spot, fut, E.daily_closes(closes, spot), FakeOptions(spot))
          for u in ("NIFTY", "BANKNIFTY")}
    inp = R.Inputs(DAY, DAY, ix, market())
    res = R.run_all(inp)
    names = [r.name for r in res.rows]
    assert "NIFTY MR-4 full (A+B)" in names and "BOTH combined account + R29" in names
    full = next(r for r in res.rows if r.name == "NIFTY MR-4 full (A+B)")
    assert "## By index and year" in R.markdown(res)
    assert len(full.trades) == 1 and full.trades[0].setup == "A"
    both = next(r for r in res.rows if r.name == "BOTH combined account (R27-R30)")
    assert len(both.trades) == 1  # same minute, same direction: one index only (R27)
    text = R.markdown(res)
    assert "Verdict per index" in text and "FAIL" in text  # 1 trade is far below 60
    assert set(res.wf) == {"NIFTY", "BANKNIFTY"}


# ----------------------------------------------------------------------------- loader + database
def test_loader_fetches_the_legs_then_backtest_reads_them(session, monkeypatch):
    from app.db.models import MinuteFetch
    from app.ema921 import loader as L
    from app.meanrev import data as D
    from app.meanrev import loader as ML
    from tests.test_ema921_loader import FakeGroww

    start, end = date(2026, 10, 12), date(2026, 10, 16)
    days = [start + timedelta(days=k) for k in range(5)]

    def level(day):  # each day opens 0.5% below the last close and fills the gap by 10:00
        return PREV * (1.0 + 0.0002 * days.index(day)) if day in days else PREV

    class Groww(FakeGroww):
        def get_historical_candles(self, exchange, segment, groww_symbol, start_time, end_time,
                                   candle_interval):
            got = super().get_historical_candles(exchange, segment, groww_symbol, start_time,
                                                 end_time, candle_interval)
            day = date.fromisoformat(start_time[:10])
            out = []
            for i, r in enumerate(got["candles"]):
                if groww_symbol == "NSE-INDIAVIX":
                    p = 14.0
                else:
                    prev = level(day - timedelta(days=3 if day.weekday() == 0 else 1))
                    o = prev * 0.995 + i * (prev + 5 - prev * 0.995) / 45 if i < 45 else prev + 5
                    p = o if segment == "CASH" or groww_symbol.endswith("FUT") else None
                    if p is None:
                        strike, opt = L.parse_contract(groww_symbol)
                        p = max(0.0, (o - strike) if opt == "CE" else (strike - o)) + 60
                out.append([r[0], p, p + 0.5, p - 0.5, p, r[5], None])
            return {"candles": out}

    stored = closes_for(start, 260, PREV)
    monkeypatch.setattr(D, "stored_daily", lambda s, sym: stored if sym != "INDIAVIX"
                        else pd.Series(dtype=float))
    g = Groww()
    ML.load_all(session, g, start, end, log=lambda _: None)
    legs = [c for c in g.calls if c[0] == "candles" and c[1].endswith(("CE", "PE"))]
    assert legs, "the loader should fetch the option legs the rules trade"
    assert session.query(MinuteFetch).filter(MinuteFetch.groww_symbol == "NSE-INDIAVIX").count() > 0
    inp = D.load_inputs(session, start, end)
    assert set(inp.indices) == {"NIFTY", "BANKNIFTY"} and inp.market.vix_915
    res = R.run_all(inp)
    full = next(r for r in res.rows if r.name == "NIFTY MR-4 full (A+B)")
    assert full.trades and all(t.setup == "A" for t in full.trades)
    assert all(t.day.weekday() != 1 for t in full.trades)  # Tuesday = NIFTY expiry: no trade


def test_missing_futures_volume_makes_the_verdict_not_judged():
    spot = pd.DataFrame(minute_rows(DAY, gap_down_fill, wick=0.5))
    closes = closes_for(DAY)
    ix = {"NIFTY": R.IndexInput("NIFTY", spot, pd.DataFrame(), E.daily_closes(closes, spot),
                                FakeOptions(spot))}
    res = R.run_all(R.Inputs(DAY, DAY, ix, market()))
    assert res.data_problems and "futures volume" in res.data_problems[0]
    text = R.markdown(res)
    assert "NOT JUDGED" in text and "DATA INCOMPLETE" in text
