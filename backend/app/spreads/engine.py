"""SP-1.0 engine: credit spreads, debit spreads and the range-day iron condor (rules in __init__).

How it reuses the other engines, so every strategy is scored the same way:
- CS-1.0 / DS-1.0 run the ET-1.0 / ORB-1.0 engine once with several "modes": the ATM leg plus one
  extra leg per width (registered in ema921.engine.EXTRA_LEGS). Their exits depend only on NIFTY
  (opposite cross, range stop, square-off), never on the leg, so the legs of one spread share the
  same signal time and exit time. `pair` joins them by (day, signal time), like a SQL inner join:
  a signal where one leg had no price drops out.
- IC-1.0 is the ST-1.0 straddle engine with sold strikes moved out (short_offset), the wing closed
  with its stopped side, a 10:00 entry and a day filter (`condor_day_filter`).
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, time

import pandas as pd

from app.core.market_time import IST, trading_days
from app.ema921 import engine as EE
from app.ema921 import trend as T
from app.orb import engine as O
from app.spreads import (
    DS_RVOL,
    IC_SHORT_OFFSET,
    IC_STOP_MULT,
    IC_WING,
    RANGE_LOOKBACK,
    RANGE_MINUTES,
    VIX_AVG_DAYS,
    WIDTHS,
)
from app.straddle import engine as ST

CS_SHORT, DS_LONG = "ATM_SELL", "ATM_BUY"


def cs_wing(w: int) -> str:
    return f"CS_WING_{w}"


def ds_short(w: int) -> str:
    return f"DS_SHORT_{w}"


def _register() -> None:
    for w in WIDTHS:
        # Credit spread hedge: bullish -> buy the put W lower; bearish -> buy the call W higher.
        EE.EXTRA_LEGS[cs_wing(w)] = (lambda d, atm, cfg, w=w:
                                     ("BUY", "PE", atm - w) if d > 0 else ("BUY", "CE", atm + w))
        # Debit spread short leg: bullish -> sell the call W higher; bearish -> sell the put W lower.
        EE.EXTRA_LEGS[ds_short(w)] = (lambda d, atm, cfg, w=w:
                                      ("SELL", "CE", atm + w) if d > 0 else ("SELL", "PE", atm - w))


_register()


# ----------------------------------------------------------------------------- one position
@dataclass
class Position:
    """One whole spread or condor: what the pass bar counts as ONE trade."""
    row: str
    day: date
    expiry: date
    entry_time: object  # IST datetime of the first leg's fill
    direction: int  # +1 bullish, -1 bearish, 0 neutral (condor)
    legs: list = field(default_factory=list)  # ema921 Trade or straddle Leg objects
    exit_reason: str = ""
    stale: bool = False

    @property
    def gross_pnl(self) -> float:
        return round(sum(x.gross_pnl for x in self.legs), 2)

    @property
    def friction(self) -> float:
        return round(sum(x.slippage + _charges(x) for x in self.legs), 2)

    @property
    def net_pnl(self) -> float:
        return round(sum(x.net_pnl for x in self.legs), 2)

    @property
    def expiry_day(self) -> bool:
        """Taken on a weekly expiry day (the trade itself uses the next expiry: no 0-DTE)."""
        return self.day in EXPIRY_DAYS


EXPIRY_DAYS: set[date] = set()  # filled by `set_expiry_days` from the stored expiry list


def set_expiry_days(expiries) -> None:
    EXPIRY_DAYS.clear()
    EXPIRY_DAYS.update(expiries)


def _charges(leg) -> float:
    c = leg.charges
    return c.get("total", 0.0) if isinstance(c, dict) else float(c)


def pair(row: str, first: list[EE.Trade], second: list[EE.Trade]) -> list[Position]:
    """Join two legs' trade lists on (day, signal time). Unmatched signals are dropped."""
    other = {(t.day, t.signal_time): t for t in second}
    out = []
    for t in first:
        o = other.get((t.day, t.signal_time))
        if o is None or t.exit_time is None or o.exit_time is None:
            continue
        out.append(Position(row, t.day, t.expiry, t.entry_time, t.direction, [t, o],
                            t.exit_reason or "", t.stale_exit or o.stale_exit))
    return out


def unmatched(first: list[EE.Trade], second: list[EE.Trade]) -> int:
    """Signals where only one of the two legs could be traded (the other had no price)."""
    a = {(t.day, t.signal_time) for t in first}
    b = {(t.day, t.signal_time) for t in second}
    return len(a ^ b)


