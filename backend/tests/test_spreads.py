"""SP-1.0 spreads (credit, debit, range-day iron condor) and TP-1.0 trend-pullback buying."""

from dataclasses import replace
from datetime import date, time

import pandas as pd
import pytest

from app.core.market_time import IST
from app.ema921 import engine as EE
from app.pullback import engine as TP
from app.pullback import loader as PL
from app.spreads import engine as E
from app.spreads import loader as SL
from app.spreads import report as R
from app.straddle import engine as ST
from tests import test_straddle as TS
from tests.test_ema921 import DAY, EXPIRY, WARM, FakeOptions, flat, spot_frame
from tests.test_orb_et import rise_after_range

HOL = frozenset()
CFG = EE.Config()


# ----------------------------------------------------------------------------- legs
def test_spread_legs_registered():
    # ATM 24500 for spot 24510.
    assert EE.leg_for(E.cs_wing(100), 1, 24510, CFG) == ("BUY", "PE", 24400)
    assert EE.leg_for(E.cs_wing(150), -1, 24510, CFG) == ("BUY", "CE", 24650)
    assert EE.leg_for(E.ds_short(100), 1, 24510, CFG) == ("SELL", "CE", 24600)
    assert EE.leg_for(E.ds_short(150), -1, 24510, CFG) == ("SELL", "PE", 24350)
    assert EE.leg_for("ITM1_BUY", 1, 24510, CFG) == ("BUY", "CE", 24450)
    assert EE.leg_for("ITM1_BUY", -1, 24510, CFG) == ("BUY", "PE", 24550)
    # the original modes are unchanged
    assert EE.leg_for("ATM_SELL", 1, 24510, CFG) == ("SELL", "PE", 24500)


# ----------------------------------------------------------------------------- debit spread (ORB)
def test_debit_spread_pairs_both_legs_and_adds_costs():
    spot = spot_frame((WARM, flat), (DAY, rise_after_range))
    out = E.run_debit(spot, FakeOptions(spot), DAY, DAY, CFG, holidays=HOL)
    (p,) = out["DS-100"]
    long_leg, short_leg = p.legs
    assert (long_leg.side, long_leg.option_type, long_leg.strike) == ("BUY", "CE", 24500)
    assert (short_leg.side, short_leg.option_type, short_leg.strike) == ("SELL", "CE", 24600)
    assert long_leg.exit_time == short_leg.exit_time  # one position, one exit
    assert p.net_pnl == pytest.approx(long_leg.net_pnl + short_leg.net_pnl, abs=0.01)
    assert p.friction > long_leg.friction  # the second leg's costs count too
    assert len(out["ORB ATM buy (single leg)"]) == 1


def test_a_leg_without_data_drops_the_whole_spread():
    spot = spot_frame((WARM, flat), (DAY, rise_after_range))
    opts = FakeOptions(spot, missing={f"NSE-NIFTY-{EXPIRY:%d%b%y}-24600-CE"})
    out = E.run_debit(spot, opts, DAY, DAY, CFG, holidays=HOL)
    assert out["DS-100"] == [] and len(out["DS-150"]) == 1


def test_volume_filter_blocks_the_debit_spread():
    spot = spot_frame((WARM, flat), (DAY, rise_after_range))
    out = E.run_debit(spot, FakeOptions(spot), DAY, DAY, CFG, entry_filter=lambda c: False,
                      holidays=HOL, suffix=" VF")
    assert out["DS-100 VF"] == [] and out["DS-150 VF"] == []


def test_expiry_day_flag():
    E.set_expiry_days([DAY])
    p = E.Position("x", DAY, EXPIRY, None, 1)
    assert p.expiry_day
    E.set_expiry_days([EXPIRY])
    assert not p.expiry_day


# ----------------------------------------------------------------------------- iron condor
def test_condor_strikes_entry_and_wing_closes_with_its_stopped_side():
    # Spot 24510 -> ATM 24500. Short CE 24650 rises 1 point a minute from 40 after 10:00:
    # stop at 1.5 x entry = 60, reached on the 1m high (close + 1) at minute 45 + 19.
    m1000 = 45
    paths = {"24650CE": lambda i: 40.0 + max(0, i - m1000), "24350PE": lambda i: 40.0,
             "24850CE": lambda i: 10.0, "24150PE": lambda i: 10.0}
    cfg = replace(E.condor_config(), slippage_pct=0, slippage_points=0)
    t, why = ST.run_day(TS.DAY, TS.spot_day(), TS.Opts(paths), cfg, [TS.EXPIRY])
    assert why is None
    legs = {x.role: x for x in t.legs}
    assert legs["SHORT_CE"].strike == 24650 and legs["SHORT_PE"].strike == 24350
    assert legs["WING_CE"].strike == 24850 and legs["WING_PE"].strike == 24150
    assert legs["SHORT_CE"].entry_time.astimezone(IST).time() == time(10, 0)
    ce = legs["SHORT_CE"]
    assert ce.exit_reason == "STOP" and ce.exit_price == 60
    assert ce.exit_time.astimezone(IST).time() == time(10, 19)
    wing = legs["WING_CE"]
    assert wing.exit_reason == "STOP" and wing.exit_time.astimezone(IST).time() == time(10, 20)
    assert legs["SHORT_PE"].exit_reason == "TIME" and legs["WING_PE"].exit_reason == "TIME"
    assert legs["WING_PE"].exit_time.astimezone(IST).time() == time(15, 15)


