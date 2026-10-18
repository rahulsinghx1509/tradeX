"""Open-interest study: roll-safe OI changes, labels, no look-ahead and held-back validation."""

import numpy as np
import pandas as pd

from app.services import oi_study as oi


def _long(days, contracts):
    """contracts: {name: (first_index, last_index, start_oi, daily_step)}"""
    rows = []
    for name, (a, b, start, step) in contracts.items():
        for k, i in enumerate(range(a, b + 1)):
            rows.append((days[i], name, start + step * k))
    return pd.DataFrame(rows, columns=["day", "contract", "oi"])


def test_expiry_and_listing_do_not_count_as_oi_change():
    days = pd.bdate_range("2024-01-01", periods=6)
    # Contract A expires after day 2; B is listed on day 3 with a big OI. Each grows +10/day.
    long = _long(days, {"A": (0, 2, 1000, 10), "B": (3, 5, 5000, 10), "C": (0, 5, 2000, 10)})
    d = oi.daily_oi(long)
    assert np.isnan(d["oi_change"].iloc[0])
    assert d["oi_change"].iloc[1:].tolist() == [20, 20, 10, 20, 20]  # day 3: only C is common
    assert d["total_oi"].iloc[3] == 5000 + 2030


def test_labels_follow_price_and_oi_direction():
    days = pd.bdate_range("2024-01-01", periods=30)
    price = pd.Series(np.linspace(100, 130, 30), index=days)
    rising = oi.daily_oi(_long(days, {"A": (0, 29, 1000, 50)}))
    falling = oi.daily_oi(_long(days, {"A": (0, 29, 5000, -50)}))
    assert (oi.label_days(price, rising)["label"].dropna() == "LONG_BUILDUP").all()
    assert (oi.label_days(price, falling)["label"].dropna() == "SHORT_COVERING").all()
    assert (oi.label_days(price[::-1].set_axis(days), rising)["label"].dropna()
            == "SHORT_BUILDUP").all()


def test_forward_return_starts_next_day():
    days = pd.bdate_range("2024-01-01", periods=40)
    price = pd.Series(np.arange(100.0, 140.0), index=days)
    d = oi.label_days(price, oi.daily_oi(_long(days, {"A": (0, 39, 1000, 5)})))
    i = 10
    assert d["ret_5d_pct"].iat[i] == (price.iat[i + 6] / price.iat[i + 1] - 1) * 100


def test_run_reports_development_only_and_drops_leaking_returns():
    days = pd.bdate_range("2024-10-01", "2025-06-30")
    rng = np.random.default_rng(3)
    price = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, len(days)))), index=days)
    long = _long(days, {"A": (0, len(days) - 1, 10_000, 0)})
    long["oi"] = long["oi"] + rng.integers(-300, 300, len(long))
    df = oi.run({"NIFTY": price}, {"NIFTY": long})
    assert (df["day"].dt.date < oi.VALIDATION_FROM).all()
    assert df["ret_20d_pct"].tail(21).isna().all() and df["ret_20d_pct"].notna().any()
    md = oi.to_markdown(df, "now")
    assert "LONG_BUILDUP" in md and "held back" in md


def test_empty_input_explains_what_to_run():
    assert "futures-history" in oi.to_markdown(pd.DataFrame(), "now")
