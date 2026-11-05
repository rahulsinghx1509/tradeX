"""Signals -> simulated option trades, for the three execution modes.

The numbers in brackets (#1, #9 ...) point to the rows of the rules doc's "Ambiguities
settled" table: https://claude.ai/artifact/PiDc6FGqdqS6xnc1z6sZq4

Walk-through for one trading day and one mode:
1. Take that day's EMA crosses (closing before 15:00).
2. A cross with a trade open closes it (opposite cross). If the cross is inside the entry
   window (and passes the cross-number filter), a new trade opens in its direction.
3. Trailing stop (added 2026-11-01, on by default): a chandelier stop on NIFTY spot, checked on
   each closed signal bar. Long: stop = highest high since entry - 2 x ATR(14); short: lowest
   low + 2 x ATR(14). The stop only ever tightens. A bar closing beyond it exits the trade at the
   option's open of the next minute (TRAIL_STOP); the engine then waits for the next cross.
4. Whatever is still open is squared off at 15:00.
Prices come from the option's own 1-minute candles: the open of the minute the signal bar
closes (#1). Missing data is never filled with zero: an entry without a price is skipped
(NO_DATA, #11); an exit without a price uses the last known price and is flagged (#12).
"""

from __future__ import annotations

import bisect
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Protocol

import pandas as pd

from app.core.market_time import IST, is_trading_day
from app.ema921 import bars as B
from app.ema921 import costs as C
from app.ema921 import trend as T

MODES = ("ITM_BUY", "ATM_BUY", "ATM_SELL")
MODE_LABELS = {"ITM_BUY": "Deep ITM buy", "ATM_BUY": "ATM buy", "ATM_SELL": "ATM sell"}
EXIT_LABELS = {"OPPOSITE_CROSS": "opposite cross", "TRAIL_STOP": "trailing stop",
               "RANGE_STOP": "range stop", "SQUARE_OFF": "15:00 square-off"}
START_CHOICES = (time(9, 30), time(10, 0), time(10, 30))
CUTOFF_CHOICES = tuple(time(h, m) for h, m in
                       [(11, 30), (12, 0), (12, 30), (13, 0), (13, 30), (14, 0), (14, 30)])


@dataclass(frozen=True)
class Config:
    timeframe: int = 5  # minutes: 5, 15 or 30
    start: time = time(9, 30)  # entry window start (#4)
    cutoff: time = time(14, 30)  # entry window cutoff (#4)
    square_off: time = time(15, 0)  # (#7)
    cross_numbers: frozenset[int] | None = None  # e.g. {1} = only the day's first cross (#8)
    lot_size: int = 65  # flat for every date (#14)
    lots: int = 1
    slippage_pct: float = 0.005  # (#15)
    slippage_points: float = 0.5  # (#15)
    strike_step: int = 50  # (#9)
    itm_steps: int = 5  # (#9)
    roll_on_expiry_day: bool = True  # (#10)
    fill_grace_minutes: int = 2  # (#11, #12)
    max_missing_spot_minutes: int = 0  # (#13)
    capital: float = 100_000.0  # for drawdown % only (#18)
    whipsaw_bars: int = 2  # (#19)
    stop_atr_mult: float | None = 2.0  # chandelier trailing stop on spot; None or 0 = off
    atr_period: int = 14
    trend_filter: bool = False  # ET-1.0 (app/ema921/trend.py): entries only with the 15m trend

    @property
    def qty(self) -> int:
        return self.lot_size * self.lots


class OptionData(Protocol):
    """Where the engine gets option facts from (the database, or a fake in the tests)."""

    def expiries(self) -> list[date]: ...

    def contract(self, expiry: date, strike: float, option_type: str) -> str | None: ...

    def minutes(self, symbol: str, day: date) -> pd.DataFrame | None: ...