def test_straddle_default_is_unchanged():
    assert [x[3] for x in ST.legs_for(24500, ST.Config())] == [24500, 24500, 25000, 24000]


def _range_spot(days_widths):
    frames = []
    for d, w in days_widths:
        def path(i, w=w):
            return 24500.0 + (w if i == 10 else 0)
        frames.append((d, path))
    return spot_frame(*frames)


def test_range_day_needs_20_days_and_below_median():
    ranges = {date(2026, 9, d): 0.5 for d in range(1, 21)}
    assert E.range_day_status(date(2026, 9, 10), ranges) == "RANGE_WARMUP"
    ranges[date(2026, 10, 1)] = 0.4
    assert E.range_day_status(date(2026, 10, 1), ranges) is None
    ranges[date(2026, 10, 1)] = 0.5  # equal to the median is not narrow
    assert E.range_day_status(date(2026, 10, 1), ranges) == "WIDE_OPEN"
    assert E.range_day_status(date(2026, 10, 2), ranges) == "NO_RANGE_DATA"


def test_opening_range_pct_uses_0915_to_0959():
    spot = _range_spot([(DAY, 49.0)])  # a spike at 09:25; test candles have high/low = +-1
    r = E.opening_ranges_pct(spot)[DAY]
    assert r == pytest.approx((24500 + 49 + 1 - 24499) / 24500 * 100)


def test_vix_calm():
    days = pd.bdate_range("2026-09-01", "2026-10-12").date
    calm = pd.Series([15.0] * (len(days) - 1) + [14.0], index=list(days))
    assert E.vix_calm_status(DAY, calm) is None
    high = pd.Series([14.0] * (len(days) - 1) + [15.0], index=list(days))  # +7.1%: no jump
    assert E.vix_calm_status(DAY, high) == "VIX_HIGH"
    assert E.vix_calm_status(DAY, None) == "NO_VIX"


# ----------------------------------------------------------------------------- scoring
def _pos(day, net):
    class Leg:
        gross_pnl = net
        slippage = 0.0
        charges = {"total": 0.0}
        net_pnl = net
    return E.Position("x", day, EXPIRY, None, 1, [Leg()])


def test_pass_bar_needs_trades_both_years_and_pf():
    start = date(2024, 11, 5)
    good = [_pos(date(2025, 1, 2), 100.0)] * 30 + [_pos(date(2026, 1, 2), 100.0)] * 30 + \
        [_pos(date(2026, 2, 2), -50.0)] * 5
    assert E.passes(good, 100_000, start, 50)
    assert not E.passes(good[:40], 100_000, start, 50)  # under 50 trades
    one_year = [_pos(date(2025, 1, 2), 100.0)] * 60 + [_pos(date(2026, 1, 2), -10.0)]
    assert not E.passes(one_year, 100_000, start, 50)  # year 2 lost


# ----------------------------------------------------------------------------- TP-1.0
def pullback_path(i):
    """Range 24500, breakout from 09:30, pullback to EMA 9 at 10:15, rally after."""
    if i < 15:
        return 24500.0
    if i < 45:
        return 24500.0 + 2 * (i - 15)
    if i < 55:
        return 24560.0 - 2.5 * (i - 45)
    if i < 120:
        return 24535.0 + 1.5 * (i - 55)
    return 24632.5


def fut_from(spot, volume=1000.0):
    return spot.assign(close=spot["close"] + 20, high=spot["high"] + 20, low=spot["low"] + 20,
                       volume=volume)[["time", "high", "low", "close", "volume"]]


