"""RT-1.0: re-test of the RL-1.0 rule (ET-1.0 ATM sell, no expiry days, 2% trailing stop and its
day/week limits) on every day stored from Nov 2023 to yesterday. Asked by RK 2026-11-28 ("test
ET-1.0 again"), docs/01 §28.

This is a RE-CHECK, not a fresh out-of-sample test: the held-back year (Nov 2023 - Oct 2024) was
used once on 2026-11-05. The only days no decision was ever based on are those from 2026-11-05
(NEW_FROM), a few weeks. So nothing here is tuned: the rule is exactly risk.HOLDOUT_LEVEL, and the
stress grid below was fixed on 2026-11-28 BEFORE any of it was run.

Pass bar (fixed before the run, RK's bar for a ₹1L account, 1 lot):
- profit factor >= 1.10,
- max drawdown < 20% of the ₹1L account,
- every complete test year (Nov - Oct) net > 0. Calendar and financial years are reported too;
  the first and last are part-years, so they are shown but not judged.

Stress grid (robustness, NOT a search: no row replaces the rule, whichever looks best):
- trailing stop ₹1,500 / ₹2,000 / ₹2,500 (1.5% / 2% / 2.5% of ₹1L); day/week limits unchanged;
- slippage 1x (0.5% + 0.5 point a side, the engine default from PR #45), 2x (1% + 1 pt),
  3x (1.5% + 1.5 pt);
- the rule's trade list with its best 3, 5 and 10 trades removed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

from app.spreads import risk as RK
from app.spreads.engine import Position, summarize

FIRST_DAY = date(2023, 11, 1)
NEW_FROM = date(2026, 11, 5)  # the last risk sweep / held-back run was on 2026-11-05
PF_MIN = 1.10
DD_MAX_PCT = 20.0

STOPS = (1500.0, 2000.0, 2500.0)
SLIPPAGE = (("1x (0.5% + 0.5 pt)", 0.005, 0.5),
            ("2x (1% + 1 pt)", 0.010, 1.0),
            ("3x (1.5% + 1.5 pt)", 0.015, 1.5))
DROP_BEST = (3, 5, 10)


def level(stop: float) -> RK.Level:
    """The RL-1.0 level with another trailing-stop size (day/week limits unchanged)."""
    return replace(RK.HOLDOUT_LEVEL, name=f"₹{stop:,.0f} trail", stop=stop)


# ----------------------------------------------------------------------------- periods
def nov_oct_year(d: date) -> str:
    """Nov - Oct years, the split used for every earlier result (Y1 was the held-back year)."""
    y = d.year if d.month >= 11 else d.year - 1
    return f"Nov {y} - Oct {y + 1}"


def calendar_year(d: date) -> str:
    return str(d.year)


def financial_year(d: date) -> str:
    """Indian financial year, April to March: FY2024-25 = 1 Apr 2024 - 31 Mar 2025."""
    y = d.year if d.month >= 4 else d.year - 1
    return f"FY{y}-{(y + 1) % 100:02d}"


def _bounds(label_of, label: str) -> tuple[date, date]:
    """The full period's first and last calendar day, to tell part-years from complete ones."""
    if label_of is calendar_year:
        y = int(label)
        return date(y, 1, 1), date(y, 12, 31)
    if label_of is financial_year:
        y = int(label[2:6])
        return date(y, 4, 1), date(y + 1, 3, 31)
    y = int(label.split()[1])
    return date(y, 11, 1), date(y + 1, 10, 31)


@dataclass
class PeriodRow:
    label: str
    complete: bool  # the data covers the whole period
    trades: int
    win_rate: float | None
    gross: float
    costs: float
    net: float
    pf: float | None
    max_dd_pct: float | None  # inside this period, starting from the full account


def by_period(ps: list[Position], label_of, first: date, last: date,
              capital: float) -> list[PeriodRow]:
    """One row per period (like GROUP BY label), oldest first. `first`/`last` = data range."""
    groups: dict[str, list[Position]] = {}
    for p in ps:
        groups.setdefault(label_of(p.day), []).append(p)
    labels = sorted({label_of(first), label_of(last), *groups},
                    key=lambda lb: _bounds(label_of, lb)[0])
    out = []
    for lb in labels:
        a, b = _bounds(label_of, lb)
        sub = groups.get(lb, [])
        s = summarize(sub, capital, a)
        out.append(PeriodRow(lb, a >= first and b <= last, s.trades, s.win_rate, s.gross_pnl,
                             s.friction, s.net_pnl, s.profit_factor, s.max_drawdown_pct))
    return out


# ----------------------------------------------------------------------------- verdict
@dataclass
class Verdict:
    ok: bool
    pf_ok: bool
    dd_ok: bool
    years_ok: bool
    reasons: list[str]


def verdict(ps: list[Position], capital: float, first: date, last: date) -> Verdict:
    s = summarize(ps, capital, first)
    years = [r for r in by_period(ps, nov_oct_year, first, last, capital) if r.complete]
    pf_ok = (s.profit_factor or 0) >= PF_MIN
    dd_ok = s.max_drawdown_pct is not None and s.max_drawdown_pct < DD_MAX_PCT
    years_ok = bool(years) and all(r.net > 0 for r in years)
    why = []
    if not pf_ok:
        why.append(f"PF {s.profit_factor} < {PF_MIN}")
    if not dd_ok:
        why.append(f"max DD {s.max_drawdown_pct}% >= {DD_MAX_PCT:g}%")
    if not years_ok:
        bad = [r.label for r in years if r.net <= 0] or ["no complete year"]
        why.append("year(s) not positive: " + ", ".join(bad))
    return Verdict(pf_ok and dd_ok and years_ok, pf_ok, dd_ok, years_ok, why)


def drop_best(ps: list[Position], n: int) -> list[Position]:
    """The list without its n most profitable trades (ties: the earlier trade goes first)."""
    best = sorted(range(len(ps)), key=lambda i: (-ps[i].net_pnl, i))[:n]
    gone = set(best)
    return [p for i, p in enumerate(ps) if i not in gone]
