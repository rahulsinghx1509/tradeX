"""9/21 EMA paper engine: live minute-by-minute run matches the backtest; alerts; safety."""

import inspect
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select

from app.core.market_time import IST
from app.db.models import PaperTrade
from app.ema921 import engine as E
from app.ema921 import loader as L
from app.ema921 import paper as P
from app.ema921 import store as S
from tests.test_ema921_loader import FakeGroww

DAY = date(2026, 10, 16)  # Friday
HISTORY = (date(2026, 10, 5), date(2026, 10, 15))


class ClockGroww(FakeGroww):
    """Groww as seen live: candles only up to the current minute (the forming one included)."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.clock: datetime | None = None
        self.stop_spot_at: datetime | None = None

    def get_historical_candles(self, **kw):
        body = super().get_historical_candles(**kw)
        if self.clock is None:
            return body
        cut = self.clock
        if kw["segment"] == "CASH" and self.stop_spot_at is not None:
            cut = min(cut, self.stop_spot_at)
        keep = [r for r in body["candles"]
                if datetime.fromisoformat(r[0]).replace(tzinfo=IST) <= cut]
        return {"candles": keep}


class Notes:
    def __init__(self):
        self.sent: list[str] = []

    def send(self, text: str) -> None:
        self.sent.append(text)


def _at(hh, mm, ss=5):
    return datetime.combine(DAY, time(hh, mm, ss), tzinfo=IST).astimezone(UTC)


def _run_day(session, g, notes, cfg, step_minutes=5, until=(15, 6)):
    state = P.PaperState()
    t = _at(9, 16)
    end = _at(*until)
    while t <= end:
        g.clock = t.astimezone(IST)
        P.step(session, g, notes, cfg, E.MODES, state, t)
        t += timedelta(minutes=step_minutes)
    return state


def _history(session):
    L.load_all(session, FakeGroww(), *HISTORY, log=lambda _: None)


def test_paper_day_matches_the_backtest(session):
    _history(session)
    cfg = E.Config(timeframe=15)
    g, notes = ClockGroww(), Notes()
    _run_day(session, g, notes, cfg)

    rows = list(session.scalars(select(PaperTrade).order_by(PaperTrade.mode, PaperTrade.signal_time)))
    assert rows and all(r.status == "CLOSED" for r in rows)

    # The same day as a plain backtest. The paper engine stopped storing NIFTY at 15:06, so the
    # rest of the day is loaded first (otherwise the backtest calls it a day with a gap).
    L.load_spot(session, FakeGroww(), DAY, DAY, L.LoadReport(), log=lambda _: None)
    spot = S.load_spot(session, DAY, DAY)
    bt = E.run(spot, S.DbOptionData(session), DAY, DAY, cfg)
    expected = sorted((t.mode, t.entry_price, t.exit_price, t.net_pnl) for m in E.MODES
                       for t in bt.trades[m])
    got = sorted((r.mode, float(r.entry_price), float(r.exit_price), float(r.net_pnl)) for r in rows)
    assert got == expected

    entries = [m for m in notes.sent if "ENTRY" in m]
    exits = [m for m in notes.sent if "EXIT" in m]
    assert all("PAPER" in m and "No order was placed" in m for m in entries + exits)
    signals = {(r.signal_time) for r in rows}
    assert len(entries) == len(signals)  # one message per signal, all modes together
    assert len(exits) >= 1
    assert any("day summary" in m for m in notes.sent)


def test_open_trade_stays_open_until_its_exit_is_known(session):
    _history(session)
    g, notes = ClockGroww(), Notes()
    _run_day(session, g, notes, E.Config(timeframe=15), step_minutes=1, until=(11, 0))
    rows = list(session.scalars(select(PaperTrade)))
    assert rows
    open_rows = [r for r in rows if r.status == "OPEN"]
    assert open_rows and all(r.exit_time is None for r in open_rows)
    # No entry is ever announced before its fill minute has closed.
    for r in rows:
        assert r.created_at.replace(tzinfo=UTC) >= (r.entry_time.replace(tzinfo=UTC)
                                                    + timedelta(minutes=1))


def test_running_the_same_minute_twice_sends_nothing_new(session):
    _history(session)
    g, notes = ClockGroww(), Notes()
    state = P.PaperState()
    cfg = E.Config(timeframe=15)
    for t in (_at(11, 0), _at(11, 0)):
        g.clock = t.astimezone(IST)
        P.step(session, g, notes, cfg, E.MODES, state, t)
    first_count = len(notes.sent)
    g.clock = _at(11, 0).astimezone(IST)
    P.step(session, g, notes, cfg, E.MODES, state, _at(11, 0))
    assert len(notes.sent) == first_count


def test_stale_spot_data_means_no_trade(session):
    _history(session)
    g, notes = ClockGroww(), Notes()
    g.stop_spot_at = _at(10, 0).astimezone(IST)
    state = P.PaperState()
    g.clock = _at(10, 30).astimezone(IST)
    status = P.step(session, g, notes, E.Config(), E.MODES, state, _at(10, 30))
    assert status.startswith("NO TRADE")
    assert len(notes.sent) == 1 and "late" in notes.sent[0]
    P.step(session, g, notes, E.Config(), E.MODES, state, _at(10, 31))
    assert len(notes.sent) == 1  # told once, not every minute


def test_quiet_outside_market_hours(session):
    notes = Notes()
    sat = datetime(2026, 10, 17, 10, 0, tzinfo=IST).astimezone(UTC)
    assert P.step(session, FakeGroww(), notes, E.Config(), E.MODES, P.PaperState(), sat).startswith("closed")
    early = datetime(2026, 10, 16, 9, 0, tzinfo=IST).astimezone(UTC)
    assert P.step(session, FakeGroww(), notes, E.Config(), E.MODES, P.PaperState(), early) == \
        "waiting for the open"
    assert notes.sent == []


def test_paper_engine_has_no_order_calls():
    src = inspect.getsource(P) + inspect.getsource(L) + inspect.getsource(E)
    for word in ("place_order", "modify_order", "cancel_order", "PlaceOrder", "smart_order"):
        assert word not in src
