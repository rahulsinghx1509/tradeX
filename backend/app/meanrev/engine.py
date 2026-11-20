"""MR-4 engine: candidates from the index chart, exits on the index, fills on the option.

Rules and their numbers (R1-R31) are in app/meanrev/__init__.py. Three steps:
1. `prepare` turns one index's 1-minute spot (plus futures volume and daily closes) into a
   DayInfo per trading day: the 09:15 open, previous close, bias, 15-minute bars with VWAP and
   Bollinger bands, and every Setup B reversal signal of the day. Done once per index.
2. `candidates` applies one set of Params (gap band, trend block, setups, R29) and the day
   filters (R3-R6) to a DayInfo: the entries the strategy WOULD take, with their exits worked
   out on the index (`spot_exits`). Nothing depends on the option yet.
3. `run` walks the days in time order like an account: one position at a time, 2 a day, the
   day and week loss limits, R27/R28 when two indices fire in the same minute. Each candidate
   it accepts is priced on the option (`price`): strike near 0.7 delta, fills at the option's
   1-minute open with slippage, the full cost stack. A candidate the option can't price (no
   data, risk above the cap) is skipped and the account looks at the next one.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

import pandas as pd

from app.core.market_time import IST
from app.ema921 import bars as B
from app.ema921 import costs as C
from app.ema921 import engine as EE
from app.ema921 import store as S
from app.meanrev import (
    DAY_LOSS,
    MAX_TRADES_DAY,
    RISK_FREE,
    TARGET_DELTA,
    TIME_STOP_MINUTES,
    VIX_MAX,
    WEEK_LOSS,
    Params,
)
from app.meanrev import indicators as I

SQUARE_OFF_M = 360  # 15:15 = 360 minutes after 09:15
A_ENTRY_M = 5  # 09:20
A_STOP_FROM_M = 15  # 09:30: the 09:15-09:29 extreme is complete
TREND_MINUTES = 60  # 09:15-10:14
B_FIRST_START_M = 45  # a reversal bar starting at 10:00 ...
B_LAST_CLOSE_M = 330  # ... closing by 14:45
FILL_GRACE = 2


def _t(d: date, m: int) -> pd.Timestamp:
    """IST time of minute m after 09:15 on day d."""
    return pd.Timestamp(datetime.combine(d, time(9, 15)), tz=IST) + timedelta(minutes=m)


# ----------------------------------------------------------------------------- step 1: prepare
@dataclass
class Signal:
    """A Setup B reversal found on the 15-minute chart (before any filter)."""
    direction: int
    bar: int  # index of the reversal bar within the day (0 = 09:15-09:30)
    pattern: str
    extreme: float  # lowest low (long) / highest high (short) of the pattern bars
    stretch: float  # |close - VWAP| / VWAP standard deviation of the reversal bar
    new_extreme: bool = False  # this bar made a new day low (long) / high (short): for R29


@dataclass
class DayInfo:
    underlying: str
    day: date
    complete: bool  # all 375 spot minutes present
    o: list[float]  # 1-minute open/high/low/close by minute since 09:15 (NaN if missing)
    h: list[float]
    lo: list[float]
    c: list[float]
    prev_close: float | None
    bias: int | None
    vwap_m: list[float]  # VWAP target level in force during minute m (last closed 15m bar)
    bbmid_m: list[float]
    signals: list[Signal] = field(default_factory=list)
    bar_lows: list[float] = field(default_factory=list)  # 15m lows / highs (R29 on NIFTY)
    bar_highs: list[float] = field(default_factory=list)
    has_vwap: bool = False

    @property
    def open(self) -> float:
        return self.o[0]


def _nan_list(n: int = B.SESSION_MINUTES) -> list[float]:
    return [float("nan")] * n


def _day_minutes(spot: pd.DataFrame) -> dict[date, pd.DataFrame]:
    if spot.empty:
        return {}
    d = spot.copy()
    ist = pd.to_datetime(d["time"], utc=True).dt.tz_convert(IST)
    d["day"] = ist.dt.date
    d["m"] = ((ist.dt.hour * 60 + ist.dt.minute) - (9 * 60 + 15)).astype(int)
    d = d[(d["m"] >= 0) & (d["m"] < B.SESSION_MINUTES)].drop_duplicates(["day", "m"], keep="last")
    return {k: g.sort_values("m").reset_index(drop=True) for k, g in d.groupby("day")}


def daily_closes(stored: pd.Series | None, spot: pd.DataFrame) -> pd.Series:
    """Official daily closes (stored daily candles), filled with the last 1-minute close for any
    spot day the daily table lacks. Indexed by Timestamp(date)."""
    from_1m = {}
    for d, g in _day_minutes(spot).items():
        last = g["close"].dropna()
        if not last.empty:
            from_1m[pd.Timestamp(d)] = float(last.iloc[-1])
    s = pd.Series(from_1m, dtype=float)
    if stored is not None and not stored.empty:
        st = stored.copy()
        st.index = pd.DatetimeIndex([pd.Timestamp(pd.Timestamp(x).date()) for x in st.index])
        s = pd.concat([s[~s.index.isin(st.index)], st.astype(float)])
    return s.sort_index()


def prepare(underlying: str, spot: pd.DataFrame, fut: pd.DataFrame | None,
            closes: pd.Series) -> dict[date, DayInfo]:
    """DayInfo for every day in `spot` (see the module doc)."""
    by_day = _day_minutes(spot)
    fut_by_day = _day_minutes(fut.assign(open=float("nan"))) if fut is not None and not fut.empty \
        else {}
    bias = I.daily_bias(closes)
    close_list = closes.dropna().sort_index()
    prev_of: dict[date, float] = {}
    idx = list(close_list.index)
    for i in range(1, len(idx)):
        prev_of[idx[i].date()] = float(close_list.iloc[i - 1])
    for d in by_day:  # a spot day the closes don't list: the last close before it
        if d not in prev_of:
            before = close_list[close_list.index < pd.Timestamp(d)]
            if not before.empty:
                prev_of[d] = float(before.iloc[-1])

    bars = B.resample(spot, 15)
    bb = I.bollinger(bars["close"]) if not bars.empty else pd.DataFrame()
    bars = pd.concat([bars, bb], axis=1) if not bars.empty else bars
    bars_by_day = {k: g.reset_index(drop=True) for k, g in bars.groupby("day")} if not bars.empty \
        else {}

    out: dict[date, DayInfo] = {}
    for d, g in by_day.items():
        o, h, lo, c = _nan_list(), _nan_list(), _nan_list(), _nan_list()
        for r in g.itertuples(index=False):
            o[r.m], h[r.m], lo[r.m], c[r.m] = (float(r.open), float(r.high), float(r.low),
                                               float(r.close))
        complete = len(g) == B.SESSION_MINUTES and not g[["open", "high", "low", "close"]].isna().any().any()
        info = DayInfo(underlying, d, complete, o, h, lo, c, prev_of.get(d), bias.get(d),
                       _nan_list(), _nan_list())
        day_bars = bars_by_day.get(d)
        if day_bars is not None:
            _add_bars(info, g, fut_by_day.get(d), day_bars)
        out[d] = info
    return out


def _add_bars(info: DayInfo, minutes: pd.DataFrame, fut_day: pd.DataFrame | None,
              bars: pd.DataFrame) -> None:
    """VWAP per bar, the minute-by-minute target levels, and the day's reversal signals."""
    vw = pd.DataFrame()
    if fut_day is not None and not fut_day.empty and "volume" in fut_day:
        vol = pd.to_numeric(fut_day.set_index("m")["volume"], errors="coerce")
        vw = I.vwap_bands(minutes, vol).set_index("m")
    n = len(bars)
    vwap, sd, vlo, vhi = ([float("nan")] * n for _ in range(4))
    for j in range(n):
        last_m = min(B.SESSION_MINUTES, (j + 1) * 15) - 1
        if not vw.empty and last_m in vw.index:
            vwap[j], sd[j] = vw.at[last_m, "vwap"], vw.at[last_m, "vwap_sd"]
            vlo[j], vhi[j] = vw.at[last_m, "vwap_lo"], vw.at[last_m, "vwap_hi"]
    info.has_vwap = any(pd.notna(x) for x in vwap)
    bb_mid = [float(x) for x in bars["bb_mid"]]
    for m in range(B.SESSION_MINUTES):  # levels from the last bar CLOSED before minute m
        j = m // 15 - 1
        if 0 <= j < n:
            info.vwap_m[m], info.bbmid_m[m] = float(vwap[j]), bb_mid[j]
    ob = [I.Bar(float(r.open), float(r.high), float(r.low), float(r.close))
          for r in bars.itertuples(index=False)]
    info.bar_lows = [b.low for b in ob]
    info.bar_highs = [b.high for b in ob]

    def stretched(i: int, direction: int) -> bool:
        if i < 0 or pd.isna(vlo[i]) or pd.isna(bars.at[i, "bb_lo"]):
            return False
        if direction > 0:
            return ob[i].close < vlo[i] and ob[i].low <= bars.at[i, "bb_lo"]
        return ob[i].close > vhi[i] and ob[i].high >= bars.at[i, "bb_hi"]

    for j in range(n):
        start_m, close_m = j * 15, (j + 1) * 15
        if start_m < B_FIRST_START_M or close_m > B_LAST_CLOSE_M:
            continue
        for direction in (1, -1):
            pat = I.reversal(ob[: j + 1], direction)
            if pat is None:
                continue
            name, span = pat
            window = (j - 1, j) if span == 1 else (j - 2, j - 1, j)
            if not any(stretched(i, direction) for i in window):
                continue
            pbars = ob[j - span + 1: j + 1]
            if direction > 0:
                ext = min(b.low for b in pbars)
                new_ext = ob[j].low < min(info.bar_lows[:j])
            else:
                ext = max(b.high for b in pbars)
                new_ext = ob[j].high > max(info.bar_highs[:j])
            stretch = abs(ob[j].close - vwap[j]) / sd[j] if sd[j] and sd[j] > 0 else 0.0
            info.signals.append(Signal(direction, j, name, ext, round(stretch, 3), new_ext))