def single(row: str, trades: list[EE.Trade]) -> list[Position]:
    """A single-leg comparison row (ET ATM sell, ORB ATM buy) as Positions."""
    return [Position(row, t.day, t.expiry, t.entry_time, t.direction, [t], t.exit_reason or "",
                     t.stale_exit) for t in trades if t.exit_time is not None]


# ----------------------------------------------------------------------------- CS-1.0 / DS-1.0
def et_config(base: EE.Config) -> EE.Config:
    return replace(base, timeframe=T.SIGNAL_TIMEFRAME, start=T.ENTRY_START, cutoff=T.ENTRY_CUTOFF,
                   stop_atr_mult=None, trend_filter=True)


def run_credit(spot, opts, start, end, base: EE.Config, holidays=None) -> dict[str, list[Position]]:
    """{row: positions}: CS-100, CS-150 and the single-leg ET ATM sell for comparison."""
    modes = (CS_SHORT, *(cs_wing(w) for w in WIDTHS))
    res = EE.run(spot, opts, start, end, et_config(base), modes, holidays)
    out = {f"CS-{w}": pair(f"CS-{w}", res.trades[CS_SHORT], res.trades[cs_wing(w)]) for w in WIDTHS}
    out["ET ATM sell (single leg)"] = single("ET ATM sell (single leg)", res.trades[CS_SHORT])
    return out


def run_debit(spot, opts, start, end, base: EE.Config, entry_filter=None, holidays=None,
              suffix: str = "") -> dict[str, list[Position]]:
    """{row: positions}: DS-100, DS-150 (+ suffix) and the single-leg ORB ATM buy."""
    modes = (DS_LONG, *(ds_short(w) for w in WIDTHS))
    res = O.run(spot, opts, start, end, base, modes, holidays, entry_filter=entry_filter)
    out = {f"DS-{w}{suffix}": pair(f"DS-{w}{suffix}", res.trades[DS_LONG], res.trades[ds_short(w)])
           for w in WIDTHS}
    out[f"ORB ATM buy{suffix} (single leg)"] = single(f"ORB ATM buy{suffix} (single leg)",
                                                       res.trades[DS_LONG])
    return out


def rvol_filter(lookup) -> Callable:
    return lookup.filter(DS_RVOL)


# ----------------------------------------------------------------------------- IC-1.0
def condor_config(base: ST.Config | None = None) -> ST.Config:
    return replace(base or ST.Config(), entry=time(10, 0), exit=time(15, 15),
                   short_offset=IC_SHORT_OFFSET, wing_points=IC_WING, stop_pct=IC_STOP_MULT - 1,
                   close_wing_with_stop=True, vix_filter=False)


def opening_ranges_pct(spot: pd.DataFrame) -> dict[date, float]:
    """{day: (high - low) of the 09:15-09:59 one-minute candles / 09:15 open x 100}.
    Only days with all 45 candles, each with a high and low, and a 09:15 open."""
    if spot.empty:
        return {}
    ist = pd.to_datetime(spot["time"], utc=True).dt.tz_convert(IST)
    since = (ist.dt.hour * 60 + ist.dt.minute) - (9 * 60 + 15)
    first = spot[(since >= 0) & (since < RANGE_MINUTES)].assign(day=ist.dt.date, m=since)
    out = {}
    for d, g in first.groupby("day"):
        g = g.drop_duplicates("m")
        if len(g) < RANGE_MINUTES or g["high"].isna().any() or g["low"].isna().any():
            continue
        o = g.loc[g["m"] == 0, "open"]
        if o.empty or pd.isna(o.iloc[0]) or o.iloc[0] <= 0:
            continue
        out[d] = float((g["high"].max() - g["low"].min()) / o.iloc[0] * 100)
    return out


def range_day_status(day: date, ranges: dict[date, float]) -> str | None:
    """None if `day` is a range day (rule B1), else the skip reason."""
    today = ranges.get(day)
    if today is None:
        return "NO_RANGE_DATA"
    before = [ranges[d] for d in sorted(ranges) if d < day][-RANGE_LOOKBACK:]
    if len(before) < RANGE_LOOKBACK:
        return "RANGE_WARMUP"
    return None if today < statistics.median(before) else "WIDE_OPEN"


