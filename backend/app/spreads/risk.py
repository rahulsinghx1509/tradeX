"""RL-1.0: loss AND profit limits for a ₹1 lakh account (asked by RK 2026-11-05, docs/01 §26).

A study, not a new strategy: no PASS verdicts are given and the held-back year is not touched.
Levels fixed on 2026-11-05 BEFORE the run, following common desk practice (risk 1-2% of capital
a trade, reward 1.5-2x the risk, stop for the day at 2-3% down, lock the day at about twice the
day's loss limit, stop for the week at 5-6% down):

| Level        | Stop / trade | Target / trade | Day loss | Day profit lock | Week loss |
|--------------|--------------|----------------|----------|-----------------|-----------|
| NONE         | -            | -              | -        | -               | -         |
| PLAN         | ₹500         | 1.5 R = ₹750   | ₹1,000   | -               | ₹2,500    |
| 1% 1:1.5     | ₹1,000       | ₹1,500         | ₹2,000   | ₹4,000          | ₹5,000    |
| 1% 1:2       | ₹1,000       | ₹2,000         | ₹2,000   | ₹4,000          | ₹5,000    |
| 2% 1:1.5     | ₹2,000       | ₹3,000         | ₹3,000   | ₹6,000          | ₹6,000    |
| 2% 1:2       | ₹2,000       | ₹4,000         | ₹3,000   | ₹6,000          | ₹6,000    |
| 1% trail     | ₹1,000 trail | none           | ₹2,000   | ₹4,000          | ₹5,000    |
| 2% trail     | ₹2,000 trail | none           | ₹3,000   | ₹6,000          | ₹6,000    |

Trailing levels (added 2026-11-05 at RK's ask, still before any run): the stop starts at the stop
₹ (1R) away from the entry premium and follows the best premium seen so far at that same 1R
distance, never loosening (a sell: stop = lowest low so far + R; a buy: highest high so far - R).
So at +1R it reaches breakeven and after that it locks profit. No fixed target: the trade runs
until the trail is hit or its normal exit. Each candle is checked against the stop from the
candles BEFORE it, then the best price is updated (the worse order is assumed inside a candle).
TP-1.0 trailing rows keep its index stop and risk cap, drop its R target, and add this trail.

How each part works:
- ET ATM sell (no stop of its own): the stop and target are on the OPTION premium, 1 lot of 65.
  Stop = entry price + stop ₹ / 65; target = entry price - target ₹ / 65. Each 1-minute candle
  from the entry candle to the trade's normal exit is checked: high reaching the stop exits at the
  stop (at the open if it gapped past), else low reaching the target exits at the target (or the
  better open). Both in one candle = the stop (we can't know which came first; assume the worse).
  Otherwise the trade exits as before. Slippage and charges as always.
- TP-1.0 (has an index stop): the stop ₹ is the most the estimated 1-lot risk may be (larger =
  skipped), and the target is that R multiple (1.5 or 2) of its own index stop.
- Day / week loss and the day profit lock: before each new entry, if today's realised NET P&L is
  at or below minus the day loss, or at or above the profit lock, or the week's (Mon-Sun) is at or
  below minus the week loss, the entry is not taken. Open trades are never cut by these.
  (For TP-1.0 they are applied to its trade list afterwards: a skipped trade doesn't free the
  engine for a different later setup that day. A small approximation.)
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

import pandas as pd

from app.ema921 import costs as C
from app.ema921 import engine as EE
from app.spreads.engine import Position


@dataclass(frozen=True)
class Level:
    name: str
    stop: float | None  # ₹ a trade
    reward: float | None  # target = reward x stop
    day: float | None  # day loss limit, ₹
    week: float | None  # week loss limit, ₹
    day_profit: float | None  # day profit lock, ₹
    trail: bool = False  # trailing stop at 1R behind the best premium, no target

    @property
    def target(self) -> float | None:
        return None if self.stop is None or self.reward is None else self.stop * self.reward


LEVELS = (Level("NONE", None, None, None, None, None),
          Level("PLAN 0.5% 1:1.5", 500, 1.5, 1000, 2500, None),
          Level("1% 1:1.5", 1000, 1.5, 2000, 5000, 4000),
          Level("1% 1:2", 1000, 2.0, 2000, 5000, 4000),
          Level("2% 1:1.5", 2000, 1.5, 3000, 6000, 6000),
          Level("2% 1:2", 2000, 2.0, 3000, 6000, 6000),
          Level("1% trail", 1000, None, 2000, 5000, 4000, trail=True),
          Level("2% trail", 2000, None, 3000, 6000, 6000, trail=True))


def apply_exit_rule(tr: EE.Trade, m: EE._Minutes, level: Level, cfg: EE.Config) -> EE.Trade:
    """The level's per-trade exit: fixed stop + target, or the trailing stop (or nothing)."""
    if level.stop is None:
        return tr
    if level.trail:
        return trailing(tr, m, level.stop, cfg)
    return stop_target(tr, m, level.stop, level.target, cfg)