def test_futures_vwap():
    spot = spot_frame((DAY, flat))
    fb = TP.futures_bars(fut_from(spot))
    bar = fb[pd.Timestamp("2026-10-13 09:20", tz=IST)]
    assert bar.vwap == pytest.approx(24520.0) and bar.close == 24520.0
    gap = fut_from(spot)
    gap.loc[7, "volume"] = float("nan")  # 09:22 has no volume: VWAP unknown from then on
    fb = TP.futures_bars(gap)
    assert fb[pd.Timestamp("2026-10-13 09:20", tz=IST)].vwap is not None
    assert fb[pd.Timestamp("2026-10-13 10:00", tz=IST)].vwap is None


def test_pullback_long_trade_hits_target():
    spot = spot_frame((WARM, flat), (DAY, pullback_path))
    res = TP.run(spot, fut_from(spot), FakeOptions(spot), DAY, DAY, holidays=HOL)
    (atm,) = res.trades["ATM_BUY"]
    (itm,) = res.trades["ITM1_BUY"]
    assert atm.option_type == "CE" and atm.strike == 24550 and itm.strike == 24500
    assert atm.signal_time.time() == time(10, 20)  # green bar after the 10:15 pullback bar
    assert atm.exit_reason == "TARGET" and atm.exit_time.time() == time(10, 35)


def test_pullback_needs_vwap_and_skips_expiry_days():
    spot = spot_frame((WARM, flat), (DAY, pullback_path))
    res = TP.run(spot, fut_from(spot, volume=float("nan")), FakeOptions(spot), DAY, DAY,
                 holidays=HOL)
    assert res.trades["ATM_BUY"] == []
    res = TP.run(spot, fut_from(spot), FakeOptions(spot, expiries=(DAY, EXPIRY)), DAY, DAY,
                 holidays=HOL)
    assert res.trades["ATM_BUY"] == [] and res.no_trade_days[DAY] == "EXPIRY_DAY"


def test_risk_limit_skips_big_stops():
    spot = spot_frame((WARM, flat), (DAY, pullback_path))
    # stop distance 14.5 points: ATM 14.5 x 0.5 x 65 = ₹471 (taken), 1-ITM x 0.6 = ₹566 (skipped)
    res = TP.run(spot, fut_from(spot), FakeOptions(spot), DAY, DAY, limits=True, holidays=HOL)
    assert len(res.trades["ATM_BUY"]) == 1 and res.trades["ITM1_BUY"] == []
    assert any(s.reason == "RISK_TOO_BIG" for s in res.skips)


def test_tp_loader_asks_only_for_missing_legs():
    spot = spot_frame((WARM, flat), (DAY, pullback_path))

    class Db:
        def expiries(self):
            return [EXPIRY]

        def contract(self, e, k, t):
            return None

        def minutes(self, s, d):
            return None

    rec = PL.Recorder(Db())
    TP.run(spot, fut_from(spot), rec, DAY, DAY, holidays=HOL)
    assert (DAY, EXPIRY, 24550.0, "CE") in rec.asked and (DAY, EXPIRY, 24500.0, "CE") in rec.asked


def test_spread_loader_lists_condor_and_orb_legs():
    spot = spot_frame((WARM, flat), (DAY, rise_after_range))
    legs = SL.needed_legs(spot, DAY, DAY, [EXPIRY], CFG)
    have = {(k, t) for _, _, k, t in legs}
    assert {(24500.0, "CE"), (24600.0, "CE"), (24650.0, "CE")} <= have  # ORB ATM + DS shorts
    # condor from the 09:59 close (24500 + 2 x 30 = 24560 -> ATM 24550)
    assert {(24700.0, "CE"), (24400.0, "PE"), (24900.0, "CE"), (24200.0, "PE")} <= have


def test_report_renders():
    spot = spot_frame((WARM, flat), (DAY, rise_after_range))
    rows = [R.Row(n, "DS-1.0", ps, True) for n, ps in
            E.run_debit(spot, FakeOptions(spot), DAY, DAY, CFG, holidays=HOL).items()]
    md = R.to_markdown(rows, {"coverage": 1.0}, DAY, DAY, 100_000, True, "t")
    assert "DS-100" in md and "fail" in md


# ----------------------------------------------------------------------------- RL-1.0 limits
def test_day_and_week_limits_stop_new_entries():
    from app.spreads import risk as RK

    d1, d2, d3 = date(2026, 10, 12), date(2026, 10, 13), date(2026, 10, 14)  # same week
    ps = [_pos(d1, -1500.0), _pos(d1, -800.0), _pos(d1, 300.0),  # 3rd skipped: day loss 2,300
          _pos(d2, -2500.0), _pos(d2, 100.0),                     # 2nd skipped: day loss 2,500
          _pos(d3, 700.0)]                                         # week loss 4,800 < 5,000: taken
    kept, skipped = RK.apply_day_week(ps, RK.Level("t", None, None, 2000, 5000, None))
    assert [p.net_pnl for p in kept] == [-1500.0, -800.0, -2500.0, 700.0] and skipped == 2
    kept, skipped = RK.apply_day_week(ps, RK.Level("t", None, None, 2000, 4000, None))
    assert [p.net_pnl for p in kept] == [-1500.0, -800.0, -2500.0] and skipped == 3
    assert RK.worst_period(ps, weekly=False) == -2400.0 and RK.over(ps, 1000) == 2


