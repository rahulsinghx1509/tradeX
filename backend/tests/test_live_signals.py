"""Multi-strategy PAPER signal engine: numbered trades, live = backtest, messages, safety."""

import inspect
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.market_time import IST
from app.db.models import SignalTrade
from app.ema921 import engine as E
from app.ema921 import loader as L
from app.ema921 import store as S
from app.live import engine as LE
from app.orb import engine as O
from tests.test_ema921_loader import FakeGroww
from tests.test_ema921_paper import DAY, ClockGroww, Notes, _at, _history

MODE = "ATM_BUY"


def _run_day(session, g, notes, strategies=tuple(LE.STRATEGIES), step_minutes=5, until=(15, 6)):
    state = LE.LiveState()
    t, end = _at(9, 16), _at(*until)
    while t <= end:
        g.clock = t.astimezone(IST)
        LE.step(session, g, notes, strategies, MODE, state, t)
        t += timedelta(minutes=step_minutes)
    return state


def _rows(session):
    return list(session.scalars(select(SignalTrade).order_by(SignalTrade.id)))


def test_live_day_matches_each_backtest_and_numbers_every_trade(session):
    _history(session)
    g, notes = ClockGroww(), Notes()
    _run_day(session, g, notes)
    rows = _rows(session)
    assert {r.strategy for r in rows} == set(LE.STRATEGIES)
    assert all(r.status == "CLOSED" and r.mode == MODE and r.lot_size == 65 for r in rows)
    assert [r.id for r in rows] == list(range(rows[0].id, rows[0].id + len(rows)))
    assert sum(1 for r in rows if r.strategy == "ORB-1.0") == 1  # one breakout a day

    # Each strategy's live trades equal its plain backtest on the completed day.
    L.load_spot(session, FakeGroww(), DAY, DAY, L.LoadReport(), log=lambda _: None)
    spot = S.load_spot(session, DAY, DAY, L.WARMUP_CALENDAR_DAYS)
    opts = S.DbOptionData(session)
    for code, strat in LE.STRATEGIES.items():
        bt = strat.run(spot, opts, DAY, MODE, None)
        expected = sorted((t.entry_price, t.exit_price, t.exit_reason, t.net_pnl) for t in bt.trades[MODE])
        got = sorted((float(r.entry_price), float(r.exit_price), r.exit_reason, float(r.net_pnl))
                     for r in rows if r.strategy == code)
        assert got == expected, code


def test_entry_and_exit_messages_quote_the_same_trade_number(session):
    _history(session)
    g, notes = ClockGroww(), Notes()
    _run_day(session, g, notes)
    rows = _rows(session)
    for r in rows:
        entry = [m for m in notes.sent if f"Trade #{r.id} · ENTRY" in m]
        exit_ = [m for m in notes.sent if f"Trade #{r.id} · EXIT" in m]
        assert len(entry) == 1 and len(exit_) == 1
        assert r.strategy in entry[0] and "Action: BUY NIFTY" in entry[0]
        assert "1 lot = 65" in entry[0] and "Exit at the first of" in entry[0]
        assert "Action: SELL NIFTY" in exit_[0] and "net" in exit_[0]
        assert notes.sent.index(entry[0]) < notes.sent.index(exit_[0])
    assert all("PAPER" in m and "No order was placed" in m for m in notes.sent)
    orb = next(r for r in rows if r.strategy == "ORB-1.0")
    assert "09:15-09:29 range" in orb.exit_plan
    report = [m for m in notes.sent if "Day report" in m]
    assert len(report) == 1 and all(f"#{r.id} {r.strategy}" in report[0] for r in rows)
    assert len(notes.photos) == 1 and notes.photos[0].startswith(b"\x89PNG")  # ONE picture too


def test_open_trades_are_not_closed_early_and_minutes_repeat_safely(session):
    _history(session)
    g, notes = ClockGroww(), Notes()
    state = _run_day(session, g, notes, step_minutes=1, until=(11, 0))
    rows = _rows(session)
    assert rows and any(r.status == "OPEN" for r in rows)
    for r in rows:  # never announced before its fill minute has closed
        assert r.created_at.replace(tzinfo=UTC) >= r.entry_time.replace(tzinfo=UTC) + timedelta(minutes=1)
    before = len(notes.sent)
    g.clock = _at(11, 0).astimezone(IST)
    LE.step(session, g, notes, tuple(LE.STRATEGIES), MODE, state, _at(11, 0))
    assert len(notes.sent) == before


def test_orb_live_never_sees_a_bar_before_it_closes():
    from tests.test_ema921 import DAY as D
    from tests.test_ema921 import WARM, FakeOptions, flat, spot_frame
    from tests.test_orb_et import rise_after_range

    spot = spot_frame((WARM, flat), (D, rise_after_range))
    import pandas as pd

    t = pd.Timestamp.combine(D, datetime.min.time()).tz_localize(IST)
    early = O.run(spot, FakeOptions(spot), D, D, None, (MODE,), as_of=t + timedelta(hours=9, minutes=34))
    assert early.trades[MODE] == []
    live = O.run(spot, FakeOptions(spot), D, D, None, (MODE,), as_of=t + timedelta(hours=9, minutes=37))
    (tr,) = live.trades[MODE]
    assert tr.exit_time is None  # still running


