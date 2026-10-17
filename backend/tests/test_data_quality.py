from datetime import UTC, datetime, timedelta

from app.core.market_time import bar_starts_for_day
from app.providers.base import Candle
from app.services import data_quality as dq

T0 = datetime(2026, 10, 15, 3, 45, tzinfo=UTC)  # 09:15 IST


def c(t, o=100.0, h=101.0, low=99.0, cl=100.5, v=10, oi=None):
    return Candle(t, o, h, low, cl, v, oi)


def test_valid_candle_has_no_problem():
    assert dq.ohlc_problem(c(T0)) is None


def test_invalid_ohlc_reasons():
    assert dq.ohlc_problem(c(T0, h=99.5)) == "INCONSISTENT_OHLC"  # high below close
    assert dq.ohlc_problem(c(T0, low=100.8)) == "INCONSISTENT_OHLC"  # low above open
    assert dq.ohlc_problem(c(T0, o=0)) == "NON_POSITIVE_PRICE"
    assert dq.ohlc_problem(c(T0, cl=None)) == "MISSING_PRICE"
    assert dq.ohlc_problem(c(T0, v=-1)) == "NEGATIVE_VOLUME"
    # Bad ticks seen from Groww: a daily high of ~195,000 on a ~1,900 stock.
    assert dq.ohlc_problem(c(T0, h=195000)) == "PRICE_SPIKE"
    assert dq.ohlc_problem(c(T0, low=1)) == "PRICE_SPIKE"
    assert dq.ohlc_problem(c(T0, h=195000), spike_check=False) is None


def test_missing_volume_is_not_an_ohlc_error():
    # Missing stays missing: None volume is allowed and is never turned into 0.
    assert dq.ohlc_problem(c(T0, v=None)) is None


def test_dedupe_exact_and_conflicting():
    rows, res = dq.dedupe([c(T0), c(T0)])
    assert len(rows) == 1 and res.status == dq.WARN
    rows, res = dq.dedupe([c(T0), c(T0, cl=100.7)])
    assert len(rows) == 1 and res.status == dq.FAIL
    assert rows[0].close == 100.7  # last copy wins, and the conflict is logged


def test_incomplete_candle_is_dropped():
    bars = bar_starts_for_day(T0.date(), "1h")
    now = bars[2] + timedelta(minutes=30)  # third bar still forming
    kept, res = dq.drop_incomplete([c(b) for b in bars[:3]], "1h", now)
    assert len(kept) == 2 and res.details["dropped"] == 1


def test_gap_detection_finds_missing_hour():
    bars = bar_starts_for_day(T0.date(), "1h")
    res = dq.check_gaps([c(b) for b in bars if b != bars[3]], "1h", frozenset())
    assert res.status == dq.WARN and res.details["missing"] == 1


def test_no_gaps_passes():
    bars = bar_starts_for_day(T0.date(), "1h")
    assert dq.check_gaps([c(b) for b in bars], "1h", frozenset()).status == dq.PASS


def test_missing_oi_for_futures_warns():
    res = dq.check_missing_values([c(T0, oi=None)], expect_volume=True, expect_oi=True)
    assert res.status == dq.WARN and res.details["missing_open_interest"] == 1
