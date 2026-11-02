"""ORB-1.0 opening-range breakout and ET-1.0 EMA trend filter, on NIFTY and BANKNIFTY settings."""

from datetime import date, time, timedelta

import pandas as pd
import pytest

from app.core.market_time import IST, trading_days
from app.ema921 import bars as B
from app.ema921 import engine as E
from app.ema921 import loader as EL
from app.ema921 import report as R
from app.ema921 import store as S
from app.ema921 import trend as T
from app.ema921.metrics import passes_dev_bar
from app.orb import engine as O
from app.orb import loader as OL
from tests.test_ema921 import DAY, EXPIRY, WARM, FakeOptions, flat, spot_frame, up_then_down
from tests.test_ema921_loader import END, START, FakeGroww

HOL = frozenset()


def rise_after_range(i):
    """Flat 24500 for the range (09:15-09:29), then +2 points a minute until 11:00, then flat."""
    if i < 15:
        return 24500.0
    return 24500.0 + 2 * min(i - 15, 90)


def rise_then_collapse(i):
    """Breaks up at 09:35, then falls through the range low by 11:00."""
    if i < 15:
        return 24500.0
    if i < 30:
        return 24500.0 + 2 * (i - 15)
    return max(24400.0, 24530.0 - 3 * (i - 30))


def _orb(spot, modes=E.MODES, cfg=None):
    return O.run(spot, FakeOptions(spot), DAY, DAY, cfg, modes, holidays=HOL)


# ----------------------------------------------------------------------------- ORB-1.0
def test_opening_range_is_0915_to_0929():
    spot = spot_frame((DAY, rise_after_range))
    rng = O.opening_ranges(spot)[DAY]
    # Test candles have high = price + 1, low = price - 1; the 09:30 candle is not in the range.
    assert (rng.high, rng.low) == (24501.0, 24499.0)


def test_breakout_long_then_square_off():
    spot = spot_frame((WARM, flat), (DAY, rise_after_range))
    res = _orb(spot)
    for mode in E.MODES:
        (t,) = res.trades[mode]
        assert t.direction == 1 and t.exit_reason == "SQUARE_OFF"
        assert t.signal_time.time() == time(9, 35)  # first bar closing above 24501
        assert t.entry_time == t.signal_time
        assert t.exit_time.time() == time(15, 0)
    assert res.trades["ATM_SELL"][0].option_type == "PE"
    assert res.trades["ITM_BUY"][0].strike == E.atm_strike(res.trades["ITM_BUY"][0].nifty_entry) - 250


def test_range_stop_and_only_one_trade_a_day():
    spot = spot_frame((WARM, flat), (DAY, rise_then_collapse))
    res = _orb(spot, modes=("ATM_BUY",))
    (t,) = res.trades["ATM_BUY"]  # the later downside break does not open a second trade
    assert t.direction == 1 and t.exit_reason == "RANGE_STOP"
    assert t.nifty_exit < 24499.0
    assert t.exit_time.minute % 5 == 0 and t.exit_time > t.entry_time


def test_no_break_and_late_break_are_no_trade_days():
    spot = spot_frame((WARM, flat), (DAY, flat))
    assert _orb(spot).no_trade_days[DAY] == "NO_BREAK"

    def late(i):  # first break after 13:00
        return 24500.0 if i < 230 else 24600.0
    assert _orb(spot_frame((WARM, flat), (DAY, late))).no_trade_days[DAY] == "NO_BREAK"


def test_missing_option_data_skips_not_zero():
    spot = spot_frame((WARM, flat), (DAY, rise_after_range))
    sym = f"NSE-NIFTY-{EXPIRY:%d%b%y}-24500-CE"
    res = O.run(spot, FakeOptions(spot, missing={sym}), DAY, DAY, None, ("ATM_BUY",), HOL)
    assert res.trades["ATM_BUY"] == []
    assert res.skips[0].reason == "NO_DATA"
    assert res.no_trade_days[DAY].startswith("ENTRIES_SKIPPED")


def test_banknifty_settings():
    cfg = EL.config_for("BANKNIFTY")
    assert (cfg.lot_size, cfg.strike_step) == (30, 100)
    assert E.leg_for("ITM_BUY", 1, 52_340.0, cfg) == ("BUY", "CE", 51_800.0)  # ATM 52300 - 500
    spot = spot_frame((WARM, flat), (DAY, rise_after_range))
    t = O.run(spot, FakeOptions(spot), DAY, DAY, cfg, ("ATM_BUY",), HOL).trades["ATM_BUY"][0]
    assert t.strike % 100 == 0
    assert t.gross_pnl == pytest.approx((t.exit_price - t.entry_price) * 30, abs=0.01)


# ----------------------------------------------------------------------------- ET-1.0
def _cross(direction, spot_px, at=time(11, 0)):
    t = pd.Timestamp.combine(DAY, at).tz_localize(IST)
    return B.Cross(DAY, t - timedelta(minutes=5), t, direction, spot_px, 1)


def test_filter_needs_15m_trend_and_day_side():
    opens = {DAY: 24500.0}
    up, down, tie = (lambda t: 1), (lambda t: -1), (lambda t: 0)
    assert T.allowed(_cross(1, 24520), up, opens)
    assert not T.allowed(_cross(1, 24520), down, opens)  # against the 15m trend
    assert not T.allowed(_cross(1, 24480), up, opens)  # below the day's open
    assert not T.allowed(_cross(1, 24500), up, opens)  # a tie fails
    assert not T.allowed(_cross(1, 24520), tie, opens)
    assert T.allowed(_cross(-1, 24480), down, opens)
    assert not T.allowed(_cross(-1, 24480), down, {})  # no 09:15 candle: no entry