@dataclass
class Trade:
    mode: str
    day: date
    direction: int  # NIFTY view: +1 long (bullish cross), -1 short
    side: str  # BUY / SELL (the option leg)
    option_type: str  # CE / PE
    strike: float
    expiry: date
    dte: int
    symbol: str
    cross_number: int
    signal_time: datetime  # IST, close of the entry signal bar
    entry_time: datetime  # IST, minute of the entry fill
    entry_price: float  # option 1m open, before slippage
    entry_fill: float
    nifty_entry: float
    exit_time: datetime | None = None
    exit_price: float | None = None
    exit_fill: float | None = None
    exit_reason: str | None = None  # OPPOSITE_CROSS / TRAIL_STOP / SQUARE_OFF
    nifty_exit: float | None = None
    stale_exit: bool = False
    bars_held: int = 0
    gross_pnl: float = 0.0
    slippage: float = 0.0
    charges: dict[str, float] = field(default_factory=dict)
    net_pnl: float = 0.0
    mae: float | None = None  # worst open loss during the trade, ₹ (negative = loss)
    whipsaw: bool = False  # closed by the opposite cross within `whipsaw_bars` bars (#19)

    @property
    def friction(self) -> float:
        return round(self.slippage + self.charges.get("total", 0.0), 2)


@dataclass(frozen=True)
class Skip:
    mode: str
    day: date
    signal_time: datetime
    reason: str  # NO_EXPIRY / NO_CONTRACT / NO_DATA
    detail: str = ""


@dataclass
class Result:
    config: Config
    trades: dict[str, list[Trade]]
    skips: list[Skip]
    no_trade_days: dict[date, str]  # day -> reason
    days_tested: list[date]


