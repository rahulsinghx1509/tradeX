"""9/21 EMA options backtest: bars, crosses, contract choice, fills, costs, trades, metrics."""

from datetime import date, datetime, time, timedelta

import pandas as pd
import pytest

from app.core.market_time import IST
from app.ema921 import bars as B
from app.ema921 import costs as C
from app.ema921 import engine as E
from app.ema921.metrics import summarize

WARM = date(2026, 10, 12)  # Monday: warm-up day, never traded
DAY = date(2026, 10, 13)  # Tuesday: the tested day
EXPIRY = date(2026, 10, 20)
HOLIDAYS = frozenset()


def minutes_for(day: date, price_at, skip: set[int] = frozenset()) -> list[dict]:
    rows = []
    for i in range(B.SESSION_MINUTES):
        if i in skip:
            continue
        t = pd.Timestamp(datetime.combine(day, time(9, 15)), tz=IST) + timedelta(minutes=i)
        p = price_at(i)
        rows.append({"time": t.tz_convert("UTC"), "open": p, "high": p + 1, "low": p - 1, "close": p})
    return rows


def flat(i):
    return 24500.0


def up_then_down(i):
    """Flat to 10:00, up 100 points by 11:00, down 300 by 13:00, flat after."""
    if i < 45:
        return 24500.0
    if i < 105:
        return 24500.0 + (i - 45) * 100 / 60
    if i < 225:
        return 24600.0 - (i - 105) * 300 / 120
    return 24300.0


def spot_frame(*days_and_paths, skip=None):
    rows = []
    for d, f in days_and_paths:
        rows += minutes_for(d, f, (skip or {}).get(d, set()))
    return pd.DataFrame(rows)


class FakeOptions:
    """Option price = intrinsic value + 100, moving minute by minute with the spot."""

    def __init__(self, spot: pd.DataFrame, expiries=(EXPIRY,), missing: set[str] = frozenset(),
                 cut_after: dict[str, int] | None = None):
        self.spot, self._exp, self.missing = spot, list(expiries), missing
        self.cut_after = cut_after or {}
        self.calls = []

    def expiries(self):
        return self._exp

    def contract(self, expiry, strike, opt):
        return f"NSE-NIFTY-{expiry:%d%b%y}-{strike:g}-{opt}"

    def minutes(self, symbol, day):
        self.calls.append(symbol)
        if symbol in self.missing:
            return None
        strike, opt = float(symbol.split("-")[-2]), symbol.split("-")[-1]
        s = self.spot.copy()
        ist = pd.to_datetime(s["time"], utc=True).dt.tz_convert(IST)
        s = s[ist.dt.date == day].copy()
        if symbol in self.cut_after:
            s = s.iloc[: self.cut_after[symbol]]
        val = (s["close"] - strike).clip(lower=0) if opt == "CE" else (strike - s["close"]).clip(lower=0)
        p = val + 100
        return pd.DataFrame({"time": s["time"], "open": p, "high": p + 2, "low": p - 2, "close": p})


# ----------------------------------------------------------------------------- bars
def test_resample_aligns_to_0915_and_counts_bars():
    spot = spot_frame((DAY, flat))
    five = B.resample(spot, 5)
    assert len(five) == 75
    assert five["bar_start"].iloc[0].time() == time(9, 15)
    assert five["bar_close"].iloc[-1].time() == time(15, 30)
    thirty = B.resample(spot, 30)
    assert len(thirty) == 13
    assert thirty["expected"].iloc[-1] == 15  # 15:15-15:30 is only 15 minutes
    assert (thirty["minutes"] == thirty["expected"]).all()


def test_resample_ohlc_of_a_bar():
    spot = spot_frame((DAY, lambda i: 100.0 + i))
    first = B.resample(spot, 15).iloc[0]
    assert (first["open"], first["close"], first["high"], first["low"]) == (100, 114, 115, 99)


def test_ema_seeded_with_first_close():
    bars = pd.DataFrame({"close": [10.0, 20.0]})
    out = B.add_emas(bars)
    assert out["ema9"].iloc[0] == 10.0
    assert out["ema9"].iloc[1] == pytest.approx(10 + 0.2 * 10)


def _bars_from_diffs(diffs):
    """Bars whose EMA difference is given directly (EMAs pre-filled)."""
    n = len(diffs)
    start = pd.Timestamp(datetime.combine(DAY, time(9, 15)), tz=IST)
    return pd.DataFrame({
        "day": [DAY] * n, "bar_start": [start + timedelta(minutes=5 * i) for i in range(n)],
        "bar_close": [start + timedelta(minutes=5 * i + 5) for i in range(n)],
        "close": [100.0] * n, "ema9": [100.0 + d for d in diffs], "ema21": [100.0] * n})