def test_trend_uses_the_last_closed_15m_bar_and_needs_warm_up():
    spot = spot_frame((WARM, flat), (DAY, up_then_down))
    bars15 = B.add_emas(B.resample(spot, 15))
    sign = T.trend_at(bars15)
    assert sign(bars15["bar_close"].iloc[5]) == 0  # fewer than 21 bars: blocked
    late = bars15[bars15["bar_close"] == pd.Timestamp.combine(DAY, time(11, 0)).tz_localize(IST)]
    assert sign(late["bar_close"].iloc[0] + timedelta(minutes=4)) == 1  # rising since 10:00


def test_filter_blocks_entries_but_never_exits():
    spot = spot_frame((WARM, flat), (DAY, up_then_down))
    plain = E.run(spot, FakeOptions(spot), DAY, DAY, E.Config(stop_atr_mult=None), ("ATM_BUY",),
                  holidays=HOL)
    cfg = E.Config(stop_atr_mult=None, trend_filter=True, cutoff=T.ENTRY_CUTOFF)
    res = E.run(spot, FakeOptions(spot), DAY, DAY, cfg, ("ATM_BUY",), holidays=HOL)
    blocked = T.blocked(spot, B.crosses(B.add_emas(B.resample(spot, 5))))
    kept = [t for t in plain.trades["ATM_BUY"] if pd.Timestamp(t.signal_time) not in blocked]
    assert len(res.trades["ATM_BUY"]) == len(kept) < len(plain.trades["ATM_BUY"])
    for t in res.trades["ATM_BUY"]:
        assert pd.Timestamp(t.signal_time) not in blocked
        assert t.exit_reason in ("OPPOSITE_CROSS", "SQUARE_OFF")
    if not res.trades["ATM_BUY"]:
        assert res.no_trade_days[DAY] == "TREND_FILTER"


def test_filter_off_by_default_keeps_the_paper_engine_unchanged():
    assert E.Config().trend_filter is False


# ----------------------------------------------------------------------------- shared
def test_pass_bar(monkeypatch):
    def t(day, net):
        return E.Trade(mode="ATM_BUY", day=day, direction=1, side="BUY", option_type="CE",
                       strike=24500, expiry=day, dte=1, symbol="x", cross_number=1,
                       signal_time=pd.Timestamp.combine(day, time(10)).to_pydatetime(),
                       entry_time=pd.Timestamp.combine(day, time(10)).to_pydatetime(),
                       entry_price=100, entry_fill=100, nifty_entry=24500, net_pnl=net)
    start = date(2024, 11, 1)
    good = [t(date(2025, 1, 2), 3000), t(date(2025, 1, 3), -1000),
            t(date(2026, 1, 2), 3000), t(date(2026, 1, 3), -1000)]
    ok, why = passes_dev_bar(good, 100_000, start)
    assert ok and "PF 3.0" in why
    one_bad_year = [t(date(2025, 1, 2), 9000), t(date(2026, 1, 2), -1000)]
    assert not passes_dev_bar(one_bad_year, 100_000, start)[0]
    deep = [t(date(2025, 1, 2), -60_000), t(date(2025, 1, 3), 70_000),
            t(date(2026, 1, 2), 1000)]
    assert not passes_dev_bar(deep, 100_000, start)[0]  # 60% drawdown
    assert passes_dev_bar([], 100_000, start) == (False, "no trades")


def test_report_names_each_result():
    spot = spot_frame((WARM, flat), (DAY, rise_then_collapse))
    res = _orb(spot)
    md = R.to_markdown([res], DAY, DAY, title="x", labels=["NIFTY ORB"])
    assert "| NIFTY ORB | ATM buy |" in md and "range stop" in md


class BankGroww(FakeGroww):
    """Prices around 24500 still, but BANKNIFTY symbols and 100-point strikes."""

    def get_contracts(self, exchange, underlying_symbol, expiry_date):
        assert underlying_symbol == "BANKNIFTY"
        e = date.fromisoformat(expiry_date)
        return {"contracts": [f"NSE-BANKNIFTY-{e:%d%b%y}-{k}-{t}" for k in range(23000, 26100, 100)
                              for t in ("CE", "PE")]}


def test_orb_loader_for_banknifty_then_backtest(session):
    g = BankGroww()
    rep = OL.load_all(session, g, START, END, "BANKNIFTY", log=lambda _: None)
    assert not rep.errors and rep.contracts_needed > 0
    assert ("candles", "NSE-BANKNIFTY", START.isoformat()) in g.calls
    assert not [c for c in g.calls if c[0] == "candles" and c[1].startswith("NSE-NIFTY")]
    spot = S.load_spot(session, START, END, 0, "BANKNIFTY")
    assert len(spot) == 375 * len(trading_days(START, END))
    assert S.load_spot(session, START, END, 0).empty  # NIFTY untouched
    res = O.run(spot, S.DbOptionData(session, "BANKNIFTY"), START, END, EL.config_for("BANKNIFTY"),
                holidays=HOL)
    trades = [t for m in E.MODES for t in res.trades[m]]
    assert trades and not res.skips
    assert all(t.symbol.startswith("NSE-BANKNIFTY-") and t.strike % 100 == 0 for t in trades)
