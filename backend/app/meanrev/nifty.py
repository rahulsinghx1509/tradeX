"""MR-4N: the NIFTY-only follow-up to MR-4 (backtest only, no orders).

Asked by RK on 2026-11-27 after MR-4 failed on NIFTY (PF 0.89: gross +₹8.4k, costs ₹16k):
"I need you test for nifty only". The coordinator's first pick of changes, fixed BEFORE any
result was seen:

N1. NIFTY only, Setup A (gap fill) only: Setup B fired 5 times in 3 years. Every MR-4 rule
    for Setup A stays (R3-R5 day filter, R7-R9 bias and half size, R11 09:20 entry after a
    confirming first bar, R12 target previous close / stop the 09:15-09:29 extreme, 15:15 exit,
    R24 one position, R25 ₹3k day / ₹6k week loss).
N2. Bigger gaps only, four variants (|gap| = 09:15 open / previous close - 1):
      G0.3   0.3% - 0.8%   (MR-4 as posted, the baseline)
      G0.5   0.5% - 2.0%
      G0.75  0.75% - 2.0%
      ATR0.5 at least 0.5 x ATR(14) of the daily candles up to yesterday (as % of the previous
             close), at most 2.0%
    Settled: the big-gap variants lift MR-4's 0.8% cap (">= 0.75%" with a 0.8% cap would leave
    almost nothing) to 2.0%; bigger gaps are news shocks, and the VIX > 20 filter still applies.
N3. Two instruments for every variant:
      OPT  the MR-4 option: BUY the ITM strike nearest 0.7 delta (R23), option costs (R2).
      FUT  NIFTY near-month futures, 1 lot (65; half size = 0.5 lot as in R9). Fills at the
           futures 1-minute open of the entry / exit minute, slippage 1 point a side
           (NOT_VERIFIED), futures cost stack (FUT_RATES, NOT_VERIFIED). Note: 1 NIFTY futures
           lot needs roughly ₹1.5-2L margin, more than the ₹1L account (NOT_VERIFIED).
N4. Out of sample: all 3 years were already seen, so each year is tested with the variant picked
    on the OTHER two years ("tune on 2, test on the third"): the highest net PF with at least
    WF_MIN_TRAIN_TRADES trades (ties: the earlier variant in the list above). The three test
    years joined = the out-of-sample result, judged per instrument against the MR-4 bar (R26):
    PF >= 1.10, max DD < 20% of ₹1L, >= 60 trades, every year net > 0. Testing year 1 with
    variants picked on years 2-3 uses later data, which is fine for a stable rule but is stated.
    The full-period rows are information only (they were picked with hindsight).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from datetime import date

import pandas as pd

from app.ema921 import engine as EE
from app.meanrev import FUT_SYMBOLS, PARAMS, WF_MIN_TRAIN_TRADES
from app.meanrev import engine as E
from app.meanrev import report as R
from app.services.indicators import atr as atr_points

VERSION = "MR-4N"
UNDERLYING = "NIFTY"
BIG_GAP_MAX = 2.0
ATR_DAYS = 14
FUT_SLIPPAGE_POINTS = 1.0  # NOT_VERIFIED: NIFTY near-month spread is usually 0.5-1.5 points


@dataclass(frozen=True)
class Variant:
    key: str
    gap_min_pct: float
    gap_max_pct: float
    atr_mult: float | None = None  # gap must also be >= atr_mult x ATR(14) %


VARIANTS = (
    Variant("G0.3", 0.3, 0.8),
    Variant("G0.5", 0.5, BIG_GAP_MAX),
    Variant("G0.75", 0.75, BIG_GAP_MAX),
    Variant("ATR0.5", 0.0, BIG_GAP_MAX, 0.5),
)
INSTRUMENTS = ("OPT", "FUT")


# ----------------------------------------------------------------------------- futures costs
@dataclass(frozen=True)
class FutRates:
    effective_from: date
    brokerage_per_order: float
    stt_sell: float  # share of the SELL-side turnover
    exchange_txn: float  # both sides
    sebi_per_crore: float
    stamp_buy: float
    gst: float


# NOT_VERIFIED (exchange / SEBI circulars, Groww's brokerage page). Last row on or before the day.
FUT_RATES = (
    FutRates(date(2023, 4, 1), 20.0, 0.000125, 0.00002, 10.0, 0.00002, 0.18),
    FutRates(date(2024, 10, 1), 20.0, 0.0002, 0.0000173, 10.0, 0.00002, 0.18),
)


def fut_charges(buy_value: float, sell_value: float, day: date, orders: int = 2) -> float:
    r = [x for x in FUT_RATES if x.effective_from <= day]
    r = r[-1] if r else FUT_RATES[0]
    turnover = buy_value + sell_value
    brokerage = r.brokerage_per_order * orders
    exchange = turnover * r.exchange_txn
    sebi = turnover * r.sebi_per_crore / 1e7
    total = brokerage + sell_value * r.stt_sell + exchange + sebi + buy_value * r.stamp_buy \
        + (brokerage + exchange + sebi) * r.gst
    return round(total, 2)


def fut_pricer(fut_minutes: dict[date, EE._Minutes], lot: int):
    """price(candidate) for the futures rows: a Trade, or the reason it can't be taken."""
    def price(c: E.Candidate) -> E.Trade | str:
        m = fut_minutes.get(c.day)
        if m is None:
            return "NO_FUT_DATA"
        hit = m.open_at(c.entry_time, E.FILL_GRACE)
        if hit is None:
            return "NO_FUT_DATA"
        et, ep = hit
        qty = lot * c.size
        long = c.direction > 0
        efill = ep + FUT_SLIPPAGE_POINTS if long else ep - FUT_SLIPPAGE_POINTS
        fills = []
        for e in c.exits:
            at = E._t(c.day, e.minute)
            got = m.open_at(at, E.FILL_GRACE)
            stale = got is None
            if got is None:
                got = m.last_before(at) or (et, ep)
            xt, xp = got
            xfill = xp - FUT_SLIPPAGE_POINTS if long else xp + FUT_SLIPPAGE_POINTS
            fills.append(E.Fill(e.fraction, xt.to_pydatetime(), e.reason, xp, xfill, stale))
        sign = 1 if long else -1
        gross = sum((f.price - ep) * sign * qty * f.fraction for f in fills)
        filled = sum((f.fill - efill) * sign * qty * f.fraction for f in fills)
        exit_value = sum(f.fill * qty * f.fraction for f in fills)
        buy_v, sell_v = (efill * qty, exit_value) if long else (exit_value, efill * qty)
        ch = fut_charges(buy_v, sell_v, c.day, orders=1 + len(fills))
        tr = E.Trade(c.underlying, c.setup, c.day, c.direction, c.pattern, c.size, qty, "FUT", 0.0,
                     c.day, FUT_SYMBOLS[c.underlying], 1.0, 0.0, "-", et.to_pydatetime(),
                     c.entry_spot, round(c.stop, 2), ep, efill, fills)
        tr.gross_pnl = round(gross, 2)
        tr.slippage = round(gross - filled, 2)
        tr.charges = ch
        tr.net_pnl = round(filled - ch, 2)
        return tr
    return price


