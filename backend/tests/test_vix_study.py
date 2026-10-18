"""VIX study: forward realised volatility, no leak into validation, loading VIX as inactive."""

import numpy as np
import pandas as pd
from sqlalchemy import select

from app.db.models import Instrument, MarketCandle
from app.services import data_quality as dq
from app.services import vix_study as vs
from app.services.ingestion import sync_instruments
from tests.conftest import NOW


def _frame(days, close):
    close = np.asarray(close, dtype=float)
    return pd.DataFrame({"high": close, "low": close, "close": close}, index=days)


def test_realised_uses_the_next_21_days_only():
    days = pd.bdate_range("2023-01-02", periods=60)
    # Calm for 30 days, then alternating ±1% moves: realised is 0 until the window sees them.
    rets = np.r_[np.zeros(30), np.tile([0.01, -0.01], 15)]
    nifty = _frame(days, 100 * np.exp(np.cumsum(rets)))
    t = vs.daily_table(nifty, _frame(days, np.full(60, 15.0)))
    assert t.loc[days[0], "realised"] == 0  # returns of days 1-21 are all zero
    assert t.loc[days[29], "realised"] > 10  # days 30-50 all move 1%
    assert np.isclose(t.loc[days[0], "gap"], 15.0) and np.isclose(t.loc[days[0], "seller_share"], 1.0)
    assert t.index[-1] == days[60 - 1 - vs.WINDOW]  # last day with a full forward window


def test_run_drops_windows_that_reach_into_validation():
    days = pd.bdate_range("2024-10-01", "2025-06-30")
    rng = np.random.default_rng(0)
    nifty = _frame(days, 100 * np.exp(np.cumsum(rng.normal(0, 0.01, len(days)))))
    df = vs.run(nifty, _frame(days, np.full(len(days), 14.0)))
    dev_days = days[days < pd.Timestamp(vs.VALIDATION_FROM)]
    assert df.index[-1] == dev_days[-1 - vs.WINDOW]
    report = vs.to_markdown(df, len(days), "now")
    assert "By VIX level" in report and "validation is held back" in report


def test_no_vix_history_gives_a_clear_report():
    report = vs.to_markdown(vs.run(_frame(pd.bdate_range("2024-01-01", periods=5), np.ones(5)),
                                   _frame(pd.DatetimeIndex([]), [])), 0, "now")
    assert "NSE-INDIAVIX" in report


def test_load_vix_stores_inactive_without_spike_check(session, provider):
    sync_instruments(session, provider, ["NIFTY"], 1)
    res = vs.load_vix(session, provider, NOW)
    assert res.status == "SUCCESS" and res.rows_written > 0
    vix = session.scalar(select(Instrument).where(Instrument.symbol == dq.VIX_SYMBOL))
    assert vix.is_active is False and vix.provider_symbol == "NSE-INDIAVIX"
    assert session.scalar(select(MarketCandle).where(MarketCandle.instrument_id == vix.id)) is not None
    assert vs.load_vix(session, provider, NOW).status == "SUCCESS"  # safe to re-run


def test_report_warns_when_values_do_not_look_like_vix():
    days = pd.bdate_range("2024-01-01", periods=80)
    nifty = _frame(days, 100 * np.exp(np.cumsum(np.tile([0.01, -0.01], 40))))
    assert "WARNING" in vs.to_markdown(vs.run(nifty, _frame(days, np.full(80, 2500.0))), 80, "now")
    assert "WARNING" not in vs.to_markdown(vs.run(nifty, _frame(days, np.full(80, 15.0))), 80, "now")
