"""RT-1.0: the ET-1.0 option sell re-test (periods, pass bar, stress grid, report)."""

from datetime import date, datetime

from app.ema921 import engine as EE
from app.spreads import engine as E
from app.spreads import retest as RT
from app.spreads import retest_report as RR
from app.spreads import risk as RK
from tests.test_ema921 import DAY, EXPIRY, WARM, FakeOptions, flat, spot_frame
from tests.test_orb_et import rise_after_range


def _pos(day, net):
    class Leg:
        gross_pnl = net
        slippage = 0.0
        charges = {"total": 0.0}
        net_pnl = net
    return E.Position("x", day, EXPIRY, None, 1, [Leg()])


def test_period_labels():
    assert RT.nov_oct_year(date(2023, 11, 1)) == "Nov 2023 - Oct 2024"
    assert RT.nov_oct_year(date(2024, 10, 31)) == "Nov 2023 - Oct 2024"
    assert RT.financial_year(date(2024, 3, 31)) == "FY2023-24"
    assert RT.financial_year(date(2024, 4, 1)) == "FY2024-25"
    assert RT.calendar_year(date(2025, 12, 31)) == "2025"


def test_by_period_marks_part_years_and_keeps_empty_ones():
    ps = [_pos(date(2023, 11, 6), 500.0), _pos(date(2025, 6, 2), -200.0)]
    rows = RT.by_period(ps, RT.calendar_year, date(2023, 11, 1), date(2026, 11, 27), 100_000)
    assert [r.label for r in rows] == ["2023", "2025", "2026"]  # 2024 has no trades here
    assert [r.complete for r in rows] == [False, True, False]
    assert rows[2].trades == 0 and rows[2].net == 0
    fy = RT.by_period(ps, RT.financial_year, date(2023, 11, 1), date(2026, 11, 27), 100_000)
    assert [r.label for r in fy] == ["FY2023-24", "FY2025-26", "FY2026-27"]
    assert [r.complete for r in fy] == [False, True, False]


def test_verdict_needs_pf_dd_and_every_complete_year():
    first, last = date(2023, 11, 1), date(2026, 11, 27)
    good = []
    for y in (2023, 2024, 2025):  # one win and one smaller loss in each Nov - Oct year
        good += [_pos(date(y, 12, 4), 1000.0), _pos(date(y, 12, 5), -500.0)]
    good.append(_pos(date(2026, 11, 10), -400.0))  # the part-year is not judged
    assert RT.verdict(good, 100_000, first, last).ok
    bad_year = good[:2] + [_pos(date(2024, 12, 4), -100.0)] + good[4:]
    v = RT.verdict(bad_year, 100_000, first, last)
    assert not v.ok and v.pf_ok and not v.years_ok and "Nov 2024 - Oct 2025" in v.reasons[0]
    deep = good + [_pos(date(2025, 1, 6), -25_000.0)]
    v = RT.verdict(deep, 100_000, first, last)
    assert not v.dd_ok and not v.pf_ok


def test_drop_best_removes_the_biggest_winners():
    ps = [_pos(date(2024, 1, d), x) for d, x in ((1, 100.0), (2, 900.0), (3, -50.0), (4, 900.0))]
    assert [p.net_pnl for p in RT.drop_best(ps, 1)] == [100.0, -50.0, 900.0]
    assert [p.net_pnl for p in RT.drop_best(ps, 3)] == [-50.0]
    assert RT.drop_best(ps, 0) == ps


def test_stop_levels_keep_the_limits_and_2pct_is_the_rule():
    lv = RT.level(2000.0)
    base = RK.HOLDOUT_LEVEL
    assert (lv.stop, lv.trail, lv.day, lv.week, lv.day_profit) == \
        (base.stop, base.trail, base.day, base.week, base.day_profit)
    assert RT.level(1500.0).day == base.day and RT.level(2500.0).stop == 2500.0
    assert (RT.SLIPPAGE[0][1], RT.SLIPPAGE[0][2]) == (EE.Config().slippage_pct,
                                                     EE.Config().slippage_points)


