"""One minute of the multi-strategy PAPER engine, plus its Telegram messages.

Every minute during market hours:
1. fetch today's closed NIFTY 1-minute candles and store them (shared with the ema-paper engine);
2. NO TRADE if the newest candle is more than STALE_MINUTES old;
3. run each strategy's own engine on today with as_of = now (same rules as its backtest);
4. a new trade gets the next trade number and an ENTRY message with everything needed to act
   on it; a newly filled exit sends an EXIT message quoting the same trade number;
5. after 15:05, one day report listing every trade by number.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.market_time import IST, is_trading_day
from app.db.models import SignalTrade
from app.ema921 import bars as B
from app.ema921 import engine as E
from app.ema921 import loader as L
from app.ema921 import store as S
from app.ema921 import trend as T
from app.ema921.paper import LiveOptions, closed_only
from app.orb import engine as O

log = logging.getLogger(__name__)

STALE_MINUTES = 3
FIRST_RUN = time(9, 16)
SUMMARY_AT = time(15, 5)
PAPER = "🧪 PAPER"


@dataclass(frozen=True)
class Strategy:
    code: str
    name: str
    run: Callable[..., E.Result]  # (spot, options, day, mode, as_of) -> Result
    exit_plan: Callable[[E.Trade, pd.DataFrame], str]


def _ema_cfg() -> E.Config:
    return E.Config(timeframe=5)  # same defaults as the ema-paper service (2 x ATR stop on)


def _et_cfg() -> E.Config:
    return E.Config(timeframe=T.SIGNAL_TIMEFRAME, start=T.ENTRY_START, cutoff=T.ENTRY_CUTOFF,
                    stop_atr_mult=None, trend_filter=True)


def _run_ema(spot, options, day, mode, as_of):
    return E.run(spot, options, day, day, _ema_cfg(), (mode,), as_of=as_of)


def _run_et(spot, options, day, mode, as_of):
    return E.run(spot, options, day, day, _et_cfg(), (mode,), as_of=as_of)


def _run_orb(spot, options, day, mode, as_of):
    return O.run(spot, options, day, day, None, (mode,), as_of=as_of)


def _ema_plan(t: E.Trade, spot: pd.DataFrame) -> str:
    cfg = _ema_cfg()
    bars = B.add_atr(B.resample(spot, cfg.timeframe), cfg.atr_period)
    row = bars[bars["bar_close"] == pd.Timestamp(t.signal_time)]
    stop = ""
    if not row.empty and pd.notna(row["atr"].iloc[0]):
        r = row.iloc[0]
        level = r["high"] - cfg.stop_atr_mult * r["atr"] if t.direction > 0 \
            else r["low"] + cfg.stop_atr_mult * r["atr"]
        side = "below" if t.direction > 0 else "above"
        stop = (f" a 5-min NIFTY close {side} the trailing stop (starts at {level:,.2f}, "
                f"{cfg.stop_atr_mult:g} x ATR, moves only in your favour);")
    return f"Exit at the first of:{stop} the opposite 9/21 EMA cross on 5-min NIFTY; 15:00."


def _et_plan(t: E.Trade, spot: pd.DataFrame) -> str:
    return "Exit at the first of: the opposite 9/21 EMA cross on 5-min NIFTY; 15:00."


def _orb_plan(t: E.Trade, spot: pd.DataFrame) -> str:
    rng = O.opening_ranges(spot).get(t.day)
    if rng is None:
        return "Exit: 5-min NIFTY close back beyond the far side of the 09:15-09:29 range, or 15:00."
    level, side = (rng.low, "below") if t.direction > 0 else (rng.high, "above")
    return (f"Exit at the first of: a 5-min NIFTY close {side} {level:,.2f} (the other side of the "
            f"09:15-09:29 range {rng.low:,.2f}-{rng.high:,.2f}); 15:00.")


STRATEGIES: dict[str, Strategy] = {
    "EMA-921": Strategy("EMA-921", "9/21 EMA cross (5m)", _run_ema, _ema_plan),
    "ORB-1.0": Strategy("ORB-1.0", "Opening-range breakout", _run_orb, _orb_plan),
    "ET-1.0": Strategy("ET-1.0", "9/21 EMA with 15m trend", _run_et, _et_plan),
}


@dataclass
class LiveState:
    day: date | None = None
    options: LiveOptions | None = None
    warmed_up: bool = False
    stale_alerted: bool = False
    summary_sent: bool = False
    skips_alerted: set = field(default_factory=set)


# ----------------------------------------------------------------------------- messages
def _hm(t) -> str:
    ts = pd.Timestamp(t)
    if ts.tzinfo is None:  # read back from SQLite (stored as UTC, zone dropped)
        ts = ts.tz_localize("UTC")
    return ts.tz_convert(IST).strftime("%H:%M")


def _rs(x) -> str:
    return "–" if x is None else (f"-₹{abs(float(x)):,.0f}" if float(x) < 0 else f"₹{float(x):,.0f}")


def _contract(r: SignalTrade) -> str:
    return f"NIFTY {float(r.strike):g} {r.option_type} · expiry {r.expiry_date:%d %b %Y}"


def entry_message(r: SignalTrade) -> str:
    s = STRATEGIES.get(r.strategy)
    arrow = "▲ LONG" if r.direction > 0 else "▼ SHORT"
    value = float(r.entry_price) * r.lot_size
    money = (f"Premium ≈ {_rs(value)} for 1 lot" if r.side == "BUY"
             else f"Premium received ≈ {_rs(value)}; selling needs margin (about ₹1L+ a lot)")
    return "\n".join([
        f"{PAPER} · Trade #{r.id} · ENTRY · {r.strategy} ({s.name if s else r.strategy})",
        f"{_hm(r.entry_time)} IST · {arrow} NIFTY at {float(r.nifty_entry or 0):,.2f}",
        f"Action: {r.side} {_contract(r)}",
        f"Quantity: 1 lot = {r.lot_size}",
        f"Price: ₹{float(r.entry_price):g} (1-min open); with slippage ≈ ₹{float(r.entry_fill):g}",
        money,
        r.exit_plan,
        f"I'll send 'Trade #{r.id} EXIT' when it closes.",
        "Paper trade only. No order was placed."])


def exit_message(r: SignalTrade) -> str:
    action = "SELL" if r.side == "BUY" else "BUY back"
    why = E.EXIT_LABELS.get(r.exit_reason, r.exit_reason)
    stale = "\n⚠ Exit price is the last known price (no fresh candle)." if r.stale_exit else ""
    nifty = (f"NIFTY {float(r.nifty_entry):,.2f} → {float(r.nifty_exit):,.2f}"
             if r.nifty_entry is not None and r.nifty_exit is not None else "")
    costs = float(r.slippage or 0) + float(r.charges or 0)
    return "\n".join([
        f"{PAPER} · Trade #{r.id} · EXIT · {r.strategy}",
        f"{_hm(r.exit_time)} IST · reason: {why}",
        f"Action: {action} {_contract(r)} · 1 lot = {r.lot_size}",
        f"Price: ₹{float(r.entry_price):g} → ₹{float(r.exit_price):g} "
        f"(with slippage ₹{float(r.entry_fill):g} → ₹{float(r.exit_fill):g})",
        nifty,
        f"P&L: gross {_rs(r.gross_pnl)} · slippage + charges {_rs(costs)} · net {_rs(r.net_pnl)}"
        + stale,
        "Paper trade only. No order was placed."])


def summary_message(day: date, rows: list[SignalTrade]) -> str:
    lines = [f"{PAPER} · Day report {day:%d %b %Y} · all strategies"]
    if not rows:
        lines.append("No trades today.")
        return "\n".join(lines)
    for r in sorted(rows, key=lambda x: x.id):
        out = (f"{_hm(r.exit_time)} {E.EXIT_LABELS.get(r.exit_reason, r.exit_reason)}"
               if r.exit_time is not None else "still OPEN")
        px = (f"₹{float(r.entry_price):g} → ₹{float(r.exit_price):g}" if r.exit_price is not None
              else f"₹{float(r.entry_price):g} → open")
        lines.append(f"#{r.id} {r.strategy} · {r.side} {float(r.strike):g} {r.option_type} · "
                     f"{_hm(r.entry_time)} → {out} · {px} · net {_rs(r.net_pnl)}")
    lines.append("")
    for code in STRATEGIES:
        rs = [r for r in rows if r.strategy == code]
        if rs:
            nets = [float(r.net_pnl) for r in rs if r.net_pnl is not None]
            wins = sum(1 for x in nets if x > 0)
            lines.append(f"{code}: {len(rs)} trades, {wins} winners, net {_rs(sum(nets))}")
    nets = [float(r.net_pnl) for r in rows if r.net_pnl is not None]
    lines.append(f"Total net today: {_rs(sum(nets))}")
    lines.append("Paper trades only. No order was placed.")
    return "\n".join(lines)


# ----------------------------------------------------------------------------- the step
def _utc(t: datetime | None) -> datetime | None:
    return None if t is None else pd.Timestamp(t).tz_convert("UTC").to_pydatetime()


def _upsert(session: Session, code: str, t: E.Trade, plan: Callable[[], str], lot: int,
            now: datetime) -> tuple[str | None, SignalTrade]:
    row = session.scalar(select(SignalTrade).where(
        SignalTrade.strategy == code, SignalTrade.trade_date == t.day,
        SignalTrade.signal_time == _utc(t.signal_time)))
    event = None
    if row is None:
        row = SignalTrade(strategy=code, trade_date=t.day, mode=t.mode, signal_time=_utc(t.signal_time),
                          direction=t.direction, side=t.side, option_type=t.option_type,
                          strike=t.strike, expiry_date=t.expiry, groww_symbol=t.symbol, lot_size=lot,
                          entry_time=_utc(t.entry_time), entry_price=t.entry_price,
                          entry_fill=t.entry_fill, nifty_entry=t.nifty_entry,
                          exit_plan=plan()[:300], status="OPEN", stale_exit=False, created_at=now)
        session.add(row)
        session.flush()  # assigns the trade number
        event = "entry"
    if t.exit_time is not None and row.status == "OPEN":
        row.exit_time, row.exit_price, row.exit_fill = _utc(t.exit_time), t.exit_price, t.exit_fill
        row.exit_reason, row.nifty_exit, row.stale_exit = t.exit_reason, t.nifty_exit, t.stale_exit
        row.gross_pnl, row.slippage = t.gross_pnl, t.slippage
        row.charges, row.net_pnl = t.charges.get("total"), t.net_pnl
        row.status, row.closed_at = "CLOSED", now
        event = "exit" if event is None else "entry+exit"
    return event, row


def step(session: Session, client: Any, notifier: Any, strategies: tuple[str, ...], mode: str,
         state: LiveState, now: datetime) -> str:
    """One minute of the engine. Returns a short status for the log."""
    ist = now.astimezone(IST)
    today = ist.date()
    if not is_trading_day(today):
        return "closed (not a trading day)"
    if state.day != today:
        state.__init__(day=today)
    if ist.time() < FIRST_RUN:
        return "waiting for the open"
    if ist.time() >= SUMMARY_AT:
        if not state.summary_sent:
            rows = list(session.scalars(select(SignalTrade).where(SignalTrade.trade_date == today)))
            notifier.send(summary_message(today, rows))
            state.summary_sent = True
            return "day report sent"
        return "closed for the day"

    as_of = pd.Timestamp(ist.replace(second=0, microsecond=0))
    if not state.warmed_up:  # the EMAs need the last few days of NIFTY
        L.load_spot(session, client, today, today - timedelta(days=1), L.LoadReport(), log=log.info)
        state.warmed_up = True
    try:
        spot_today = closed_only(L.fetch_day(client, S.SPOT_SYMBOL, "CASH", today), as_of)
    except Exception as exc:
        spot_today = []
        log.warning("spot candles failed: %s", exc)
    if spot_today:
        S.save_minutes(session, S.SPOT_SYMBOL, today, spot_today)
        session.commit()
    last = spot_today[-1].time if spot_today else None
    if last is None or last < as_of.tz_convert("UTC").to_pydatetime() - timedelta(minutes=STALE_MINUTES + 1):
        if not state.stale_alerted:
            notifier.send(f"{PAPER} · signals: NIFTY 1-minute data is late at {as_of:%H:%M} (last "
                          f"candle {_hm(last) if last else 'none'}). NO TRADE until it is back.")
            state.stale_alerted = True
        return "NO TRADE: stale spot data"
    if state.stale_alerted:
        notifier.send(f"{PAPER} · signals: NIFTY data is back at {as_of:%H:%M}.")
        state.stale_alerted = False

    if state.options is None:
        state.options = LiveOptions(session, client, today)
    state.options.as_of = as_of
    spot = S.load_spot(session, today, today, L.WARMUP_CALENDAR_DAYS)
    lot = S.UNDERLYINGS[S.UNDERLYING][0]
    entries, exits, open_now = [], [], 0
    for code in strategies:
        strat = STRATEGIES[code]
        try:
            res = strat.run(spot, state.options, today, mode, as_of)
        except Exception:  # one strategy failing must not stop the others
            log.exception("%s crashed this minute", code)
            continue
        for t in res.trades[mode]:
            event, row = _upsert(session, code, t, lambda t=t, plan=strat.exit_plan: plan(t, spot), lot, now)
            if event in ("entry", "entry+exit"):
                entries.append(row)
            if event in ("exit", "entry+exit"):
                exits.append(row)
            open_now += t.exit_time is None
        for s in res.skips:
            key = (code, s.signal_time, s.reason)
            if key not in state.skips_alerted:
                state.skips_alerted.add(key)
                notifier.send(f"{PAPER} · {code}: entry at {_hm(s.signal_time)} skipped "
                              f"({s.reason} {s.detail}). NO TRADE.")
    session.commit()
    for r in sorted(entries, key=lambda x: x.id):
        notifier.send(entry_message(r))
    for r in sorted(exits, key=lambda x: (x.exit_time, x.id)):
        notifier.send(exit_message(r))
    return f"ok at {as_of:%H:%M}: {len(entries)} entries, {len(exits)} exits, {open_now} open"


def parse_strategies(text: str) -> tuple[str, ...]:
    if text.strip().lower() in ("", "all"):
        return tuple(STRATEGIES)
    out = tuple(x.strip().upper() for x in text.split(",") if x.strip())
    bad = [x for x in out if x not in STRATEGIES]
    if bad:
        raise ValueError(f"unknown strategy {bad}; choose from {list(STRATEGIES)}")
    return out

