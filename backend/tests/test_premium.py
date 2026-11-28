"""PR-1.0: iron fly construction, A/B entry days, settlement, filters and the pass bar."""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from app import premium as P
from app.premium import engine as E
from app.premium import report as R
from app.services import option_metrics as om
from app.services import vix_warning

PREV_EXP, EXP = date(2024, 1, 4), date(2024, 1, 11)  # Thursdays
DAYS = [date(2024, 1, 4), date(2024, 1, 5), date(2024, 1, 8), date(2024, 1, 9),
        date(2024, 1, 10), date(2024, 1, 11)]
FRI, MON = date(2024, 1, 5), date(2024, 1, 8)
S0, IV = 21700.0, 0.13


def frame(spot_by_day: dict | None = None, untraded: tuple | None = None) -> pd.DataFrame:
    rows = []
    for d in DAYS:
        s = (spot_by_day or {}).get(d, S0)
        for e in (PREV_EXP, EXP, date(2024, 1, 18)):
            if e < d:
                continue
            t = max((e - d).days, 0.2) / 365
            for k in np.arange(20500, 23000, 50.0):
                for kind in ("CE", "PE"):
                    px = max(0.05, round(om.black76(s, k, t, IV, kind == "CE"), 2))
                    vol = 0 if untraded == (d, kind, k) else 100
                    rows.append(dict(trade_date=d, instrument=kind, expiry_date=e, strike=k,
                                     close=px, settle=px, volume=vol))
    return pd.DataFrame(rows)


def spot(values: dict | None = None) -> pd.Series:
    days = pd.bdate_range("2023-11-01", "2024-01-11")
    s = pd.Series(S0 * np.exp(np.random.default_rng(1).normal(0, 0.006, len(days)).cumsum()), index=days)
    s[pd.Timestamp(FRI)] = S0
    for d, v in (values or {}).items():
        s[pd.Timestamp(d)] = v
    return s


def vix(jump: bool = False) -> pd.Series:
    days = pd.bdate_range("2023-11-01", "2024-01-11")
    v = pd.Series(14.0, index=days)
    if jump:
        v[pd.Timestamp(date(2024, 1, 4))] = 16.0  # +14% over 5 days, day before Friday
    return v


def test_vj_threshold_matches_project_rule():
    assert P.VJ_JUMP_PCT == vix_warning.JUMP_PCT


def test_open_fly_strikes_and_wing():
    book = E.Book(frame())
    legs, wing = E.open_fly(book, FRI, EXP, S0)
    atm = legs[0].strike
    assert atm == 21700
    assert wing == max(100.0, round((legs[0].entry + legs[1].entry) / 50) * 50)
    assert [lg.strike for lg in legs] == [atm, atm, atm + wing, atm - wing]
    assert [lg.side for lg in legs] == [-1, -1, 1, 1]


def test_untraded_leg_skips_entry():
    book = E.Book(frame(untraded=(FRI, "PE", 21700.0)))
    assert E.open_fly(book, FRI, EXP, S0) is None


def test_candidate_a_friday_to_monday_gross_and_costs():
    sk = {}
    book = E.Book(frame())
    trades = E.build_a(book, spot(), vix(), sk)
    assert [(t.entry_day, t.exit_day, t.expiry) for t in trades] == [(FRI, MON, EXP)]
    t = trades[0]
    exits = [book.traded(MON, EXP, lg.kind, lg.strike) for lg in t.legs]
    credit = sum(-lg.side * lg.entry for lg in t.legs)
    back = sum(-lg.side * px for lg, px in zip(t.legs, exits, strict=True))
    assert t.gross == pytest.approx((credit - back) * P.LOT)
    assert t.gross > 0  # flat market over a weekend: time decay favours the seller
    assert t.net < t.gross and t.net_2x < t.net
    assert t.costs == pytest.approx(t.gross - t.net)
    assert not t.vj_blocked


def test_candidate_b_settles_on_expiry_close_and_caps_loss():
    book = E.Book(frame())
    up = spot({EXP: S0 + 2000})  # far beyond the call wing
    trades = E.build_b(book, up, vix(), {})
    t = [x for x in trades if x.expiry == EXP][0]
    assert t.entry_day == FRI
    assert t.gross == pytest.approx(-t.max_loss)  # lost the full wing minus the credit
    calm = E.build_b(book, spot({EXP: t.atm}), vix(), {})
    c = [x for x in calm if x.expiry == EXP][0]
    assert c.gross == pytest.approx(c.credit_pts * P.LOT)  # pinned: keeps the whole credit
    # out-of-the-money legs cost nothing at expiry; pinned at ATM all four expire worthless
    assert c.costs == pytest.approx(c.gross - c.net)


def test_vj_filter_blocks_jump_and_missing_vix():
    assert E.vj_blocked(vix(jump=True), FRI)
    assert not E.vj_blocked(vix(), FRI)
    assert E.vj_blocked(None, FRI)
    stale = vix()[vix().index < pd.Timestamp("2023-12-20")]
    assert E.vj_blocked(stale, FRI)


def test_vrp_filter_uses_only_past_days():
    s = spot()
    hv = E.hv20(s, FRI)
    assert hv is not None and 5 < hv < 20
    v = vix()
    assert E.vrp_ok(v * 0 + hv + 1, s, FRI)
    assert not E.vrp_ok(v * 0 + hv - 1, s, FRI)
    s2 = s.copy()
    s2[pd.Timestamp(FRI)] = S0 * 1.5  # the entry day itself must not count
    assert E.hv20(s2, FRI) == pytest.approx(hv)


def _fake(nets: list[float], start=date(2020, 1, 3)) -> list[E.Trade]:
    out = []
    for i, n in enumerate(nets):
        d = start + timedelta(days=7 * i)
        out.append(E.Trade("A", d, d + timedelta(days=3), d + timedelta(days=6), S0, S0, 200,
                           100, 90, gross=n + 300, net=n, net_2x=n - 50, costs=300, max_loss=5000))
    return out


def test_bar_pass_and_each_failure():
    nets = [1500, -1000] * 200  # 400 weekly trades over ~7.7 years, PF 1.5
    s = E.summarize(_fake(nets))
    assert s["verdict"] == "PASS", s["checks"]
    s = E.summarize(_fake([1500, -1000] * 20))
    assert not s["checks"]["trades"] and s["verdict"] == "FAIL"
    s = E.summarize(_fake([1050, -1000] * 200))
    assert not s["checks"]["pf"]
    big = [-1000] * 25 + [1500, -1000] * 200  # 25 straight losses = ₹25k drawdown
    assert not E.summarize(_fake(big))["checks"]["dd"]
    lucky = [-100] * 300 + [20000] * 5  # everything rides on 5 trades
    s = E.summarize(_fake(lucky))
    assert not s["checks"]["drop5"]


def test_report_renders():
    book = E.Book(frame())
    trades = E.build_a(book, spot(), vix(), {}) + E.build_b(book, spot({EXP: S0}), vix(), {})
    md = R.to_markdown(trades, {"A: no exit price": 1}, {"first": DAYS[0], "last": DAYS[-1],
                                                         "spot_last": EXP, "vix_last": EXP}, "now")
    assert "Verdict" in md and "**FAIL**" in md  # too few trades to pass
