"""One minute of the multi-strategy PAPER engine, plus its Telegram messages.

Every minute during market hours (5 s past the minute, and again at :20 and :35 on 5-minute
closes, see seconds_to_next_look):
1. fetch today's closed NIFTY 1-minute candles and store them (shared with the ema-paper engine);
   if only the just-closed candle is missing, look again a few seconds later (_fetch_spot);
2. NO TRADE if the newest candle is more than STALE_MINUTES old;
3. run each strategy's own engine on today with as_of = now (same rules as its backtest); the
   option fill is the fill minute's 1-minute open, read as soon as that candle has started;
4. a new trade gets the day's next trade number (YYYYMMDD-NN, e.g. 20261105-01) and an ENTRY
   message with everything needed to act on it (plus the signal bar's NIFTY futures volume vs
   its usual level, VF-1.0, info only); a newly filled exit sends an EXIT message quoting the
   same trade number;
5. after 15:05, one day report listing every trade by number.
"""

from __future__ import annotations

import logging
import time as _time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.market_time import IST, is_trading_day
from app.db.models import PaperTrade, SignalTrade
from app.ema921 import bars as B
from app.ema921 import engine as E
from app.ema921 import loader as L
from app.ema921 import store as S
from app.ema921 import trend as T
from app.ema921.paper import LiveOptions, closed_only
from app.orb import engine as O
from app.volume import futures as VF
from app.volume import rvol as RV

log = logging.getLogger(__name__)

STALE_MINUTES = 3
# Groww can publish the just-closed NIFTY candle a few seconds after the minute. Rather than
# waiting a whole minute for it (RK 2026-11-06: alerts were late), look again a few times.
SPOT_RETRIES = 4
SPOT_RETRY_SECONDS = 4
# Seconds past the minute at which the service looks. Every signal and exit is decided on a
# 5-minute close, so on those minutes it looks three times: an option candle Groww hasn't
# published at :05 is picked up at :20 or :35 instead of a minute later.
LOOKS_EVERY_MINUTE = (5,)
LOOKS_ON_5MIN_CLOSE = (5, 20, 35)
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
    futures: Any = None  # VF.LiveFutures (volume for the alerts)


# ----------------------------------------------------------------------------- messages
def _hm(t) -> str:
    ts = pd.Timestamp(t)
    if ts.tzinfo is None:  # read back from SQLite (stored as UTC, zone dropped)
        ts = ts.tz_localize("UTC")
    return ts.tz_convert(IST).strftime("%H:%M")


def _rs(x) -> str:
    return "–" if x is None else (f"-₹{abs(float(x)):,.0f}" if float(x) < 0 else f"₹{float(x):,.0f}")


LATE_MINUTES = 5  # an entry first seen this long after its fill was missed live (e.g. a restart)


def _late(entry_time, seen_at) -> bool:
    def utc(t):
        ts = pd.Timestamp(t)
        return ts.tz_localize("UTC") if ts.tzinfo is None else ts
    return utc(seen_at) - utc(entry_time) > timedelta(minutes=LATE_MINUTES)


def _contract(r: SignalTrade) -> str:
    return f"NIFTY {float(r.strike):g} {r.option_type} · expiry {r.expiry_date:%d %b %Y}"


def entry_message(r: SignalTrade, volume: str | None = None) -> str:
    s = STRATEGIES.get(r.strategy)
    arrow = "▲ LONG" if r.direction > 0 else "▼ SHORT"
    value = float(r.entry_price) * r.lot_size
    money = (f"Premium ≈ {_rs(value)} for 1 lot" if r.side == "BUY"
             else f"Premium received ≈ {_rs(value)}; selling needs margin (about ₹1L+ a lot)")
    late = []
    if r.created_at is not None and _late(r.entry_time, r.created_at):
        late = [f"⚠ LATE: this signal fired at {_hm(r.entry_time)} but is only being sent at "
                f"{_hm(r.created_at)} (the service was not running then). The price now is "
                "different: do not chase it."]
    return "\n".join(late + [
        f"{PAPER} · Trade #{label(r)} · ENTRY · {r.strategy} ({s.name if s else r.strategy})",
        f"{_hm(r.entry_time)} IST · {arrow} NIFTY at {float(r.nifty_entry or 0):,.2f}",
        f"Action: {r.side} {_contract(r)}",
        f"Quantity: 1 lot = {r.lot_size}",
        f"Price: ₹{float(r.entry_price):g} (1-min open); with slippage ≈ ₹{float(r.entry_fill):g}",
        money,
        *([volume] if volume else []),
        r.exit_plan,
        f"I'll send 'Trade #{label(r)} EXIT' when it closes.",
        "Paper trade only. No order was placed."])


