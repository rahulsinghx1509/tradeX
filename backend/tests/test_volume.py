"""VF-1.0: futures volume, relative volume, the entry filter and the live alert line."""

from datetime import UTC, date, datetime, time, timedelta

import pandas as pd
from sqlalchemy import select

from app.core.market_time import IST, trading_days
from app.db.models import MinuteFetch, SignalTrade
from app.ema921 import bars as B
from app.ema921 import engine as E
from app.orb import engine as O
from app.volume import FUT_SYMBOL, LOOKBACK_DAYS
from app.volume import futures as VF
from app.volume import rvol as RV
from tests.test_ema921 import DAY, WARM, FakeOptions, flat, spot_frame
from tests.test_ema921_loader import FakeGroww
from tests.test_ema921_paper import ClockGroww, Notes, _history
from tests.test_live_signals import _rows, _run_day


def _vol_minutes(days, volume_at=lambda d, i: 100.0, skip=None):
    rows = []
    for d in days:
        for i in range(375):
            if skip and (d, i) in skip:
                continue
            t = datetime.combine(d, time(9, 15), tzinfo=IST) + timedelta(minutes=i)
            rows.append({"time": t.astimezone(UTC), "volume": volume_at(d, i)})
    return pd.DataFrame(rows)


DAYS = trading_days(date(2026, 9, 1), date(2026, 10, 13))[-(LOOKBACK_DAYS + 1):]


def test_bar_volume_is_the_sum_and_missing_stays_missing():
    df = _vol_minutes(DAYS[:1], skip={(DAYS[0], 7)})
    bars = RV.bar_volumes(df)
    assert len(bars) == 75
    assert bars.iloc[0]["volume"] == 500 and bars.iloc[0]["bar_close"].time() == time(9, 20)
    assert pd.isna(bars.iloc[1]["volume"])  # 09:20 bar lost a minute: no volume, not 400


def test_rvol_compares_the_same_time_of_day_over_20_days():
    last = DAYS[-1]
    df = _vol_minutes(DAYS, lambda d, i: 300.0 if (d == last and i < 5) else (900.0 if i < 5 else 100.0))
    look = RV.RvolLookup(df)
    first_bar = datetime.combine(last, time(9, 20), tzinfo=IST)
    rvol, vol, avg = look.at(first_bar)
    # The busy 09:15 bar is compared with earlier 09:15 bars (4500), not with the day's quiet bars.
    assert (vol, avg) == (1500.0, 4500.0) and round(rvol, 4) == round(1 / 3, 4)
    assert look.at(first_bar + timedelta(minutes=5))[0] == 1.0
    assert look.at(datetime.combine(DAYS[-2], time(9, 20), tzinfo=IST))[0] is None  # only 19 days before


def test_filter_needs_rvol_at_or_above_threshold_and_missing_is_no_trade():
    last = DAYS[-1]
    df = _vol_minutes(DAYS, lambda d, i: 200.0 if d == last and 5 <= i < 10 else 100.0)
    look = RV.RvolLookup(df)

    def cross(hh, mm):
        bc = pd.Timestamp(datetime.combine(last, time(hh, mm)), tz=IST)
        return B.Cross(last, bc - timedelta(minutes=5), bc, 1, 24500.0, 1)

    assert look.filter(1.5)(cross(9, 25)) and look.filter(1.0)(cross(9, 20))
    assert not look.filter(1.5)(cross(9, 20))
    assert not look.filter(1.0)(cross(16, 0))  # no bar there: missing = NO TRADE


def test_engines_take_no_entry_the_filter_refuses():
    spot = spot_frame((WARM, flat), (DAY, lambda i: 24500.0 if i < 15 else 24500.0 + 2 * min(i - 15, 90)))
    opts = FakeOptions(spot)
    assert O.run(spot, opts, DAY, DAY, None, ("ATM_BUY",), holidays=frozenset()).trades["ATM_BUY"]
    res = O.run(spot, opts, DAY, DAY, None, ("ATM_BUY",), holidays=frozenset(), entry_filter=lambda c: False)
    assert res.trades["ATM_BUY"] == [] and res.no_trade_days[DAY] == "ENTRY_FILTER"
    res = O.run(spot, opts, DAY, DAY, None, ("ATM_BUY",), holidays=frozenset(), entry_filter=lambda c: True)
    assert len(res.trades["ATM_BUY"]) == 1


def test_near_month_contract_rolls_after_expiry():
    futs = [(date(2026, 9, 29), "F-SEP"), (date(2026, 10, 27), "F-OCT")]
    assert VF.near_month(date(2026, 9, 29), futs) == "F-SEP"  # expiry day: still the expiring one
    assert VF.near_month(date(2026, 9, 30), futs) == "F-OCT"
    assert VF.near_month(date(2026, 10, 28), futs) is None


def test_loader_stores_near_month_futures_and_skips_on_rerun(session):
    g = FakeGroww()
    rep = VF.load(session, g, date(2026, 10, 12), date(2026, 10, 14), log=lambda _: None)
    assert rep.days == 3 and not rep.errors
    fut_calls = [c for c in g.calls if c[0] == "candles"]
    assert {c[1] for c in fut_calls} == {"NSE-NIFTY-27Oct26-FUT"}  # last Tuesday of October
    vol = RV.load_volume(session, date(2026, 10, 12), date(2026, 10, 14))
    assert len(vol) == 3 * 375 and vol["volume"].notna().all()
    g2 = FakeGroww()
    assert VF.load(session, g2, date(2026, 10, 12), date(2026, 10, 14), log=lambda _: None).skipped == 3
    assert not [c for c in g2.calls if c[0] == "candles"]
    assert session.scalar(select(MinuteFetch.status).where(MinuteFetch.groww_symbol == FUT_SYMBOL)) == "OK"


def test_live_entry_alert_shows_volume_as_information(session):
    _history(session)
    g, notes = ClockGroww(), Notes()
    _run_day(session, g, notes)
    entries = [m for m in notes.sent if "· ENTRY ·" in m]
    assert entries and all("Volume (NIFTY fut" in m for m in entries)
    assert RV.load_volume(session, DAY, DAY)["volume"].notna().sum() > 0  # today's futures stored
    assert {r.strategy for r in _rows(session)}  # trades still taken: volume never filters live


def test_alert_line_wording():
    assert RV.note(None, datetime(2026, 10, 13, 5, 0, tzinfo=UTC)) == "Volume (NIFTY fut): not available"
    last = DAYS[-1]
    look = RV.RvolLookup(_vol_minutes(DAYS, lambda d, i: 150.0 if d == last else 100.0))
    line = RV.note(look, datetime.combine(last, time(10, 0), tzinfo=IST).astimezone(UTC).replace(tzinfo=None))
    assert line.startswith("Volume (NIFTY fut, 10:00 bar): 750 = 1.50x its 20-day average")
    assert "Info only" in line


def test_no_order_code():
    import inspect

    for mod in (VF, RV):
        src = inspect.getsource(mod).lower()
        assert "place_order" not in src and "modify_order" not in src
    assert SignalTrade and E  # imported modules stay in use
