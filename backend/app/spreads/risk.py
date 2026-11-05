"""RL-1.0: which loss limits fit a ₹1 lakh account (asked by RK 2026-11-05, docs/01 §26).

A study, not a new strategy: no PASS verdicts are given and the held-back year is not touched.
Levels fixed on 2026-11-05 BEFORE the run, as a share of the ₹1L capital:

- NONE:          no limits (the backtest as run).
- PLAN:          ₹500 a trade, ₹1,000 a day, ₹2,500 a week (the ChatGPT plan; 0.5% / 1% / 2.5%).
- CONSERVATIVE:  ₹1,000 a trade, ₹2,000 a day, ₹5,000 a week (1% / 2% / 5%).
- STANDARD:      ₹2,000 a trade, ₹3,000 a day, ₹6,000 a week (2% / 3% / 6%).

How a limit works: before each new entry, if the day's realised NET loss so far has reached the
day limit, or the week's (Mon-Sun) has reached the week limit, the entry is not taken. Open trades
are never cut short by these limits. The per-trade limit needs a planned stop at entry, so it only
applies to TP-1.0 (estimated 1-lot risk = stop distance x delta x 65, as in app/pullback). ET ATM
sell has no stop: its per-trade risk is reported (worst trade, trades losing more than each limit).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.spreads.engine import Position


@dataclass(frozen=True)
class Level:
    name: str
    per_trade: float | None
    day: float | None
    week: float | None


LEVELS = (Level("NONE", None, None, None),
          Level("PLAN 0.5/1/2.5%", 500, 1000, 2500),
          Level("CONSERVATIVE 1/2/5%", 1000, 2000, 5000),
          Level("STANDARD 2/3/6%", 2000, 3000, 6000))


def apply_day_week(ps: list[Position], level: Level) -> tuple[list[Position], int]:
    """(positions kept, entries skipped) under the day and week limits.

    Like a SQL running SUM(net) OVER (PARTITION BY day ORDER BY entry_time) checked before each
    row, except that skipped rows don't add to the sum."""
    kept: list[Position] = []
    skipped = 0
    day_loss: dict = {}
    week_loss: dict = {}
    for p in sorted(ps, key=lambda p: (p.day, str(p.entry_time))):
        wk = p.day.isocalendar()[:2]
        if (level.day is not None and day_loss.get(p.day, 0.0) >= level.day) or (
                level.week is not None and week_loss.get(wk, 0.0) >= level.week):
            skipped += 1
            continue
        kept.append(p)
        day_loss[p.day] = day_loss.get(p.day, 0.0) - p.net_pnl
        week_loss[wk] = week_loss.get(wk, 0.0) - p.net_pnl
    return kept, skipped


def worst_period(ps: list[Position], weekly: bool) -> float | None:
    sums: dict = {}
    for p in ps:
        k = p.day.isocalendar()[:2] if weekly else p.day
        sums[k] = sums.get(k, 0.0) + p.net_pnl
    return round(min(sums.values()), 2) if sums else None


def over(ps: list[Position], amount: float) -> int:
    """Trades that lost more than `amount` on their own."""
    return sum(1 for p in ps if p.net_pnl < -amount)
