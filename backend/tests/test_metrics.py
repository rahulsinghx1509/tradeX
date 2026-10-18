"""Indicator formulas and the Milestone 2 metrics built on them."""

from datetime import UTC, datetime

import numpy as np
import pandas as pd
from sqlalchemy.orm import sessionmaker

from app.services import indicators as ind
from app.services import metrics
from app.services.ingestion import backfill, sync_instruments
from tests.conftest import NOW


def _days(n: int) -> pd.DatetimeIndex:
    return pd.bdate_range(end="2026-10-15", periods=n)


def _frame(close: np.ndarray, spread: float = 1.0) -> pd.DataFrame:
    return pd.DataFrame({"high": close + spread, "low": close - spread, "close": close},
                        index=_days(len(close)))


def test_ema_of_constant_is_constant_and_warmup_is_missing():
    out = ind.ema(pd.Series([100.0] * 30), 20)
    assert out.iloc[:19].isna().all()
    assert (out.iloc[19:] == 100.0).all()


def test_rsi_is_100_when_price_only_rises():
    out = ind.rsi(pd.Series(np.arange(1.0, 41.0)), 14)
    assert out.iloc[:14].isna().all() and (out.iloc[14:] == 100.0).all()


def test_atr_equals_constant_range():
    close = pd.Series([100.0] * 40)
    out = ind.atr(close + 2, close - 2, close, 14)
    assert out.iloc[:14].isna().all() and np.allclose(out.iloc[14:], 4.0)


def test_adx_uptrend_has_plus_di_leading():
    close = pd.Series(np.linspace(100, 200, 100))
    adx, plus_di, minus_di = ind.adx(close + 1, close - 1, close, 14)
    assert adx.iloc[:28].isna().all()
    assert plus_di.iloc[-1] > minus_di.iloc[-1] and adx.iloc[-1] > 20


def test_weekly_closes_drop_unfinished_week():
    close = pd.Series(range(8), index=pd.bdate_range("2026-10-05", periods=8), dtype=float)
    # Mon 5 Oct .. Wed 14 Oct: the second week is not over on Wednesday.
    assert list(ind.weekly_closes(close, pd.Timestamp("2026-10-14"))) == [4.0]
    assert list(ind.weekly_closes(close, pd.Timestamp("2026-10-16"))) == [4.0, 7.0]


def test_last_complete_day():
    assert str(metrics.last_complete_day(NOW)) == "2026-10-15"  # Thursday after the close
    before_close = datetime(2026, 10, 15, 5, 0, tzinfo=UTC)  # 10:30 IST
    assert str(metrics.last_complete_day(before_close)) == "2026-10-14"


def test_uptrend_reads_up_and_missing_stays_missing():
    m = metrics.compute(_frame(np.linspace(100, 300, 450)), NOW)
    assert m.status == "OK" and m.as_of.isoformat() == "2026-10-15"
    assert m.readings["D1_daily_trend"] == "UP"
    assert m.readings["D2_weekly_trend"] == "UP"
    assert m.readings["D3_trend_strength"] == "UP"
    assert m.values["ema200"] is not None and m.values["atr14"] is not None
    assert m.values["rs20_vs_nifty"] is None  # no reference given: missing, not 0


def test_short_history_is_insufficient_not_zero():
    m = metrics.compute(_frame(np.linspace(100, 120, 60)), NOW)
    assert m.status == "INSUFFICIENT_HISTORY"
    assert m.values["ema50"] is None and m.values["close"] == 120.0
    assert set(m.readings.values()) == {"MISSING"}
    assert metrics.compute(pd.DataFrame(columns=["high", "low", "close"]), NOW).status == "NO_DATA"


def test_relative_strength_vs_reference():
    stock = _frame(np.linspace(100, 110, 300))
    nifty = _frame(np.full(300, 100.0))
    m = metrics.compute(stock, NOW, reference=nifty)
    expected = stock["close"].iloc[-1] / stock["close"].iloc[-21]
    assert m.values["rs20_vs_nifty"] == round(expected, 3)


def test_metrics_on_sample_data_and_overview(engine, provider, client):
    with sessionmaker(bind=engine, expire_on_commit=False)() as s:
        sync_instruments(s, provider, ["NIFTY", "BANKNIFTY"], 2)
        backfill(s, provider, "1d", 400, now=NOW)
    body = client.get("/api/market/overview").json()
    assert body["metrics_version"] == metrics.METRICS_VERSION
    nifty, bank = body["underlyings"]
    assert nifty["metrics"]["bars"] > 0 and nifty["metrics"]["values"]["rs20_vs_nifty"] is None
    assert set(bank["metrics"]["readings"]) == {"D1_daily_trend", "D2_weekly_trend",
                                                "D3_trend_strength", "D4_momentum"}


# --------------------------------------------------------------------------- hourly readings
def _hours(close: np.ndarray, spread: float = 1.0) -> pd.DataFrame:
    idx = pd.date_range("2026-09-01 09:15", periods=len(close), freq="h")
    return pd.DataFrame({"high": close + spread, "low": close - spread, "close": close}, index=idx)


def test_hourly_readings_need_enough_candles():
    m = metrics.compute_hourly(_hours(np.linspace(100, 110, 50)))
    assert m.status == "INSUFFICIENT_HISTORY"
    assert set(m.readings.values()) == {"MISSING"} and m.values == {}


def test_hourly_uptrend_reads_up_and_no_data_is_reported():
    m = metrics.compute_hourly(_hours(np.linspace(100, 160, 200)))
    assert m.status == "OK" and m.readings["H1_hourly_trend"] == "UP"
    assert m.readings["H3_hourly_pullback"] == "NEUTRAL"  # far above EMA20, no dip
    assert m.as_of == _hours(np.zeros(200)).index[-1].strftime("%Y-%m-%d %H:%M")
    assert metrics.compute_hourly(pd.DataFrame(columns=["high", "low", "close"])).status == "NO_DATA"


def test_hourly_pullback_is_a_dip_to_ema20_that_closes_above_it():
    close = np.linspace(100, 160, 200)
    df = _hours(close, spread=0.2)
    ema20 = ind.ema(df["close"], 20).iloc[-1]
    df.iloc[-1, df.columns.get_loc("low")] = ema20 - 0.5  # last candle dips through EMA20 ...
    m = metrics.compute_hourly(df)  # ... and still closes above it
    assert m.readings["H1_hourly_trend"] == "UP" and m.readings["H3_hourly_pullback"] == "UP"
