from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from app.core.config import Settings
from app.core.market_time import to_ist
from app.providers.base import ProviderError
from app.providers.groww import GrowwProvider, parse_candles, parse_quote
from tests.conftest import NOW

# ---------------------------------------------------------------- sample provider


def test_sample_is_deterministic_across_windows(provider):
    nifty = provider.list_instruments(["NIFTY"], 1)[0]
    a = provider.get_candles(nifty, "1h", NOW - timedelta(days=30), NOW)
    b = provider.get_candles(nifty, "1h", NOW - timedelta(days=10), NOW)
    assert a[-len(b):] == b


def test_sample_daily_matches_hourly(provider):
    nifty = provider.list_instruments(["NIFTY"], 1)[0]
    hourly = provider.get_candles(nifty, "1h", NOW - timedelta(days=3), NOW)
    daily = provider.get_candles(nifty, "1d", NOW - timedelta(days=3), NOW)
    last_day = to_ist(daily[-1].time).date()
    bars = [h for h in hourly if to_ist(h.time).date() == last_day]
    assert daily[-1].close == bars[-1].close and daily[-1].high == max(b.high for b in bars)


def test_sample_futures_have_oi_and_expire(provider):
    fut = [i for i in provider.list_instruments(["BANKNIFTY"], 2) if i.instrument_type == "FUTURE"]
    assert len(fut) == 2 and fut[0].expiry_date < fut[1].expiry_date
    candles = provider.get_candles(fut[0], "1d", NOW - timedelta(days=200), NOW)
    assert all(c.open_interest is not None for c in candles)
    assert to_ist(candles[0].time).date() >= fut[0].expiry_date - timedelta(days=90)


# ---------------------------------------------------------------- groww parsing
# Payload copied from the example response in Groww's backtesting docs
# (https://groww.in/trade-api/docs/python-sdk/backtesting). Documentation sample, not live data.
DOC_CANDLES = {
    "candles": [
        ["2025-09-24T10:30:00", 245.95, 246.15, 245.05, 245.6, 735060, None],
        ["2025-09-24T11:00:00", 245.64, 245.66, 244.8, 244.94, 682373, None],
    ],
    "closing_price": 245.40,
    "interval_in_minutes": 30,
}


def test_parse_groww_candles():
    rows = parse_candles(DOC_CANDLES, "1h")
    assert len(rows) == 2
    assert to_ist(rows[0].time).strftime("%Y-%m-%d %H:%M") == "2025-09-24 10:30"
    assert rows[0].volume == 735060 and rows[0].open_interest is None


def test_parse_groww_daily_candle_pins_to_session_open():
    rows = parse_candles({"candles": [["2025-09-24 00:00:00", 1, 2, 0.5, 1.5, 10, 99]]}, "1d")
    assert to_ist(rows[0].time).strftime("%H:%M") == "09:15" and rows[0].open_interest == 99


def test_parse_groww_epoch_timestamp():
    rows = parse_candles({"candles": [[1758686400, 1, 2, 0.5, 1.5]]}, "1h")
    assert rows[0].time == datetime.fromtimestamp(1758686400, tz=UTC) and rows[0].volume is None


def test_parse_groww_quote():
    q = parse_quote({"last_price": 100.5, "offer_price": 101, "ohlc": {"open": 99, "close": 98},
                     "open_interest": 2000, "implied_volatility": 0.25,
                     "last_trade_time": 1633072800000}, NOW)
    assert q.last_price == 100.5 and q.ask_price == 101 and q.previous_close == 98
    assert q.observed_at == datetime.fromtimestamp(1633072800, tz=UTC)
    assert q.volume is None  # not in payload -> missing, not zero


# ---------------------------------------------------------------- groww adapter
class FakeGroww:
    def __init__(self):
        self.calls = []

    def get_historical_candles(self, **kw):
        self.calls.append(kw)
        return {"candles": []}

    def get_all_instruments(self):
        return pd.DataFrame([
            {"exchange": "NSE", "segment": "FNO", "instrument_type": "FUT",
             "underlying_symbol": "NIFTY", "expiry_date": "2099-02-26", "lot_size": 65,
             "groww_symbol": "NSE-NIFTY-26Feb99-FUT", "trading_symbol": "NIFTY26NOVFUT"},
            {"exchange": "NSE", "segment": "FNO", "instrument_type": "FUT",
             "underlying_symbol": "NIFTY", "expiry_date": "2099-01-29", "lot_size": 65,
             "groww_symbol": "NSE-NIFTY-29Jan99-FUT", "trading_symbol": "NIFTY26OCTFUT"},
            {"exchange": "NSE", "segment": "FNO", "instrument_type": "FUT",
             "underlying_symbol": "NIFTY", "expiry_date": "2020-01-30", "lot_size": 75,
             "groww_symbol": "OLD", "trading_symbol": "OLD"},
            {"exchange": "NSE", "segment": "FNO", "instrument_type": "CE",
             "underlying_symbol": "NIFTY", "expiry_date": "2099-01-29", "lot_size": 65,
             "groww_symbol": "OPT", "trading_symbol": "OPT"},
        ])


def test_groww_splits_long_ranges_into_180_day_windows():
    fake = FakeGroww()
    p = GrowwProvider(Settings(), client=fake)
    inst = p.list_instruments(["NIFTY"], 1)[0]
    p.get_candles(inst, "1d", NOW - timedelta(days=400), NOW)
    assert len(fake.calls) == 3
    assert fake.calls[0]["groww_symbol"] == "NSE-NIFTY" and fake.calls[0]["candle_interval"] == "1day"


def test_groww_picks_nearest_unexpired_futures():
    p = GrowwProvider(Settings(), client=FakeGroww())
    futs = [i for i in p.list_instruments(["NIFTY"], 2) if i.instrument_type == "FUTURE"]
    # Expired (2020) contracts and options are skipped; nearest expiry comes first.
    assert [f.expiry_date for f in futs] == [date(2099, 1, 29), date(2099, 2, 26)]
    assert futs[0].lot_size == 65


def test_groww_without_credentials_gives_clear_error():
    p = GrowwProvider(Settings(_env_file=None, groww_api_key=None, groww_api_secret=None,
                               groww_totp_token=None, groww_totp_secret=None))
    with pytest.raises(ProviderError, match="No Groww credentials"):
        p.list_instruments(["NIFTY"], 1)