# ----------------------------------------------------------------------------- step 2: candidates
@dataclass
class Exit:
    fraction: float  # share of the position
    minute: int  # the option is sold at this minute's open
    reason: str


@dataclass
class Candidate:
    underlying: str
    setup: str  # A / B
    day: date
    direction: int
    entry_m: int
    entry_spot: float
    stop: float
    size: float  # 1 or the half size (R9)
    score: float  # R27: bigger = more stretched
    pattern: str
    exits: list[Exit]

    @property
    def entry_time(self) -> pd.Timestamp:
        return _t(self.day, self.entry_m)

    @property
    def last_exit_m(self) -> int:
        return max(e.minute for e in self.exits)


@dataclass(frozen=True)
class Market:
    """What both indices share: India VIX and the event calendar."""
    vix_915: dict[date, float]  # 1-minute INDIAVIX open at 09:15
    vix_daily: pd.Series  # daily closes, Timestamp(date) index
    events: frozenset[date]

    def vix(self, d: date) -> tuple[float | None, bool]:
        """(VIX at 09:15, used the fallback?) — the previous daily close if no 1-minute VIX."""
        if d in self.vix_915:
            return self.vix_915[d], False
        before = self.vix_daily[self.vix_daily.index < pd.Timestamp(d)].dropna()
        if before.empty:
            return None, True
        return float(before.iloc[-1]), True