def test_ties_are_not_crosses_and_warm_up_is_skipped():
    warm = [-1.0] * 21
    # 0 then + is a bullish cross (from <= 0); + then 0 then + is no cross.
    got = B.crosses(_bars_from_diffs(warm + [0.0, 1.0, 0.0, 1.0, -1.0]))
    assert [(c.direction, c.number) for c in got] == [(1, 1), (-1, 2)]
    # A cross inside the first 21 bars never counts.
    assert B.crosses(_bars_from_diffs([-1, 1] + [1] * 20)) == []


def test_complete_days_counts_missing_minutes():
    spot = spot_frame((DAY, flat), skip={DAY: {10, 11}})
    assert B.complete_days(spot) == {DAY: 2}


# ----------------------------------------------------------------------------- contracts
def test_atm_strike_rounds_half_up():
    assert E.atm_strike(24524.99) == 24500
    assert E.atm_strike(24525) == 24550


@pytest.mark.parametrize("mode,direction,expected", [
    ("ITM_BUY", 1, ("BUY", "CE", 24250)), ("ITM_BUY", -1, ("BUY", "PE", 24750)),
    ("ATM_BUY", 1, ("BUY", "CE", 24500)), ("ATM_BUY", -1, ("BUY", "PE", 24500)),
    ("ATM_SELL", 1, ("SELL", "PE", 24500)), ("ATM_SELL", -1, ("SELL", "CE", 24500)),
])
def test_leg_for_each_mode(mode, direction, expected):
    assert E.leg_for(mode, direction, 24510, E.Config()) == expected


def test_expiry_rolls_on_expiry_day():
    ex = [date(2026, 10, 13), date(2026, 10, 20)]
    assert E.pick_expiry(date(2026, 10, 12), ex, roll=True) == date(2026, 10, 13)
    assert E.pick_expiry(date(2026, 10, 13), ex, roll=True) == date(2026, 10, 20)
    assert E.pick_expiry(date(2026, 10, 13), ex, roll=False) == date(2026, 10, 13)
    assert E.pick_expiry(date(2026, 10, 21), ex, roll=True) is None


# ----------------------------------------------------------------------------- costs
def test_slippage_fills_round_against_the_trader():
    assert C.buy_fill(100, 0.005, 0.5) == 101.0
    assert C.buy_fill(100.01, 0.005, 0.5) == 101.05
    assert C.sell_fill(100, 0.005, 0.5) == 99.0
    assert C.sell_fill(0.3, 0.005, 0.5) == 0.05  # never below the tick


def test_charges_for_a_round_trip():
    c = C.charges(buy_value=10_000, sell_value=11_000, day=date(2026, 1, 5))
    assert c["brokerage"] == 40
    assert c["stt"] == 11.0  # 0.1% of the sell side
    assert c["exchange"] == round(21_000 * 0.0003503, 2)
    assert c["stamp"] == 0.3
    assert c["gst"] == round((40 + 21_000 * 0.0003503 + 21_000 * 10 / 1e7) * 0.18, 2)
    assert c["total"] == round(sum(v for k, v in c.items() if k != "total"), 2)
    assert C.rates_for(date(2024, 9, 30)).stt_sell == 0.000625


# ----------------------------------------------------------------------------- the engine
def _run(spot, opts, cfg=None, modes=E.MODES):
    return E.run(spot, opts, DAY, DAY, cfg or E.Config(), modes, holidays=HOLIDAYS)


def test_round_trip_with_reversal_and_square_off():
    spot = spot_frame((WARM, flat), (DAY, up_then_down))
    res = _run(spot, FakeOptions(spot), E.Config(timeframe=5))
    atm = res.trades["ATM_BUY"]
    assert [t.direction for t in atm] == [1, -1]
    long, short = atm
    assert long.option_type == "CE" and short.option_type == "PE"
    assert long.exit_reason == "OPPOSITE_CROSS" and short.exit_reason == "SQUARE_OFF"
    # Entry fill is the option's open in the minute the signal bar closes (#1).
    assert long.entry_time == long.signal_time
    assert long.entry_time.time() >= time(10, 0)
    # Reversal: the short opens at the same moment the long closes.
    assert short.entry_time == long.exit_time
    assert short.exit_time.time() == time(15, 0)
    assert long.expiry == EXPIRY and long.dte == 7
    assert res.no_trade_days == {}
    # Warm-up day is never traded.
    assert all(t.day == DAY for m in E.MODES for t in res.trades[m])


