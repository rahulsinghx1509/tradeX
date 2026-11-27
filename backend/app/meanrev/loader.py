"""Fetch what MR-4 needs from Groww. Read-only (no orders), resumable, skips what is stored.

1. NIFTY and BANKNIFTY spot 1m and expiry lists (shared with ema-load / orb-load).
2. India VIX 1m (only its 09:15 candle is used; NOT_VERIFIED that Groww serves 1m VIX: if it
   doesn't, the backtest uses the previous daily close and says how often).
3. Near-month futures 1m for each index (their volume weights the VWAP, rule R13).
4. Option legs: which strike a trade uses depends on the ATM option's price that minute and on
   earlier trades (one position at a time, loss limits), so, like the TP-1.0 loader, the whole
   backtest runs with a recorder, the missing contract-days are fetched, and it runs again until
   nothing new is asked for.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.ema921 import loader as L
from app.meanrev import FUT_SYMBOLS, UNDERLYINGS, VIX_UNDERLYING
from app.meanrev import data as D
from app.meanrev import engine as E
from app.meanrev import nifty as N
from app.meanrev import report as R
from app.pullback.loader import Recorder
from app.volume import futures as F

MAX_PASSES = 6


def load_all(session: Session, client: Any, start: date, end: date,
             log: Callable[[str], None] = print) -> list[L.LoadReport]:
    reps = []
    for u in UNDERLYINGS:
        rep = L.LoadReport()
        L.load_spot(session, client, start, end, rep, log, u)
        L.load_expiries(session, client, start, end, rep, log, u)
        reps.append(rep)
    vix = L.LoadReport()
    L.load_spot(session, client, start, end, vix, log, VIX_UNDERLYING)
    reps.append(vix)
    for u in UNDERLYINGS:
        f = F.load(session, client, start, end, log, u, FUT_SYMBOLS[u])
        if f.errors:
            reps[-1].errors += f.errors
        log(f"{u} futures: {f.days} days loaded, {f.empty} empty, {len(f.no_contract)} with no contract")
    tried: dict[str, set] = {u: set() for u in UNDERLYINGS}
    recorders: dict[str, Recorder] = {}

    def wrap(u, opts):
        recorders[u] = Recorder(opts)
        return recorders[u]

    # First the ~0.7 delta legs only; once none is new, also the nearest neighbours of the
    # ones Groww had no candles for (engine.strike_shifts).
    full_shift = E.MAX_STRIKE_SHIFT
    E.MAX_STRIKE_SHIFT = 0
    try:
        _passes(session, client, start, end, log, reps, tried, recorders, wrap, "")
        E.MAX_STRIKE_SHIFT = full_shift
        _passes(session, client, start, end, log, reps, tried, recorders, wrap, " (nearby strikes)")
    finally:
        E.MAX_STRIKE_SHIFT = full_shift
    return reps


def _passes(session, client, start, end, log, reps, tried, recorders, wrap, label) -> None:
    for p in range(1, MAX_PASSES + 1):
        recorders.clear()
        inp = D.load_inputs(session, start, end, UNDERLYINGS, wrap)
        R.run_all(inp)
        if "NIFTY" in inp.indices:  # MR-4N's bigger-gap rows need their own option legs
            ix = inp.indices["NIFTY"]
            N.run_rows(inp, N.atr_pct_before(N.daily_hlc(D.stored_daily_frame(session, "NIFTY"),
                                                         ix.spot)), None)
        total = 0
        for u, rec in recorders.items():
            new = sorted(rec.asked - tried[u])
            tried[u] |= set(new)
            total += len(new)
            if new:
                rep = L.LoadReport()
                L.fetch_legs(session, client, new, rep, u, f"{u} MR-4 legs", log)
                reps.append(rep)
        log(f"MR-4 pass {p}{label}: {total} new option legs")
        if not total:
            break