def expiry_days(underlying: str, expiries: list[date], monthly_only: bool) -> frozenset[date]:
    """R3: every expiry, or only the last expiry of each calendar month."""
    if not monthly_only:
        return frozenset(expiries)
    last: dict[tuple[int, int], date] = {}
    for e in sorted(expiries):
        last[(e.year, e.month)] = e
    return frozenset(last.values())


def day_block(info: DayInfo, p: Params, market: Market, expiry_set: frozenset[date]) -> str | None:
    """Why the whole day is skipped for this index (R3-R5, data), or None."""
    if not info.complete:
        return "SPOT_GAP"
    if info.day in expiry_set:
        return "EXPIRY_DAY"
    if info.day in market.events:
        return "EVENT_DAY"
    vix, _ = market.vix(info.day)
    if vix is None:
        return "NO_VIX"
    if vix > VIX_MAX:
        return "VIX_ABOVE_20"
    if info.bias is None or info.prev_close is None:
        return "NO_DAILY_HISTORY"
    return None


def _size(bias: int, direction: int, p: Params) -> float | None:
    if bias == 0:
        return p.half_size
    return 1.0 if bias == direction else None


def candidates(info: DayInfo, p: Params, market: Market, expiry_set: frozenset[date],
               nifty: DayInfo | None = None, why: Counter | None = None) -> list[Candidate]:
    """Every entry this index's rules would take today, in time order (no account rules yet)."""
    why = why if why is not None else Counter()
    block = day_block(info, p, market, expiry_set)
    if block:
        why[block] += 1
        return []
    out: list[Candidate] = []
    if "A" in p.setups:
        a = _setup_a(info, p, why)
        if a:
            out.append(a)
    if "B" in p.setups:
        rng = max(info.h[:TREND_MINUTES]) - min(info.lo[:TREND_MINUTES])
        if rng / info.open * 100 > p.trend_block_pct:
            why["B_TREND_DAY"] += 1
        elif not info.has_vwap:
            why["B_NO_VWAP"] += 1
        else:
            out += _setup_b(info, p, nifty, why)
    return sorted(out, key=lambda c: c.entry_m)


