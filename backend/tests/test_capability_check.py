from datetime import UTC, datetime

import pandas as pd

from app.services.capability_check import CapabilityChecker, to_markdown

NOW = datetime(2026, 10, 15, 10, 30, tzinfo=UTC)


class FakeClient:
    def get_all_instruments(self):
        return pd.DataFrame([
            {"exchange": "NSE", "segment": "FNO", "instrument_type": "FUT", "underlying_symbol": "NIFTY",
             "trading_symbol": "NIFTY26OCTFUT", "groww_symbol": "NSE-NIFTY-27Oct26-FUT",
             "expiry_date": "2026-10-27", "lot_size": 65},
        ])

    def get_historical_candles(self, **kw):
        if kw["segment"] == "FNO":
            return {"candles": [["2026-10-14 00:00:00", 1, 2, 0.5, 1.5, 10, 500]]}
        return {"candles": [["2026-10-14 09:15:00", 1, 2, 0.5, 1.5, 0, None]]}

    def get_expiries(self, **kw):
        return {"expiries": ["2025-06-26"]} if kw.get("year") else {"expiries": ["2026-10-27"]}

    def get_contracts(self, **kw):
        return {"contracts": ["NSE-NIFTY-26Jun25-FUT", "NSE-NIFTY-26Jun25-24000-CE"]}

    def get_quote(self, **kw):
        if kw["trading_symbol"] == "INDIAVIX":
            raise RuntimeError("instrument not found")
        return {"last_price": 1.0, "open_interest": 5}

    def get_option_chain(self, **kw):
        return {"strikes": {"25000": {"CE": {"greeks": {"delta": 0.5, "iv": 12}}}}}


def test_checker_reports_each_capability_without_crashing():
    rows = CapabilityChecker(FakeClient(), NOW).run_all()
    by_name = {r.capability: r.status for r in rows}
    assert by_name["NIFTY futures open interest"] == "SUPPORTED"
    assert by_name["BANKNIFTY futures open interest"] == "UNSUPPORTED"
    assert by_name["Expired contracts history (futures + options)"] == "SUPPORTED"
    assert by_name["India VIX quote (symbol INDIAVIX is a guess)"] == "SOURCE_UNAVAILABLE"
    md = to_markdown(rows, NOW)
    assert "| Capability | Status | Evidence |" in md
