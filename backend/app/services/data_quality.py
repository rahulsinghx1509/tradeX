"""Data-quality checks run on every batch of candles before it is stored.

Metric catalog rows covered: #78 freshness, #79 missing/duplicate candles,
#80 invalid OHLC. Each check returns a CheckResult with PASS / WARN / FAIL.

Rules:
- Missing values stay missing. We never fill a gap or replace None with 0.
- Invalid candles are still stored (so nothing silently disappears) but marked
  quality_status = 'INVALID' with a reason, so signals can exclude them.
- Incomplete candles (still forming) are dropped: storing them would let
  historical analysis see prices that were not final at that time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from app.core.market_time import expected_bar_starts, is_bar_complete, to_ist
from app.providers.base import Candle

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
MAX_EXAMPLES = 10  # keep log rows small
# A high more than 1.5x the open/close (or a low under 1/1.5 of it) is a bad tick, not a
# real move: NSE circuit limits stop stocks and indices at 20% in a session. Options can
# genuinely move this much, so they are not spike-checked.
MAX_WICK_RATIO = 1.5
# Problems older than this (counted back from the newest candle in the batch) are still logged
# with their counts and examples, but they no longer turn the check to WARN: signals only use
# recent data, and a single bad candle from years ago would otherwise keep every badge yellow.
RECENT_DAYS = 30


@dataclass
class CheckResult:
    check_name: str
    status: str
    details: dict = field(default_factory=dict)


def ohlc_problem(c: Candle, spike_check: bool = True) -> str | None:
    """Why a candle is invalid, or None if it is fine.

    A missing OPEN alone does not make a candle invalid: Groww sends stock and stock-futures
    candles that start at 09:15 without one, and high / low / close are still real. The open
    stays missing (never guessed); check_missing_open reports it.
    """
    if any(p is None for p in (c.high, c.low, c.close)):
        return "MISSING_PRICE"
    body = [p for p in (c.open, c.close) if p is not None]
    if any(p <= 0 for p in (*body, c.high, c.low)):
        return "NON_POSITIVE_PRICE"
    if c.high < max(body) or c.low > min(body) or c.high < c.low:
        return "INCONSISTENT_OHLC"
    if spike_check and (c.high > MAX_WICK_RATIO * max(body) or c.low * MAX_WICK_RATIO < min(body)):
        return "PRICE_SPIKE"
    if c.volume is not None and c.volume < 0:
        return "NEGATIVE_VOLUME"
    if c.open_interest is not None and c.open_interest < 0:
        return "NEGATIVE_OI"
    return None


def _is_recent(t: datetime, recent_from: datetime | None) -> bool:
    return recent_from is None or t >= recent_from


def check_missing_open(candles: list[Candle], filled_from_1h: int = 0,
                       recent_from: datetime | None = None) -> CheckResult:
    """Candles stored without an open price. Always PASS, with the counts logged.

    Groww does not send the 09:15 opening price for stocks and stock futures: the daily candle
    and the first 1h candle both arrive without it (seen on the server, 2026-10-18). This is a
    permanent provider gap, not bad data, and no metric or rule uses the open, so it must not
    keep the badge on WARN. `filled_from_1h` counts opens completed from a stored 1h candle.
    """
    missing = [c.time for c in candles if c.open is None]
    recent = sum(1 for t in missing if _is_recent(t, recent_from))
    return CheckResult("missing_open", PASS,
                       {"missing_open": len(missing), "missing_open_recent": recent,
                        "open_filled_from_1h": filled_from_1h,
                        "note": "Groww sends no 09:15 open for stocks; not used by any metric"})


def drop_incomplete(candles: list[Candle], timeframe: str, now: datetime) -> tuple[list[Candle], CheckResult]:
    kept = [c for c in candles if is_bar_complete(c.time, timeframe, now)]
    dropped = len(candles) - len(kept)
    return kept, CheckResult("incomplete_candles", PASS, {"dropped": dropped})


def dedupe(candles: list[Candle]) -> tuple[list[Candle], CheckResult]:
    """One candle per timestamp. Exact repeats (e.g. where two request windows overlap) are
    merged and only counted; conflicting repeats are a FAIL."""
    by_time: dict[datetime, Candle] = {}
    exact, conflicts = 0, []
    for c in candles:
        prev = by_time.get(c.time)
        if prev is not None:
            if prev == c:
                exact += 1
            else:
                conflicts.append(c.time.isoformat())
        by_time[c.time] = c  # the last copy wins
    status = FAIL if conflicts else PASS
    return (
        [by_time[t] for t in sorted(by_time)],
        CheckResult(
            "duplicate_candles", status,
            {"exact_duplicates": exact, "conflicting_duplicates": len(conflicts),
             "examples": conflicts[:MAX_EXAMPLES]},
        ),
    )


def validate_ohlc(candles: list[Candle], spike_check: bool = True,
                  recent_from: datetime | None = None) -> tuple[dict[datetime, str], CheckResult]:
    """Invalid candles are set aside. FAIL if the newest candle is one of them (the latest data
    is unusable), WARN if a recent one is, PASS if only older ones are (still counted)."""
    problems = {c.time: p for c in candles if (p := ohlc_problem(c, spike_check))}
    examples = [{"time": t.isoformat(), "reason": r} for t, r in list(problems.items())[:MAX_EXAMPLES]]
    newest_bad = bool(candles) and max(c.time for c in candles) in problems
    recent = sum(1 for t in problems if _is_recent(t, recent_from))
    status = FAIL if newest_bad else (WARN if recent else PASS)
    return problems, CheckResult(
        "invalid_ohlc", status, {"invalid": len(problems), "invalid_recent": recent,
                                 "newest_invalid": newest_bad, "examples": examples}
    )


def check_missing_values(candles: list[Candle], expect_volume: bool, expect_oi: bool,
                         recent_from: datetime | None = None) -> CheckResult:
    """Counts candles where volume / OI is missing although the instrument should have it.
    WARN only when a recent candle is missing them."""
    bad = [c.time for c in candles
           if (expect_volume and c.volume is None) or (expect_oi and c.open_interest is None)]
    no_vol = sum(1 for c in candles if c.volume is None) if expect_volume else 0
    no_oi = sum(1 for c in candles if c.open_interest is None) if expect_oi else 0
    recent = sum(1 for t in bad if _is_recent(t, recent_from))
    return CheckResult(
        "missing_volume_oi", WARN if recent else PASS,
        {"missing_volume": no_vol, "missing_open_interest": no_oi, "missing_recent": recent},
    )


def check_gaps(
    candles: list[Candle], timeframe: str, holidays: frozenset[date] | None = None,
    reference_times: list[datetime] | None = None, recent_from: datetime | None = None,
) -> CheckResult:
    """Expected candles (market days and hours) that did not arrive.

    `reference_times`: candle times of the market reference (the NIFTY index) over the same
    period. When given, they define which candles should exist, so exchange holidays, special
    sessions (budget-day Saturdays, Muhurat trading) and unscheduled closures are handled from
    real data instead of a hand-kept list. The holiday calendar covers anything after the
    reference's latest candle. WARN only when a recent candle is missing or unexpected.
    """
    if not candles:
        return CheckResult("missing_candles", WARN, {"reason": "no candles received"})
    have = {c.time for c in candles}
    first, last = candles[0].time, candles[-1].time
    if reference_times:
        ref = [t for t in reference_times if first <= t <= last]
        ref_last = max(reference_times)
        tail = (expected_bar_starts(ref_last + timedelta(seconds=1), last, timeframe, holidays)
                if last > ref_last else [])
        expected, basis = sorted(set(ref) | set(tail)), "NIFTY index candles"
    else:
        expected, basis = expected_bar_starts(first, last, timeframe, holidays), "holiday calendar"
    missing = [t for t in expected if t not in have]
    unexpected = sorted(have - set(expected))
    recent = sum(1 for t in (*missing, *unexpected) if _is_recent(t, recent_from))
    return CheckResult(
        "missing_candles",
        WARN if recent else PASS,
        {
            "expected": len(expected),
            "missing": len(missing),
            "problems_recent": recent,
            "missing_examples": [to_ist(t).isoformat() for t in missing[:MAX_EXAMPLES]],
            "unexpected_times": len(unexpected),
            "unexpected_examples": [to_ist(t).isoformat() for t in unexpected[:MAX_EXAMPLES]],
            "expected_from": basis,
        },
    )
