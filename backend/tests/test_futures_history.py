"""Expired-futures loader: one contract per month, stored inactive, skipped once loaded."""

from datetime import date

from sqlalchemy import func, select

from app.db.models import Instrument, MarketCandle
from app.providers.base import InstrumentInfo, ProviderError
from app.providers.sample import SampleProvider
from app.services import futures_history as fh
from tests.conftest import NOW


class ExpiredProvider(SampleProvider):
    calls = 0

    def expired_future(self, underlying, year, month):
        self.calls += 1
        if month == 2:
            return None  # e.g. no contract listed
        if month == 3:
            raise ProviderError("rate limited")
        exp = date(year, month, 25)
        return InstrumentInfo(symbol=underlying, instrument_type="FUTURE", exchange="NSE",
                              segment="FNO", provider_symbol=f"NSE-{underlying}-{exp}-FUT",
                              expiry_date=exp)


def test_months_run_up_to_last_month():
    m = fh.months(2025, date(2026, 3, 10))
    assert m[0] == (2025, 1) and m[-1] == (2026, 2) and len(m) == 14


def test_load_month_stores_inactive_contract_with_oi(session):
    p = ExpiredProvider()
    r = fh.load_month(session, p, "NIFTY", 2026, 6, NOW)
    assert r.status == "LOADED" and r.candles >= fh.MIN_STORED_CANDLES
    inst = session.scalar(select(Instrument))
    assert inst.is_active is False and inst.expiry_date == date(2026, 6, 25)
    oi = session.scalars(select(MarketCandle.open_interest)).all()
    assert oi and all(x is not None for x in oi)


def test_loaded_month_is_skipped_without_calling_the_provider(session):
    p = ExpiredProvider()
    fh.load_month(session, p, "NIFTY", 2026, 6, NOW)
    calls, rows = p.calls, session.scalar(select(func.count()).select_from(MarketCandle))
    r = fh.load_month(session, p, "NIFTY", 2026, 6, NOW)
    assert r.status == "SKIPPED" and p.calls == calls
    assert session.scalar(select(func.count()).select_from(MarketCandle)) == rows


def test_missing_and_failing_months_are_reported_not_hidden(session):
    p = ExpiredProvider()
    assert fh.load_month(session, p, "NIFTY", 2026, 2, NOW).status == "NONE"
    r = fh.load_month(session, p, "NIFTY", 2026, 3, NOW)
    assert r.status == "FAILED" and "rate limited" in r.error
