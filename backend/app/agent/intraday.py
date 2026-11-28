"""AD-1.0 intraday: from NIFTY 1-minute candles and each day's option "panel" to base trades.

A base trade belongs to one combination of entry x stop x target x time exit x structure (224 of
them). The market-condition filter is applied later (walkforward.py): a filtered candidate takes
the base trades of the days whose condition matches, so each trade is priced only once.

Minute index i = minutes since 09:15 IST (0..374). A 5-minute bar k covers minutes 5k..5k+4 and
closes at minute 5(k+1); a signal on bar k fills at the option's open of minute 5(k+1).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from app import agent as A
from app.agent.regime import Day
from app.core.market_time import IST
from app.ema921 import costs as C
from app.services import indicators as ind

N_MIN = 375
N_BAR = 75
EOD = (A.SQUARE_OFF.hour * 60 + A.SQUARE_OFF.minute) - (9 * 60 + 15)  # 360


def bar_minute(hh: int, mm: int) -> int:
    return hh * 60 + mm - (9 * 60 + 15)


@dataclass
class SpotDay:
    day: date
    o: np.ndarray
    h: np.ndarray
    low: np.ndarray
    c: np.ndarray
    # 5-minute bars and indicators carried across days
    bo: np.ndarray = field(default=None, repr=False)
    bh: np.ndarray = field(default=None, repr=False)
    bl: np.ndarray = field(default=None, repr=False)
    bc: np.ndarray = field(default=None, repr=False)
    e9: np.ndarray = field(default=None, repr=False)
    e21: np.ndarray = field(default=None, repr=False)
    rsi: np.ndarray = field(default=None, repr=False)
    bb_lo: np.ndarray = field(default=None, repr=False)
    bb_hi: np.ndarray = field(default=None, repr=False)
    st_dir: np.ndarray = field(default=None, repr=False)
    macd: np.ndarray = field(default=None, repr=False)
    macd_sig: np.ndarray = field(default=None, repr=False)


def spot_days(spot: pd.DataFrame, skipped: dict[str, int] | None = None) -> list[SpotDay]:
    """NIFTY 1m (columns time, open, high, low, close) -> complete days with indicators.

    A day with any missing minute in 09:15-15:29 is left out (as every earlier backtest)."""
    sk = skipped if skipped is not None else {}
    if spot.empty:
        return []
    ist = pd.to_datetime(spot["time"], utc=True).dt.tz_convert(IST)
    df = spot.assign(day=ist.dt.date,
                     i=((ist.dt.hour * 60 + ist.dt.minute) - (9 * 60 + 15)).astype(int))
    df = df[(df.i >= 0) & (df.i < N_MIN)]
    out: list[SpotDay] = []
    for d, g in df.groupby("day", sort=True):
        g = g.drop_duplicates("i", keep="last").set_index("i").reindex(range(N_MIN))
        arr = {k: g[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close")}
        if any(np.isnan(v).any() for v in arr.values()):
            sk["intraday: day with a missing NIFTY minute"] = sk.get(
                "intraday: day with a missing NIFTY minute", 0) + 1
            continue
        sd = SpotDay(d, arr["open"], arr["high"], arr["low"], arr["close"])
        sd.bo = sd.o[0::5]
        sd.bh = sd.h.reshape(N_BAR, 5).max(axis=1)
        sd.bl = sd.low.reshape(N_BAR, 5).min(axis=1)
        sd.bc = sd.c[4::5]
        out.append(sd)
    if not out:
        return out
    closes = pd.Series(np.concatenate([s.bc for s in out]))
    e9 = ind.ema(closes, 9).to_numpy()
    e21 = ind.ema(closes, 21).to_numpy()
    rsi = ind.rsi(closes, 14).to_numpy()
    mid = closes.rolling(20).mean()
    sd20 = closes.rolling(20).std(ddof=0)
    lo, hi = (mid - 2 * sd20).to_numpy(), (mid + 2 * sd20).to_numpy()
    highs = np.concatenate([s.bh for s in out])
    lows = np.concatenate([s.bl for s in out])
    st = supertrend(highs, lows, closes.to_numpy())
    macd = (closes.ewm(span=A.MACD_FAST, adjust=False).mean()
            - closes.ewm(span=A.MACD_SLOW, adjust=False).mean())
    sig = macd.ewm(span=A.MACD_SIGNAL, adjust=False).mean()
    macd_v, sig_v = macd.to_numpy(), sig.to_numpy()
    warm = A.MACD_SLOW + A.MACD_SIGNAL
    macd_v[:warm] = np.nan
    sig_v[:warm] = np.nan
    for n, s in enumerate(out):
        sl = slice(n * N_BAR, (n + 1) * N_BAR)
        s.e9, s.e21, s.rsi, s.bb_lo, s.bb_hi = e9[sl], e21[sl], rsi[sl], lo[sl], hi[sl]
        s.st_dir, s.macd, s.macd_sig = st[sl], macd_v[sl], sig_v[sl]
    return out


def supertrend(h: np.ndarray, low: np.ndarray, c: np.ndarray, n: int = A.ST_ATR,
               mult: float = A.ST_MULT) -> np.ndarray:
    """Supertrend direction per bar (+1 up, -1 down, NaN during warm-up). Wilder ATR."""
    prev_c = np.concatenate([[np.nan], c[:-1]])
    tr = np.nanmax(np.vstack([h - low, np.abs(h - prev_c), np.abs(low - prev_c)]), axis=0)
    atr = pd.Series(tr).ewm(alpha=1 / n, adjust=False).mean().to_numpy()
    hl2 = (h + low) / 2
    up_basic, dn_basic = hl2 + mult * atr, hl2 - mult * atr
    out = np.full(len(c), np.nan)
    upper, lower, direction = up_basic[0], dn_basic[0], 1
    for i in range(1, len(c)):
        upper = up_basic[i] if (up_basic[i] < upper or c[i - 1] > upper) else upper
        lower = dn_basic[i] if (dn_basic[i] > lower or c[i - 1] < lower) else lower
        if direction > 0 and c[i] < lower:
            direction = -1
        elif direction < 0 and c[i] > upper:
            direction = 1
        if i >= n:
            out[i] = direction
    return out


# ----------------------------------------------------------------------------- entries
def _first(cands: Iterable[tuple[int, int]]) -> tuple[int, int] | None:
    for k, d in cands:
        if d:
            return 5 * (k + 1), d
    return None


def _cross_levels(s: SpotDay, top: float, bottom: float, lo: int, hi: int) -> tuple[int, int] | None:
    """First 5-min close crossing above `top` (+1) or below `bottom` (-1); bar 0 compares with
    the day's open."""
    def x(k):
        before = s.o[0] if k == 0 else s.bc[k - 1]
        if before <= top < s.bc[k]:
            return 1
        if before >= bottom > s.bc[k]:
            return -1
        return 0
    return _first((k, x(k)) for k in range(N_BAR) if lo <= 5 * (k + 1) <= hi)


