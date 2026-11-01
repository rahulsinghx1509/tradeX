"""ST-1.0 09:20 hedged short straddle: legs, stops, VIX filter, skips, loader, report."""

from datetime import date, datetime, time, timedelta

import pandas as pd
import pytest

from app.core.market_time import IST
from app.straddle import engine as E
from app.straddle import loader as SL
from app.straddle import report as R
from tests.test_ema921_loader import FakeGroww

DAY = date(2026, 10, 13)  # Tuesday
EXPIRY = date(2026, 10, 20)


def spot_day(day=DAY, price=24510.0, skip_0919=False):
    rows = []
    for i in range(375):
        t = pd.Timestamp(datetime.combine(day, time(9, 15)), tz=IST) + timedelta(minutes=i)
        if skip_0919 and i == 4:
            continue
        rows.append({"time": t.tz_convert("UTC"), "open": price, "high": price, "low": price,
                     "close": price})
    return pd.DataFrame(rows)


class Opts:
    """Option prices by symbol: a function of the minute index (0 = 09:15)."""

    def __init__(self, paths: dict[str, callable], missing=()):
        self.paths, self.missing = paths, set(missing)

    def expiries(self):
        return [EXPIRY]

    def contract(self, expiry, strike, opt):
        return f"NSE-NIFTY-{expiry:%d%b%y}-{strike:g}-{opt}"

    def minutes(self, symbol, day):
        if symbol in self.missing:
            return None
        key = symbol.rsplit("-", 2)[-2] + symbol[-2:]
        f = self.paths.get(key, lambda i: 10.0)
        rows = []
        for i in range(375):
            t = pd.Timestamp(datetime.combine(day, time(9, 15)), tz=IST) + timedelta(minutes=i)
            p = f(i)
            rows.append({"time": t.tz_convert("UTC"), "open": p, "high": p + 1, "low": p - 1,
                         "close": p})
        return pd.DataFrame(rows)


ZERO = dict(slippage_pct=0, slippage_points=0, vix_filter=False)
M0920, M1515 = 5, 360  # minute index of 09:20 and 15:15


def test_four_legs_at_0920_and_time_exit():
    # ATM 24500 (24510 rounds down). Both sold legs decay from 100 to 60: no stop.
    decay = {"24500CE": lambda i: 100 - 40 * i / 375, "24500PE": lambda i: 100 - 40 * i / 375,
             "25000CE": lambda i: 5.0, "24000PE": lambda i: 5.0}
    t, why = E.run_day(DAY, spot_day(), Opts(decay), E.Config(**ZERO), [EXPIRY])
    assert why is None and t.atm == 24500 and t.expiry == EXPIRY
    roles = {x.role: x for x in t.legs}
    assert set(roles) == {"SHORT_CE", "SHORT_PE", "WING_CE", "WING_PE"}
    assert roles["WING_CE"].strike == 25000 and roles["WING_PE"].strike == 24000
    ce = roles["SHORT_CE"]
    assert ce.entry_time.astimezone(IST).time() == time(9, 20)
    assert ce.exit_reason == "TIME" and ce.exit_time.astimezone(IST).time() == time(15, 15)
    assert ce.entry_price == pytest.approx(100 - 40 * M0920 / 375)
    assert ce.gross_pnl == pytest.approx((ce.entry_price - ce.exit_price) * 65, abs=0.01)
    assert t.gross_pnl > 0 and t.net_pnl < t.gross_pnl  # charges still apply


def test_stop_at_30_percent_on_the_minute_high():
    # CE rises 1 point a minute from 100 at 09:20; the stop is 130 on the 1m high (close + 1).
    up = {"24500CE": lambda i: 100 + max(0, i - M0920), "24500PE": lambda i: 100.0}
    t, _ = E.run_day(DAY, spot_day(), Opts(up), E.Config(**ZERO), [EXPIRY])
    ce = next(x for x in t.legs if x.role == "SHORT_CE")
    pe = next(x for x in t.legs if x.role == "SHORT_PE")
    assert ce.exit_reason == "STOP" and ce.exit_price == 130
    # high = close + 1 reaches 130 when the close is 129, i.e. 29 minutes after 09:20
    assert ce.exit_time.astimezone(IST).time() == time(9, 49)
    assert pe.exit_reason == "TIME"  # the other leg carries on
    assert t.stops == 1


