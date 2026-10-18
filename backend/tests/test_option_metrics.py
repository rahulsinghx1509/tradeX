"""Option metrics from fo_daily: IV recovers a known volatility; PCR, max pain and checks."""

from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.db.models import FoDaily
from app.services import option_metrics as om

D = date(2024, 6, 3)
EXPIRY = D + timedelta(days=30)
F = 22000.0


def chain(day: date, vol: float = 0.15, fmt: str = "old", oi_scale: float = 1.0) -> list[FoDaily]:
    t = (EXPIRY - day).days / 365
    rows = [FoDaily(trade_date=day, symbol="NIFTY", instrument="FUT", expiry_date=EXPIRY,
                    close=F, settle=F, open_interest=int(1000 * oi_scale), volume=100,
                    lot_size=None if fmt == "old" else 25, file_format=fmt)]
    for k in np.arange(19000, 25001, 500):
        for call in (True, False):
            oi = 500 if (call and k == 23000) or (not call and k == 21000) else 100
            rows.append(FoDaily(
                trade_date=day, symbol="NIFTY", instrument="CE" if call else "PE",
                expiry_date=EXPIRY, strike=float(k),
                close=round(om.black76(F, float(k), t, vol, call), 2), settle=None,
                open_interest=int(oi * oi_scale), volume=10, file_format=fmt))
    return rows


def test_implied_vol_round_trip():
    px = om.black76(F, 22500, 0.1, 0.2, True)
    assert abs(om.implied_vol(px, F, 22500, 0.1, True) - 20.0) < 0.01
    assert np.isnan(om.implied_vol(0.0, F, 22500, 0.1, True))   # no price, no IV
    assert np.isnan(om.implied_vol(None, F, 22500, 0.1, False))


def test_metrics_from_stored_rows(session):
    session.add_all(chain(D) + chain(D + timedelta(days=1), vol=0.25))
    session.commit()
    m = om.compute(om.load_frame(session, "NIFTY"))
    assert len(m) == 2
    first = m.iloc[0]
    assert abs(first.atm_iv - 15.0) < 0.1 and abs(m.iloc[1].atm_iv - 25.0) < 0.1
    assert abs(first.iv_skew) < 0.1                       # flat volatility: no skew
    assert first.pcr_oi == 1.0
    assert first.call_wall_pct == 100 * (23000 / F - 1)
    assert first.put_wall_pct == 100 * (21000 / F - 1)
    assert 0 < first.straddle_pct < 10 and first.dte == 30
    assert np.isfinite(first.max_pain_pct)


def test_untraded_contracts_give_missing_not_zero(session):
    rows = chain(D)
    for r in rows:
        if r.instrument != "FUT":
            r.volume = 0
    session.add_all(rows)
    session.commit()
    m = om.compute(om.load_frame(session, "NIFTY")).iloc[0]
    assert np.isnan(m.atm_iv) and np.isnan(m.iv_skew) and np.isnan(m.straddle_pct)
    assert m.pcr_oi == 1.0  # OI is still known


def test_units_check_spots_a_lot_size_jump():
    old = pd.concat([pd.DataFrame([{"trade_date": D + timedelta(days=i), "instrument": "FUT",
                                    "expiry_date": EXPIRY, "open_interest": 25000.0,
                                    "lot_size": np.nan, "file_format": "old"}]) for i in range(5)])
    new = pd.concat([pd.DataFrame([{"trade_date": D + timedelta(days=10 + i), "instrument": "FUT",
                                    "expiry_date": EXPIRY, "open_interest": 1000.0,
                                    "lot_size": 25.0, "file_format": "udiff"}]) for i in range(5)])
    u = om.units_check(pd.concat([old, new]))
    assert u["ratio"] == 1000 / 25000 and u["lot"] == 25


def test_report_runs_the_vix_check():
    idx = pd.date_range("2024-01-01", periods=30, freq="B")
    iv = pd.Series(np.linspace(12, 20, 30), index=idx)
    nifty = pd.DataFrame({m: np.nan for m in om.METRICS}, index=idx).assign(atm_iv=iv)
    md = om.to_markdown({"NIFTY": nifty}, {}, iv + 0.5, "now")
    assert "correlation 1.00" in md and "PASS" in md
