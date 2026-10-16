from datetime import UTC, date, datetime

from app.core import market_time as mt

NO_HOLIDAYS = frozenset()


def ist(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=mt.IST).astimezone(UTC)


def test_hourly_bars_follow_nse_session():
    bars = mt.bar_starts_for_day(date(2026, 10, 15), "1h")
    times = [mt.to_ist(b).strftime("%H:%M") for b in bars]
    assert times == ["09:15", "10:15", "11:15", "12:15", "13:15", "14:15", "15:15"]


def test_last_hourly_bar_ends_at_close():
    last = mt.bar_starts_for_day(date(2026, 10, 15), "1h")[-1]
    assert mt.bar_end(last, "1h") == ist(2026, 10, 15, 15, 30)


def test_weekend_is_not_trading_day():
    assert not mt.is_trading_day(date(2026, 10, 17), NO_HOLIDAYS)  # Saturday
    assert mt.is_trading_day(date(2026, 10, 16), NO_HOLIDAYS)


def test_holiday_file_is_respected():
    assert not mt.is_trading_day(date(2026, 10, 16), frozenset({date(2026, 10, 16)}))


def test_market_open_window():
    assert mt.is_market_open(ist(2026, 10, 15, 9, 15), NO_HOLIDAYS)
    assert not mt.is_market_open(ist(2026, 10, 15, 9, 14), NO_HOLIDAYS)
    assert not mt.is_market_open(ist(2026, 10, 15, 15, 30), NO_HOLIDAYS)
    assert not mt.is_market_open(ist(2026, 10, 17, 11, 0), NO_HOLIDAYS)


def test_latest_closed_bar_during_session():
    now = ist(2026, 10, 15, 11, 20)
    assert mt.latest_expected_closed_bar(now, "1h", NO_HOLIDAYS) == ist(2026, 10, 15, 10, 15)
    # Today's daily candle is not complete until 15:30, so yesterday's is the latest.
    assert mt.latest_expected_closed_bar(now, "1d", NO_HOLIDAYS) == ist(2026, 10, 14, 9, 15)


def test_latest_closed_bar_on_monday_morning_is_friday():
    now = ist(2026, 10, 19, 8, 0)  # Monday before open
    assert mt.latest_expected_closed_bar(now, "1d", NO_HOLIDAYS) == ist(2026, 10, 16, 9, 15)


def test_naive_times_are_treated_as_ist():
    assert mt.to_utc(datetime(2026, 10, 15, 9, 15)) == ist(2026, 10, 15, 9, 15)