def signal(entry: str, s: SpotDay, info: Day) -> tuple[int, int] | None:
    """(fill minute, direction +1/-1) of the day's first signal of this entry type, or None."""
    m = lambda k: 5 * (k + 1)  # noqa: E731  minute the bar closes
    prev_close = info.prev_close
    if entry == "PDHL":
        if info.prev_high is None or info.prev_low is None:
            return None
        return _cross_levels(s, info.prev_high, info.prev_low, bar_minute(9, 20), bar_minute(14, 30))
    if entry == "CPR":
        if None in (info.prev_high, info.prev_low, prev_close):
            return None
        p = (info.prev_high + info.prev_low + prev_close) / 3
        bc = (info.prev_high + info.prev_low) / 2
        tc = 2 * p - bc
        return _cross_levels(s, max(tc, bc), min(tc, bc), bar_minute(9, 20), bar_minute(14, 30))
    if entry == "IB30":
        ibh, ibl = s.h[:30].max(), s.low[:30].min()
        return _first((k, 1 if s.bc[k] > ibh else -1 if s.bc[k] < ibl else 0)
                      for k in range(6, N_BAR) if m(k) <= bar_minute(13, 30))
    if entry == "SUPERTREND":
        lo, hi = bar_minute(9, 45), bar_minute(14, 30)

        def flip(k):
            a, b = s.st_dir[k - 1], s.st_dir[k]
            if not (np.isfinite(a) and np.isfinite(b)) or a == b:
                return 0
            return int(b)
        return _first((k, flip(k)) for k in range(1, N_BAR) if lo <= m(k) <= hi)
    if entry == "MACD":
        lo, hi = bar_minute(9, 45), bar_minute(14, 30)

        def mc(k):
            a = s.macd[k - 1] - s.macd_sig[k - 1]
            b = s.macd[k] - s.macd_sig[k]
            if not (np.isfinite(a) and np.isfinite(b)):
                return 0
            return 1 if a <= 0 < b else -1 if a >= 0 > b else 0
        return _first((k, mc(k)) for k in range(1, N_BAR) if lo <= m(k) <= hi)
    if entry == "ORB":
        orh, orl = s.h[:15].max(), s.low[:15].min()
        return _first((k, 1 if s.bc[k] > orh else -1 if s.bc[k] < orl else 0)
                      for k in range(3, N_BAR) if m(k) <= bar_minute(12, 30))
    if entry in ("GAPFADE", "GAPGO"):
        gap = s.o[0] / prev_close - 1
        if not (A.GAP_MIN <= abs(gap) <= A.GAP_MAX):
            return None
        back = (gap > 0 and s.bc[0] < s.bo[0]) or (gap < 0 and s.bc[0] > s.bo[0])
        away = (gap > 0 and s.bc[0] > s.bo[0]) or (gap < 0 and s.bc[0] < s.bo[0])
        if entry == "GAPFADE" and back:
            return 5, -1 if gap > 0 else 1
        if entry == "GAPGO" and away:
            return 5, 1 if gap > 0 else -1
        return None
    if entry == "EMA":
        lo, hi = bar_minute(9, 45), bar_minute(14, 30)

        def cross(k):
            a, b = s.e9[k - 1] - s.e21[k - 1], s.e9[k] - s.e21[k]
            if not (np.isfinite(a) and np.isfinite(b)):
                return 0
            return 1 if a <= 0 < b else -1 if a >= 0 > b else 0
        return _first((k, cross(k)) for k in range(1, N_BAR) if lo <= m(k) <= hi)
    if entry == "RSI":
        lo, hi = bar_minute(10, 0), bar_minute(14, 30)

        def rs(k):
            a, b = s.rsi[k - 1], s.rsi[k]
            if not (np.isfinite(a) and np.isfinite(b)):
                return 0
            return 1 if a < A.RSI_LOW <= b else -1 if a > A.RSI_HIGH >= b else 0
        return _first((k, rs(k)) for k in range(1, N_BAR) if lo <= m(k) <= hi)
    if entry == "BB":
        lo, hi = bar_minute(10, 0), bar_minute(14, 30)

        def bb(k):
            if not np.isfinite([s.bb_lo[k - 1], s.bb_lo[k], s.bb_hi[k - 1], s.bb_hi[k]]).all():
                return 0
            if s.bc[k - 1] < s.bb_lo[k - 1] and s.bc[k] >= s.bb_lo[k]:
                return 1
            if s.bc[k - 1] > s.bb_hi[k - 1] and s.bc[k] <= s.bb_hi[k]:
                return -1
            return 0
        return _first((k, bb(k)) for k in range(1, N_BAR) if lo <= m(k) <= hi)
    if entry == "TRENDPB":
        lo, hi = bar_minute(10, 0), bar_minute(14, 0)
        day_open = s.o[0]

        def pb(k):
            e = s.e21[k]
            if not np.isfinite(e):
                return 0
            if s.bc[k] > day_open * (1 + A.TREND_AWAY) and s.bl[k] <= e < s.bc[k]:
                return 1
            if s.bc[k] < day_open * (1 - A.TREND_AWAY) and s.bh[k] >= e > s.bc[k]:
                return -1
            return 0
        return _first((k, pb(k)) for k in range(N_BAR) if lo <= m(k) <= hi)
    raise ValueError(entry)


