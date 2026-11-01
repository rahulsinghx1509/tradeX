"""9/21 EMA loader, store and probe against a fake Groww client (no network)."""

from datetime import date, datetime, time, timedelta

import pandas as pd

from app.core.market_time import IST, trading_days
from app.db.models import MinuteCandle, MinuteFetch
from app.ema921 import engine as E
from app.ema921 import loader as L
from app.ema921 import probe as P
from app.ema921 import store as S

START, END = date(2026, 10, 12), date(2026, 10, 16)
HOL = frozenset()


def spot_price(day: date, i: int) -> float:
    """A zig-zag so the EMAs cross several times a day."""
    phase = (i // 40) % 2
    step = i % 40
    return 24500.0 + (step * 3 if phase == 0 else 120 - step * 3)


class FakeGroww:
    def __init__(self, empty_options=False, fail_once: set[str] = frozenset()):
        self.calls: list[tuple] = []
        self.empty_options = empty_options
        self.fail_once = set(fail_once)

    def get_historical_candles(self, exchange, segment, groww_symbol, start_time, end_time,
                               candle_interval):
        self.calls.append(("candles", groww_symbol, start_time[:10]))
        assert candle_interval == "1minute"
        if groww_symbol in self.fail_once:
            self.fail_once.discard(groww_symbol)
            raise RuntimeError("no healthy upstream")
        day = date.fromisoformat(start_time[:10])
        if segment == "FNO" and self.empty_options:
            return {"candles": []}
        rows = []
        for i in range(375):
            t = datetime.combine(day, time(9, 15)) + timedelta(minutes=i)
            p = spot_price(day, i)
            if segment == "FNO":
                strike, opt = L.parse_contract(groww_symbol)
                p = max(0.0, (p - strike) if opt == "CE" else (strike - p)) + 80
            rows.append([t.strftime("%Y-%m-%d %H:%M:%S"), p, p + 1, p - 1, p, 1000, None])
        return {"candles": rows}

    def get_expiries(self, exchange, underlying_symbol, year, month):
        self.calls.append(("expiries", year, month))
        d = date(year, month, 1)
        out = []
        while d.month == month:
            if d.weekday() == 1:  # Tuesdays
                out.append(d.isoformat())
            d += timedelta(days=1)
        return {"expiries": out}

    def get_contracts(self, exchange, underlying_symbol, expiry_date):
        self.calls.append(("contracts", expiry_date))
        e = date.fromisoformat(expiry_date)
        out = [f"NSE-NIFTY-{e:%d%b%y}-{k}-{t}" for k in range(23000, 26050, 50) for t in ("CE", "PE")]
        return {"contracts": out + [f"NSE-NIFTY-{e:%d%b%y}-FUT"]}


def test_parse_contract():
    assert L.parse_contract("NSE-NIFTY-25Nov25-24500-CE") == (24500.0, "CE")
    assert L.parse_contract("NSE-NIFTY-25Nov25-FUT") is None
    assert L.parse_contract("garbage") is None


def test_default_range_handles_leap_day():
    assert L.default_range(date(2028, 2, 29), 2) == (date(2026, 2, 28), date(2028, 2, 28))
    assert L.default_range(date(2026, 11, 1), 2) == (date(2024, 11, 1), date(2026, 10, 31))


def test_load_all_then_backtest_from_the_database(session):
    g = FakeGroww()
    rep = L.load_all(session, g, START, END, log=lambda _: None)
    assert not rep.errors
    spot_days = trading_days(START - timedelta(days=L.WARMUP_CALENDAR_DAYS), END)
    assert rep.spot_days == len(spot_days)
    assert rep.contracts_needed > 0 and rep.contract_days_fetched == rep.contracts_needed
    # Only the contracts a trade could need, never the whole chain (122 strikes x 5 days).
    option_calls = [c for c in g.calls if c[0] == "candles" and c[1] != S.SPOT_SYMBOL]
    assert len(option_calls) == rep.contracts_needed < 122 * 5

    spot = S.load_spot(session, START, END, L.WARMUP_CALENDAR_DAYS)
    assert len(spot) == 375 * len(spot_days)
    res = E.run(spot, S.DbOptionData(session), START, END, E.Config(timeframe=5), holidays=HOL)
    assert all(res.trades[m] for m in E.MODES)
    assert not [s for s in res.skips if s.reason != "NO_DATA"]
    assert not res.skips  # everything the engine needed was loaded
    t = res.trades["ATM_BUY"][0]
    assert t.expiry.weekday() == 1 and t.expiry > t.day


def test_second_run_fetches_nothing_new(session):
    L.load_all(session, FakeGroww(), START, END, log=lambda _: None)
    g2 = FakeGroww()
    L.load_all(session, g2, START, END, log=lambda _: None)
    assert [c for c in g2.calls if c[0] in ("candles", "contracts")] == []


def test_failures_are_recorded_and_retried(session):
    g = FakeGroww(fail_once={S.SPOT_SYMBOL})
    # The loader retries "no healthy upstream" once itself, so this one succeeds.
    rep = L.load_all(session, g, START, END, log=lambda _: None)
    assert not rep.errors

    class Broken(FakeGroww):
        def get_historical_candles(self, **kw):
            raise RuntimeError("boom")

    s_day = date(2026, 10, 19)
    rep = L.LoadReport()
    L.load_spot(session, Broken(), s_day, s_day, rep, log=lambda _: None)
    assert rep.errors
    assert session.query(MinuteFetch).filter_by(trade_date=s_day, status="ERROR").count() == 1
    rep2 = L.LoadReport()
    L.load_spot(session, FakeGroww(), s_day, s_day, rep2, log=lambda _: None)
    assert rep2.spot_days == 1 and S.fetched(session, S.SPOT_SYMBOL, s_day)


def test_empty_answers_stay_missing_not_zero(session):
    rep = L.load_all(session, FakeGroww(empty_options=True), START, END, log=lambda _: None)
    assert rep.contract_days_empty == rep.contracts_needed
    assert session.query(MinuteCandle).filter(MinuteCandle.groww_symbol != S.SPOT_SYMBOL).count() == 0
    spot = S.load_spot(session, START, END)
    res = E.run(spot, S.DbOptionData(session), START, END, E.Config(), holidays=HOL)
    assert all(not v for v in res.trades.values())
    assert {s.reason for s in res.skips} == {"NO_DATA"}


def test_stored_times_round_trip_as_utc(session):
    L.load_spot(session, FakeGroww(), END, END, L.LoadReport(), log=lambda _: None)
    df = S.load_minutes(session, S.SPOT_SYMBOL, END, END)
    first = pd.Timestamp(df["time"].iloc[0]).tz_convert(IST)
    assert first.time() == time(9, 15) and first.date() == END


def test_probe_report():
    rows = P.run(FakeGroww(), date(2026, 10, 20))
    assert len(rows) == len(P.AGES_DAYS)
    assert all(r.spot_bars == 375 and r.option_bars == 375 for r in rows)
    assert rows[0].spot_first == "09:15" and rows[0].contract.endswith("-CE")
    md = P.to_markdown(rows, date(2026, 10, 20))
    assert "Oldest age with expired-option 1m: 1095 days" in md


def test_duplicate_candles_from_groww_are_stored_once(session):
    class Twice(FakeGroww):
        def get_historical_candles(self, **kw):
            body = super().get_historical_candles(**kw)
            return {"candles": body["candles"] + body["candles"]}

    rep = L.LoadReport()
    L.load_spot(session, Twice(), END, END, rep, log=lambda _: None)
    assert not rep.errors
    assert len(S.load_minutes(session, S.SPOT_SYMBOL, END, END)) == 375