def _setup_a(info: DayInfo, p: Params, why: Counter) -> Candidate | None:
    gap = (info.open / info.prev_close - 1) * 100
    if not p.gap_min_pct <= abs(gap) <= p.gap_max_pct:
        return None
    direction = -1 if gap > 0 else 1
    size = _size(info.bias, direction, p)
    if size is None:
        why["A_AGAINST_BIAS"] += 1
        return None
    first_close = info.c[A_ENTRY_M - 1]
    if not (first_close < info.open if direction < 0 else first_close > info.open):
        why["A_NOT_CONFIRMED"] += 1
        return None
    if direction > 0:
        stop = min(info.lo[:A_STOP_FROM_M])
    else:
        stop = max(info.h[:A_STOP_FROM_M])
    exits = spot_exits_a(info, direction, stop)
    why["A_SIGNAL"] += 1
    return Candidate(info.underlying, "A", info.day, direction, A_ENTRY_M, info.o[A_ENTRY_M],
                     stop, size, abs(gap) / p.gap_max_pct, "gap fill", exits)


def spot_exits_a(info: DayInfo, direction: int, stop: float) -> list[Exit]:
    """Target = previous close; stop checked from 09:30; 15:15 square-off (R12, R22)."""
    target = info.prev_close
    for m in range(A_ENTRY_M, SQUARE_OFF_M):
        if m >= A_STOP_FROM_M and (info.lo[m] <= stop if direction > 0 else info.h[m] >= stop):
            return [Exit(1.0, m + 1, "STOP")]
        if info.h[m] >= target if direction > 0 else info.lo[m] <= target:
            return [Exit(1.0, m + 1, "TARGET")]
    return [Exit(1.0, SQUARE_OFF_M, "SQUARE_OFF")]


def _setup_b(info: DayInfo, p: Params, nifty: DayInfo | None, why: Counter) -> list[Candidate]:
    out = []
    for s in info.signals:
        size = _size(info.bias, s.direction, p)
        if size is None:
            why["B_AGAINST_BIAS"] += 1
            continue
        if p.r29:
            if nifty is None or len(nifty.bar_lows) <= s.bar:
                why["B_R29_NO_NIFTY"] += 1
                continue
            if s.direction > 0 and nifty.bar_lows[s.bar] < min(nifty.bar_lows[:s.bar]):
                why["B_R29_NIFTY_NEW_LOW"] += 1
                continue
            if s.direction < 0 and nifty.bar_highs[s.bar] > max(nifty.bar_highs[:s.bar]):
                why["B_R29_NIFTY_NEW_HIGH"] += 1
                continue
        entry_m = (s.bar + 1) * 15
        entry = info.o[entry_m]
        buf = p.stop_buffer_pct / 100
        stop = s.extreme * (1 - buf) if s.direction > 0 else s.extreme * (1 + buf)
        if (entry - stop) * s.direction <= 0:
            why["B_STOP_WRONG_SIDE"] += 1
            continue
        why["B_SIGNAL"] += 1
        out.append(Candidate(info.underlying, "B", info.day, s.direction, entry_m, entry, stop,
                             size, s.stretch, s.pattern, spot_exits_b(info, s.direction, entry_m,
                                                                      entry, stop)))
    return out