def vix_calm_status(day: date, vix_closes: pd.Series | None, holidays=None) -> str | None:
    """None if VIX is calm for `day` (rule B2), else the skip reason."""
    v = ST.vix_status(day, vix_closes, holidays)
    if v == "NO_VIX":
        return "NO_VIX"
    if v == "JUMP":
        return "VIX_JUMP"
    known = vix_closes[[d < day for d in vix_closes.index]].dropna()
    if len(known) < VIX_AVG_DAYS:
        return "NO_VIX"
    last = known.iloc[-VIX_AVG_DAYS:]
    return None if last.iloc[-1] <= last.mean() else "VIX_HIGH"


def condor_day_filter(spot, vix_closes, holidays=None) -> Callable[[date], str | None]:
    ranges = opening_ranges_pct(spot)

    def check(d: date) -> str | None:
        return range_day_status(d, ranges) or vix_calm_status(d, vix_closes, holidays)
    return check


def run_condor(spot, opts, start, end, vix_closes, holidays=None,
               cfg: ST.Config | None = None) -> tuple[dict[str, list[Position]], dict]:
    """({row: positions}, {row: skipped days}): range-day condor + the every-day comparison."""
    cfg = condor_config(cfg)
    rows = {"IC range days": condor_day_filter(spot, vix_closes, holidays),
            "IC every day (no filter)": None}
    out, skipped = {}, {}
    for row, flt in rows.items():
        res = ST.run(spot, opts, start, end, cfg, None, holidays, day_filter=flt)
        out[row] = [Position(row, t.day, t.expiry, t.legs[0].entry_time, 0, list(t.legs),
                             f"{t.stops} side(s) stopped" if t.stops else "TIME",
                             any(x.stale_exit for x in t.legs)) for t in res.trades]
        skipped[row] = res.skipped
    return out, skipped


# ----------------------------------------------------------------------------- scoring
@dataclass
class Summary:
    trades: int
    win_rate: float | None
    gross_pnl: float
    friction: float
    net_pnl: float
    profit_factor: float | None
    expectancy: float | None
    max_drawdown_pct: float | None
    worst_trade: float | None
    best_trade: float | None
    year1: float
    year2: float


def summarize(ps: list[Position], capital: float, start: date | None = None) -> Summary:
    ps = sorted(ps, key=lambda p: (p.day, str(p.entry_time)))
    nets = [p.net_pnl for p in ps]
    wins = [x for x in nets if x > 0]
    loss = -sum(x for x in nets if x <= 0)
    equity = peak = capital
    dd = 0.0
    for x in nets:
        equity += x
        peak = max(peak, equity)
        dd = max(dd, (peak - equity) / peak * 100 if peak > 0 else 0.0)
    first = start or (ps[0].day if ps else date.today())
    try:
        split = first.replace(year=first.year + 1)
    except ValueError:
        split = first.replace(year=first.year + 1, day=28)
    n = len(nets)
    return Summary(
        trades=n,
        win_rate=round(len(wins) / n * 100, 1) if n else None,
        gross_pnl=round(sum(p.gross_pnl for p in ps), 2),
        friction=round(sum(p.friction for p in ps), 2),
        net_pnl=round(sum(nets), 2),
        profit_factor=round(sum(wins) / loss, 2) if loss > 0 else None,
        expectancy=round(sum(nets) / n, 2) if n else None,
        max_drawdown_pct=round(dd, 1) if n else None,
        worst_trade=min(nets) if nets else None,
        best_trade=max(nets) if nets else None,
        year1=round(sum(p.net_pnl for p in ps if p.day < split), 2),
        year2=round(sum(p.net_pnl for p in ps if p.day >= split), 2),
    )


def passes(ps: list[Position], capital: float, start: date, min_trades: int,
           pf_min: float = 1.10, max_dd_pct: float = 50.0) -> bool:
    if len(ps) < min_trades:
        return False
    s = summarize(ps, capital, start)
    return (s.net_pnl > 0 and (s.profit_factor or 0) >= pf_min and s.year1 > 0 and s.year2 > 0
            and (s.max_drawdown_pct or 0) < max_dd_pct)


def coverage(lookup, start: date, end: date, holidays=None) -> float:
    """Share of the 5-minute bars in [start, end] that have an RVOL (VF-1.0 rule)."""
    expected = len(trading_days(start, end, holidays)) * 75
    if not expected:
        return 0.0
    have = sum(1 for k, r in lookup._by_close.items()
               if not pd.isna(r.rvol) and start <= k.tz_convert(IST).date() <= end)
    return have / expected
