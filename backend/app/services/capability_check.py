"""Milestone 0: check what the Groww API really gives on YOUR account.

Each check calls one documented SDK method and records:
  SUPPORTED          - the call worked and returned the expected fields
  UNSUPPORTED        - the call worked but the data is not there
  SOURCE_UNAVAILABLE - the call failed (error message is recorded)
The report contains counts, field names and timestamps only - no prices - so it
is safe to share or commit.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.market_time import to_ist
from app.providers.groww import INTERVALS, parse_candle_time, parse_candles

S, U, X = "SUPPORTED", "UNSUPPORTED", "SOURCE_UNAVAILABLE"


@dataclass
class CheckRow:
    capability: str
    status: str
    evidence: str


def _fmt(t: str, days_back: int = 0, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    return to_ist(now - timedelta(days=days_back)).strftime(t)


class CapabilityChecker:
    def __init__(self, client: Any, now: datetime | None = None):
        self.c = client
        self.now = now or datetime.now(UTC)
        self.rows: list[CheckRow] = []
        self.ctx: dict[str, Any] = {}

    def _run(self, name: str, fn: Callable[[], tuple[str, str]]) -> None:
        try:
            status, evidence = fn()
        except Exception as exc:  # report every failure, never hide it
            status, evidence = X, f"{type(exc).__name__}: {str(exc)[:200]}"
        self.rows.append(CheckRow(name, status, evidence))

    def _candles(self, segment: str, symbol: str, tf: str, days: int, end_days_back: int = 0):
        payload = self.c.get_historical_candles(
            exchange="NSE", segment=segment, groww_symbol=symbol,
            start_time=_fmt("%Y-%m-%d 09:15:00", days + end_days_back, self.now),
            end_time=_fmt("%Y-%m-%d 15:30:00", end_days_back, self.now),
            candle_interval=INTERVALS[tf],
        )
        return payload or {}

    # ----------------------------------------------------------------- checks
    def instruments(self) -> tuple[str, str]:
        df = self.c.get_all_instruments()
        self.ctx["instruments"] = df
        cols = sorted(map(str, df.columns))
        found = []
        for sym in ("NIFTY", "BANKNIFTY"):
            f = df[(df["underlying_symbol"] == sym) & (df["instrument_type"] == "FUT")]
            if len(f):
                f = f.sort_values("expiry_date")
                self.ctx[f"fut_{sym}"] = f.iloc[0].to_dict()
                found.append(f"{sym}: {len(f)} futures, lot_size={f.iloc[0].get('lot_size')}, "
                             f"groww_symbol e.g. {f.iloc[0].get('groww_symbol')}")
            idx = df[(df["trading_symbol"] == sym) & (df["segment"] == "CASH")]
            found.append(f"{sym} index row: "
                         + (f"instrument_type={idx.iloc[0].get('instrument_type')}, "
                            f"groww_symbol={idx.iloc[0].get('groww_symbol')}" if len(idx) else "not found"))
        status = S if any("futures" in x for x in found) else U
        return status, f"{len(df)} rows; columns={cols}; " + " | ".join(found)

    def index_candles(self, sym: str, tf: str, days: int) -> tuple[str, str]:
        payload = self._candles("CASH", f"NSE-{sym}", tf, days)
        raw = payload.get("candles") or []
        if not raw:
            return U, f"0 candles for NSE-{sym} {tf} over {days} days"
        candles = parse_candles(payload, tf)
        first_raw = raw[0][0]
        vol_null = sum(1 for c in candles if c.volume in (None, 0))
        evidence = (f"{len(raw)} candles; first raw timestamp={first_raw!r}; "
                    f"first parsed IST={to_ist(candles[0].time).isoformat()}; "
                    f"volume missing/zero in {vol_null}")
        if tf == "1h":
            times = sorted({to_ist(parse_candle_time(r[0])).strftime("%H:%M") for r in raw})
            evidence += f"; raw start times seen={times}"
        return S, evidence

    def futures_oi(self, sym: str) -> tuple[str, str]:
        fut = self.ctx.get(f"fut_{sym}")
        if not fut:
            return U, "no futures contract found in the instrument list"
        payload = self._candles("FNO", fut["groww_symbol"], "1d", 20)
        candles = parse_candles(payload, "1d")
        with_oi = sum(1 for c in candles if c.open_interest is not None)
        return (S if with_oi else U), f"{len(candles)} daily candles, {with_oi} with open interest"

    def expired_contracts(self) -> tuple[str, str]:
        year = to_ist(self.now).year - 1
        exp = (self.c.get_expiries(exchange="NSE", underlying_symbol="NIFTY", year=year, month=6)
               or {}).get("expiries") or []
        if not exp:
            return U, f"no expiries returned for NIFTY {year}-06"
        contracts = (self.c.get_contracts(exchange="NSE", underlying_symbol="NIFTY",
                                          expiry_date=exp[-1]) or {}).get("contracts") or []
        options = [c for c in contracts if c.endswith(("CE", "PE"))]
        futures = [c for c in contracts if c.endswith("FUT")]
        out = [f"expiry {exp[-1]}: {len(futures)} futures, {len(options)} options"]
        ok = False
        for sym in (futures[:1] + options[:1]):
            end = datetime.fromisoformat(exp[-1]).replace(tzinfo=UTC)
            payload = self.c.get_historical_candles(
                exchange="NSE", segment="FNO", groww_symbol=sym,
                start_time=(end - timedelta(days=20)).strftime("%Y-%m-%d 09:15:00"),
                end_time=end.strftime("%Y-%m-%d 15:30:00"), candle_interval=INTERVALS["1d"])
            n = len((payload or {}).get("candles") or [])
            ok = ok or n > 0
            out.append(f"{sym}: {n} daily candles")
        return (S if ok else U), "; ".join(out)

    def quote(self, segment: str, trading_symbol: str) -> tuple[str, str]:
        q = self.c.get_quote(exchange="NSE", segment=segment, trading_symbol=trading_symbol) or {}
        keys = sorted(k for k, v in q.items() if v not in (None, "", 0))
        return (S if "last_price" in q else U), f"non-empty fields: {keys}"

    def option_chain(self) -> tuple[str, str]:
        exp = (self.c.get_expiries(exchange="NSE", underlying_symbol="NIFTY") or {}).get("expiries") or []
        today = to_ist(self.now).date().isoformat()
        upcoming = [e for e in exp if e >= today]
        if not upcoming:
            return U, "no upcoming expiry found"
        chain = self.c.get_option_chain(exchange="NSE", underlying="NIFTY", expiry_date=upcoming[0]) or {}
        strikes = chain.get("strikes") or {}
        sample = next(iter(strikes.values()), {})
        greeks = sorted(((sample.get("CE") or {}).get("greeks") or {}).keys())
        return (S if strikes else U), f"expiry {upcoming[0]}: {len(strikes)} strikes; greeks={greeks}"

    def run_all(self) -> list[CheckRow]:
        self._run("Instrument list (futures, lot sizes, expiries)", self.instruments)
        self._run("NIFTY index daily candles", lambda: self.index_candles("NIFTY", "1d", 30))
        self._run("BANKNIFTY index daily candles", lambda: self.index_candles("BANKNIFTY", "1d", 30))
        self._run("NIFTY index 1-hour candles", lambda: self.index_candles("NIFTY", "1h", 7))
        self._run("History depth (NIFTY daily, Jan 2020)", self._history_depth)
        self._run("NIFTY futures open interest", lambda: self.futures_oi("NIFTY"))
        self._run("BANKNIFTY futures open interest", lambda: self.futures_oi("BANKNIFTY"))
        self._run("Expired contracts history (futures + options)", self.expired_contracts)
        self._run("Live quote: NIFTY index", lambda: self.quote("CASH", "NIFTY"))
        fut = self.ctx.get("fut_NIFTY")
        if fut:
            self._run("Live quote: NIFTY future (OI, IV)",
                      lambda: self.quote("FNO", str(fut["trading_symbol"])))
        self._run("Option chain with Greeks", self.option_chain)
        self._run("India VIX quote (symbol INDIAVIX is a guess)", lambda: self.quote("CASH", "INDIAVIX"))
        return self.rows

    def _history_depth(self) -> tuple[str, str]:
        payload = self.c.get_historical_candles(
            exchange="NSE", segment="CASH", groww_symbol="NSE-NIFTY",
            start_time="2020-01-01 09:15:00", end_time="2020-01-31 15:30:00",
            candle_interval=INTERVALS["1d"])
        n = len((payload or {}).get("candles") or [])
        return (S if n else U), f"{n} daily candles in Jan 2020"


def to_markdown(rows: list[CheckRow], when: datetime) -> str:
    lines = [
        "# Groww provider capability report",
        "",
        f"Generated {to_ist(when):%Y-%m-%d %H:%M} IST by `scripts/verify_provider_capabilities.py`.",
        "Contains counts, field names and timestamps only (no prices).",
        "",
        "| Capability | Status | Evidence |",
        "|---|---|---|",
    ]
    lines += [f"| {r.capability} | `{r.status}` | {r.evidence.replace('|', '/')} |" for r in rows]
    return "\n".join(lines) + "\n"
