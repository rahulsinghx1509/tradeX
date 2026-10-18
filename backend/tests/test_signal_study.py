"""Signal study (backtest of the four core readings): timing, weekly mapping and trade rules."""

import numpy as np
import pandas as pd

from app.services import signal_study as ss


def _frame(close: np.ndarray, spread: float = 1.0, end: str = "2026-10-16") -> pd.DataFrame:
    return pd.DataFrame({"high": close + spread, "low": close - spread, "close": close},
                        index=pd.bdate_range(end=end, periods=len(close)))


def _wave(n: int = 700) -> np.ndarray:
    t = np.arange(n)
    return 1000 + 300 * np.sin(t / 60) + t * 0.2


def test_no_look_ahead_readings_do_not_change_when_future_is_added():
    df = _frame(_wave())
    full = ss.daily_readings(df)
    cut = 500
    past = ss.daily_readings(df.iloc[:cut])
    cols = ["D1", "D2", "D3", "D4", "signal"]
    pd.testing.assert_frame_equal(full[cols].iloc[:cut], past[cols])


def test_weekly_reading_only_counts_finished_weeks():
    df = _frame(_wave())
    r = ss.daily_readings(df)
    # Monday-Thursday share the previous week's reading; Friday switches to its own week.
    week = r.loc["2026-10-12":"2026-10-15", "weeks"]
    assert week.nunique() == 1
    assert r.loc["2026-10-16", "weeks"] == week.iloc[0] + 1


def test_warm_up_days_have_no_signal():
    r = ss.daily_readings(_frame(_wave()))
    assert r["signal"].iloc[: ss.MIN_DAILY_BARS - 1].isna().all()
    assert r["signal"].notna().any()


def test_episodes_enter_next_day_and_count_only_new_signals():
    df = _frame(_wave())
    ep = ss.episodes("X", df)
    r = ss.daily_readings(df)
    assert len(ep) and set(ep["direction"]) <= {"LONG", "SHORT"}
    first = ep.iloc[0]
    i = r.index.get_loc(pd.Timestamp(first["signal_day"]))
    assert first["entry_day"] == r.index[i + 1].date()
    assert first["entry"] == r["close"].iat[i + 1]
    assert r["signal"].iat[i - 1] != r["signal"].iat[i]  # an onset, not a repeat


def test_trade_hits_target_stop_or_time():
    # Steady rise after entry: a long reaches its 2R target.
    close = np.r_[np.full(30, 100.0), np.linspace(100, 130, 15)]
    r = ss.daily_readings(_frame(close, spread=0.5))
    t = ss._trade(r, 29, 1)
    assert t["exit_reason"] == "TARGET" and t["r"] == ss.TARGET_R
    # The same path is a loss for a short.
    assert ss._trade(r, 29, -1)["exit_reason"] == "STOP"
    # Flat after entry: exits on time with ~0R.
    flat = ss.daily_readings(_frame(np.full(45, 100.0), spread=0.5))
    t = ss._trade(flat, 29, 1)
    assert t["exit_reason"] == "TIME" and abs(t["r"]) < 1e-9


def test_stats_and_summary():
    s = ss.stats(pd.Series([1.0, -1.0, 2.0, None]))
    assert s.n == 3 and round(s.hit_pct) == 67 and round(s.mean, 3) == 0.667
    t = ss.trade_summary(pd.Series([2.0, -1.0, -1.0, 2.0]))
    assert t["trades"] == 4 and t["expectancy_r"] == 0.5 and t["profit_factor"] == 2.0
    assert t["max_losing_streak"] == 2


def test_report_runs_end_to_end():
    frames = {"A": _frame(_wave()), "B": _frame(_wave()[::-1].copy())}
    ep, bl, info = ss.run(frames)
    assert set(ep["period"]) <= {"development", "validation"}
    md = ss.to_markdown(ep, bl, info, "now")
    assert "Forward return" in md and "| A |" in md and "no options, costs or slippage" in md