def test_report_renders_on_synthetic_data():
    spot = spot_frame((WARM, flat), (DAY, rise_after_range))
    text, kept = RR.build(spot, FakeOptions(spot), DAY, DAY, EE.Config(), DAY, log=lambda _: None)
    for part in ("Bar (fixed before the run)", "By test year", "By calendar year",
                 "By financial year", "Stress test 1", "Stress test 2", "Reference and data check"):
        assert part in text
    assert "FAIL" in text  # one synthetic day has no complete year to judge


def _trade(day, net):
    t = EE.Trade("ATM_SELL", day, 1, "SELL", "PE", 24500, EXPIRY, 7, "S", 1,
                 datetime(day.year, day.month, day.day, 10, 0), datetime(day.year, day.month,
                                                                         day.day, 10, 1),
                 100.0, 99.0, 24500)
    t.exit_time, t.exit_price, t.exit_reason = t.entry_time, 90.0, "TRAIL_STOP"
    t.gross_pnl = t.net_pnl = net
    t.charges = {"total": 0.0}
    return t


def test_report_with_trades(monkeypatch):
    days = [date(2023, 12, 4), date(2024, 12, 4), date(2025, 12, 4), date(2026, 11, 10)]
    ps = E.single("ET ATM sell", [_trade(d, 900.0) for d in days])

    class Res:
        skips, no_trade_days, days_tested = [], {}, days

    monkeypatch.setattr(RR, "run_rule", lambda *a: ({s: (ps, 0) for s in RT.STOPS}, ps, Res()))
    spot = spot_frame((WARM, flat), (DAY, flat))
    text, kept = RR.build(spot, FakeOptions(spot), date(2023, 11, 1), date(2026, 11, 27),
                          EE.Config(), date(2026, 11, 27), log=lambda _: None)
    assert len(kept) == 4 and "**PASS**" not in text  # PF has no loss to divide by
    assert "| 2026-11-10 | 10:01 | 24500 PE |" in text  # the new-days trade list
    assert "FY2026-27" in text and "Nov 2025 - Oct 2026" in text


def test_retest_and_margin_scripts_never_order():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for f in ("scripts/et_retest.py", "scripts/et_margin.py", "backend/app/spreads/retest.py",
              "backend/app/spreads/retest_report.py"):
        src = (root / f).read_text(encoding="utf-8")
        for word in ("place_order", "modify_order", "cancel_order", "smart_order"):
            assert word not in src, (f, word)
    assert "get_order_margin_details" in (root / "scripts/et_margin.py").read_text(encoding="utf-8")


def test_margin_script_asks_the_calculator_only(engine, monkeypatch, tmp_path, capsys):
    import importlib
    import sys
    from pathlib import Path

    from sqlalchemy.orm import sessionmaker

    from tests.test_ema921_loader import FakeGroww

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    M = importlib.import_module("et_margin")

    class Fake(FakeGroww):
        asked: list = []

        def get_instrument_by_groww_symbol(self, groww_symbol):
            _, _, e, k, t = groww_symbol.split("-")
            return {"trading_symbol": f"NIFTY{e.upper()}{k}{t}"}

        def get_order_margin_details(self, segment, orders):
            Fake.asked.append(orders)
            return {"total_requirement": 123456.0}

    class Provider:
        def __init__(self, _settings):
            pass

        def _connect(self):
            return None

        def client(self):
            return Fake()

    monkeypatch.setattr(M, "GrowwProvider", Provider)
    monkeypatch.setattr(M, "get_sessionmaker", lambda: sessionmaker(bind=engine))
    monkeypatch.setattr(M.L, "ist_today", lambda: date(2026, 10, 14))  # Wednesday
    monkeypatch.setattr(M, "REPORTS", tmp_path)
    monkeypatch.setattr(sys, "argv", ["et_margin.py", "--spot", "24512"])
    assert M.main() == 0
    text = (tmp_path / "et_margin.md").read_text(encoding="utf-8")
    assert "ATM 24500, expiry 2026-10-20" in text and "123456" in text
    assert len(Fake.asked) == 5 and Fake.asked[0][0]["transaction_type"] == "SELL"
    assert Fake.asked[0][0]["quantity"] == 65 and Fake.asked[4][1]["trading_symbol"].endswith("24650CE")