def test_stale_data_is_no_trade_and_said_once(session):
    _history(session)
    g, notes = ClockGroww(), Notes()
    g.stop_spot_at = _at(10, 0).astimezone(IST)
    state = LE.LiveState()
    g.clock = _at(10, 30).astimezone(IST)
    assert LE.step(session, g, notes, tuple(LE.STRATEGIES), MODE, state, _at(10, 30)).startswith("NO TRADE")
    LE.step(session, g, notes, tuple(LE.STRATEGIES), MODE, state, _at(10, 31))
    assert len(notes.sent) == 1 and "late" in notes.sent[0]


def test_strategy_names_and_no_order_code():
    assert LE.parse_strategies("all") == ("EMA-921", "ORB-1.0", "ET-1.0")
    assert LE.parse_strategies("orb-1.0, et-1.0") == ("ORB-1.0", "ET-1.0")
    try:
        LE.parse_strategies("XYZ")
        raise AssertionError("should fail")
    except ValueError:
        pass
    src = inspect.getsource(LE) + inspect.getsource(O) + inspect.getsource(E)
    for word in ("place_order", "modify_order", "cancel_order", "PlaceOrder", "smart_order"):
        assert word not in src


def test_signals_found_after_a_late_start_are_flagged(session):
    _history(session)
    g, notes = ClockGroww(), Notes()
    state = LE.LiveState()
    g.clock = _at(11, 30).astimezone(IST)  # service starts mid-morning
    LE.step(session, g, notes, tuple(LE.STRATEGIES), MODE, state, _at(11, 30))
    orb = [m for m in notes.sent if "ORB-1.0" in m and "ENTRY" in m]
    assert orb and orb[0].startswith("⚠ LATE") and "do not chase it" in orb[0]
    g2, notes2 = ClockGroww(), Notes()
    session.query(SignalTrade).delete()
    session.commit()
    _run_day(session, g2, notes2, step_minutes=1, until=(10, 0))
    assert notes2.sent and not any("LATE" in m for m in notes2.sent)


def test_one_day_report_covers_every_strategy_and_the_ema_paper_engine(session):
    """RK 2026-11-03: one report for every trade taken today, whatever the strategy, with the
    strategy name next to each trade number (text and picture)."""
    from app.db.models import PaperTrade
    from app.live.report_card import render_png

    t0 = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
    common = dict(trade_date=t0.date(), signal_time=t0, direction=-1, strike=23700,
                  expiry_date=t0.date(), groww_symbol="x", entry_time=t0, nifty_entry=23678.0,
                  stale_exit=False, created_at=t0)
    sig = [SignalTrade(id=1, strategy="ORB-1.0", mode=MODE, side="BUY", option_type="CE", lot_size=65,
                       entry_price=175.25, entry_fill=176.1, exit_plan="x", exit_time=t0,
                       exit_price=180, exit_fill=179.1, exit_reason="SQUARE_OFF", gross_pnl=310,
                       slippage=100, charges=60, net_pnl=150, status="CLOSED",
                       **{**common, "direction": 1, "strike": 23650}),
           SignalTrade(id=2, strategy="EMA-921", mode=MODE, side="BUY", option_type="PE", lot_size=65,
                       entry_price=195.5, entry_fill=196.5, exit_plan="x", status="OPEN", **common)]
    ema = [PaperTrade(timeframe=5, cross_number=1, mode=m, side=side, option_type=ot, entry_price=1,
                      entry_fill=1, exit_price=1, exit_fill=1, exit_time=t0, exit_reason="OPPOSITE_CROSS",
                      gross_pnl=0, slippage=0, charges=0, net_pnl=net, status="CLOSED", **common)
           for m, side, ot, net in (("ITM_BUY", "BUY", "PE", -1699), ("ATM_BUY", "BUY", "PE", -960),
                                    ("ATM_SELL", "SELL", "CE", -1334))]
    msg = LE.summary_message(t0.date(), sig, ema)
    assert "#1 ORB-1.0" in msg and "#2 EMA-921" in msg and "still OPEN" in msg
    assert "9/21 EMA paper engine net: -₹3,993" in msg
    assert "Total net today (numbered trades): ₹150" in msg
    assert "9/21 EMA paper engine" in LE.summary_message(t0.date(), [], ema)
    for s_rows, e_rows in ((sig, ema), (sig, []), ([], ema), ([], [])):
        png = render_png(t0.date(), s_rows, e_rows)
        assert png.startswith(b"\x89PNG") and len(png) > 1000
