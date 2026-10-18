"""Feature screen: no look-ahead, no leak into validation, BH q-values, front-contract choice."""

import numpy as np
import pandas as pd

from app.services import feature_screen as fs


def _spot(days, seed=0):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, len(days))))
    return pd.DataFrame({"high": close * 1.005, "low": close * 0.995, "close": close}, index=days)


def test_features_do_not_change_when_the_future_changes():
    days = pd.bdate_range("2021-01-01", "2023-12-29")
    spot = _spot(days)
    cut = 500
    later = spot.copy()
    later.iloc[cut + 1:] *= 1.5  # rewrite everything after day `cut`
    a = fs.build_table("X", spot, pd.DataFrame(), spot, spot)
    b = fs.build_table("X", later, pd.DataFrame(), later, later)
    feats = list(fs.FEATURES)
    pd.testing.assert_frame_equal(a[feats].iloc[:cut + 1], b[feats].iloc[:cut + 1])
    # Targets start at D+1's close: the day before the change still sees the old future.
    assert a["ret_5d_fwd"].iloc[cut] != b["ret_5d_fwd"].iloc[cut] or cut + 1 >= len(days)


def test_target_is_return_from_next_close():
    days = pd.bdate_range("2022-01-03", periods=300)
    spot = _spot(days, 1)
    t = fs.build_table("X", spot, pd.DataFrame(), None, None)
    c = spot["close"]
    assert np.isclose(t["ret_5d_fwd"].iloc[10], (c.iloc[16] / c.iloc[11] - 1) * 100)
    assert t["rs20_vs_nifty"].isna().all() and t["vix"].isna().all()  # missing stays missing


def test_dev_only_drops_windows_that_reach_validation():
    days = pd.bdate_range("2024-06-03", "2025-06-30")
    t = fs.build_table("X", _spot(days), pd.DataFrame(), None, None)
    dev = fs.dev_only(t)
    assert dev.index.max() < pd.Timestamp(fs.VALIDATION_FROM)
    assert dev["ret_20d_fwd"].iloc[-21:].isna().all() and dev["ret_20d_fwd"].iloc[-22:-21].notna().all()
    assert dev["vol_surprise"].iloc[-22:].isna().all()


def test_benjamini_hochberg_matches_hand_calculation():
    p = pd.Series([0.01, 0.04, 0.03, 0.20], index=list("abcd"))
    q = fs.benjamini_hochberg(p)
    # sorted 0.01,0.03,0.04,0.20 -> 0.04, 0.053, 0.053, 0.20
    assert np.allclose(q[["a", "c", "b", "d"]], [0.04, 0.04 * 4 / 3, 0.04 * 4 / 3, 0.20])


def test_screen_finds_a_planted_signal_and_not_noise():
    days = pd.bdate_range("2019-01-01", "2024-12-31")
    rng = np.random.default_rng(3)
    dev = pd.DataFrame({f: rng.normal(size=len(days)) for f in fs.FEATURES}, index=days)
    for tgt in fs.TARGETS:
        dev[tgt] = rng.normal(size=len(days))
    dev["ret_10d_fwd"] = dev["rsi14"] + rng.normal(size=len(days))  # planted
    res = fs.screen(dev)
    surv = res[res["survives"]]
    assert ("rsi14", "ret_10d_fwd") in set(zip(surv["feature"], surv["target"], strict=True))
    assert len(surv) <= 5  # pure noise elsewhere rarely survives the correction
    report = fs.to_markdown(dev, res, {"X": {"days": 1, "first": "a", "last": "b", "fut": 0,
                                             "vix": 0}}, "now")
    assert "Survivors" in report and "rsi14" in report


def test_too_little_history_gives_a_clear_report():
    days = pd.bdate_range("2024-01-01", periods=60)
    dev = fs.dev_only(fs.build_table("X", _spot(days), pd.DataFrame(), None, None))
    report = fs.to_markdown(dev, fs.screen(dev), {}, "now")
    assert "Not enough development months" in report


def test_futures_features_use_the_nearest_unexpired_contract():
    days = pd.bdate_range("2023-01-02", periods=40)
    spot = pd.Series(100.0, index=days)
    exp1, exp2 = days[19], days[39]
    rows = []
    for d in days:
        if d <= exp1:
            rows.append((d, 1, exp1, 101.0, 10.0, 1000.0))
        rows.append((d, 2, exp2, 102.0, 5.0, 500.0))
    fut = pd.DataFrame(rows, columns=["day", "contract", "expiry", "close", "volume", "oi"])
    ff = fs.futures_features(spot, fut)
    assert np.isclose(ff.loc[days[0], "basis_pct"], 1.0)  # front = contract 1
    assert np.isclose(ff.loc[days[25], "basis_pct"], 2.0)  # after expiry, contract 2
    assert np.isclose(ff.loc[days[0], "next_month_oi_share"], 500 / 1500)
    assert ff.loc[days[0], "dte"] == (exp1 - days[0]).days
    assert ff["oi_chg_5d_pct"].iloc[25:].fillna(0).eq(0).all()  # flat OI after the roll
    empty = fs.futures_features(spot, pd.DataFrame(columns=fut.columns))
    assert empty.isna().all().all()  # no futures data stays missing


def test_mechanical_volatility_links_never_survive():
    days = pd.bdate_range("2019-01-01", "2024-12-31")
    rng = np.random.default_rng(4)
    dev = pd.DataFrame({f: rng.normal(size=len(days)) for f in [*fs.FEATURES, *fs.TARGETS]},
                       index=days)
    dev["vol_surprise"] = -dev["hv20"] + rng.normal(size=len(days))
    res = fs.screen(dev).set_index(["feature", "target"])
    assert res.loc[("hv20", "vol_surprise"), "q"] < 0.01
    assert not res.loc[("hv20", "vol_surprise"), "survives"]
    assert " m" in fs.to_markdown(dev, res.reset_index(), {}, "now")