def test_pnl_parts_add_up():
    spot = spot_frame((WARM, flat), (DAY, up_then_down))
    res = _run(spot, FakeOptions(spot))
    for mode in E.MODES:
        for t in res.trades[mode]:
            assert t.net_pnl == pytest.approx(t.gross_pnl - t.slippage - t.charges["total"], abs=0.02)
            assert t.slippage > 0
    sell = res.trades["ATM_SELL"][0]
    assert sell.side == "SELL" and sell.option_type == "PE"  # bullish -> sell the put
    assert sell.mae is not None and sell.mae <= 0


def test_cutoff_blocks_entries_but_not_exits():
    spot = spot_frame((WARM, flat), (DAY, up_then_down))
    res = _run(spot, FakeOptions(spot), E.Config(cutoff=time(11, 30)), modes=("ATM_BUY",))
    trades = res.trades["ATM_BUY"]
    assert len(trades) == 1  # the bearish cross after 11:30 only closes the long
    assert trades[0].exit_reason == "OPPOSITE_CROSS"


def test_cross_number_filter_applies_to_entries_only():
    spot = spot_frame((WARM, flat), (DAY, up_then_down))
    res = _run(spot, FakeOptions(spot), E.Config(cross_numbers=frozenset({2})), modes=("ATM_BUY",))
    assert [t.cross_number for t in res.trades["ATM_BUY"]] == [2]


def test_missing_option_data_skips_the_entry():
    spot = spot_frame((WARM, flat), (DAY, up_then_down))
    sym = "NSE-NIFTY-20Oct26-24500-CE"
    res = _run(spot, FakeOptions(spot, missing={sym}), modes=("ATM_BUY",))
    assert [s.reason for s in res.skips] == ["NO_DATA"]
    assert [t.option_type for t in res.trades["ATM_BUY"]] == ["PE"]


def test_missing_exit_price_uses_last_known_and_flags_it():
    spot = spot_frame((WARM, flat), (DAY, up_then_down))
    res = _run(spot, FakeOptions(spot, cut_after={"NSE-NIFTY-20Oct26-24500-PE": 300}),
               modes=("ATM_BUY",))
    short = res.trades["ATM_BUY"][-1]
    assert short.stale_exit
    assert short.exit_time < datetime.combine(DAY, time(15, 0), tzinfo=IST)


def test_no_expiry_listed_is_a_skip_not_a_zero():
    spot = spot_frame((WARM, flat), (DAY, up_then_down))
    res = _run(spot, FakeOptions(spot, expiries=[date(2026, 10, 6)]), modes=("ATM_BUY",))
    assert res.trades["ATM_BUY"] == []
    assert {s.reason for s in res.skips} == {"NO_EXPIRY"}
    assert res.no_trade_days[DAY].startswith("ENTRIES_SKIPPED")


def test_gap_in_spot_data_makes_a_no_trade_day():
    spot = spot_frame((WARM, flat), (DAY, up_then_down), skip={DAY: {100}})
    res = _run(spot, FakeOptions(spot))
    assert res.no_trade_days[DAY].startswith("SPOT_GAP")
    assert all(not v for v in res.trades.values())


def test_flat_day_has_no_cross():
    spot = spot_frame((WARM, flat), (DAY, flat))
    res = _run(spot, FakeOptions(spot))
    assert res.no_trade_days == {DAY: "NO_CROSS"}


def test_holiday_is_listed():
    hol = frozenset({DAY})
    spot = spot_frame((WARM, flat))
    res = E.run(spot, FakeOptions(spot), DAY, DAY, E.Config(), holidays=hol)
    assert res.no_trade_days == {DAY: "HOLIDAY"}


# ----------------------------------------------------------------------------- metrics
def _t(net, hour):
    t = datetime.combine(DAY, time(hour), tzinfo=IST)
    return E.Trade(mode="ATM_BUY", day=DAY, direction=1, side="BUY", option_type="CE", strike=1,
                   expiry=EXPIRY, dte=7, symbol="x", cross_number=1, signal_time=t, entry_time=t,
                   entry_price=1, entry_fill=1, nifty_entry=1, net_pnl=net, gross_pnl=net)


def test_summary_numbers():
    s = summarize([_t(1000, 10), _t(-500, 11), _t(-500, 12), _t(2000, 13), _t(0, 14)], 100_000)
    assert s.trades == 5 and s.wins == 2 and s.win_rate == 40.0
    assert s.net_pnl == 2000
    assert s.profit_factor == 3.0
    assert s.payoff_ratio == round(1500 / (1000 / 3), 2)
    assert s.expectancy == 400
    assert s.max_drawdown == 1000
    assert s.max_drawdown_pct == round(1000 / 101_000 * 100, 2)


def test_summary_of_nothing():
    s = summarize([], 100_000)
    assert s.trades == 0 and s.win_rate is None and s.profit_factor is None
