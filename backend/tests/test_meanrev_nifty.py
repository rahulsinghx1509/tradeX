"""MR-4N: NIFTY gap-fill variants, futures pricing, leave-one-year-out pick."""

from __future__ import annotations

from collections import Counter
from datetime import date

import pandas as pd

from app.ema921 import engine as EE
from app.meanrev import engine as E
from app.meanrev import nifty as N
from app.meanrev import report as R
from tests.test_meanrev import DAY, PREV, FakeOptions, closes_for, gap_down_fill, market, minute_rows


def _inputs():
    spot = pd.DataFrame(minute_rows(DAY, gap_down_fill, wick=0.5))
    fut = spot.assign(volume=1000.0)
    ix = {"NIFTY": R.IndexInput("NIFTY", spot, fut, E.daily_closes(closes_for(DAY), spot),
                                FakeOptions(spot))}
    return R.Inputs(DAY, DAY, ix, market()), spot, fut


def test_gap_variants_filter_by_size_and_atr():
    inp, spot, fut = _inputs()  # gap = -0.5%
    rows = N.run_rows(inp, {DAY: 0.8}, N.fut_minutes_by_day(fut))
    got = {(r.variant, r.instrument): len(r.trades) for r in rows}
    assert got[("G0.3", "OPT")] == 1 and got[("G0.5", "OPT")] == 1
    assert got[("G0.75", "OPT")] == 0  # 0.5% gap is too small
    assert got[("ATR0.5", "OPT")] == 1  # 0.5% >= 0.5 x 0.8%
    rows = N.run_rows(inp, {DAY: 1.2}, None)  # 0.5% < 0.5 x 1.2%; no futures -> no FUT rows
    assert {(r.variant, r.instrument): len(r.trades) for r in rows}[("ATR0.5", "OPT")] == 0
    assert all(r.instrument == "OPT" for r in rows)


def test_futures_trade_pnl_slippage_and_costs():
    inp, spot, fut = _inputs()
    rows = N.run_rows(inp, {}, N.fut_minutes_by_day(fut))
    t = next(r for r in rows if r.variant == "G0.3" and r.instrument == "FUT").trades[0]
    assert t.option_type == "FUT" and t.direction == 1 and t.qty == 32.5  # half size, no bias
    f = t.exits[0]
    assert t.entry_fill == t.entry_price + N.FUT_SLIPPAGE_POINTS
    assert f.fill == f.price - N.FUT_SLIPPAGE_POINTS
    assert t.gross_pnl == round((f.price - t.entry_price) * t.qty, 2) and t.gross_pnl > 0
    assert t.slippage == round(2 * N.FUT_SLIPPAGE_POINTS * t.qty, 2)
    assert 200 < t.charges < 320  # ₹40 brokerage + STT 0.02% of ~₹8L sold + exchange, stamp, GST
    assert t.net_pnl == round(t.gross_pnl - t.slippage - t.charges, 2)


def test_atr_uses_only_earlier_days():
    idx = pd.bdate_range("2026-01-01", periods=30)
    daily = pd.DataFrame({"high": 101.0, "low": 99.0, "close": 100.0}, index=idx)
    daily.iloc[-1] = [200.0, 50.0, 100.0]  # a huge last day must not change its own ATR
    a = N.atr_pct_before(daily)
    last = idx[-1].date()
    assert abs(a[last] - 2.0) < 1e-9


def _trade(day, net):
    return E.Trade("NIFTY", "A", day, 1, "x", 1.0, 65, "CE", 0.0, day, "s", 0.7, 0.1, "ATM",
                   pd.Timestamp(day).to_pydatetime(), 1.0, 1.0, 1.0, 1.0, [], net_pnl=net)


def test_leave_one_year_out_picks_on_the_other_years():
    start = date(2023, 11, 1)
    days = [date(2023, 12, 1), date(2024, 12, 1), date(2025, 12, 1)]
    good = [_trade(d, 100.0 if i % 3 else -50.0) for d in days for i in range(30)]
    bad = [_trade(d, -10.0) for d in days for i in range(30)]
    rows = [N.NRow("G0.3", "OPT", bad, Counter()), N.NRow("G0.5", "OPT", good, Counter())]
    picks = N.leave_one_year_out(rows, "OPT", start)
    assert [p.variant for p in picks] == ["G0.5"] * 3
    assert all(len(p.test_trades) == 30 for p in picks)
    s, ok, failed = N.oos_verdict(picks, start)
    assert s.trades == 90 and ok and not failed


def test_fut_minutes_need_an_open_column():
    assert N.fut_minutes_by_day(pd.DataFrame({"time": [], "close": []})) == {}
    assert isinstance(N.fut_minutes_by_day(_inputs()[2])[DAY], EE._Minutes)
    assert PREV > 0