def exit_message(r: SignalTrade) -> str:
    action = "SELL" if r.side == "BUY" else "BUY back"
    why = E.EXIT_LABELS.get(r.exit_reason, r.exit_reason)
    stale = "\n⚠ Exit price is the last known price (no fresh candle)." if r.stale_exit else ""
    nifty = (f"NIFTY {float(r.nifty_entry):,.2f} → {float(r.nifty_exit):,.2f}"
             if r.nifty_entry is not None and r.nifty_exit is not None else "")
    costs = float(r.slippage or 0) + float(r.charges or 0)
    return "\n".join([
        f"{PAPER} · Trade #{label(r)} · EXIT · {r.strategy}",
        f"{_hm(r.exit_time)} IST · reason: {why}",
        f"Action: {action} {_contract(r)} · 1 lot = {r.lot_size}",
        f"Price: ₹{float(r.entry_price):g} → ₹{float(r.exit_price):g} "
        f"(with slippage ₹{float(r.entry_fill):g} → ₹{float(r.exit_fill):g})",
        nifty,
        f"P&L: gross {_rs(r.gross_pnl)} · slippage + charges {_rs(costs)} · net {_rs(r.net_pnl)}"
        + stale,
        "Paper trade only. No order was placed."])


def summary_message(day: date, rows: list[SignalTrade], ema: list | None = None) -> str:
    """ONE report for every trade alerted today: the numbered trades, then the 9/21 EMA paper
    engine's 3-mode legs. The headline total adds up EVERY alerted trade (RK 2026-11-03)."""
    lines = [f"{PAPER} · Day report {day:%d %b %Y} · all strategies"]
    ema_lines = _ema_lines(ema or [])
    if not rows:
        lines.append("No numbered trades today.")
        lines += ema_lines
        lines += _total_lines(rows, ema or [])
        lines.append("Paper trades only. No order was placed.")
        return "\n".join(lines)
    for r in sorted(rows, key=_order):
        out = (f"{_hm(r.exit_time)} {E.EXIT_LABELS.get(r.exit_reason, r.exit_reason)}"
               if r.exit_time is not None else "still OPEN")
        px = (f"₹{float(r.entry_price):g} → ₹{float(r.exit_price):g}" if r.exit_price is not None
              else f"₹{float(r.entry_price):g} → open")
        lines.append(f"#{label(r)} {r.strategy} · {r.side} {float(r.strike):g} {r.option_type} · "
                     f"{_hm(r.entry_time)} → {out} · {px} · net {_rs(r.net_pnl)}")
    lines.append("")
    for code in STRATEGIES:
        rs = [r for r in rows if r.strategy == code]
        if rs:
            nets = [float(r.net_pnl) for r in rs if r.net_pnl is not None]
            wins = sum(1 for x in nets if x > 0)
            lines.append(f"{code}: {len(rs)} trades, {wins} winners, net {_rs(sum(nets))}")
    lines += ema_lines
    lines += _total_lines(rows, ema or [])
    lines.append("Paper trades only. No order was placed.")
    return "\n".join(lines)