def trailing(tr: EE.Trade, m: EE._Minutes, stop_rs: float, cfg: EE.Config) -> EE.Trade:
    """A copy of `tr` exited by a 1R trailing stop on the premium, if hit before its exit."""
    if tr.exit_time is None or not m.times:
        return tr
    sell = tr.side == "SELL"
    r = stop_rs / cfg.qty
    best = tr.entry_price
    stop = best + r if sell else best - r
    start, end = pd.Timestamp(tr.entry_time), pd.Timestamp(tr.exit_time)
    rows = m.df[(m.df["t"] >= start) & (m.df["t"] < end)]
    for row in rows.itertuples(index=False):
        hi = row.high if pd.notna(row.high) else row.close
        lo = row.low if pd.notna(row.low) else row.close
        if pd.isna(hi) or pd.isna(lo):
            continue
        o = row.open if pd.notna(row.open) else None
        if hi >= stop if sell else lo <= stop:
            gap = o is not None and (o >= stop if sell else o <= stop) and row.t > start
            # TRAIL_STOP once the trail has reached breakeven or better, else a plain STOP
            reason = "TRAIL_STOP" if (stop <= tr.entry_price if sell
                                      else stop >= tr.entry_price) else "STOP"
            return _refill(tr, row.t, max(C.TICK, float(o) if gap else stop), reason, cfg)
        best = min(best, lo) if sell else max(best, hi)
        stop = min(stop, best + r) if sell else max(stop, best - r)
    return tr


# ----------------------------------------------------------------------------- per-trade stop/target
def stop_target(tr: EE.Trade, m: EE._Minutes, stop_rs: float, target_rs: float,
                cfg: EE.Config) -> EE.Trade:
    """A copy of `tr` exited at a premium stop or target if one was hit first (see module doc)."""
    if tr.exit_time is None or not m.times:
        return tr
    q = cfg.qty
    sell = tr.side == "SELL"
    e = tr.entry_price
    stop = e + stop_rs / q if sell else e - stop_rs / q
    target = e - target_rs / q if sell else e + target_rs / q
    start, end = pd.Timestamp(tr.entry_time), pd.Timestamp(tr.exit_time)
    rows = m.df[(m.df["t"] >= start) & (m.df["t"] < end)]
    for r in rows.itertuples(index=False):
        hi = r.high if pd.notna(r.high) else r.close
        lo = r.low if pd.notna(r.low) else r.close
        if pd.isna(hi) or pd.isna(lo):
            continue
        o = r.open if pd.notna(r.open) else None
        lost = hi >= stop if sell else lo <= stop
        won = lo <= target if sell else hi >= target
        if lost:
            gap = o is not None and (o >= stop if sell else o <= stop) and r.t > start
            return _refill(tr, r.t, float(o) if gap else stop, "STOP", cfg)
        if won:
            gap = o is not None and (o <= target if sell else o >= target) and r.t > start
            return _refill(tr, r.t, max(C.TICK, float(o) if gap else target), "TARGET", cfg)
    return tr


def _refill(tr: EE.Trade, t, price: float, reason: str, cfg: EE.Config) -> EE.Trade:
    """Same P&L arithmetic as ema921.engine._close, for a new exit."""
    out = copy.copy(tr)
    exit_buy = tr.side == "SELL"
    fill = (C.buy_fill if exit_buy else C.sell_fill)(price, cfg.slippage_pct, cfg.slippage_points)
    out.exit_time, out.exit_price, out.exit_fill, out.exit_reason = t.to_pydatetime(), price, fill, reason
    out.stale_exit = False
    sign = 1 if tr.side == "BUY" else -1
    q = cfg.qty
    out.gross_pnl = round(sign * (price - tr.entry_price) * q, 2)
    filled = sign * (fill - tr.entry_fill) * q
    out.slippage = round(out.gross_pnl - filled, 2)
    buy_px, sell_px = (tr.entry_fill, fill) if tr.side == "BUY" else (fill, tr.entry_fill)
    out.charges = C.charges(buy_px * q, sell_px * q, tr.day)
    out.net_pnl = round(filled - out.charges["total"], 2)
    return out


# ----------------------------------------------------------------------------- day / week limits
def apply_day_week(ps: list[Position], level: Level) -> tuple[list[Position], int]:
    """(positions kept, entries skipped) under the day loss, day profit lock and week loss.

    Like a SQL running SUM(net) OVER (PARTITION BY day ORDER BY entry_time) checked before each
    row, except that skipped rows don't add to the sum."""
    kept: list[Position] = []
    skipped = 0
    day_pnl: dict = {}
    week_pnl: dict = {}
    for p in sorted(ps, key=lambda p: (p.day, str(p.entry_time))):
        wk = p.day.isocalendar()[:2]
        today, week = day_pnl.get(p.day, 0.0), week_pnl.get(wk, 0.0)
        if ((level.day is not None and today <= -level.day)
                or (level.day_profit is not None and today >= level.day_profit)
                or (level.week is not None and week <= -level.week)):
            skipped += 1
            continue
        kept.append(p)
        day_pnl[p.day] = today + p.net_pnl
        week_pnl[wk] = week + p.net_pnl
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


def exits(ps: list[Position]) -> str:
    """'STOP 12, TARGET 30, ...' counts of how the trades ended."""
    c: dict = {}
    for p in ps:
        r = p.legs[0].exit_reason if p.legs else p.exit_reason
        c[r] = c.get(r, 0) + 1
    return ", ".join(f"{k} {v}" for k, v in sorted(c.items(), key=lambda kv: -kv[1]))


# ----------------------------------------------------------------------------- RL-1.0 held-back test
# Fixed 2026-11-05 after the development-years sweep (docs/01 §26), BEFORE the held-back year is
# looked at: ET-1.0 ATM sell, no trades on weekly expiry days, the "2% trail" level (₹2,000 1R
# trailing stop on the premium, no target, ₹3,000 day loss, ₹6,000 day profit lock, ₹6,000 week
# loss). One run on Nov 2023 - Oct 2024 (`tradex.sh et-holdout`, refused a second time; it shares
# the held-back-year marker with spread-backtest). Bar: net > 0 and profit factor >= 1.05.
# Chosen from 24 combinations on the same 2 years, so a pass here is the only evidence that counts.
HOLDOUT_LEVEL = next(lv for lv in LEVELS if lv.name == "2% trail")
HOLDOUT_PF = 1.05
