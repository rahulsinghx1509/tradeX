from datetime import timedelta

from sqlalchemy import func, select

from app.db.models import DataQualityLog, IngestionRun, Instrument, MarketCandle
from app.providers.base import Candle, ProviderError
from app.providers.sample import SampleProvider
from app.services.ingestion import backfill, ingest_candles, sync_instruments
from tests.conftest import NOW


def count(session):
    return session.scalar(select(func.count()).select_from(MarketCandle))


def test_sync_instruments_creates_index_and_futures(session, provider):
    sync_instruments(session, provider, ["NIFTY", "BANKNIFTY"], 2)
    rows = session.scalars(select(Instrument)).all()
    assert len(rows) == 6
    assert {r.instrument_type for r in rows} == {"INDEX", "FUTURE"}
    assert all(r.is_sample for r in rows)
    # Running again does not duplicate instruments.
    sync_instruments(session, provider, ["NIFTY", "BANKNIFTY"], 2)
    assert session.scalar(select(func.count()).select_from(Instrument)) == 6


def test_repeated_ingestion_does_not_duplicate_candles(session, provider):
    sync_instruments(session, provider, ["NIFTY"], 1)
    backfill(session, provider, "1d", 60, now=NOW)
    first = count(session)
    assert first > 0
    backfill(session, provider, "1d", 60, now=NOW)
    backfill(session, provider, "1d", 30, now=NOW)  # overlapping window
    assert count(session) == first


def test_candles_are_labelled_sample(session, provider):
    sync_instruments(session, provider, ["NIFTY"], 1)
    backfill(session, provider, "1h", 5, now=NOW)
    flags = set(session.scalars(select(MarketCandle.is_sample)).all())
    sources = set(session.scalars(select(MarketCandle.source)).all())
    assert flags == {True} and sources == {"sample"}


def test_index_volume_stays_null_not_zero(session, provider):
    sync_instruments(session, provider, ["NIFTY"], 1)
    backfill(session, provider, "1d", 10, now=NOW)
    idx = session.scalar(select(Instrument).where(Instrument.instrument_type == "INDEX"))
    vols = set(session.scalars(select(MarketCandle.volume).where(MarketCandle.instrument_id == idx.id)))
    assert vols == {None}


class BrokenProvider(SampleProvider):
    def get_candles(self, *a, **k):
        raise ProviderError("network down")


def test_provider_failure_is_logged_not_hidden(session, provider):
    sync_instruments(session, provider, ["NIFTY"], 1)
    inst = session.scalar(select(Instrument).where(Instrument.instrument_type == "INDEX"))
    res = ingest_candles(session, BrokenProvider(), inst, "1d", NOW - timedelta(days=5), NOW, NOW)
    assert res.status == "FAILED"
    run = session.get(IngestionRun, res.run_id)
    assert run.status == "FAILED" and "network down" in run.error_message
    log = session.scalar(select(DataQualityLog).where(DataQualityLog.run_id == res.run_id))
    assert log.check_name == "source_availability" and log.status == "FAIL"
    assert count(session) == 0


class BadDataProvider(SampleProvider):
    def get_candles(self, instrument, timeframe, start, end):
        good = super().get_candles(instrument, timeframe, start, end)
        bad = Candle(good[0].time, 100.0, 90.0, 95.0, 99.0)  # high below open: invalid
        return [bad] + good[1:]


def test_invalid_candle_is_stored_but_flagged(session, provider):
    sync_instruments(session, provider, ["NIFTY"], 1)
    inst = session.scalar(select(Instrument).where(Instrument.instrument_type == "INDEX"))
    ingest_candles(session, BadDataProvider(today=provider._today), inst, "1d",
                   NOW - timedelta(days=10), NOW, NOW)
    flagged = session.scalars(select(MarketCandle).where(MarketCandle.quality_status == "INVALID")).all()
    assert len(flagged) == 1 and flagged[0].quality_reason == "INCONSISTENT_OHLC"


def test_no_incomplete_candle_is_stored(session, provider):
    sync_instruments(session, provider, ["NIFTY"], 1)
    inst = session.scalar(select(Instrument).where(Instrument.instrument_type == "INDEX"))
    during_session = NOW - timedelta(hours=5)  # 11:00 IST: today's daily candle still forming
    ingest_candles(session, provider, inst, "1d", during_session - timedelta(days=5),
                   during_session, during_session)
    latest = session.scalar(select(func.max(MarketCandle.candle_time)))
    assert latest.date() < during_session.date()