def test_tp_custom_per_trade_limit():
    spot = spot_frame((WARM, flat), (DAY, pullback_path))
    lim = TP.Limits(per_trade=1000, day=2000, week=5000)  # 1-ITM risk ₹566 now fits
    res = TP.run(spot, fut_from(spot), FakeOptions(spot), DAY, DAY, limits=lim, holidays=HOL)
    assert len(res.trades["ITM1_BUY"]) == 1


def test_day_profit_lock():
    from app.spreads import risk as RK

    d = date(2026, 10, 12)
    ps = [_pos(d, 3000.0), _pos(d, 1500.0), _pos(d, -900.0)]  # 3rd skipped: day up 4,500
    kept, skipped = RK.apply_day_week(ps, RK.Level("t", None, None, 2000, 5000, 4000))
    assert len(kept) == 2 and skipped == 1


def _sell_trade(path):
    """An ATM sell at 10:00 on DAY held to 15:00, with option price path(i) (i = minute)."""
    rows = []
    for i in range(375):
        t = pd.Timestamp(f"{DAY} 09:15", tz=IST) + pd.Timedelta(minutes=i)
        p = path(i)
        rows.append({"time": t.tz_convert("UTC"), "open": p, "high": p + 1, "low": p - 1, "close": p})
    m = EE._Minutes(pd.DataFrame(rows))
    entry = pd.Timestamp(f"{DAY} 10:00", tz=IST)
    tr = EE.Trade("ATM_SELL", DAY, 1, "SELL", "PE", 24500, EXPIRY, 7, "S", 1, entry.to_pydatetime(),
                  entry.to_pydatetime(), path(45), path(45), 24500)
    tr.exit_time = pd.Timestamp(f"{DAY} 15:00", tz=IST).to_pydatetime()
    return tr, m


def test_premium_stop_and_target_on_a_sell():
    from app.spreads import risk as RK

    cfg = replace(CFG, slippage_pct=0, slippage_points=0)
    # stop ₹1,300 = 20 points above 100; target ₹1,950 = 30 points below
    up = lambda i: 100.0 + max(0, i - 45)  # noqa: E731
    tr, m = _sell_trade(up)
    out = RK.stop_target(tr, m, 1300, 1950, cfg)
    assert out.exit_reason == "STOP" and out.exit_price == 120
    assert out.gross_pnl == pytest.approx(-1300) and tr.exit_reason is None  # original untouched
    down = lambda i: 100.0 - max(0, i - 45)  # noqa: E731
    tr, m = _sell_trade(down)
    out = RK.stop_target(tr, m, 1300, 1950, cfg)
    assert out.exit_reason == "TARGET" and out.exit_price == 70 and out.gross_pnl == pytest.approx(1950)
    flat_px = lambda i: 100.0  # noqa: E731
    tr, m = _sell_trade(flat_px)
    assert RK.stop_target(tr, m, 1300, 1950, cfg) is tr  # neither hit: unchanged


def test_trailing_stop_locks_profit_on_a_sell():
    from app.spreads import risk as RK

    cfg = replace(CFG, slippage_pct=0, slippage_points=0)
    # R = ₹1,300 = 20 points. Premium falls 100 -> 60 by minute 85, then rises again.
    def path(i):
        if i <= 45:
            return 100.0
        if i <= 85:
            return 100.0 - (i - 45)
        return 60.0 + (i - 85)
    tr, m = _sell_trade(path)
    out = RK.trailing(tr, m, 1300, cfg)
    # lowest low 59 (close 60 - 1) -> stop 79; the high (close + 1) reaches 79 at close 78
    assert out.exit_reason == "TRAIL_STOP" and out.exit_price == 79
    assert out.gross_pnl == pytest.approx((100 - 79) * 65)
    # straight loss: the entry candle's low 99 tightens the stop to 119 (still a loss = STOP)
    tr, m = _sell_trade(lambda i: 100.0 + max(0, i - 45))
    out = RK.trailing(tr, m, 1300, cfg)
    assert out.exit_reason == "STOP" and out.exit_price == 119
