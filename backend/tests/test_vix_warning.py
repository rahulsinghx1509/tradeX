"""VIX-jump warning VJ-1.0: when it fires, staleness, one alert per jump, API field."""

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from app.core.market_time import IST
from app.services import vix_warning as vw
from app.services.collector import CollectorState, run_cycle
from app.services.ingestion import IngestResult, sync_instruments
from tests.test_alerts_and_collector import Recorder, ist

# Thursday 2026-10-15, 16:00 IST: the day's close is final.
AFTER_CLOSE = datetime(2026, 10, 15, 16, 0, tzinfo=IST).astimezone(UTC)


def _vix(closes, end="2026-10-15"):
    days = pd.bdate_range(end=end, periods=len(closes))
    return pd.DataFrame({"high": closes, "low": closes, "close": closes}, index=days)


def test_jump_above_threshold_is_a_warning_that_starts_today():
    closes = np.r_[np.full(20, 12.0), 12.0 * 1.10]  # +10% over 5 days on the last day
    w = vw.evaluate(_vix(closes), AFTER_CLOSE)
    assert w.status == "WARNING" and w.started_today and w.as_of == "2026-10-15"
    assert np.isclose(w.change_5d_pct, 10.0) and "NO TRADE" in w.message
    assert "buy/sell" in w.message


def test_second_day_of_a_jump_is_not_a_new_start():
    closes = np.r_[np.full(20, 12.0), 13.2, 13.3]
    w = vw.evaluate(_vix(closes), AFTER_CLOSE)
    assert w.status == "WARNING" and not w.started_today


def test_small_change_is_clear():
    w = vw.evaluate(_vix(np.r_[np.full(20, 12.0), 12.5]), AFTER_CLOSE)
    assert w.status == "CLEAR" and not w.started_today


def test_just_below_threshold_is_clear():
    w = vw.evaluate(_vix(np.r_[np.full(20, 10.0), 10.0 * (1 + (vw.JUMP_PCT - 0.05) / 100)]),
                    AFTER_CLOSE)
    assert w.status == "CLEAR"


def test_stale_or_missing_vix_means_no_trade():
    stale = vw.evaluate(_vix(np.full(20, 12.0), end="2026-10-01"), AFTER_CLOSE)
    assert stale.status == "NO_DATA" and "stale" in stale.message and "NO TRADE" in stale.message
    assert vw.evaluate(None, AFTER_CLOSE).status == "NO_DATA"
    # One trading day behind is allowed (the close loads after the market shuts).
    assert vw.evaluate(_vix(np.full(20, 12.0), end="2026-10-14"), AFTER_CLOSE).status == "CLEAR"


def test_future_rows_are_ignored():
    """During the day only completed closes count; today's partial row is not used."""
    during = datetime(2026, 10, 15, 11, 0, tzinfo=IST).astimezone(UTC)
    closes = np.r_[np.full(20, 12.0), 15.0]  # the jump is on 2026-10-15, not complete at 11:00
    assert vw.evaluate(_vix(closes), during).status == "CLEAR"


def test_collector_sends_one_alert_when_a_jump_starts(session, provider, monkeypatch):
    sync_instruments(session, provider, ["NIFTY"], 1)
    warning = vw.VixWarning(vw.VERSION, "WARNING", "2026-10-15", 15.0, 9.5, vw.JUMP_PCT, True,
                            "India VIX jumped. Option selling: NO TRADE.")
    monkeypatch.setattr(vw, "refresh", lambda *a: IngestResult(1, "SUCCESS"))
    monkeypatch.setattr(vw, "current", lambda *a: warning)
    rec, state = Recorder(), CollectorState()
    assert run_cycle(session, provider, rec, state, ist(15, 15, 45)) == "end_of_day"
    alerts = [m for m in rec.messages if "volatility warning (VJ-1.0)" in m]
    assert len(alerts) == 1 and "NO TRADE" in alerts[0]
    assert any("Volatility warning: WARNING" in m for m in rec.messages)
    state.eod_done_for = None  # a second end-of-day run for the same close sends nothing new
    run_cycle(session, provider, rec, state, ist(15, 15, 50))
    assert len([m for m in rec.messages if "volatility warning (VJ-1.0)" in m]) == 1


def test_collector_loads_vix_at_end_of_day(session, provider):
    sync_instruments(session, provider, ["NIFTY"], 1)
    rec = Recorder()
    run_cycle(session, provider, rec, CollectorState(), ist(15, 15, 45))
    assert vw.vix_instrument(session, provider.name) is not None
    assert any("Volatility warning:" in m for m in rec.messages)


def test_overview_has_the_warning(client):
    body = client.get("/api/market/overview").json()
    assert body["vix_warning"]["version"] == vw.VERSION
    assert body["vix_warning"]["status"] == "NO_DATA"  # empty database: no VIX stored