def test_gap_through_the_stop_fills_at_the_open():
    gap = {"24500CE": lambda i: 100.0 if i < 100 else 200.0}
    t, _ = E.run_day(DAY, spot_day(), Opts(gap), E.Config(**ZERO), [EXPIRY])
    ce = next(x for x in t.legs if x.role == "SHORT_CE")
    assert ce.exit_reason == "STOP" and ce.exit_price == 200.0  # worse than 130: the gap


def test_missing_leg_or_spot_skips_the_day():
    opts = Opts({}, missing={"NSE-NIFTY-20Oct26-25000-CE"})
    t, why = E.run_day(DAY, spot_day(), opts, E.Config(**ZERO), [EXPIRY])
    assert t is None and why.startswith("NO_DATA")
    t, why = E.run_day(DAY, spot_day(skip_0919=True), Opts({}), E.Config(**ZERO), [EXPIRY])
    assert t is None and why == "NO_SPOT_AT_ENTRY"


def test_expiry_day_rolls_to_next_week():
    t, _ = E.run_day(EXPIRY, spot_day(EXPIRY), Opts({}), E.Config(**ZERO),
                     [EXPIRY, EXPIRY + timedelta(days=7)])
    assert t.expiry == EXPIRY + timedelta(days=7) and t.dte == 7


def _vix(values_by_day):
    return pd.Series(values_by_day)


def test_vix_jump_and_missing_vix_mean_no_trade():
    days = [date(2026, 10, d) for d in (5, 6, 7, 8, 9, 12)]  # 6 trading days before 13 Oct
    calm = _vix(dict(zip(days, [12, 12, 12, 12, 12, 12.5], strict=True)))
    jump = _vix(dict(zip(days, [12, 12, 12, 12, 12, 13.0], strict=True)))  # +8.3% > 7.8
    assert E.vix_status(DAY, calm) == "CLEAR"
    assert E.vix_status(DAY, jump) == "JUMP"
    assert E.vix_status(DAY, calm.drop(date(2026, 10, 12))) == "NO_VIX"  # stale
    assert E.vix_status(DAY, None) == "NO_VIX"
    cfg = E.Config(slippage_pct=0, slippage_points=0)
    res = E.run(spot_day(), Opts({}), DAY, DAY, cfg, jump)
    assert res.trades == [] and res.skipped == {DAY: "VIX_JUMP"}
    res = E.run(spot_day(), Opts({}), DAY, DAY, cfg, calm)
    assert len(res.trades) == 1 and res.skipped == {}


def test_summary_report_and_pass_bar():
    decay = {"24500CE": lambda i: 100 - 40 * i / 375, "24500PE": lambda i: 100 - 40 * i / 375}
    spot = pd.concat([spot_day(d) for d in (DAY, date(2026, 10, 14))])
    res = E.run(spot, Opts(decay), DAY, date(2026, 10, 14), E.Config(**ZERO))
    s = E.summarize(res.trades, 100_000)
    assert s.days == 2 and s.net_pnl > 0 and s.legs_stopped_pct == 0
    md = R.to_markdown(res, DAY, date(2026, 10, 14))
    assert "ST-1.0" in md and "Pass bar" in md and "| 2 |" in md
    ok, _ = E.passes_dev_bar(res.trades, 100_000)
    assert ok is False  # all in year 1, so year 2 is not > 0


def test_loader_fetches_four_legs_a_day(session):
    start, end = date(2026, 10, 12), date(2026, 10, 16)
    g = FakeGroww()
    rep = SL.load_all(session, g, start, end, log=lambda _: None)
    assert not rep.errors
    assert rep.contracts_needed == 4 * 5 == rep.contract_days_fetched
    from app.ema921 import store as S
    spot = S.load_spot(session, start, end, 0)
    res = E.run(spot, S.DbOptionData(session), start, end, E.Config(vix_filter=False))
    assert len(res.trades) == 5 and all(len(t.legs) == 4 for t in res.trades)
    g2 = FakeGroww()
    SL.load_all(session, g2, start, end, log=lambda _: None)
    assert [c for c in g2.calls if c[0] in ("candles", "contracts")] == []


def test_no_order_calls():
    import inspect

    from app.straddle import loader
    src = inspect.getsource(E) + inspect.getsource(loader)
    for word in ("place_order", "modify_order", "cancel_order", "smart_order"):
        assert word not in src