# ----------------------------------------------------------------------------- contract choice
def atm_strike(spot: float, step: int = 50) -> float:
    """Spot rounded to the nearest strike; exactly half-way rounds up (#9)."""
    return float(int((spot + step / 2) // step) * step)


def leg_for(mode: str, direction: int, spot: float, cfg: Config) -> tuple[str, str, float]:
    """(side, option type, strike) for a signal (#9).

    Bullish: ITM buy CE, ATM buy CE, ATM sell PE. Bearish: the mirror image.
    """
    atm = atm_strike(spot, cfg.strike_step)
    if mode == "ATM_SELL":
        return "SELL", "PE" if direction > 0 else "CE", atm
    opt = "CE" if direction > 0 else "PE"
    if mode == "ATM_BUY":
        return "BUY", opt, atm
    if mode == "ITM_BUY":
        shift = cfg.itm_steps * cfg.strike_step
        return "BUY", opt, atm - shift if opt == "CE" else atm + shift
    raise ValueError(f"unknown mode {mode}")


def pick_expiry(day: date, expiries: list[date], roll: bool) -> date | None:
    """Nearest weekly expiry on or after `day`; on expiry day itself, the next one (#10)."""
    for e in sorted(expiries):
        if e > day or (e == day and not roll):
            return e
    return None


# ----------------------------------------------------------------------------- prices
class _Minutes:
    """One option's 1-minute candles for one day, searchable by IST minute."""

    def __init__(self, df: pd.DataFrame | None):
        if df is None or df.empty:
            self.times: list[pd.Timestamp] = []
            self.df = pd.DataFrame(columns=["open", "high", "low", "close"])
            return
        d = df.copy()
        d["t"] = pd.to_datetime(d["time"], utc=True).dt.tz_convert(IST)
        d = d.sort_values("t").drop_duplicates("t")
        self.df = d.reset_index(drop=True)
        self.times = list(self.df["t"])

    def open_at(self, t: pd.Timestamp, grace: int) -> tuple[pd.Timestamp, float] | None:
        """Open of the first candle starting in [t, t + grace minutes)."""
        i = bisect.bisect_left(self.times, t)
        while i < len(self.times) and self.times[i] < t + timedelta(minutes=grace):
            o = self.df.at[i, "open"]
            if pd.notna(o):
                return self.times[i], float(o)
            i += 1
        return None

    def last_before(self, t: pd.Timestamp) -> tuple[pd.Timestamp, float] | None:
        i = bisect.bisect_left(self.times, t) - 1
        while i >= 0:
            c = self.df.at[i, "close"]
            if pd.notna(c):
                return self.times[i], float(c)
            i -= 1
        return None

    def extreme(self, start: pd.Timestamp, end: pd.Timestamp, worst_high: bool) -> float | None:
        """Highest high (sell leg) or lowest low (buy leg) of the candles in [start, end)."""
        mask = (self.df["t"] >= start) & (self.df["t"] < end) if self.times else []
        if not len(mask) or not mask.any():
            return None
        col = self.df.loc[mask, "high" if worst_high else "low"].dropna()
        if col.empty:
            return None
        return float(col.max() if worst_high else col.min())


def _ist(d: date, t: time) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(d, t), tz=IST)


# ----------------------------------------------------------------------------- the run
def no_trade_days(spot: pd.DataFrame, start: date, end: date, cfg: Config,
                  holidays: frozenset[date] | None = None,
                  as_of: pd.Timestamp | None = None) -> tuple[list[date], dict[date, str]]:
    """(tradable days, {day: reason}) for every weekday in [start, end] (#13).
    Live (`as_of`): today only has to be complete up to the last closed minute."""
    missing = B.complete_days(spot)
    if as_of is not None and as_of.date() in missing:
        today = as_of.date()
        elapsed = int((as_of - _ist(today, B.OPEN)).total_seconds() // 60)
        expected = max(0, min(B.SESSION_MINUTES, elapsed))
        missing[today] = max(0, expected - (B.SESSION_MINUTES - missing[today]))
    ok, why = [], {}
    d = start
    while d <= end:
        if not is_trading_day(d, holidays):
            if d in missing:  # Muhurat, Saturday budget session ...
                why[d] = "SPECIAL_SESSION"
            elif d.weekday() < 5:
                why[d] = "HOLIDAY"
        elif d not in missing:
            why[d] = "NO_SPOT_DATA"
        elif missing[d] > cfg.max_missing_spot_minutes:
            why[d] = f"SPOT_GAP ({missing[d]} min missing)"
        else:
            ok.append(d)
        d += timedelta(days=1)
    return ok, why


def run(spot: pd.DataFrame, options: OptionData, start: date, end: date, cfg: Config,
        modes: tuple[str, ...] = MODES, holidays: frozenset[date] | None = None,
        as_of: pd.Timestamp | None = None,
        entry_filter: Callable[[B.Cross], bool] | None = None) -> Result:
    """Backtest [start, end]. `spot` should also hold some days BEFORE `start` so the EMAs are
    warmed up (#3); those days are never traded.

    `entry_filter` (e.g. the VF-1.0 volume filter, app/volume): a cross it returns False for opens
    no trade; its exit of an open trade still counts, like the ET-1.0 trend filter.

    `as_of` is for the live paper engine: only bars that have closed by then count, a fill whose
    1-minute candle may still arrive is left pending (not skipped), and a trade that is still
    running is returned with exit_time None. The backtest leaves it None.
    """
    signal_bars = B.add_atr(B.add_emas(B.resample(spot, cfg.timeframe)), cfg.atr_period)
    if as_of is not None:
        signal_bars = signal_bars[signal_bars["bar_close"] <= as_of].reset_index(drop=True)
    bars_by_day: dict[date, list[_Bar]] = {}
    for r in signal_bars.itertuples(index=False):
        bars_by_day.setdefault(r.day, []).append(
            _Bar(r.bar_close, float(r.high), float(r.low), float(r.close), float(r.atr)))
    all_crosses = B.crosses(signal_bars)
    days, why = no_trade_days(spot, start, end, cfg, holidays, as_of)
    blocked = T.blocked(spot, all_crosses) if cfg.trend_filter else frozenset()
    if entry_filter is not None:
        blocked = blocked | frozenset(c.bar_close for c in all_crosses if not entry_filter(c))
    by_day: dict[date, list[B.Cross]] = {}
    for c in all_crosses:
        by_day.setdefault(c.day, []).append(c)
    bar_index = {t: i for i, t in enumerate(signal_bars["bar_close"])}
    expiries = options.expiries()
    trades: dict[str, list[Trade]] = {m: [] for m in modes}
    skips: list[Skip] = []
    cache: dict[tuple[str, date], _Minutes] = {}

    def minutes(symbol: str, day: date) -> _Minutes:
        key = (symbol, day)
        if key not in cache:
            cache[key] = _Minutes(options.minutes(symbol, day))
        return cache[key]

    spot_px = _SpotPrices(spot)
    for d in days:
        crosses_today = [c for c in by_day.get(d, []) if c.bar_close.time() < cfg.square_off]
        made = 0
        for mode in modes:
            day_trades = _run_day(mode, d, crosses_today, options, expiries, cfg, minutes,
                                  skips, bar_index, spot_px, as_of, bars_by_day.get(d, []),
                                  blocked)
            trades[mode].extend(day_trades)
            made += len(day_trades)
        if not made:
            if not crosses_today:
                why[d] = "NO_CROSS"
            elif not any(_entry_allowed(c, cfg) for c in crosses_today):
                why[d] = "NO_CROSS_IN_WINDOW"
            elif not any(_entry_allowed(c, cfg, blocked) for c in crosses_today):
                why[d] = "TREND_FILTER" if entry_filter is None else "ENTRY_FILTER"
            else:
                why[d] = "ENTRIES_SKIPPED (no option data)"
    return Result(cfg, trades, skips, dict(sorted(why.items())), days)


def _entry_allowed(c: B.Cross, cfg: Config, blocked: frozenset = frozenset()) -> bool:
    t = c.bar_close.time()
    if not (cfg.start <= t <= cfg.cutoff):
        return False
    if c.bar_close in blocked:  # ET-1.0 trend filter said no (exits are never blocked)
        return False
    return cfg.cross_numbers is None or c.number in cfg.cross_numbers


class _SpotPrices:
    """NIFTY 1m open at a minute (for the square-off), else the last close before it."""

    def __init__(self, spot: pd.DataFrame):
        d = spot.assign(t=pd.to_datetime(spot["time"], utc=True)).sort_values("t")
        self.times = list(d["t"])
        self.opens = list(d["open"])
        self.closes = list(d["close"])

    def at(self, t: pd.Timestamp) -> float | None:
        tu = t.tz_convert("UTC")
        i = bisect.bisect_left(self.times, tu)
        if i < len(self.times) and self.times[i] == tu and pd.notna(self.opens[i]):
            return float(self.opens[i])
        return float(self.closes[i - 1]) if i > 0 else None


@dataclass(frozen=True)
class _Bar:
    close_time: pd.Timestamp  # IST
    high: float
    low: float
    close: float
    atr: float


def _stop_hit(tr: Trade, bars: list[_Bar], before: pd.Timestamp, cfg: Config) -> _Bar | None:
    """First signal bar after entry and before `before` that closes beyond the chandelier stop.

    Long: stop = highest high since entry - k x ATR, raised only. Short: the mirror image.
    The entry (signal) bar seeds the extreme; checking starts with the next bar."""
    k = cfg.stop_atr_mult
    if not k:
        return None
    entry = pd.Timestamp(tr.signal_time)
    seed = [b for b in bars if b.close_time == entry]
    if not seed or pd.isna(seed[0].atr):
        return None
    long = tr.direction > 0
    extreme = seed[0].high if long else seed[0].low
    stop = extreme - k * seed[0].atr if long else extreme + k * seed[0].atr
    for b in bars:
        if b.close_time <= entry:
            continue
        if b.close_time >= before:
            break
        if pd.isna(b.atr):
            continue
        if long:
            extreme = max(extreme, b.high)
            stop = max(stop, extreme - k * b.atr)
            if b.close < stop:
                return b
        else:
            extreme = min(extreme, b.low)
            stop = min(stop, extreme + k * b.atr)
            if b.close > stop:
                return b
    return None


def _pending(as_of: pd.Timestamp | None, t: pd.Timestamp, cfg: Config) -> bool:
    """Live only: the fill candle for minute t may still arrive."""
    return as_of is not None and as_of < t + timedelta(minutes=cfg.fill_grace_minutes)


def _run_day(mode, d, crosses_today, options, expiries, cfg, minutes, skips, bar_index,
             spot, as_of=None, day_bars: list[_Bar] | None = None,
             blocked: frozenset = frozenset()) -> list[Trade]:
    out: list[Trade] = []
    open_trade: Trade | None = None
    day_bars = day_bars or []

    def stop_out(before: pd.Timestamp) -> bool | None:
        """Close the open trade if its trailing stop was hit before `before`.
        None: no hit. True: closed. False: live, exit candle not in yet."""
        hit = _stop_hit(open_trade, day_bars, before, cfg)
        if hit is None:
            return None
        return _close(open_trade, hit.close_time, "TRAIL_STOP", hit.close, cfg, minutes,
                      bar_index, as_of)

    for c in crosses_today:
        if open_trade is not None:
            stopped = stop_out(c.bar_close)
            if stopped is not None:
                out.append(open_trade)
                open_trade = None
                if not stopped:
                    return out
        if open_trade is not None:
            closed = _close(open_trade, c.bar_close, "OPPOSITE_CROSS", c.spot, cfg, minutes,
                            bar_index, as_of)
            out.append(open_trade)
            open_trade = None
            if not closed:  # exit still waiting for its candle; nothing later is known yet
                return out
        if _entry_allowed(c, cfg, blocked):
            open_trade = _open(mode, d, c, options, expiries, cfg, minutes, skips, as_of)
    if open_trade is not None:
        t = _ist(d, cfg.square_off)
        stopped = stop_out(t)
        if stopped is not None:
            out.append(open_trade)
            return out
        if as_of is None or as_of >= t:
            _close(open_trade, t, "SQUARE_OFF", spot.at(t), cfg, minutes, bar_index, as_of)
        out.append(open_trade)
    return out


def _open(mode, d, c: B.Cross, options, expiries, cfg, minutes, skips, as_of=None) -> Trade | None:
    side, opt, strike = leg_for(mode, c.direction, c.spot, cfg)
    expiry = pick_expiry(d, expiries, cfg.roll_on_expiry_day)
    if expiry is None:
        skips.append(Skip(mode, d, c.bar_close, "NO_EXPIRY"))
        return None
    symbol = options.contract(expiry, strike, opt)
    if symbol is None:
        skips.append(Skip(mode, d, c.bar_close, "NO_CONTRACT", f"{expiry} {strike:g} {opt}"))
        return None
    hit = minutes(symbol, d).open_at(c.bar_close, cfg.fill_grace_minutes)
    if hit is None:
        if not _pending(as_of, c.bar_close, cfg):
            skips.append(Skip(mode, d, c.bar_close, "NO_DATA", symbol))
        return None
    t, price = hit
    fill = (C.buy_fill if side == "BUY" else C.sell_fill)(price, cfg.slippage_pct, cfg.slippage_points)
    return Trade(mode=mode, day=d, direction=c.direction, side=side, option_type=opt, strike=strike,
                 expiry=expiry, dte=(expiry - d).days, symbol=symbol, cross_number=c.number,
                 signal_time=c.bar_close.to_pydatetime(), entry_time=t.to_pydatetime(),
                 entry_price=price, entry_fill=fill, nifty_entry=c.spot)


def _close(tr: Trade, at: pd.Timestamp, reason: str, nifty: float | None, cfg: Config, minutes,
           bar_index, as_of=None) -> bool:
    """Fill the exit. False (trade left open) only live, while the exit candle may still arrive."""
    m = minutes(tr.symbol, tr.day)
    hit = m.open_at(at, cfg.fill_grace_minutes)
    if hit is None and _pending(as_of, at, cfg):
        return False
    if hit is None:
        hit = m.last_before(at)
        tr.stale_exit = True
    if hit is None:  # nothing at all after entry: exit at the entry price, flagged
        hit = (pd.Timestamp(tr.entry_time), tr.entry_price)
        tr.stale_exit = True
    t, price = hit
    exit_side_buy = tr.side == "SELL"
    fill = (C.buy_fill if exit_side_buy else C.sell_fill)(price, cfg.slippage_pct, cfg.slippage_points)
    tr.exit_time, tr.exit_price, tr.exit_fill = t.to_pydatetime(), price, fill
    tr.exit_reason, tr.nifty_exit = reason, nifty
    sign = 1 if tr.side == "BUY" else -1
    q = cfg.qty
    tr.gross_pnl = round(sign * (price - tr.entry_price) * q, 2)
    filled = sign * (fill - tr.entry_fill) * q
    tr.slippage = round(tr.gross_pnl - filled, 2)
    buy_px, sell_px = (tr.entry_fill, fill) if tr.side == "BUY" else (fill, tr.entry_fill)
    tr.charges = C.charges(buy_px * q, sell_px * q, tr.day)
    tr.net_pnl = round(filled - tr.charges["total"], 2)
    entry_bar = bar_index.get(pd.Timestamp(tr.signal_time))
    exit_bar = bar_index.get(at) if reason != "SQUARE_OFF" else None
    if entry_bar is not None and exit_bar is not None:
        tr.bars_held = exit_bar - entry_bar
        tr.whipsaw = reason == "OPPOSITE_CROSS" and tr.bars_held <= cfg.whipsaw_bars
    elif entry_bar is not None:
        tr.bars_held = int((at - pd.Timestamp(tr.signal_time)).total_seconds() // (60 * cfg.timeframe))
    worst = m.extreme(pd.Timestamp(tr.entry_time), t, worst_high=tr.side == "SELL")
    if worst is not None:
        tr.mae = round(min(0.0, sign * (worst - tr.entry_price) * q), 2)
    return True