# ----------------------------------------------------------------------------- inputs
def atr_pct_before(daily: pd.DataFrame) -> dict[date, float]:
    """{day: ATR(14) as % of the close, from the daily candles up to the day BEFORE}."""
    if daily is None or daily.empty or len(daily) <= ATR_DAYS:
        return {}
    d = daily.sort_index()
    a = atr_points(d["high"], d["low"], d["close"], ATR_DAYS) / d["close"] * 100
    out, idx = {}, list(d.index)
    for i in range(1, len(idx)):
        v = a.iloc[i - 1]
        if pd.notna(v):
            out[pd.Timestamp(idx[i]).date()] = float(v)
    return out


def daily_hlc(stored: pd.DataFrame | None, spot: pd.DataFrame) -> pd.DataFrame:
    """Stored daily candles, filled with days built from the 1-minute spot."""
    rows = {}
    for d, g in E._day_minutes(spot).items():
        if not g.empty:
            rows[pd.Timestamp(d)] = (float(g["high"].max()), float(g["low"].min()),
                                     float(g["close"].dropna().iloc[-1]) if g["close"].notna().any()
                                     else float("nan"))
    s = pd.DataFrame.from_dict(rows, orient="index", columns=["high", "low", "close"])
    if stored is not None and not stored.empty:
        st = stored[["high", "low", "close"]].astype(float)
        st.index = pd.DatetimeIndex([pd.Timestamp(pd.Timestamp(x).date()) for x in st.index])
        s = pd.concat([s[~s.index.isin(st.index)], st])
    return s.sort_index()


def fut_minutes_by_day(fut: pd.DataFrame | None) -> dict[date, EE._Minutes]:
    """Futures 1-minute candles WITH their open (the MR-4 VWAP only needed volume)."""
    if fut is None or fut.empty or "open" not in fut:
        return {}
    d = fut.copy()
    d["day"] = pd.to_datetime(d["time"], utc=True).dt.tz_convert(E.IST).dt.date
    return {k: EE._Minutes(g.drop(columns="day")) for k, g in d.groupby("day")}