def spot_exits_b(info: DayInfo, direction: int, entry_m: int, entry: float,
                 stop: float) -> list[Exit]:
    """R19-R22: half at the middle Bollinger band, half at VWAP, breakeven after the first,
    90 minutes, 15:15. A stop and a target in the same minute = the stop."""
    left = {"MID_BAND": 0.5, "VWAP": 0.5}
    out: list[Exit] = []
    end_m = min(entry_m + TIME_STOP_MINUTES, SQUARE_OFF_M)
    moved = False
    for m in range(entry_m, end_m):
        hit_stop = info.lo[m] <= stop if direction > 0 else info.h[m] >= stop
        if hit_stop:
            reason = "BREAKEVEN" if moved else "STOP"
            out += [Exit(f, m + 1, reason) for f in left.values()]
            return _merge(out)
        levels = {"MID_BAND": info.bbmid_m[m], "VWAP": info.vwap_m[m]}
        for name in list(left):
            lv = levels[name]
            if pd.notna(lv) and (info.h[m] >= lv if direction > 0 else info.lo[m] <= lv):
                out.append(Exit(left.pop(name), m + 1, name))
        if out and not moved:
            moved, stop = True, entry
        if not left:
            return _merge(out)
    reason = "TIME_90" if end_m < SQUARE_OFF_M else "SQUARE_OFF"
    out += [Exit(f, end_m, reason) for f in left.values()]
    return _merge(out)


def _merge(exits: list[Exit]) -> list[Exit]:
    """Halves sold in the same minute for the same reason are one order."""
    merged: dict[tuple[int, str], float] = {}
    for e in exits:
        merged[(e.minute, e.reason)] = merged.get((e.minute, e.reason), 0.0) + e.fraction
    return [Exit(f, m, r) for (m, r), f in sorted(merged.items())]


# ----------------------------------------------------------------------------- step 3: price + run
@dataclass
class Fill:
    fraction: float
    time: datetime
    reason: str
    price: float
    fill: float
    stale: bool = False


@dataclass
class Trade:
    underlying: str
    setup: str
    day: date
    direction: int
    pattern: str
    size: float
    qty: float  # units (lot x size)
    option_type: str
    strike: float
    expiry: date
    symbol: str
    delta: float
    iv: float
    iv_source: str  # ATM / VIX
    entry_time: datetime
    entry_spot: float
    stop: float
    entry_price: float
    entry_fill: float
    exits: list[Fill]
    gross_pnl: float = 0.0
    slippage: float = 0.0
    charges: float = 0.0
    net_pnl: float = 0.0

    @property
    def exit_time(self) -> datetime:
        return max(f.time for f in self.exits)

    @property
    def exit_reason(self) -> str:
        return "+".join(f.reason for f in self.exits)

    @property
    def stale(self) -> bool:
        return any(f.stale for f in self.exits)

    @property
    def friction(self) -> float:
        return round(self.slippage + self.charges, 2)


@dataclass
class Instrument:
    """One index's option side for the run."""
    underlying: str
    options: EE.OptionData
    lot: int
    step: int
    max_risk: float
    _cache: dict = field(default_factory=dict)

    def minutes(self, symbol: str, d: date) -> EE._Minutes:
        key = (symbol, d)
        if key not in self._cache:
            self._cache[key] = EE._Minutes(self.options.minutes(symbol, d))
        return self._cache[key]


def years_to(expiry: date, t: pd.Timestamp) -> float:
    end = pd.Timestamp(datetime.combine(expiry, time(15, 30)), tz=IST)
    return max((end - t).total_seconds(), 60.0) / (365 * 86400)


