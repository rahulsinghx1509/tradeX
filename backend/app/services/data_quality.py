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
from datetime import date, datetime

from app.core.market_time import expected_bar_starts, is_bar_complete, to_ist
from app.providers.base import Candle

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
MAX_EXAMPLES = 10  # keep log rows small
# A high more than 1.5x the open/close (or a low under 1/1.5 of it) is a bad tick, not a
# real move: NSE circuit limits stop stocks and indices at 20% in a session. Options can
# genuinely move this much, so they are not spike-checked.
MAX_WICK_RATIO = 1.5


@dataclass
class CheckResult:
    check_name: str
    status: str
    details: dict = field(default_factory=dict)


def ohlc_problem(c: Candle, spike_check: bool = True) -> str | None:
    """Why a candle is invalid, or None if it is fine."""
    prices = (c.open, c.high, c.low, c.close)
    if any(p is None for p in prices):
        return "MISSING_PRICE"
    if any(p <= 0 for p in prices):
        return "NON_POSITIVE_PRICE"
    if c.high < max(c.open, c.close) or c.low > min(c.open, c.close) or c.high < c.low:
        return "INCONSISTENT_OHLC"
    if spike_check and (c.high > MAX_WICK_RATIO * max(c.open, c.close)
                        or c.low * MAX_WICK_RATIO < min(c.open, c.close)):
        return "PRICE_SPIKE"
    if c.volume is not None and c.volume < 0:
        return "NEGATIVE_VOLUME"
    if c.open_interest is not None and c.open_interest < 0:
        return "NEGATIVE_OI"
    return None


def drop_incomplete(candles: list[Candle], timeframe: str, now: datetime) -> tuple[list[Candle], CheckResult]:
    kept = [c for c in candles if is_bar_complete(c.time, timeframe, now)]
    dropped = len(candles) - len(kept)
    return kept, CheckResult("incomplete_candles", PASS, {"dropped": dropped})


def dedupe(candles: list[Candle]) -> tuple[list[Candle], CheckResult]:
    """One candle per timestamp. Exact repeats are harmless; conflicting repeats are a FAIL."""
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
    status = FAIL if conflicts else (WARN if exact else PASS)
    return (
        [by_time[t] for t in sorted(by_time)],
        CheckResult(
            "duplicate_candles", status,
            {"exact_duplicates": exact, "conflicting_duplicates": len(conflicts),
             "examples": conflicts[:MAX_EXAMPLES]},
        ),
    )


def validate_ohlc(candles: list[Candle], spike_check: bool = True) -> tuple[dict[datetime, str], CheckResult]:
    problems = {c.time: p for c in candles if (p := ohlc_problem(c, spike_check))}
    examples = [{"time": t.isoformat(), "reason": r} for t, r in list(problems.items())[:MAX_EXAMPLES]]
    return problems, CheckResult(
        "invalid_ohlc", FAIL if problems else PASS, {"invalid": len(problems), "examples": examples}
    )


def check_missing_values(candles: list[Candle], expect_volume: bool, expect_oi: bool) -> CheckResult:
    """Counts candles where volume / OI is missing although the instrument should have it."""
    no_vol = sum(1 for c in candles if c.volume is None) if expect_volume else 0
    no_oi = sum(1 for c in candles if c.open_interest is None) if expect_oi else 0
    return CheckResult(
        "missing_volume_oi", WARN if (no_vol or no_oi) else PASS,
        {"missing_volume": no_vol, "missing_open_interest": no_oi},
    )


def check_gaps(
    candles: list[Candle], timeframe: str, holidays: frozenset[date] | None = None
) -> CheckResult:
    """Expected candles (market days and hours) that did not arrive."""
    if not candles:
        return CheckResult("missing_candles", WARN, {"reason": "no candles received"})
    have = {c.time for c in candles}
    expected = expected_bar_starts(candles[0].time, candles[-1].time, timeframe, holidays)
    missing = [t for t in expected if t not in have]
    unexpected = sorted(have - set(expected))
    return CheckResult(
        "missing_candles",
        WARN if (missing or unexpected) else PASS,
        {
            "expected": len(expected),
            "missing": len(missing),
            "missing_examples": [to_ist(t).isoformat() for t in missing[:MAX_EXAMPLES]],
            "unexpected_times": len(unexpected),
            "unexpected_examples": [to_ist(t).isoformat() for t in unexpected[:MAX_EXAMPLES]],
            "note": "Holidays count as missing until app/data/nse_holidays.csv is filled in.",
        },
    )
