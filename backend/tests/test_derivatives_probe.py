"""Derivatives history probe: counts OI per year and survives failing calls."""

from datetime import UTC, datetime

from app.services import derivatives_probe as dp


class FakeClient:
    def get_expiries(self, **kw):
        if kw["year"] == 2020:
            return {"expiries": []}
        if kw["underlying_symbol"] == "BAD":
            raise RuntimeError("rate limited")
        return {"expiries": [f"{kw['year']}-06-20", f"{kw['year']}-06-27"]}

    def get_contracts(self, **kw):
        e = kw["expiry_date"]
        return {"contracts": [f"NSE-X-{e}-FUT", f"NSE-X-{e}-100-CE", f"NSE-X-{e}-200-CE",
                              f"NSE-X-{e}-300-CE", f"NSE-X-{e}-200-PE"]}

    def get_historical_candles(self, **kw):
        oi = None if kw["start_time"].startswith("2021") else 500
        return {"candles": [["2022-06-01 00:00:00", 1, 2, 0.5, 1.5, 10, oi],
                            ["2022-06-02 00:00:00", 1, 2, 0.5, 1.5, 10, 600]]}


def test_probe_counts_contracts_candles_and_oi():
    rows = dp.probe(FakeClient(), ["NIFTY"], [2020, 2021, 2022])
    r20, r21, r22 = rows
    assert r20.note == "no expiries returned" and r20.fut_candles is None
    assert r22.expiry == "2022-06-27" and r22.futures == 1 and r22.options == 4
    assert (r22.fut_candles, r22.fut_with_oi) == (2, 2)
    assert (r21.fut_candles, r21.fut_with_oi) == (2, 1)  # missing OI is counted as missing
    md = dp.to_markdown(rows, datetime(2026, 10, 18, tzinfo=UTC))
    assert "| NIFTY | 2021 | 2 |" in md


def test_one_failure_does_not_stop_the_probe():
    rows = dp.probe(FakeClient(), ["BAD", "NIFTY"], [2022])
    assert rows[0].note.startswith("error: rate limited")
    assert rows[1].fut_with_oi == 2


def test_middle_call_strike_is_picked():
    assert dp._strike("NSE-NIFTY-26Jun25-24000-CE") == 24000


def test_default_years_include_this_year_only_after_june():
    assert dp.default_years(datetime(2026, 10, 18, tzinfo=UTC))[-1] == 2026
    assert dp.default_years(datetime(2026, 3, 1, tzinfo=UTC))[-1] == 2025
