"""Intraday data points: hand-checked VWAP, opening range, pivots/CPR, gap and RVOL."""

from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from app.core.market_time import bar_end, bar_starts_for_day, trading_days
from app.services import intraday_metrics as im


def test_minute_bars_follow_the_session():
    d = date(2026, 10, 29)
    five, fifteen = bar_starts_for_day(d, "5m"), bar_starts_for_day(d, "15m")
    assert len(five) == 75 and len(fifteen) == 25
    assert five[0] == datetime(2026, 10, 29, 3, 45, tzinfo=UTC)  # 09:15 IST
    assert five[-1] == datetime(2026, 10, 29, 9, 55, tzinfo=UTC)  # 15:25 IST
    assert bar_end(fifteen[-1], "15m") == datetime(2026, 10, 29, 10, 0, tzinfo=UTC)  # 15:30


def _bars(days: list[date], volume=lambda i, k: 100.0) -> pd.DataFrame:
    rows = []
    for i, d in enumerate(days):
        for k, t in enumerate(bar_starts_for_day(d, "5m")):
            base = 100 + i + k * 0.1
            rows.append({"time": t, "open": base, "high": base + 1, "low": base - 1,
                         "close": base + 0.5, "volume": volume(i, k)})
    return pd.DataFrame(rows)


def test_vwap_bands_and_day_range():
    out = im.bar_metrics(_bars([date(2026, 10, 29)]))
    tp0 = (101 + 99 + 100.5) / 3
    assert out["vwap"].iloc[0] == pytest.approx(tp0)
    tps = [(100 + k * 0.1) + 0.5 / 3 for k in range(3)]
    assert out["vwap"].iloc[2] == pytest.approx(np.mean(tps))  # equal volume: plain average
    assert out["vwap_upper_1"].iloc[2] == pytest.approx(np.mean(tps) + np.std(tps))
    assert out["day_high"].iloc[4] == pytest.approx(100.4 + 1)
    assert out["day_low"].iloc[4] == 99


def test_missing_volume_makes_vwap_missing_for_the_rest_of_the_day():
    days = [date(2026, 10, 28), date(2026, 10, 29)]
    out = im.bar_metrics(_bars(days, volume=lambda i, k: np.nan if (i, k) == (0, 10) else 100))
    first = out[out["session"] == days[0]]
    assert first["vwap"].iloc[:10].notna().all() and first["vwap"].iloc[10:].isna().all()
    assert out[out["session"] == days[1]]["vwap"].notna().all()  # next day starts clean


def test_opening_range_appears_only_once_it_has_finished():
    out = im.bar_metrics(_bars([date(2026, 10, 29)]))
    assert out["or_high"].iloc[:2].isna().all()  # 09:15 and 09:20 bars: range not finished
    assert out["or_high"].iloc[2] == pytest.approx(100.2 + 1)  # 09:25 bar closes at 09:30
    assert out["or_low"].iloc[2] == 99
    assert out["or_high"].iloc[-1] == out["or_high"].iloc[2]


def test_rvol_compares_with_the_same_time_on_past_days():
    days = trading_days(date(2026, 10, 15), date(2026, 10, 29), frozenset())
    out = im.bar_metrics(_bars(days, volume=lambda i, k: 300.0 if i == len(days) - 1 else 100.0))
    last = out[out["session"] == days[-1]]
    assert last["rvol"].iloc[0] == pytest.approx(3.0) and last["rvol"].iloc[-1] == pytest.approx(3.0)
    assert out[out["session"] == days[0]]["rvol"].isna().all()  # no past days yet
    n = im.RVOL_MIN_DAYS
    assert out[out["session"] == days[n]]["rvol"].iloc[0] == 1.0  # enough past days from here
    assert out[out["session"] == days[n - 1]]["rvol"].isna().all()


def test_pivots_cpr_and_gap_use_only_the_previous_day():
    t = [datetime(2026, 10, d, 3, 45, tzinfo=UTC) for d in (28, 29)]
    daily = pd.DataFrame({"time": t, "open": [100, 112], "high": [110, 115],
                          "low": [90, 108], "close": [105, 113]})
    lv = im.daily_levels(daily)
    assert lv["pivot"].isna().iloc[0]
    row = lv.iloc[1]
    p = (110 + 90 + 105) / 3
    assert row["pivot"] == pytest.approx(p)
    assert (row["r1"], row["s1"]) == (pytest.approx(2 * p - 90), pytest.approx(2 * p - 110))
    assert row["r3"] == pytest.approx(110 + 2 * (p - 90))
    assert (row["cpr_bottom"], row["cpr_top"]) == (pytest.approx(100), pytest.approx(2 * p - 100))
    assert row["gap_pct"] == pytest.approx((112 - 105) / 105 * 100)
    assert row["session"] == date(2026, 10, 29)


def test_values_never_use_later_bars():
    days = [date(2026, 10, 28), date(2026, 10, 29)]
    full = im.bar_metrics(_bars(days))
    cut = im.bar_metrics(_bars(days).iloc[:100])
    cols = ["vwap", "or_high", "day_high", "ema9", "rsi14", "rvol"]
    pd.testing.assert_frame_equal(full[cols].iloc[:100], cut[cols])


def test_fifteen_minute_bars_get_a_one_bar_opening_range():
    d = date(2026, 10, 29)
    rows = [{"time": t, "open": 1, "high": 2 + k, "low": 1, "close": 1.5, "volume": 10}
            for k, t in enumerate(bar_starts_for_day(d, "15m"))]
    out = im.bar_metrics(pd.DataFrame(rows))
    assert out["or_high"].iloc[0] == 2 and out["or_high"].iloc[-1] == 2
    assert timedelta(minutes=15) == bar_starts_for_day(d, "15m")[1] - bar_starts_for_day(d, "15m")[0]