def _ema_lines(ema: list) -> list[str]:
    if not ema:
        return []
    out = ["", "9/21 EMA paper engine (same 5m cross, 3 ways):"]
    for r in sorted(ema, key=lambda x: (pd.Timestamp(x.entry_time), x.mode)):
        when = (f"{_hm(r.entry_time)} → {_hm(r.exit_time)}" if r.exit_time is not None
                else f"{_hm(r.entry_time)} → still OPEN")
        out.append(f"{E.MODE_LABELS.get(r.mode, r.mode)} · {r.side} {float(r.strike):g} "
                   f"{r.option_type} · {when} · net {_rs(r.net_pnl)}")
    nets = [float(r.net_pnl) for r in ema if r.net_pnl is not None]
    out.append(f"9/21 EMA paper engine net: {_rs(sum(nets))}")
    return out


def _total_lines(rows: list, ema: list) -> list[str]:
    nets = [float(r.net_pnl) for r in list(rows) + list(ema) if r.net_pnl is not None]
    still_open = sum(1 for r in list(rows) + list(ema) if r.net_pnl is None)
    extra = f" ({still_open} still open, not counted)" if still_open else ""
    return ["", f"TOTAL net today, every alerted trade ({len(nets)} closed): {_rs(sum(nets))}{extra}"]


# ----------------------------------------------------------------------------- the step
def _utc(t: datetime | None) -> datetime | None:
    return None if t is None else pd.Timestamp(t).tz_convert("UTC").to_pydatetime()


def format_trade_no(day: date, n: int) -> str:
    """Trade number shown everywhere: the IST trading day, then that day's count, 20261105-01."""
    return f"{day:%Y%m%d}-{n:02d}"


def next_trade_no(session: Session, day: date) -> str:
    """The next number for `day`, counted across ALL strategies (NN restarts at 01 each day).
    One `signals` process writes this table, so max + 1 is safe."""
    nos = session.scalars(select(SignalTrade.trade_no).where(SignalTrade.trade_date == day))
    used = [int(x.rsplit("-", 1)[1]) for x in nos if x and "-" in x]
    return format_trade_no(day, max(used, default=0) + 1)


def label(r: SignalTrade) -> str:
    """The trade number for messages; the database id only if a row somehow has none."""
    return r.trade_no or str(r.id)


def _order(r: SignalTrade):
    return (r.trade_no or "", r.id or 0)


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
        row.trade_no = next_trade_no(session, t.day)
        session.add(row)
        session.flush()
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
         state: LiveState, now: datetime, sleep: Callable[[float], None] = _time.sleep) -> str:
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
            ema = list(session.scalars(select(PaperTrade).where(PaperTrade.trade_date == today)))
            notifier.send(summary_message(today, rows, ema))
            state.summary_sent = True
            if hasattr(notifier, "send_photo"):  # the same ONE report as a picture
                try:
                    from app.live.report_card import render_png
                    notifier.send_photo(render_png(today, rows, ema),
                                        f"PAPER day report {today:%d %b %Y}, all strategies. "
                                        "No order was placed.")
                except Exception:  # a picture problem must never stop the signal engine
                    log.exception("day report picture not sent")
            return "day report sent"
        return "closed for the day"

    started = _time.monotonic()
    as_of = pd.Timestamp(ist.replace(second=0, microsecond=0))
    if not state.warmed_up:  # the EMAs need the last few days of NIFTY
        L.load_spot(session, client, today, today - timedelta(days=1), L.LoadReport(), log=log.info)
        state.warmed_up = True
    spot_today = _fetch_spot(client, today, as_of, sleep)
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
    state.options.new_round()  # a second look in the same minute fetches fresh option candles
    _futures_minute(session, client, state, today, as_of)
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
    lookup = _volume_lookup(session, today) if entries else None
    for r in sorted(entries, key=_order):
        notifier.send(entry_message(r, RV.note(lookup, r.signal_time)))
        log.info("Trade #%s ENTRY alert sent %s after its signal (%s IST)", label(r),
                 _delay(r.signal_time, now, started), _hm(r.signal_time))
    for r in sorted(exits, key=lambda x: (x.exit_time, _order(x))):
        notifier.send(exit_message(r))
        log.info("Trade #%s EXIT alert sent %s after its exit minute (%s IST)", label(r),
                 _delay(r.exit_time, now, started), _hm(r.exit_time))
    took = _time.monotonic() - started
    return (f"ok at {as_of:%H:%M}: {len(entries)} entries, {len(exits)} exits, {open_now} open "
            f"({took:.1f}s)")


