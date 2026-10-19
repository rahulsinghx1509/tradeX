"""VS-1.0: trade construction, settlement, filters and the call-wall check."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.services import feature_screen as fs
from app.services import option_metrics as om
from app.services import option_selling as vs

PREV, EXP, ENTRY = date(2023, 1, 25), date(2023, 2, 23), date(2023, 1, 27)
FWD, IV = 18000.0, 15.0


def frame(untraded: float | None = None) -> pd.DataFrame:
    rows = []
    for d in (date(2023, 1, 20), ENTRY):
        for e in (PREV, EXP):
            if e > d:
                rows.append(dict(trade_date=d, instrument="FUT", expiry_date=e, strike=np.nan,
                                 close=FWD, settle=FWD, volume=100, open_interest=1000))
    t = (EXP - ENTRY).days / 365
    for k in np.arange(16500, 19600, 100.0):
        for kind, call in (("CE", True), ("PE", False)):
            px = om.black76(FWD, k, t, IV / 100, call)
            rows.append(dict(trade_date=ENTRY, instrument=kind, expiry_date=EXP, strike=k,
                             close=round(px, 2), settle=round(px, 2),
                             volume=0 if k == untraded else 50, open_interest=500))
    return pd.DataFrame(rows)


def metrics() -> pd.DataFrame:
    return pd.DataFrame({"atm_iv": [IV]}, index=pd.DatetimeIndex([pd.Timestamp(ENTRY)]))


def spot(at_expiry: float) -> pd.Series:
    return pd.Series([FWD, at_expiry], index=pd.DatetimeIndex([pd.Timestamp(ENTRY), pd.Timestamp(EXP)]))


def test_entry_is_day_after_previous_expiry():
    assert vs.entry_days(frame()) == [(ENTRY, EXP)]


def test_fly_keeps_credit_when_calm_and_loses_at_most_1r():
    calm = {t.structure: t for t in vs.trades_for("NIFTY", frame(), spot(FWD), metrics(), lot=50)}
    fly = calm["fly"]
    # 1 SD = 0.15 × √(27/365) × 18000 ≈ 734 → wings at the nearest strikes 18700 / 17300
    assert (fly.short_call, fly.short_put, fly.long_call, fly.long_put) == (18000, 18000, 18700, 17300)
    assert fly.pnl == pytest.approx(fly.credit) and fly.r > 0  # calm: kept the whole credit
    crash = {t.structure: t for t in vs.trades_for("NIFTY", frame(), spot(15000), metrics(), lot=50)}
    assert crash["fly"].r == pytest.approx(-1.0) and crash["condor"].r == pytest.approx(-1.0)


def test_untraded_leg_means_no_trade():
    trades = vs.trades_for("NIFTY", frame(untraded=18700.0), spot(FWD), metrics())
    assert "fly" not in {t.structure for t in trades}  # its long call never traded


def test_missing_expiry_close_is_never_guessed():
    s = pd.Series([FWD], index=pd.DatetimeIndex([pd.Timestamp(ENTRY)]))
    assert vs.trades_for("NIFTY", frame(), s, metrics()) == []


def test_filters_use_only_past_data():
    t = pd.DataFrame([vars(x) for x in vs.trades_for("NIFTY", frame(), spot(FWD), metrics())])
    days = pd.bdate_range("2023-01-16", "2023-01-27")
    vix = pd.Series(10.0, index=days)
    vix.loc[pd.Timestamp(ENTRY)] = 20.0  # jump on the entry day itself: not known before entry
    rank = pd.DataFrame({"NIFTY": [0.9]}, index=pd.DatetimeIndex([pd.Timestamp(ENTRY)]))
    tagged = vs.tag(t, vix, rank)
    assert not tagged["vix_warning"].any() and tagged["call_wall_top"].all()
    vix.loc[pd.Timestamp("2023-01-26")] = 20.0  # jump the day before: skip
    assert vs.tag(t, vix, rank)["vix_warning"].all()
    assert len(vs.variant(vs.tag(t, vix, rank), "B")) == 0


def test_residual_check_removes_an_iv_copy():
    rng = np.random.default_rng(0)
    idx = pd.bdate_range("2020-01-01", periods=900)
    parts = []
    for i in range(12):
        true_iv = 20 + 5 * np.sin(np.arange(900) / 40 + i)
        iv = true_iv + rng.normal(0, 3, 900)  # measured IV: its noise reverts
        rv = true_iv * np.exp(rng.normal(-0.1, 0.1, 900))
        p = pd.DataFrame({"symbol": f"S{i}", "atm_iv": iv, "iv_rank_1y": rng.uniform(0, 1, 900),
                          "ret_20d": rng.normal(0, 3, 900), "vol_surprise": rng.normal(0, 0.2, 900),
                          "rv_vs_iv": np.log(rv / iv)}, index=idx)
        p["call_wall_pct"] = 0.3 * p["atm_iv"] + rng.normal(0, 0.2, 900)  # a copy of IV only
        parts.append(p)
    res = vs.residual_check(pd.concat(parts))
    raw = res[(res.feature == "call_wall_pct") & (res.target == "rv_vs_iv")].iloc[0]
    resid = res[(res.feature == "call_wall_resid") & (res.target == "rv_vs_iv")].iloc[0]
    assert abs(raw["t"]) > 4 and abs(resid["t"]) < 3


def test_report_renders():
    t = pd.DataFrame([vars(x) for x in vs.trades_for("NIFTY", frame(), spot(FWD), metrics())])
    t = vs.tag(t, None, pd.DataFrame())
    t["vix_warning"] = False
    check = pd.DataFrame({"feature": ["call_wall_pct"], "target": ["rv_vs_iv"], "ic": [-0.1],
                          "t": [-5.0]})
    md = vs.to_markdown(t, check, {"NIFTY": 0}, "now")
    assert "VS-1.0" in md and "| fly | A: every month | 1 |" in md
    assert fs.VALIDATION_FROM.isoformat() in md