# ----------------------------------------------------------------------------- exits
def exit_minute(s: SpotDay, e: int, direction: int, stop: float, exit_mode: str,
                time_exit: int | None) -> tuple[int, str]:
    """(minute whose open closes the trade, reason). Checked on NIFTY 1m highs/lows.

    exit_mode 2R: target 2 x stop; NONE: stop/time only; TRAIL: after 1 x stop in profit the stop
    moves to entry, then trails 1 x stop distance behind the best price (checked minute by
    minute; in each minute the old stop is tested before the new best price moves it)."""
    ref = s.o[e]
    risk = ref * stop
    stop_lvl = ref - direction * risk
    last = min(e + time_exit, EOD) if time_exit else EOD
    end_reason = "TIME" if time_exit and e + time_exit < EOD else "EOD"
    if last <= e:
        return e, "TIME"
    lo, hi = s.low[e:last], s.h[e:last]
    if exit_mode == "TRAIL":
        best = ref
        for j in range(len(lo)):
            if (direction > 0 and lo[j] <= stop_lvl) or (direction < 0 and hi[j] >= stop_lvl):
                return min(e + j + 1, N_MIN - 1), "TRAIL" if (stop_lvl - ref) * direction >= 0 else "STOP"
            best = max(best, hi[j]) if direction > 0 else min(best, lo[j])
            if (best - ref) * direction >= risk:
                trail = best - direction * risk
                stop_lvl = max(stop_lvl, trail) if direction > 0 else min(stop_lvl, trail)
        return last, end_reason
    tgt_lvl = ref + direction * 2 * risk if exit_mode == "2R" else None
    if direction > 0:
        stop_hit, tgt_hit = lo <= stop_lvl, (hi >= tgt_lvl) if tgt_lvl else None
    else:
        stop_hit, tgt_hit = hi >= stop_lvl, (lo <= tgt_lvl) if tgt_lvl else None
    js = int(np.argmax(stop_hit)) if stop_hit.any() else None
    jt = int(np.argmax(tgt_hit)) if tgt_hit is not None and tgt_hit.any() else None
    if js is not None and (jt is None or js <= jt):
        return min(e + js + 1, N_MIN - 1), "STOP"
    if jt is not None:
        return min(e + jt + 1, N_MIN - 1), "TARGET"
    return last, end_reason


