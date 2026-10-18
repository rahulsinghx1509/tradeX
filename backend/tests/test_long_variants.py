"""Long-only variants: entries, trailing stop, strength filter, overlap and the dev-only report."""

import numpy as np
import pandas as pd

from app.services import long_variants as lv


def _frame(close: np.ndarray, spread: float = 1.0) -> pd.DataFrame:
    return pd.DataFrame({"high": close + spread, "low": close - spread, "close": close},
                        index=pd.bdate_range(end="2026-10-16", periods=len(close)))


def _wave(n: int = 900) -> np.ndarray:
    t = np.arange(n)
    return 1000 + 300 * np.sin(t / 60) + 40 * np.sin(t / 7) + t * 0.3


def test_pullback_days_touch_ema20_inside_an_uptrend():
    r = lv.prepare(_frame(_wave()), None)
    days = lv.signal_days(r, "pullback")
    assert len(days)
    for i in days:
        assert r["signal"].iat[i] == 1
        assert r["low"].iat[i] <= r["ema20"].iat[i] and r["low"].iat[i - 1] > r["ema20"].iat[i - 1]


def test_onset_days_are_first_day_of_long_run():
    r = lv.prepare(_frame(_wave()), None)
    for i in lv.signal_days(r, "onset"):
        assert r["signal"].iat[i] == 1 and r["signal"].iat[i - 1] != 1


def test_trailing_stop_follows_a_rise_and_exits_on_the_drop():
    close = np.r_[np.full(30, 100.0), np.linspace(100, 140, 20), np.linspace(140, 110, 10)]
    r = lv.prepare(_frame(close, spread=0.5), None)
    t = lv._trail_trade(r, 29)
    assert t["exit_reason"] == "STOP"
    assert t["r"] > 1  # locked in profit, unlike a fixed stop


def test_trailing_stop_never_moves_down_and_times_out():
    close = np.r_[np.full(30, 100.0), np.linspace(100, 160, lv.TRAIL_MAX_HOLD + 5)]
    r = lv.prepare(_frame(close, spread=0.5), None)
    t = lv._trail_trade(r, 29)
    assert t["exit_reason"] == "TIME" and t["days"] == lv.TRAIL_MAX_HOLD and t["r"] > 0


def test_strength_filter_skips_weak_and_missing_reference():
    df = _frame(_wave())
    strong = lv.prepare(df, _frame(np.full(900, 1000.0)))  # flat NIFTY: instrument must rise
    v = next(x for x in lv.VARIANTS if x.key == "D")
    a = next(x for x in lv.VARIANTS if x.key == "A")
    took = lv.trades("X", strong, v)
    assert len(took) <= len(lv.trades("X", strong, a))
    for d in took["signal_day"]:
        assert strong.loc[pd.Timestamp(d), "strength"] > 0
    missing = lv.prepare(df, None)
    assert lv.trades("X", missing, v).empty
    assert len(lv.trades("NIFTY", missing, v))  # the reference itself skips the filter


def test_trades_never_overlap():
    r = lv.prepare(_frame(_wave()), None)
    for v in lv.VARIANTS:
        t = lv.trades("NIFTY", r, v)
        if len(t) > 1:
            assert (pd.Series(t["signal_day"]).iloc[1:].to_numpy()
                    > pd.Series(t["exit_day"]).iloc[:-1].to_numpy()).all()


def test_run_reports_development_only():
    frames = {"NIFTY": _frame(_wave()), "X": _frame(_wave()[::-1].copy())}
    tr, info = lv.run(frames)
    assert len(tr) and (tr["exit_day"] < info["split_date"]).all()
    md = lv.to_markdown(tr, info, "now")
    assert "held back" in md and "| A |" in md and "| F |" in md
