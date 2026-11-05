"""Build every SP-1.0 and TP-1.0 row for a date range, score it and write the report.

Used by scripts/spread_backtest.py. One row = one strategy variant; one trade = one whole
position (all legs, all costs).
"""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from app.ema921 import engine as EE
from app.pullback import MODE_LABELS as TP_LABELS
from app.pullback import MODES as TP_MODES
from app.pullback import VERSION as TP_VERSION
from app.pullback import engine as TP
from app.spreads import MIN_TRADES, VERSION
from app.spreads import engine as E
from app.volume import rvol as RV

MIN_COVERAGE = 0.80


@dataclass
class Row:
    name: str
    strategy: str  # CS-1.0 / IC-1.0 / DS-1.0 / TP-1.0 / comparison
    positions: list[E.Position]
    judged: bool  # counts for the pass bar
    note: str = ""


def vix_closes(session) -> pd.Series | None:
    from app.core.config import get_settings
    from app.services.metrics import daily_frame
    from app.services.vix_warning import vix_instrument

    inst = vix_instrument(session, get_settings().data_provider)
    if inst is None:
        return None
    df = daily_frame(session, inst.id)
    return pd.Series(df["close"].to_numpy(), index=[t.date() for t in df.index])


def build_rows(spot, fut_minutes, opts, start: date, end: date, base: EE.Config, vix,
               log=print) -> tuple[list[Row], dict]:
    """Every row, plus facts for the report header (coverage, condor skip reasons ...)."""
    E.set_expiry_days(opts.expiries())
    facts: dict = {}
    rows: list[Row] = []

    log("CS-1.0 credit spreads on ET-1.0 signals...")
    for name, ps in E.run_credit(spot, opts, start, end, base).items():
        single = "single leg" in name
        rows.append(Row(name, "comparison" if single else "CS-1.0", ps, not single))

    log("IC-1.0 iron condor...")
    ic, skipped = E.run_condor(spot, opts, start, end, vix)
    for name, ps in ic.items():
        rows.append(Row(name, "IC-1.0" if "range" in name else "comparison", ps, "range" in name))
    facts["ic_skips"] = Counter(v.split(" ")[0] for v in skipped["IC range days"].values())
    facts["vix_rows"] = 0 if vix is None else int(vix.notna().sum())

    lookup = RV.RvolLookup(fut_minutes[["time", "volume"]]) if not fut_minutes.empty else None
    cov = E.coverage(lookup, start, end) if lookup is not None else 0.0
    facts["coverage"] = cov
    log("DS-1.0 debit spreads on ORB-1.0 breakouts...")
    for name, ps in E.run_debit(spot, opts, start, end, base, suffix=" (no volume filter)").items():
        rows.append(Row(name, "comparison", ps, False))
    if lookup is not None:
        vf = E.run_debit(spot, opts, start, end, base, entry_filter=E.rvol_filter(lookup),
                         suffix=" VF")
        for name, ps in vf.items():
            single = "single leg" in name
            rows.append(Row(name, "comparison" if single else "DS-1.0", ps,
                            not single and cov >= MIN_COVERAGE,
                            "" if cov >= MIN_COVERAGE or single else "volume coverage < 80%"))
    else:
        for w in (100, 150):
            rows.append(Row(f"DS-{w} VF", "DS-1.0", [], False, "no futures volume stored"))

    log("TP-1.0 trend-pullback buying...")
    for limits in (False, True):
        if fut_minutes.empty:
            break
        res = TP.run(spot, fut_minutes, opts, start, end, base, TP_MODES, limits)
        for m in TP_MODES:
            name = f"TP {TP_LABELS[m]}" + (" limits" if limits else " (no limits)")
            judged = limits and cov >= MIN_COVERAGE
            rows.append(Row(name, "TP-1.0" if limits else "comparison",
                            E.single(name, res.trades[m]), judged,
                            "" if judged or not limits else "futures coverage < 80%"))
        if limits:
            facts["tp_skips"] = Counter(s.reason for s in res.skips)
    if fut_minutes.empty:
        for m in TP_MODES:
            rows.append(Row(f"TP {TP_LABELS[m]} limits", "TP-1.0", [], False,
                            "no futures data stored (run fut-load)"))
    return rows, facts


