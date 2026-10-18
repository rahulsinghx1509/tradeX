"""Option-chain snapshots: stored once per day near the close, raw payload kept, failures reported."""

from datetime import date, timedelta

from sqlalchemy import func, select

from app.db.models import OptionChainSnapshot
from app.providers.base import ProviderError
from app.providers.sample import SampleProvider
from app.services import option_chain as oc
from app.services.collector import CollectorState, run_cycle
from app.services.ingestion import sync_instruments
from tests.test_alerts_and_collector import Recorder, ist

CHAIN = {"underlying_ltp": 25000,
         "strikes": {"25000": {"CE": {"ltp": 120, "open_interest": 10, "greeks": {"iv": 12}},
                               "PE": {"ltp": 90}},
                     "25100": {"CE": {"ltp": 80}}}}


class ChainProvider(SampleProvider):
    def __init__(self, fail=()):
        super().__init__()
        self.fail, self.calls = set(fail), 0

    def upcoming_expiries(self, underlying, today, count):
        return [today + timedelta(days=7 * k) for k in range(count)]

    def option_chain(self, underlying, expiry):
        self.calls += 1
        if underlying in self.fail:
            raise ProviderError("rate limited")
        return CHAIN


def rows(session):
    return session.scalar(select(func.count()).select_from(OptionChainSnapshot))


def test_snapshot_stores_raw_chain_per_expiry(session):
    res = oc.snapshot(session, ChainProvider(), ["NIFTY"], ist(15, 15, 20))
    assert [r.status for r in res] == ["SUCCESS", "SUCCESS"] and rows(session) == 2
    snap = session.scalar(select(OptionChainSnapshot))
    assert snap.payload == CHAIN and snap.strikes == 2 and snap.trade_date == date(2026, 10, 15)


def test_same_day_snapshot_replaces_not_duplicates(session):
    p = ChainProvider()
    oc.snapshot(session, p, ["NIFTY"], ist(15, 15, 20))
    oc.snapshot(session, p, ["NIFTY"], ist(15, 15, 25))
    assert rows(session) == 2


def test_failure_is_reported_and_others_still_stored(session):
    res = oc.snapshot(session, ChainProvider(fail={"BANKNIFTY"}), ["BANKNIFTY", "NIFTY"],
                      ist(15, 15, 20))
    assert [r.status for r in res].count("FAILED") == 2 and rows(session) == 2


def test_collector_takes_chains_once_after_1515(session):
    p = ChainProvider()
    sync_instruments(session, p, ["NIFTY"], 1)
    rec, state = Recorder(), CollectorState()
    assert run_cycle(session, p, rec, state, ist(15, 14, 0)) == "intraday"
    assert run_cycle(session, p, rec, state, ist(15, 15, 16)) == "intraday+chains"
    assert run_cycle(session, p, rec, state, ist(15, 15, 21)) == "intraday"
    assert p.calls == oc.CHAIN_EXPIRIES and rows(session) == oc.CHAIN_EXPIRIES


def test_field_names_lists_what_groww_sends():
    f = oc.field_names(CHAIN)
    assert f == {"top": ["strikes", "underlying_ltp"], "leg": ["greeks", "ltp", "open_interest"],
                 "greeks": ["iv"]}
