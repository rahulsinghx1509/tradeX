"""Hourly study: daily context from the previous day only, triggers, exits, held-back period."""

import numpy as np
import pandas as pd

from app.services import hourly_study as hs

HOURS = ["09:15", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00"]


def _daily(days, close, spread=2.0):
    return pd.DataFrame({"high": close + spread, "low": close - spread, "close": close}, index=days)


def _hourly(days, close_per_day, spread=0.5):
    idx, close = [], []
    for d, c in zip(days, close_per_day, strict=True):
        for k, t in enumerate(HOURS):
            idx.append(pd.Timestamp(f"{d.date()} {t}"))
            close.append(c + 0.01 * k)
    close = np.array(close)
    return pd.DataFrame({"high": close + spread, "low": close - spread, "close": close},
                        index=pd.DatetimeIndex(idx))


def test_daily_context_comes_from_the_previous_day():
    days = pd.bdate_range("2024-01-01", periods=80)
    close = np.r_[np.full(79, 100.0), 200.0]  # a jump on the last day only
    h = hs.prepare(_daily(days, close), _hourly(days, close))
    last_day = h[h["day"] == days[-1]]
    # The jump day's hourly candles must see the day before's high, not their own day's.
    assert (last_day["prev_high"] == 102.0).all()
    assert h.loc[h["day"] == days[0], "daily_atr"].isna().all()  # nothing before the first day


def test_trade_stop_target_and_time_exit():
    idx = pd.date_range("2024-01-01 09:15", periods=50, freq="h")
    h = pd.DataFrame({"high": 101.0, "low": 99.0, "close": 100.0, "daily_atr": 5.0}, index=idx)
    assert hs._trade(h, 0) == {"exit_row": 35, "exit_reason": "TIME", "r": 0.0}
    h2 = h.copy()
    h2.iloc[3, h2.columns.get_loc("high")] = 111.0  # +2R = 110
    assert hs._trade(h2, 0)["exit_reason"] == "TARGET"
    h2.iloc[3, h2.columns.get_loc("low")] = 94.0  # same candle touches the stop at 95: stop first
    assert hs._trade(h2, 0) == {"exit_row": 3, "exit_reason": "STOP", "r": -1.0}


def test_baseline_buys_first_candle_of_uptrend_days_only():
    days = pd.bdate_range("2023-01-02", periods=120)
    close = np.linspace(100, 160, 120)
    h = hs.prepare(_daily(days, close), _hourly(days, close))
    rows = hs.trigger_rows(h, hs.VARIANTS[0])
    assert len(rows) and (h.index[rows].strftime("%H:%M") == "09:15").all()
    falling = hs.prepare(_daily(days, close[::-1]), _hourly(days, close[::-1]))
    assert len(hs.trigger_rows(falling, hs.VARIANTS[0])) == 0  # no daily uptrend, no trade
    assert len(hs.trigger_rows(falling, hs.VARIANTS[4])) == 0  # no hourly uptrend either


def test_run_keeps_development_trades_only_and_reports():
    days = pd.bdate_range("2024-06-03", "2025-06-30")
    close = 100 + np.cumsum(np.sin(np.arange(len(days)) / 5) + 0.3)
    tr, info = hs.run({"NIFTY": _daily(days, close)}, {"NIFTY": _hourly(days, close)})
    assert len(tr) and (tr["exit_time"] < pd.Timestamp(hs.VALIDATION_FROM)).all()
    assert info["coverage"]["NIFTY"][2] == len(days) * len(HOURS)
    report = hs.to_markdown(tr, info, "now")
    assert "Edge vs A" in report and "validation is held back" in report


def test_no_hourly_history_gives_a_clear_report():
    tr, info = hs.run({"NIFTY": _daily(pd.bdate_range("2024-01-01", periods=5), np.ones(5))},
                      {"NIFTY": pd.DataFrame(columns=["high", "low", "close"])})
    assert tr.empty and "backfill --timeframes 1h" in hs.to_markdown(tr, info, "now")