def price(c: Candidate, inst: Instrument, market: Market, cfg: EE.Config,
          check_risk: bool) -> Trade | str:
    """The option trade for a candidate, or the reason it can't be taken."""
    expiry = EE.pick_expiry(c.day, inst.options.expiries(), True)
    if expiry is None:
        return "NO_EXPIRY"
    t = c.entry_time
    call = c.direction > 0
    opt = "CE" if call else "PE"
    yrs = years_to(expiry, t)
    atm = EE.atm_strike(c.entry_spot, inst.step)
    iv, src = None, "ATM"
    atm_sym = inst.options.contract(expiry, atm, opt)
    if atm_sym is not None:
        hit = inst.minutes(atm_sym, c.day).open_at(t, FILL_GRACE)
        if hit is not None:
            iv = I.implied_vol(hit[1], c.entry_spot, atm, yrs, RISK_FREE, call)
    if iv is None:
        vix, _ = market.vix(c.day)
        if vix is None:
            return "NO_IV"
        iv, src = vix / 100, "VIX"
    strike, delta = I.strike_near_delta(c.entry_spot, atm, inst.step, yrs, iv, RISK_FREE, call,
                                        TARGET_DELTA)
    qty = inst.lot * c.size
    if check_risk and abs(c.entry_spot - c.stop) * delta * qty > inst.max_risk:
        return "RISK_TOO_BIG"
    sym = inst.options.contract(expiry, strike, opt)
    if sym is None:
        return "NO_CONTRACT"
    m = inst.minutes(sym, c.day)
    hit = m.open_at(t, FILL_GRACE)
    if hit is None:
        return "NO_DATA"
    et, ep = hit
    efill = C.buy_fill(ep, cfg.slippage_pct, cfg.slippage_points)
    fills = []
    for e in c.exits:
        at = _t(c.day, e.minute)
        got = m.open_at(at, FILL_GRACE)
        stale = got is None
        if got is None:
            got = m.last_before(at) or (et, ep)
        xt, xp = got
        fills.append(Fill(e.fraction, xt.to_pydatetime(), e.reason, xp,
                          C.sell_fill(xp, cfg.slippage_pct, cfg.slippage_points), stale))
    tr = Trade(c.underlying, c.setup, c.day, c.direction, c.pattern, c.size, qty, opt, strike,
               expiry, sym, round(delta, 3), round(iv, 4), src, et.to_pydatetime(), c.entry_spot,
               round(c.stop, 2), ep, efill, fills)
    gross = sum((f.price - ep) * qty * f.fraction for f in fills)
    filled = sum((f.fill - efill) * qty * f.fraction for f in fills)
    sell_value = sum(f.fill * qty * f.fraction for f in fills)
    ch = C.charges(efill * qty, sell_value, c.day, orders=1 + len(fills))
    tr.gross_pnl = round(gross, 2)
    tr.slippage = round(gross - filled, 2)
    tr.charges = ch["total"]
    tr.net_pnl = round(filled - ch["total"], 2)
    return tr


@dataclass
class RunResult:
    trades: list[Trade]
    skips: Counter


def run(cands: list[Candidate], instruments: dict[str, Instrument], market: Market,
        cfg: EE.Config, day_loss: float | None = DAY_LOSS, week_loss: float | None = WEEK_LOSS,
        max_per_day: int = MAX_TRADES_DAY) -> RunResult:
    """The account (R24, R25, R27, R28, R30). `cands` may hold one index or both."""
    trades: list[Trade] = []
    skips: Counter = Counter()
    day_pnl: dict[date, float] = {}
    week_pnl: dict[tuple, float] = {}
    by_time: dict[tuple[date, int], list[Candidate]] = {}
    for c in cands:
        by_time.setdefault((c.day, c.entry_m), []).append(c)
    busy_until: tuple[date, int] | None = None
    taken_today: dict[date, int] = {}
    for (d, m) in sorted(by_time):
        group = by_time[(d, m)]
        wk = d.isocalendar()[:2]
        if busy_until is not None and (d, m) < busy_until:
            skips["POSITION_OPEN"] += len(group)
            continue
        if taken_today.get(d, 0) >= max_per_day:
            skips["MAX_2_A_DAY"] += len(group)
            continue
        if (day_loss is not None and day_pnl.get(d, 0.0) <= -day_loss) or \
                (week_loss is not None and week_pnl.get(wk, 0.0) <= -week_loss):
            skips["LOSS_LIMIT"] += len(group)
            continue
        if len({c.direction for c in group}) > 1:  # R28
            skips["OPPOSITE_SIGNALS"] += len(group)
            continue
        ordered = sorted(group, key=lambda c: -c.score)  # R27: the more stretched first
        for i, c in enumerate(ordered):
            got = price(c, instruments.get(c.underlying), market, cfg, check_risk=c.setup == "B")
            if isinstance(got, str):
                skips[got] += 1
                continue
            trades.append(got)
            busy_until = (d, c.last_exit_m)
            taken_today[d] = taken_today.get(d, 0) + 1
            day_pnl[d] = day_pnl.get(d, 0.0) + got.net_pnl
            week_pnl[wk] = week_pnl.get(wk, 0.0) + got.net_pnl
            if len(ordered) - i - 1:
                skips["R27_OTHER_INDEX"] += len(ordered) - i - 1
            break
    return RunResult(trades, skips)


# ----------------------------------------------------------------------------- convenience
def instrument_for(underlying: str, options: EE.OptionData, p: Params) -> Instrument:
    lot, step = S.UNDERLYINGS[underlying]
    return Instrument(underlying, options, lot, step, p.max_risk)