def live_state(s: SpotDay, e: int) -> str:
    """'T' trending, 'F' flat, 'N' neither: NIFTY's last closed minute vs the day's open."""
    move = abs(s.c[e - 1] / s.o[0] - 1) if e >= 1 else 0.0
    return "T" if move >= A.LIVE_TREND_PCT else "F" if move < A.LIVE_FLAT_PCT else "N"


# ----------------------------------------------------------------------------- structures
def legs_for(structure: str, direction: int, spot: float) -> list[tuple[str, float, int]]:
    """[(CE/PE, strike, +1 bought / -1 sold)]."""
    atm = float(round(spot / A.STEP) * A.STEP)
    kind = "CE" if direction > 0 else "PE"
    other = "PE" if direction > 0 else "CE"
    if structure == "BUY_ATM":
        return [(kind, atm, 1)]
    if structure == "BUY_ITM":
        return [(kind, atm - direction * A.STEP, 1)]
    if structure == "DEBIT":
        return [(kind, atm, 1), (kind, atm + direction * 2 * A.STEP, -1)]
    if structure == "CREDIT":
        return [(other, atm, -1), (other, atm - direction * 4 * A.STEP, 1)]
    raise ValueError(structure)


Panel = Callable[[str, float], np.ndarray | None]  # (CE/PE, strike) -> 375 option opens


def _price_from(arr: np.ndarray | None, i: int, until: int) -> tuple[int, float] | None:
    if arr is None:
        return None
    for j in range(i, min(until, N_MIN - 1) + 1):
        if np.isfinite(arr[j]) and arr[j] > 0:
            return j, float(arr[j])
    return None


