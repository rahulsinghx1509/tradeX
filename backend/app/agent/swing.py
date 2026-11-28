"""AD-1.0 swing: weekly / weekend / overnight base trades on NSE's daily option closes.

Reuses the PR-1.0 Book (fo_daily rows by day, expiry and strike) and its settlement rules.
"""

from __future__ import annotations

from datetime import date, datetime, time

import numpy as np
import pandas as pd

from app import agent as A
from app.agent.regime import Day
from app.core.market_time import IST
from app.ema921 import costs as C
from app.premium import engine as PE


def base_keys() -> list[tuple[str, str, str]]:
    out = []
    for h in A.HOLDINGS:
        for cond, structs in {**A.NEUTRAL, **A.DIRECTIONAL}.items():
            out += [(h, cond, s) for s in structs]
    return out


def key_name(h: str, cond: str, struct: str) -> str:
    return f"S|{h}|{cond}|{struct}"


def direction(cond: str, info: Day) -> int | None:
    """0 = neutral trade, +1/-1 = side, None = condition not met."""
    if cond == "ALWAYS":
        return 0
    if cond == "VRP":
        return 0 if info.vrp else None
    if cond == "RANGE":
        return 0 if info.adx is not None and info.adx < A.ADX_RANGE else None
    if cond == "TREND":
        return info.trend_side or None
    if cond == "REVERT":
        if info.rsi2 is None:
            return None
        return 1 if info.rsi2 < A.RSI2_LOW else -1 if info.rsi2 > A.RSI2_HIGH else None
    raise ValueError(cond)


def legs_for(book: PE.Book, day: date, expiry: date, spot: float, struct: str,
             side: int) -> list[PE.Leg] | None:
    strikes = book.strikes.get((day, expiry))
    if strikes is None or len(strikes) == 0:
        return None
    atm = float(strikes[np.argmin(np.abs(strikes - spot))])
    want: list[tuple[str, float, int]]
    if struct == "FLY":
        ce, pe = book.traded(day, expiry, "CE", atm), book.traded(day, expiry, "PE", atm)
        if ce is None or pe is None:
            return None
        wing = max(PE.P.MIN_WING, float(round((ce + pe) / A.STEP) * A.STEP))
        want = [("CE", atm, -1), ("PE", atm, -1), ("CE", atm + wing, 1), ("PE", atm - wing, 1)]
    elif struct == "CONDOR":
        want = [("CE", atm + 100, -1), ("PE", atm - 100, -1), ("CE", atm + 300, 1), ("PE", atm - 300, 1)]
    else:
        kind, other = ("CE", "PE") if side > 0 else ("PE", "CE")
        if struct == "CREDIT":
            want = [(other, atm, -1), (other, atm - side * 200, 1)]
        elif struct == "DEBIT":
            want = [(kind, atm, 1), (kind, atm + side * 200, -1)]
        elif struct == "LONG":
            want = [(kind, atm, 1)]
        else:
            raise ValueError(struct)
    legs = []
    for kind, k, s in want:
        px = book.traded(day, expiry, kind, k)
        if px is None:
            return None
        legs.append(PE.Leg(kind, k, s, px))
    return legs


def _cash(legs, prices, opening: bool, day: date, mult: float) -> tuple[float, float]:
    """Cash per unit and ₹ charges for opening (or closing) every leg at market."""
    pct, pts = A.SLIP_PCT * mult, A.SLIP_PTS * mult
    cash = buy_v = sell_v = 0.0
    for lg, px in zip(legs, prices, strict=True):
        buying = (lg.side > 0) == opening
        f = C.buy_fill(px, pct, pts) if buying else C.sell_fill(px, pct, pts)
        if buying:
            buy_v += f
            cash -= f
        else:
            sell_v += f
            cash += f
    return cash, C.charges(buy_v * A.LOT, sell_v * A.LOT, day, orders=len(legs))["total"]


