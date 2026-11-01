"""ST-1.0 backtest engine: one hedged short straddle per day (rules in app/straddle/__init__.py).

Prices come from the stored 1-minute option candles. Nothing is ever filled with zero: a day
without a price for every leg is skipped and listed with its reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

import pandas as pd

from app.core.market_time import IST, is_trading_day, trading_days
from app.ema921 import costs as C
from app.ema921.engine import OptionData, _Minutes, atm_strike, pick_expiry

VERSION = "ST-1.0"
VIX_JUMP_PCT = 7.8  # VJ-1.0, fixed by its final test (services/vix_warning.py)
VIX_LOOKBACK = 5


@dataclass(frozen=True)
class Config:
    entry: time = time(9, 20)
    exit: time = time(15, 15)
    wing_points: int = 500
    stop_pct: float = 0.30  # stop on each sold leg: entry price x 1.30
    lot_size: int = 65
    slippage_pct: float = 0.005
    slippage_points: float = 0.5
    strike_step: int = 50
    roll_on_expiry_day: bool = True
    fill_grace_minutes: int = 2
    vix_filter: bool = True
    capital: float = 100_000.0  # for drawdown % only; margin is not modelled


@dataclass
class Leg:
    role: str  # SHORT_CE / SHORT_PE / WING_CE / WING_PE
    side: str  # SELL or BUY
    option_type: str
    strike: float
    symbol: str
    entry_time: datetime
    entry_price: float
    entry_fill: float
    exit_time: datetime | None = None
    exit_price: float | None = None
    exit_fill: float | None = None
    exit_reason: str | None = None  # STOP / TIME
    stale_exit: bool = False
    gross_pnl: float = 0.0
    slippage: float = 0.0
    charges: float = 0.0
    net_pnl: float = 0.0


@dataclass
class DayTrade:
    day: date
    expiry: date
    dte: int
    spot: float
    atm: float
    legs: list[Leg] = field(default_factory=list)

    @property
    def gross_pnl(self) -> float:
        return round(sum(x.gross_pnl for x in self.legs), 2)

    @property
    def slippage(self) -> float:
        return round(sum(x.slippage for x in self.legs), 2)

    @property
    def charges(self) -> float:
        return round(sum(x.charges for x in self.legs), 2)

    @property
    def net_pnl(self) -> float:
        return round(sum(x.net_pnl for x in self.legs), 2)

    @property
    def stops(self) -> int:
        return sum(1 for x in self.legs if x.exit_reason == "STOP")


@dataclass
class Result:
    config: Config
    trades: list[DayTrade]
    skipped: dict[date, str]  # day -> reason
    days_tested: int


# ----------------------------------------------------------------------------- VIX filter (VJ-1.0)
def vix_status(day: date, vix_closes: pd.Series | None, holidays=None) -> str:
    """JUMP, CLEAR or NO_VIX for the morning of `day`, from the closes known by then.

    `vix_closes` is India VIX daily closes indexed by date. The previous trading day's close
    must be there; the 5-day change is that close against the one 5 trading days earlier."""
    if vix_closes is None or vix_closes.empty:
        return "NO_VIX"
    known = vix_closes[[d < day for d in vix_closes.index]].dropna()
    prev_days = trading_days(day - timedelta(days=10), day - timedelta(days=1), holidays)
    if len(known) <= VIX_LOOKBACK or not prev_days or known.index[-1] != prev_days[-1]:
        return "NO_VIX"
    change = (known.iloc[-1] / known.iloc[-1 - VIX_LOOKBACK] - 1) * 100
    return "JUMP" if change > VIX_JUMP_PCT else "CLEAR"


# ----------------------------------------------------------------------------- one day
def legs_for(atm: float, cfg: Config) -> list[tuple[str, str, str, float]]:
    """(role, side, option type, strike) of the four legs."""
    w = cfg.wing_points
    return [("SHORT_CE", "SELL", "CE", atm), ("SHORT_PE", "SELL", "PE", atm),
            ("WING_CE", "BUY", "CE", atm + w), ("WING_PE", "BUY", "PE", atm - w)]


def spot_at_entry(spot_day: pd.DataFrame, day: date, cfg: Config) -> float | None:
    """Close of the one-minute candle just before the entry minute (09:19 for a 09:20 entry)."""
    t = pd.Timestamp(datetime.combine(day, cfg.entry), tz=IST) - timedelta(minutes=1)
    times = pd.to_datetime(spot_day["time"], utc=True).dt.tz_convert(IST)
    row = spot_day[times == t]
    if row.empty or pd.isna(row["close"].iloc[0]):
        return None
    return float(row["close"].iloc[0])


def _stop_exit(m: _Minutes, entry_t: pd.Timestamp, exit_t: pd.Timestamp,
               stop: float) -> tuple[pd.Timestamp, float] | None:
    """First candle in [entry, exit) whose high reaches the stop: (time, fill price before slippage)."""
    df = m.df
    if not m.times:
        return None
    rows = df[(df["t"] >= entry_t) & (df["t"] < exit_t)]
    for r in rows.itertuples(index=False):
        high = r.high if pd.notna(r.high) else r.close
        if pd.isna(high) or high < stop:
            continue
        price = r.open if pd.notna(r.open) and r.open >= stop else stop
        return r.t, float(price)
    return None


def _settle(leg: Leg, t: pd.Timestamp, price: float, reason: str, stale: bool, day: date,
            cfg: Config) -> None:
    exit_buy = leg.side == "SELL"
    fill = (C.buy_fill if exit_buy else C.sell_fill)(price, cfg.slippage_pct, cfg.slippage_points)
    leg.exit_time, leg.exit_price, leg.exit_fill = t.to_pydatetime(), price, fill
    leg.exit_reason, leg.stale_exit = reason, stale
    sign = 1 if leg.side == "BUY" else -1
    q = cfg.lot_size
    leg.gross_pnl = round(sign * (price - leg.entry_price) * q, 2)
    filled = sign * (fill - leg.entry_fill) * q
    leg.slippage = round(leg.gross_pnl - filled, 2)
    buy_px, sell_px = (leg.entry_fill, fill) if leg.side == "BUY" else (fill, leg.entry_fill)
    leg.charges = C.charges(buy_px * q, sell_px * q, day)["total"]
    leg.net_pnl = round(filled - leg.charges, 2)


def run_day(day: date, spot_day: pd.DataFrame, options: OptionData, cfg: Config,
            expiries: list[date]) -> tuple[DayTrade | None, str | None]:
    """(trade, None) or (None, skip reason)."""
    spot = spot_at_entry(spot_day, day, cfg)
    if spot is None:
        return None, "NO_SPOT_AT_ENTRY"
    expiry = pick_expiry(day, expiries, cfg.roll_on_expiry_day)
    if expiry is None:
        return None, "NO_EXPIRY"
    atm = atm_strike(spot, cfg.strike_step)
    entry_t = pd.Timestamp(datetime.combine(day, cfg.entry), tz=IST)
    exit_t = pd.Timestamp(datetime.combine(day, cfg.exit), tz=IST)
    trade = DayTrade(day, expiry, (expiry - day).days, spot, atm)
    minutes: dict[str, _Minutes] = {}
    for role, side, opt, strike in legs_for(atm, cfg):
        symbol = options.contract(expiry, strike, opt)
        if symbol is None:
            return None, f"NO_CONTRACT ({strike:g} {opt})"
        m = _Minutes(options.minutes(symbol, day))
        hit = m.open_at(entry_t, cfg.fill_grace_minutes)
        if hit is None:
            return None, f"NO_DATA ({strike:g} {opt})"
        t, price = hit
        fill = (C.sell_fill if side == "SELL" else C.buy_fill)(price, cfg.slippage_pct,
                                                               cfg.slippage_points)
        trade.legs.append(Leg(role, side, opt, strike, symbol, t.to_pydatetime(), price, fill))
        minutes[role] = m
    for leg in trade.legs:
        m = minutes[leg.role]
        if leg.side == "SELL":
            hit = _stop_exit(m, pd.Timestamp(leg.entry_time), exit_t,
                             leg.entry_price * (1 + cfg.stop_pct))
            if hit is not None:
                _settle(leg, hit[0], hit[1], "STOP", False, day, cfg)
                continue
        out = m.open_at(exit_t, cfg.fill_grace_minutes)
        stale = out is None
        if out is None:
            out = m.last_before(exit_t) or (pd.Timestamp(leg.entry_time), leg.entry_price)
        _settle(leg, out[0], out[1], "TIME", stale, day, cfg)
    return trade, None


# ----------------------------------------------------------------------------- the run
def run(spot: pd.DataFrame, options: OptionData, start: date, end: date, cfg: Config,
        vix_closes: pd.Series | None = None, holidays=None) -> Result:
    times = pd.to_datetime(spot["time"], utc=True).dt.tz_convert(IST) if not spot.empty else None
    by_day = dict(tuple(spot.groupby(times.dt.date))) if times is not None else {}
    expiries = sorted(options.expiries())
    trades: list[DayTrade] = []
    skipped: dict[date, str] = {}
    tested = 0
    d = start
    while d <= end:
        if not is_trading_day(d, holidays):
            d += timedelta(days=1)
            continue
        tested += 1
        if d not in by_day:
            skipped[d] = "NO_SPOT_DATA"
        elif cfg.vix_filter and (v := vix_status(d, vix_closes, holidays)) != "CLEAR":
            skipped[d] = "VIX_JUMP" if v == "JUMP" else "NO_VIX"
        else:
            trade, why = run_day(d, by_day[d], options, cfg, expiries)
            if trade is None:
                skipped[d] = why
            else:
                trades.append(trade)
        d += timedelta(days=1)
    return Result(cfg, trades, skipped, tested)


# ----------------------------------------------------------------------------- summary
@dataclass
class Summary:
    days: int
    win_rate: float | None
    gross_pnl: float
    friction: float
    net_pnl: float
    profit_factor: float | None
    expectancy: float | None
    max_drawdown: float
    max_drawdown_pct: float | None
    worst_day: float | None
    best_day: float | None
    legs_stopped_pct: float | None  # share of sold legs closed by their stop
    both_stopped_days: int


def summarize(trades: list[DayTrade], capital: float) -> Summary:
    nets = [t.net_pnl for t in sorted(trades, key=lambda t: t.day)]
    wins = [x for x in nets if x > 0]
    losses = [-x for x in nets if x <= 0]
    equity = peak = capital
    max_dd = max_dd_pct = 0.0
    for x in nets:
        equity += x
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        if peak > 0:
            max_dd_pct = max(max_dd_pct, (peak - equity) / peak * 100)
    n = len(nets)
    sold = [leg for t in trades for leg in t.legs if leg.side == "SELL"]
    return Summary(
        days=n,
        win_rate=round(len(wins) / n * 100, 1) if n else None,
        gross_pnl=round(sum(t.gross_pnl for t in trades), 2),
        friction=round(sum(t.slippage + t.charges for t in trades), 2),
        net_pnl=round(sum(nets), 2),
        profit_factor=round(sum(wins) / sum(losses), 2) if sum(losses) > 0 else None,
        expectancy=round(sum(nets) / n, 2) if n else None,
        max_drawdown=round(max_dd, 2),
        max_drawdown_pct=round(max_dd_pct, 2) if n else None,
        worst_day=min(nets) if nets else None,
        best_day=max(nets) if nets else None,
        legs_stopped_pct=(round(sum(1 for x in sold if x.exit_reason == "STOP") / len(sold) * 100, 1)
                          if sold else None),
        both_stopped_days=sum(1 for t in trades if t.stops == 2),
    )


def passes_dev_bar(trades: list[DayTrade], capital: float) -> tuple[bool, str]:
    """Rule 8: net > 0, profit factor >= 1.10 and both development years net > 0."""
    s = summarize(trades, capital)
    if not trades:
        return False, "no trades"
    first = min(t.day for t in trades)
    split = first.replace(year=first.year + 1) if not (first.month == 2 and first.day == 29) \
        else first.replace(year=first.year + 1, day=28)
    y1 = sum(t.net_pnl for t in trades if t.day < split)
    y2 = sum(t.net_pnl for t in trades if t.day >= split)
    ok = s.net_pnl > 0 and (s.profit_factor or 0) >= 1.10 and y1 > 0 and y2 > 0
    def rs(x: float) -> str:
        return f"-₹{abs(x):,.0f}" if x < 0 else f"₹{x:,.0f}"

    return ok, (f"net {rs(s.net_pnl)}, profit factor {s.profit_factor}, year 1 {rs(y1)}, "
                f"year 2 {rs(y2)}")