# ----------------------------------------------------------------------------- rows
@dataclass
class NRow:
    variant: str
    instrument: str
    trades: list[E.Trade]
    skips: Counter


def variant_candidates(prepared, inp: R.Inputs, v: Variant, atr_pct: dict[date, float],
                       why: Counter) -> list[E.Candidate]:
    p = replace(PARAMS[UNDERLYING], setups=("A",), gap_min_pct=v.gap_min_pct,
                gap_max_pct=v.gap_max_pct)
    out = []
    for c in R._candidates(prepared, inp, p, why):
        if v.atr_mult is not None:
            info = prepared[UNDERLYING][c.day]
            gap = abs(info.open / info.prev_close - 1) * 100
            a = atr_pct.get(c.day)
            if a is None:
                why["NO_ATR"] += 1
                continue
            if gap < v.atr_mult * a:
                why["GAP_BELOW_ATR"] += 1
                continue
        out.append(c)
    return out


def run_rows(inp: R.Inputs, atr_pct: dict[date, float],
             fut_minutes: dict[date, EE._Minutes] | None, prepared=None) -> list[NRow]:
    ix = inp.indices[UNDERLYING]
    if prepared is None:
        prepared = {UNDERLYING: E.prepare(UNDERLYING, ix.spot, ix.fut, ix.closes)}
    p = PARAMS[UNDERLYING]
    inst = E.instrument_for(UNDERLYING, ix.options, p)
    rows = []
    for v in VARIANTS:
        why: Counter = Counter()
        cands = variant_candidates(prepared, inp, v, atr_pct, why)
        res = E.run(cands, {UNDERLYING: inst}, inp.market, inp.cfg)
        rows.append(NRow(v.key, "OPT", res.trades, res.skips))
        if fut_minutes:
            fres = E.run(cands, {}, inp.market, inp.cfg,
                         pricer=fut_pricer(fut_minutes, inst.lot))
            rows.append(NRow(v.key, "FUT", fres.trades, fres.skips))
    return rows


# ----------------------------------------------------------------------------- walk-forward
@dataclass
class Pick:
    test: tuple[date, date]
    variant: str
    train_trades: int
    train_pf: float | None
    test_trades: list[E.Trade]


def leave_one_year_out(rows: list[NRow], instrument: str, start: date) -> list[Pick]:
    mine = [r for r in rows if r.instrument == instrument]
    picks = []
    for a, b in R.year_bounds(start):
        best = None
        for r in mine:
            train = [t for t in r.trades if not a <= t.day < b]
            s = R.score(train, start)
            if s.trades < WF_MIN_TRAIN_TRADES:
                continue
            pf = s.pf if s.pf is not None else (float("inf") if s.net > 0 else 0.0)
            if best is None or pf > best[0] + 1e-12:
                best = (pf, r, s)
        if best is None:  # nothing has enough trades: the posted variant
            r = mine[0]
            s = R.score([t for t in r.trades if not a <= t.day < b], start)
            best = (s.pf, r, s)
        _, r, s = best
        picks.append(Pick((a, b), r.variant, s.trades, s.pf,
                          [t for t in r.trades if a <= t.day < b]))
    return picks


def oos_verdict(picks: list[Pick], start: date) -> tuple[R.Score, bool, list[str]]:
    trades = [t for p in picks for t in p.test_trades]
    s = R.score(trades, start)
    checks = R.bar_checks(s)
    failed = [k for k, ok in checks.items() if not ok]
    return s, not failed, failed