def _entries(book: PE.Book, holding: str) -> list[tuple[date, date, date]]:
    """(entry day, exit day, expiry) for every possible entry of this holding."""
    out = []
    if holding == "WEEKLY":
        for prev in PE.weekly_expiries(book):
            d = book.next_day(prev)
            if d is None:
                continue
            ex = book.nearest_expiry_after(d, d)
            if ex is not None and (ex - d).days >= 2:
                out.append((d, ex, ex))
        return out
    for d in book.days:
        nd = book.next_day(d)
        if nd is None:
            continue
        weekend = (nd - d).days >= A.WEEKEND_GAP_DAYS
        if weekend != (holding == "WEEKEND"):
            continue
        ex = book.nearest_expiry_after(d, nd)
        if ex is not None:
            out.append((d, nd, ex))
    return out


def all_trades(book: PE.Book, spot: pd.Series, info: dict[date, Day],
               skipped: dict[str, int], first: date = A.SWING_FIRST) -> list[dict]:
    out = []
    for h in A.HOLDINGS:
        for d, xd, ex in _entries(book, h):
            if d < first:
                continue
            di = info.get(d)
            if di is None or not di.ok:
                k = "swing: day with missing ADX/VIX"
                skipped[k] = skipped.get(k, 0) + 1
                continue
            s0 = PE._spot_on(spot, d)
            if s0 is None:
                continue
            s_exit = PE._spot_on(spot, xd) if h == "WEEKLY" else None
            if h == "WEEKLY" and s_exit is None:
                continue  # still open or no settlement price: never guessed
            for cond, structs in {**A.NEUTRAL, **A.DIRECTIONAL}.items():
                side = direction(cond, di)
                if side is None:
                    continue
                for st in structs:
                    if st in A.SELLING and di.vj:
                        continue
                    legs = legs_for(book, d, ex, s0, st, side)
                    if legs is None:
                        skipped["swing: a leg did not trade at entry"] = skipped.get(
                            "swing: a leg did not trade at entry", 0) + 1
                        continue
                    entry_px = [lg.entry for lg in legs]
                    nets = {}
                    if h == "WEEKLY":
                        settle, c_set = PE._settle_cash(legs, s_exit)
                        gross = (sum(-lg.side * lg.entry for lg in legs) + settle) * A.LOT
                        for mult in (1.0, 2.0):
                            cin, ch = _cash(legs, entry_px, True, d, mult)
                            nets[mult] = (cin + settle) * A.LOT - ch - c_set
                    else:
                        exits = []
                        for lg in legs:
                            px, _ = book.exit_price(xd, ex, lg.kind, lg.strike)
                            exits.append(px)
                        if any(px is None for px in exits):
                            skipped["swing: no exit price"] = skipped.get("swing: no exit price", 0) + 1
                            continue
                        gross = sum(lg.side * (px - lg.entry)
                                    for lg, px in zip(legs, exits, strict=True)) * A.LOT
                        for mult in (1.0, 2.0):
                            cin, c1 = _cash(legs, entry_px, True, d, mult)
                            cout, c2 = _cash(legs, exits, False, xd, mult)
                            nets[mult] = (cin + cout) * A.LOT - c1 - c2
                    out.append({
                        "track": "S", "base": key_name(h, cond, st), "family": h, "live": "N",
                        "entry_day": d, "exit_day": xd,
                        "entry_time": pd.Timestamp(datetime.combine(d, time(15, 29)), tz=IST),
                        "exit_time": pd.Timestamp(datetime.combine(xd, time(15, 30)), tz=IST),
                        "direction": side, "structure": st, "reason": h, "spot_entry": s0,
                        "gross": gross, "net": nets[1.0], "net_2x": nets[2.0],
                        "costs": gross - nets[1.0],
                        "legs": " ".join(
                            f"{'B' if lg.side > 0 else 'S'} {lg.strike:g}{lg.kind}@{lg.entry:.2f}"
                            for lg in legs)})
    return out