def rs(x: float | None) -> str:
    if x is None:
        return "–"
    return f"-₹{abs(x):,.0f}" if x < 0 else f"₹{x:,.0f}"


def nv(x) -> str:
    return "–" if x is None else f"{x:g}"


def verdict(row: Row, capital: float, start: date) -> str:
    if not row.judged:
        return "n/a" if row.strategy == "comparison" else f"not judged ({row.note})"
    return "PASS" if E.passes(row.positions, capital, start, MIN_TRADES) else "fail"


def to_markdown(rows: list[Row], facts: dict, start: date, end: date, capital: float,
                default_slip: bool, title: str) -> str:
    out = [f"# {title}", "", f"Range {start} to {end}. Rules: backend/app/spreads/__init__.py "
           f"({VERSION}) and backend/app/pullback/__init__.py ({TP_VERSION}), fixed 2026-11-05 "
           "before any run.", "",
           f"Pass bar (judged rows only): at least {MIN_TRADES} trades, net > 0, profit factor >= "
           "1.10, each year net > 0, max drawdown < 50% of ₹1L, default slippage (0.5% + 0.5 pt a "
           "side on every leg). One trade = one whole position, all legs and costs.", ""]
    if not default_slip:
        out += ["**Not the default slippage: the pass bar does not apply to this run.**", ""]
    out += [f"Futures volume/VWAP coverage: {facts.get('coverage', 0):.0%} of 5-minute bars.", ""]
    out += ["| Row | Strategy | Trades | Win % | Gross | Costs | Net | PF | Year 1 | Year 2 | "
            "Max DD | Worst | Net excl. expiry days | PF excl. | Verdict |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        s = E.summarize(r.positions, capital, start)
        ex = [p for p in r.positions if not p.expiry_day]
        sx = E.summarize(ex, capital, start)
        v = verdict(r, capital, start) if default_slip else "n/a"
        out.append(f"| {r.name} | {r.strategy} | {s.trades} | {nv(s.win_rate)} "
                   f"| {rs(s.gross_pnl)} | {rs(s.friction)} | {rs(s.net_pnl)} | "
                   f"{nv(s.profit_factor)} | {rs(s.year1)} | {rs(s.year2)} | "
                   f"{nv(s.max_drawdown_pct)}% | {rs(s.worst_trade)} | {rs(sx.net_pnl)} | "
                   f"{nv(sx.profit_factor)} | {v} |")
    if facts.get("ic_skips"):
        out += ["", "IC range-day filter, days skipped by reason: "
                + ", ".join(f"{k} {v}" for k, v in facts["ic_skips"].most_common()) + "."]
    if facts.get("tp_skips"):
        out += ["", "TP-1.0 with limits, signals not taken: "
                + ", ".join(f"{k} {v}" for k, v in facts["tp_skips"].most_common()) + "."]
    out += ["", "Expiry-day columns drop trades taken ON a weekly expiry day (those trades already "
            "use the next expiry, never 0-DTE). Information only; the verdict uses all days.",
            "", "Several rows are scored, so one PASS can be luck: only the single best passing "
            "row gets the one held-back-year check."]
    return "\n".join(out) + "\n"


def write_csv(rows: list[Row], path: Path) -> int:
    n = 0
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["row", "day", "expiry", "entry_time", "direction", "legs", "exit_reason",
                    "gross_pnl", "friction", "net_pnl", "expiry_day", "stale"])
        for r in rows:
            for p in r.positions:
                legs = "; ".join(f"{x.side} {x.strike:g}{x.option_type} {x.entry_price}->{x.exit_price}"
                                 for x in p.legs)
                w.writerow([r.name, p.day, p.expiry, p.entry_time, p.direction, legs, p.exit_reason,
                            p.gross_pnl, p.friction, p.net_pnl, p.expiry_day, p.stale])
                n += 1
    return n