# ----------------------------------------------------------------------------- report
def markdown(inp: R.Inputs, rows: list[NRow], atr_days: int, fut_days: int) -> str:
    st = inp.start
    yb = R.year_bounds(st)
    out = [f"# {VERSION}: NIFTY Setup A (gap fill), bigger gaps, options vs futures "
           f"({inp.start} to {inp.end})", "",
           "Rules: backend/app/meanrev/nifty.py (N1-N4, fixed 2026-11-27 before this run). "
           "Backtest only, no orders.", "",
           "Years: " + ", ".join(f"Y{i + 1} {a} to {b}" for i, (a, b) in enumerate(yb)) + ".",
           f"Data: ATR(14) known on {atr_days} days; futures 1-minute prices on {fut_days} days.",
           "", "## Verdict (out of sample: each year tested with the variant picked on the other two)",
           ""]
    for ins in INSTRUMENTS:
        if not any(r.instrument == ins for r in rows):
            out.append(f"- **{ins}: NOT JUDGED** (no futures 1-minute prices stored).")
            continue
        picks = leave_one_year_out(rows, ins, st)
        s, ok, failed = oos_verdict(picks, st)
        out.append(f"- **{ins}: {'PASS' if ok else 'FAIL'}**: {s.trades} trades, net {R.rs(s.net)}, "
                   f"PF {R.nv(s.pf)}, max DD {s.max_dd_pct:g}%, years "
                   + ", ".join(R.rs(y) for y in s.years)
                   + ("" if ok else f"; fails: {', '.join(failed)}") + ".")
        for i, p in enumerate(picks):
            ts = R.score(p.test_trades, st)
            out.append(f"  - Y{i + 1}: picked {p.variant} (other years {p.train_trades} trades, "
                       f"PF {R.nv(p.train_pf)}) -> {ts.trades} trades, net {R.rs(ts.net)}, "
                       f"PF {R.nv(ts.pf)}")
    out += ["", "## Every variant, full 3 years (information: picked with hindsight)", "",
            "| Variant | Instr | Trades | Win % | Gross | Costs + slippage | Net | PF | Max DD | "
            "Y1 | Y2 | Y3 | Bar |", "|" + "---|" * 13]
    for r in rows:
        s = R.score(r.trades, st)
        ok = all(R.bar_checks(s).values())
        ys = " | ".join(f"{R.rs(y)} ({n})" for y, n in zip(s.years, s.years_n, strict=True))
        out.append(f"| {r.variant} | {r.instrument} | {s.trades} | {R.nv(s.win_rate)} | "
                   f"{R.rs(s.gross)} | {R.rs(s.friction)} | {R.rs(s.net)} | {R.nv(s.pf)} | "
                   f"{s.max_dd_pct:g}% | {ys} | {'meets' if ok else 'below'} |")
    skips = Counter()
    for r in rows:
        skips.update({f"{r.instrument} {k}": v for k, v in r.skips.items()})
    if skips:
        out += ["", "Skipped entries: " + ", ".join(f"{k} {v}" for k, v in sorted(skips.items())) + "."]
    out += ["", "## Caveats", "",
            "- Futures slippage (1 point a side) and the futures cost table are NOT_VERIFIED. One "
            "NIFTY futures lot needs about ₹1.5-2L margin (NOT_VERIFIED), more than the ₹1L account.",
            "- Half size on no-bias days is simulated as half a lot (real trading needs 2 lots for "
            "full size).",
            "- 4 variants x 2 instruments = 8 rows; the out-of-sample verdict is the honest one. "
            "Testing Y1 uses variants picked on Y2-Y3 (later data).", ""]
    return "\n".join(out)


def summary(inp: R.Inputs, rows: list[NRow]) -> str:
    """One screen for the terminal: the verdict lines and a compact variant table."""
    st = inp.start
    lines = [f"{VERSION} NIFTY gap fill, {inp.start} to {inp.end}. Out-of-sample verdict:"]
    for ins in INSTRUMENTS:
        if not any(r.instrument == ins for r in rows):
            lines.append(f"  {ins}: NOT JUDGED (no futures prices)")
            continue
        picks = leave_one_year_out(rows, ins, st)
        s, ok, failed = oos_verdict(picks, st)
        lines.append(f"  {ins}: {'PASS' if ok else 'FAIL'}  {s.trades} trades, net {R.rs(s.net)}, "
                     f"PF {R.nv(s.pf)}, DD {s.max_dd_pct:g}%, picks "
                     + "/".join(p.variant for p in picks)
                     + ("" if ok else f"  (fails: {', '.join(failed)})"))
    lines += ["", f"{'Variant':<8}{'Inst':<5}{'Trades':>7}{'Win%':>6}{'Gross':>10}{'Costs':>9}"
              f"{'Net':>10}{'PF':>6}{'DD%':>6}{'Y1':>9}{'Y2':>9}{'Y3':>9}"]
    for r in rows:
        s = R.score(r.trades, st)
        lines.append(f"{r.variant:<8}{r.instrument:<5}{s.trades:>7}{R.nv(s.win_rate):>6}"
                     f"{s.gross:>10,.0f}{s.friction:>9,.0f}{s.net:>10,.0f}{R.nv(s.pf):>6}"
                     f"{s.max_dd_pct:>6.1f}" + "".join(f"{y:>9,.0f}" for y in s.years))
    return "\n".join(lines)