def price_trade(legs, panel: Panel, entry_min: int, exit_min: int, day: date,
                mult_list=(1.0, 2.0)) -> dict | None:
    """Gross and net ₹ of one round trip; None if any leg has no price (never guessed)."""
    ins, outs = [], []
    for kind, strike, _ in legs:
        arr = panel(kind, strike)
        a = _price_from(arr, entry_min, entry_min + A.PRICE_GRACE)
        if a is None:
            return None
        b = _price_from(arr, max(exit_min, a[0]), N_MIN - 1)
        if b is None:
            return None
        ins.append(a[1])
        outs.append(b[1])
    gross = sum(side * (b - a) for (_, _, side), a, b in zip(legs, ins, outs, strict=True)) * A.LOT
    res = {"gross": gross}
    for mult in mult_list:
        pct, pts = A.SLIP_PCT * mult, A.SLIP_PTS * mult
        buy_v = sell_v = 0.0
        cash = 0.0
        for (_, _, side), a, b in zip(legs, ins, outs, strict=True):
            if side > 0:
                fa, fb = C.buy_fill(a, pct, pts), C.sell_fill(b, pct, pts)
                buy_v += fa
                sell_v += fb
                cash += fb - fa
            else:
                fa, fb = C.sell_fill(a, pct, pts), C.buy_fill(b, pct, pts)
                sell_v += fa
                buy_v += fb
                cash += fa - fb
        ch = C.charges(buy_v * A.LOT, sell_v * A.LOT, day, orders=2 * len(legs))["total"]
        res[mult] = cash * A.LOT - ch
    res["legs"] = " ".join(f"{'B' if s > 0 else 'S'} {k:g}{t}@{p:.2f}"
                           for (t, k, s), p in zip(legs, ins, strict=True))
    return res


def base_keys() -> list[tuple[str, float, str, int | None, str]]:
    return [(e, st, ex, te, su) for e in A.ENTRIES for st in A.STOPS for ex in A.EXITS
            for te in A.TIME_EXITS for su in A.I_STRUCTURES]


def key_name(e, st, ex, te, su) -> str:
    return f"I|{e}|{st * 100:g}%|{ex}|{'60m' if te else 'EOD'}|{su}"


def _ts(d: date, minute: int) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(d, datetime.min.time()), tz=IST) + timedelta(
        minutes=9 * 60 + 15 + minute)


def day_trades(s: SpotDay, info: Day, panel: Panel, skipped: dict[str, int]) -> list[dict]:
    """Every base trade of one day (first signal per entry type)."""
    if not info.ok:
        skipped["intraday: day with missing ADX/VIX/previous close"] = skipped.get(
            "intraday: day with missing ADX/VIX/previous close", 0) + 1
        return []
    out = []
    for entry in A.ENTRIES:
        sig = signal(entry, s, info)
        if sig is None:
            continue
        e, direction = sig
        if e >= EOD:
            continue
        live = live_state(s, e)
        for st in A.STOPS:
            for ex in A.EXITS:
                for te in A.TIME_EXITS:
                    x, why = exit_minute(s, e, direction, st, ex, te)
                    for su in A.I_STRUCTURES:
                        sells = su in A.SELLING
                        if sells and info.vj:
                            continue
                        legs = legs_for(su, direction, s.o[e])
                        p = price_trade(legs, panel, e, x, s.day)
                        if p is None:
                            skipped["intraday: option leg without a price"] = skipped.get(
                                "intraday: option leg without a price", 0) + 1
                            continue
                        out.append({
                            "track": "I", "base": key_name(entry, st, ex, te, su), "family": entry,
                            "live": live,
                            "entry_day": s.day, "exit_day": s.day,
                            "entry_time": _ts(s.day, e), "exit_time": _ts(s.day, x),
                            "direction": direction, "structure": su, "reason": why,
                            "spot_entry": float(s.o[e]), "gross": p["gross"], "net": p[1.0],
                            "net_2x": p[2.0], "costs": p["gross"] - p[1.0], "legs": p["legs"]})
    return out


def needed_legs(s: SpotDay, info: Day) -> set[tuple[str, float]]:
    """Every (CE/PE, strike) this day's signals could trade (the loader fetches these)."""
    need: set[tuple[str, float]] = set()
    if info.prev_close is None:
        return need
    for entry in A.ENTRIES:
        sig = signal(entry, s, info)
        if sig is None or sig[0] >= EOD:
            continue
        e, direction = sig
        for su in A.I_STRUCTURES:
            need |= {(k, strike) for k, strike, _ in legs_for(su, direction, s.o[e])}
    return need