def _delay(event_time, now: datetime, started: float) -> str:
    """How long after the signal/exit minute the message went out, for the log."""
    ts = pd.Timestamp(event_time)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts
    secs = (pd.Timestamp(now) - ts).total_seconds() + (_time.monotonic() - started)
    return f"{secs:.0f}s"


def _fetch_spot(client: Any, today: date, as_of: pd.Timestamp,
                sleep: Callable[[float], None]) -> list:
    """Today's closed NIFTY 1-minute candles. If only the candle that just closed is missing,
    Groww is a few seconds behind: look again up to SPOT_RETRIES times before going on.
    Real gaps (stale data) are not waited for; the caller says NO TRADE."""
    want = as_of.tz_convert("UTC").to_pydatetime() - timedelta(minutes=1)  # the just-closed minute
    candles: list = []
    for attempt in range(SPOT_RETRIES + 1):
        try:
            candles = closed_only(L.fetch_day(client, S.SPOT_SYMBOL, "CASH", today), as_of)
        except Exception as exc:
            log.warning("spot candles failed: %s", exc)
        last = candles[-1].time if candles else None
        if last is not None and last >= want:
            if attempt:
                log.info("NIFTY %s candle arrived after %d extra look(s)", _hm(want), attempt)
            return candles
        if last is None or last < want - timedelta(minutes=2) or attempt == SPOT_RETRIES:
            break  # stale, or still missing after the retries: decide with what we have
        sleep(SPOT_RETRY_SECONDS)
    if candles and candles[-1].time < want:
        log.warning("NIFTY %s candle not published by Groww yet; going on with %s",
                    _hm(want), _hm(candles[-1].time))
    return candles


def seconds_to_next_look(epoch: float) -> float:
    """Seconds to sleep until the next look (see LOOKS_EVERY_MINUTE / LOOKS_ON_5MIN_CLOSE).
    IST is UTC+5:30, so a 5-minute close in IST is one in UTC too."""
    minute, sec = divmod(epoch, 60)
    looks = LOOKS_ON_5MIN_CLOSE if int(minute) % 5 == 0 else LOOKS_EVERY_MINUTE
    later = [x for x in looks if x > sec + 0.5]
    if later:
        return later[0] - sec
    nxt = int(minute) + 1
    first = (LOOKS_ON_5MIN_CLOSE if nxt % 5 == 0 else LOOKS_EVERY_MINUTE)[0]
    return 60 - sec + first


def _futures_minute(session: Session, client: Any, state: LiveState, today: date,
                    as_of: pd.Timestamp) -> None:
    """Store today's NIFTY futures 1m candles (for volume). Never stops the signal engine."""
    try:
        if state.futures is None:
            state.futures = VF.LiveFutures(session, client, today)
            state.futures.warm_up(log=log.info)
        state.futures.fetch_today(lambda c: closed_only(c, as_of))
    except Exception as exc:
        session.rollback()
        log.warning("futures volume not updated: %s", exc)


def _volume_lookup(session: Session, today: date) -> RV.RvolLookup | None:
    try:
        return RV.RvolLookup(RV.load_volume(session, VF.history_start(today), today))
    except Exception:
        log.exception("volume lookup failed")
        return None


def parse_strategies(text: str) -> tuple[str, ...]:
    if text.strip().lower() in ("", "all"):
        return tuple(STRATEGIES)
    out = tuple(x.strip().upper() for x in text.split(",") if x.strip())
    bad = [x for x in out if x not in STRATEGIES]
    if bad:
        raise ValueError(f"unknown strategy {bad}; choose from {list(STRATEGIES)}")
    return out

