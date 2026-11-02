"""Live PAPER engine for the 9/21 EMA strategy: the backtest rules, run minute by minute.

Every minute during market hours it:
1. fetches today's closed NIFTY 1-minute candles from Groww and stores them;
2. stops (NO TRADE) if the latest candle is more than STALE_MINUTES old;
3. re-runs the SAME engine as the backtest (engine.run with as_of = now) on today, using the
   options' own 1-minute candles, so paper fills follow exactly the backtest's rules;
4. compares the result with the paper_trades table: a new trade sends a PAPER ENTRY message,
   a newly filled exit sends a PAPER EXIT message (one Telegram message per signal, all modes
   together);
5. after 15:05 sends one end-of-day trade report (every trade, then totals per mode),
   as text and then as a picture (report_card.py).

This is PAPER ONLY. It never places, modifies or cancels an order: the Groww calls it makes
are get_historical_candles, get_expiries and get_contracts (a test checks this).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.market_time import IST, is_trading_day
from app.db.models import PaperTrade
from app.ema921 import engine as E
from app.ema921 import loader as L
from app.ema921 import store as S

log = logging.getLogger(__name__)

STALE_MINUTES = 3
FIRST_RUN = time(9, 16)
SUMMARY_AT = time(15, 5)
MINUTE_CACHE_SECONDS = 45


def closed_only(candles: list, as_of: pd.Timestamp) -> list:
    """Drop the candle still forming: a 1m candle is closed once its start + 1 minute <= as_of."""
    cut = as_of.tz_convert("UTC").to_pydatetime() - timedelta(minutes=1)
    return [c for c in candles if c.time <= cut]


class LiveOptions:
    """OptionData for today: expiries and contracts from the DB (fetched from Groww when
    missing), option candles fetched live and stored."""

    def __init__(self, session: Session, client: Any, today: date):
        self.session, self.client, self.today = session, client, today
        self._fetched_contracts: set[date] = set()
        self._minutes: dict[tuple[str, date], tuple[pd.Timestamp, pd.DataFrame | None]] = {}
        self.as_of: pd.Timestamp | None = None
        rep = L.LoadReport()
        L.load_expiries(self.session, client, today, today, rep, log=log.info)
        self._data = S.DbOptionData(session)

    def expiries(self) -> list[date]:
        return self._data.expiries()

    def contract(self, expiry: date, strike: float, option_type: str) -> str | None:
        sym = self._data.contract(expiry, strike, option_type)
        if sym is None and expiry not in self._fetched_contracts:
            self._fetched_contracts.add(expiry)
            L.load_contracts(self.session, self.client, [expiry], L.LoadReport())
            self._data = S.DbOptionData(self.session)
            sym = self._data.contract(expiry, strike, option_type)
        if sym is None:  # Groww's list has gaps: try the contract directly (see loader.try_unlisted)
            sym = L.try_unlisted(self.session, self.client, expiry, strike, option_type, self.today,
                                 L.LoadReport(), live=True)
            if sym is not None:
                self._data = S.DbOptionData(self.session)
        return sym

    def minutes(self, symbol: str, day: date) -> pd.DataFrame | None:
        if day != self.today:
            return self._data.minutes(symbol, day)
        key = (symbol, day)
        hit = self._minutes.get(key)
        if hit and self.as_of is not None and hit[0] == self.as_of:
            return hit[1]
        try:
            candles = closed_only(L.fetch_day(self.client, symbol, "FNO", day), self.as_of)
        except Exception as exc:  # no price is "no data", never zero
            log.warning("option candles for %s failed: %s", symbol, exc)
            return hit[1] if hit else None
        S.save_minutes(self.session, symbol, day, candles)
        self.session.commit()
        df = S.load_minutes(self.session, symbol, day, day)
        df = None if df.empty else df
        self._minutes[key] = (self.as_of, df)
        return df


@dataclass
class PaperState:
    """What the engine remembers between minutes (the rest is in the paper_trades table)."""

    day: date | None = None
    options: LiveOptions | None = None
    warmed_up: bool = False
    stale_alerted: bool = False
    summary_sent: bool = False
    skips_alerted: set = field(default_factory=set)


# ----------------------------------------------------------------------------- messages
def _hm(t) -> str:
    ts = pd.Timestamp(t)
    if ts.tzinfo is None:  # read back from the database (stored as UTC; SQLite drops the zone)
        ts = ts.tz_localize("UTC")
    return ts.tz_convert(IST).strftime("%H:%M")


def _leg(t: E.Trade) -> str:
    return f"{t.side} NIFTY {float(t.strike):g} {t.option_type} ({t.expiry:%d %b})"


def entry_message(tf: int, items: list[E.Trade]) -> str:
    first = items[0]
    arrow = "▲ LONG" if first.direction > 0 else "▼ SHORT"
    lines = [f"🧪 PAPER · 9/21 EMA {tf}m · ENTRY {arrow} NIFTY at {_hm(first.entry_time)}",
             f"NIFTY {first.nifty_entry:,.2f} · cross #{first.cross_number} of the day"]
    for t in items:
        lines.append(f"• {E.MODE_LABELS[t.mode]}: {_leg(t)} @ {t.entry_price:g} "
                     f"(fill {t.entry_fill:g} after slippage)")
    lines.append("Paper trade only. No order was placed.")
    return "\n".join(lines)


def exit_message(tf: int, items: list[E.Trade]) -> str:
    first = items[0]
    why = E.EXIT_LABELS.get(first.exit_reason, first.exit_reason)
    lines = [f"🧪 PAPER · 9/21 EMA {tf}m · EXIT at {_hm(first.exit_time)} ({why})"]
    for t in items:
        stale = " · STALE price" if t.stale_exit else ""
        lines.append(f"• {E.MODE_LABELS[t.mode]}: {_leg(t)} {t.entry_price:g} → {t.exit_price:g} · "
                     f"net ₹{t.net_pnl:,.0f}{stale}")
    lines.append("Paper trade only. No order was placed.")
    return "\n".join(lines)


def _rs(x) -> str:
    return "–" if x is None else (f"-₹{abs(float(x)):,.0f}" if float(x) < 0 else f"₹{float(x):,.0f}")


def summary_message(day: date, tf: int, rows: list[PaperTrade]) -> str:
    """End-of-day trade report: every paper trade of the day, then the totals per mode."""
    lines = [f"🧪 PAPER · 9/21 EMA {tf}m · day report {day:%d %b %Y}"]
    if not rows:
        lines.append("No paper trades today.")
        return "\n".join(lines)
    rows = sorted(rows, key=lambda r: (r.entry_time, E.MODES.index(r.mode) if r.mode in E.MODES else 9))
    by_signal: dict = defaultdict(list)
    for r in rows:
        by_signal[r.signal_time].append(r)
    for i, group in enumerate(by_signal.values(), 1):
        r0 = group[0]
        arrow = "▲ LONG" if r0.direction > 0 else "▼ SHORT"
        nifty = f" · NIFTY {float(r0.nifty_entry):,.0f}" if r0.nifty_entry is not None else ""
        nifty += f" → {float(r0.nifty_exit):,.0f}" if r0.nifty_exit is not None else ""
        out = (f"{_hm(r0.exit_time)} {E.EXIT_LABELS.get(r0.exit_reason, r0.exit_reason)}"
               if r0.exit_time is not None else "still OPEN")
        lines += ["", f"Trade {i}: {arrow} {_hm(r0.entry_time)} → {out}{nifty}"]
        for r in group:
            leg = f"{r.side} {float(r.strike):g} {r.option_type}"
            px = f"{float(r.entry_fill):g} → {float(r.exit_fill):g}" if r.exit_fill is not None \
                else f"{float(r.entry_fill):g} → open"
            stale = " · STALE price" if r.stale_exit else ""
            lines.append(f"• {E.MODE_LABELS.get(r.mode, r.mode)}: {leg} {px} · net {_rs(r.net_pnl)}{stale}")
    lines += ["", "Totals per mode:"]
    by_mode: dict[str, list[PaperTrade]] = defaultdict(list)
    for r in rows:
        by_mode[r.mode].append(r)
    for mode in E.MODES:
        rs = by_mode.get(mode, [])
        if not rs:
            continue
        nets = [float(r.net_pnl) for r in rs if r.net_pnl is not None]
        gross = sum(float(r.gross_pnl) for r in rs if r.gross_pnl is not None)
        costs = sum(float(r.slippage or 0) + float(r.charges or 0) for r in rs)
        wins = sum(1 for x in nets if x > 0)
        lines.append(f"• {E.MODE_LABELS[mode]}: {len(rs)} trades, {wins} winners, gross {_rs(gross)}, "
                     f"slippage + charges {_rs(costs)}, net {_rs(sum(nets))}")
    all_nets = [float(r.net_pnl) for r in rows if r.net_pnl is not None]
    lines.append(f"Total P&L today (all modes): {_rs(sum(all_nets)) if all_nets else '–'}")
    lines.append("Paper trades only. No order was placed.")
    return "\n".join(lines)


# ----------------------------------------------------------------------------- the step
def _utc(t: datetime | None) -> datetime | None:
    """Store UTC (the database convention; SQLite would otherwise drop the IST offset)."""
    return None if t is None else pd.Timestamp(t).tz_convert("UTC").to_pydatetime()


def _upsert(session: Session, tf: int, t: E.Trade, now: datetime) -> tuple[str | None, PaperTrade]:
    """Save one engine trade. Returns ("entry" | "exit" | None, row)."""
    row = session.scalar(select(PaperTrade).where(
        PaperTrade.trade_date == t.day, PaperTrade.timeframe == tf, PaperTrade.mode == t.mode,
        PaperTrade.signal_time == _utc(t.signal_time)))
    event = None
    if row is None:
        row = PaperTrade(trade_date=t.day, timeframe=tf, mode=t.mode,
                         signal_time=_utc(t.signal_time),
                         direction=t.direction, side=t.side, option_type=t.option_type,
                         strike=t.strike, expiry_date=t.expiry, groww_symbol=t.symbol,
                         cross_number=t.cross_number, entry_time=_utc(t.entry_time),
                         entry_price=t.entry_price, entry_fill=t.entry_fill, nifty_entry=t.nifty_entry,
                         status="OPEN", stale_exit=False, created_at=now)
        session.add(row)
        event = "entry"
    if t.exit_time is not None and row.status == "OPEN":
        row.exit_time, row.exit_price, row.exit_fill = _utc(t.exit_time), t.exit_price, t.exit_fill
        row.exit_reason, row.nifty_exit, row.stale_exit = t.exit_reason, t.nifty_exit, t.stale_exit
        row.gross_pnl, row.slippage = t.gross_pnl, t.slippage
        row.charges, row.net_pnl = t.charges.get("total"), t.net_pnl
        row.status, row.closed_at = "CLOSED", now
        event = "exit" if event is None else "entry+exit"
    return event, row


def step(session: Session, client: Any, notifier: Any, cfg: E.Config, modes: tuple[str, ...],
         state: PaperState, now: datetime) -> str:
    """One minute of the paper engine. Returns a short status for the log."""
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
            rows = list(session.scalars(select(PaperTrade).where(
                PaperTrade.trade_date == today, PaperTrade.timeframe == cfg.timeframe)))
            notifier.send(summary_message(today, cfg.timeframe, rows))
            state.summary_sent = True
            if hasattr(notifier, "send_photo"):  # the same report as a picture; text already went
                try:
                    from app.ema921.report_card import render_png
                    notifier.send_photo(render_png(today, cfg.timeframe, rows),
                                        f"PAPER day report {today:%d %b %Y}. No order was placed.")
                except Exception:  # a picture problem must never stop the paper engine
                    log.exception("day report picture not sent")
            return "day report sent"
        return "closed for the day"

    as_of = pd.Timestamp(ist.replace(second=0, microsecond=0))
    if not state.warmed_up:  # make sure the last few days of spot are stored, for the EMAs
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
            notifier.send(f"🧪 PAPER · 9/21 EMA: NIFTY 1-minute data is late at {as_of:%H:%M} "
                          "(last candle " + (_hm(last) if last else "none") + "). NO TRADE until it is back.")
            state.stale_alerted = True
        return "NO TRADE: stale spot data"
    if state.stale_alerted:
        notifier.send(f"🧪 PAPER · 9/21 EMA: NIFTY data is back at {as_of:%H:%M}.")
        state.stale_alerted = False

    if state.options is None:
        state.options = LiveOptions(session, client, today)
    state.options.as_of = as_of
    spot = S.load_spot(session, today, today, L.WARMUP_CALENDAR_DAYS)
    res = E.run(spot, state.options, today, today, cfg, modes, as_of=as_of)

    entries: dict[pd.Timestamp, list[E.Trade]] = defaultdict(list)
    exits: dict[pd.Timestamp, list[E.Trade]] = defaultdict(list)
    for mode in modes:
        for t in res.trades[mode]:
            event, _ = _upsert(session, cfg.timeframe, t, now)
            if event in ("entry", "entry+exit"):
                entries[pd.Timestamp(t.entry_time)].append(t)
            if event in ("exit", "entry+exit"):
                exits[pd.Timestamp(t.exit_time)].append(t)
    session.commit()
    for _, items in sorted(exits.items(), key=lambda kv: kv[0]):
        notifier.send(exit_message(cfg.timeframe, items))
    for _, items in sorted(entries.items(), key=lambda kv: kv[0]):
        notifier.send(entry_message(cfg.timeframe, items))
    for s in res.skips:
        key = (s.mode, s.signal_time, s.reason)
        if key not in state.skips_alerted:
            state.skips_alerted.add(key)
            notifier.send(f"🧪 PAPER · 9/21 EMA {cfg.timeframe}m: {E.MODE_LABELS[s.mode]} entry at "
                          f"{_hm(s.signal_time)} skipped ({s.reason} {s.detail}). NO TRADE.")
    open_now = sum(1 for m in modes for t in res.trades[m] if t.exit_time is None)
    return f"ok at {as_of:%H:%M}: {len(entries)} new entries, {len(exits)} exits, {open_now} open"


def utc_now() -> datetime:
    return datetime.now(UTC)
