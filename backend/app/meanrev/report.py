"""MR-4: run every row, score it against R26, walk forward, and write the report.

`run_all` is used twice: by the backtest (real option data) and by the loader (a recorder that
notes which option candles are missing), so the loader fetches exactly what the rows ask for.
"""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

import pandas as pd

from app.ema921 import engine as EE
from app.meanrev import (
    CAPITAL,
    GRID,
    MAX_DD_PCT,
    MIN_TRADES,
    PARAMS,
    PF_MIN,
    UNDERLYINGS,
    VERSION,
    WF_MIN_TRAIN_TRADES,
    Params,
)
from app.meanrev import engine as E


# ----------------------------------------------------------------------------- inputs
@dataclass
class IndexInput:
    underlying: str
    spot: pd.DataFrame
    fut: pd.DataFrame
    closes: pd.Series  # daily closes (stored + 1m), Timestamp(date) index
    options: EE.OptionData


@dataclass
class Inputs:
    start: date
    end: date
    indices: dict[str, IndexInput]
    market: E.Market
    cfg: EE.Config = field(default_factory=EE.Config)


# ----------------------------------------------------------------------------- scoring
def year_bounds(start: date, years: int = 3) -> list[tuple[date, date]]:
    """[(first day, first day of next year)), ...] 12-month years from `start`."""
    out, a = [], start
    for _ in range(years):
        b = a.replace(year=a.year + 1)
        out.append((a, b))
        a = b
    return out


@dataclass
class Score:
    trades: int
    win_rate: float | None
    gross: float
    friction: float
    net: float
    pf: float | None
    max_dd_pct: float
    worst: float | None
    years: list[float]
    years_n: list[int]


def score(trades: list[E.Trade], start: date, capital: float = CAPITAL) -> Score:
    ts = sorted(trades, key=lambda t: t.entry_time)
    nets = [t.net_pnl for t in ts]
    wins = [x for x in nets if x > 0]
    loss = -sum(x for x in nets if x <= 0)
    eq = peak = capital
    dd = 0.0
    for x in nets:
        eq += x
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak * 100 if peak > 0 else 0.0)
    yb = year_bounds(start)
    years = [round(sum(t.net_pnl for t in ts if a <= t.day < b), 2) for a, b in yb]
    years_n = [sum(1 for t in ts if a <= t.day < b) for a, b in yb]
    n = len(ts)
    return Score(n, round(len(wins) / n * 100, 1) if n else None,
                 round(sum(t.gross_pnl for t in ts), 2), round(sum(t.friction for t in ts), 2),
                 round(sum(nets), 2), round(sum(wins) / loss, 2) if loss > 0 else None,
                 round(dd, 2), min(nets) if nets else None, years, years_n)


def bar_checks(s: Score) -> dict[str, bool]:
    """R26 without the walk-forward part."""
    return {"PF >= 1.10": (s.pf or 0) >= PF_MIN, "max DD < 20%": s.max_dd_pct < MAX_DD_PCT,
            ">= 60 trades": s.trades >= MIN_TRADES,
            "every year net > 0": all(y > 0 for y in s.years) and s.trades > 0}


# ----------------------------------------------------------------------------- rows
@dataclass
class Row:
    name: str
    underlying: str  # NIFTY / BANKNIFTY / BOTH
    params: dict[str, Params]
    trades: list[E.Trade]
    skips: Counter
    days: Counter  # day-level and signal-level reasons from `candidates`
    judged: bool = False


def _candidates(prepared, inp: Inputs, p: Params, why: Counter) -> list[E.Candidate]:
    u = p.underlying
    exp = E.expiry_days(u, inp.indices[u].options.expiries(), p.monthly_expiry_only)
    nifty = prepared.get("NIFTY", {})
    out = []
    for d, info in sorted(prepared[u].items()):
        if not inp.start <= d <= inp.end:
            continue
        out += E.candidates(info, p, inp.market, exp, nifty.get(d), why)
    return out


def run_row(name: str, prepared, inp: Inputs, params: dict[str, Params],
            instruments: dict[str, E.Instrument], judged: bool = False) -> Row:
    why: Counter = Counter()
    cands = []
    for p in params.values():
        cands += _candidates(prepared, inp, p, why)
    res = E.run(cands, instruments, inp.market, inp.cfg)
    who = next(iter(params)) if len(params) == 1 else "BOTH"
    return Row(name, who, params, res.trades, res.skips, why, judged)


@dataclass
class Fold:
    train: tuple[date, date]
    test: tuple[date, date]
    gap: float
    trend: float
    train_score: Score
    test_trades: list[E.Trade]


@dataclass
class WalkForward:
    underlying: str
    folds: list[Fold]
    grid: dict[tuple[float, float], list[E.Trade]]

    @property
    def oos(self) -> list[E.Trade]:
        return [t for f in self.folds for t in f.test_trades]

    def holds(self, start: date) -> bool:
        s = score(self.oos, start)
        return s.trades > 0 and s.net > 0 and (s.pf or 0) >= PF_MIN


def _pick(grid: dict[tuple[float, float], list[E.Trade]], a: date, b: date, start: date):
    """Best grid point on [a, b): highest PF with enough trades; ties nearest (1, 1)."""
    best = None
    for (g, tr), trades in grid.items():
        sub = [t for t in trades if a <= t.day < b]
        s = score(sub, start)
        if s.trades < WF_MIN_TRAIN_TRADES:
            continue
        key = (s.pf or 0.0, -(abs(g - 1) + abs(tr - 1)))
        if best is None or key > best[0]:
            best = (key, g, tr, s)
    if best is None:  # nothing has enough trades: keep the posted values
        sub = [t for t in grid[(1.0, 1.0)] if a <= t.day < b]
        return 1.0, 1.0, score(sub, start)
    return best[1], best[2], best[3]


def walk_forward(u: str, prepared, inp: Inputs, instruments) -> WalkForward:
    grid: dict[tuple[float, float], list[E.Trade]] = {}
    for g in GRID:
        for tr in GRID:
            p = PARAMS[u].scaled(g, tr)
            grid[(g, tr)] = run_row("grid", prepared, inp, {u: p}, instruments).trades
    yb = year_bounds(inp.start)
    folds = []
    for k in (1, 2):  # train on the first k years, test on year k+1
        a, b = yb[0][0], yb[k - 1][1]
        g, tr, s = _pick(grid, a, b, inp.start)
        ta, tb = yb[k]
        folds.append(Fold((a, b), (ta, tb), g, tr, s,
                          [t for t in grid[(g, tr)] if ta <= t.day < tb]))
    return WalkForward(u, folds, grid)


@dataclass
class Results:
    inputs: Inputs
    rows: list[Row]
    wf: dict[str, WalkForward]
    vix_fallback_days: int
    iv_sources: Counter
    data_problems: list[str] = field(default_factory=list)  # any = verdicts are NOT JUDGED
    data_lines: list[str] = field(default_factory=list)


MIN_VWAP_SHARE = 0.90  # futures volume (VWAP) on at least 90% of the complete spot days
MAX_MISSING_LEGS = 0.05  # at most 5% of priced entries without option candles


def data_check(prepared, inp: Inputs, rows: list[Row]) -> tuple[list[str], list[str]]:
    """(problems, lines): is the data complete enough for a verdict? Missing futures volume
    silently switches Setup B off, and missing option legs silently drop trades."""
    problems, lines = [], []
    for u, days in prepared.items():
        inside = [i for d, i in days.items() if inp.start <= d <= inp.end and i.complete]
        with_vwap = sum(1 for i in inside if i.has_vwap)
        share = with_vwap / len(inside) if inside else 0.0
        lines.append(f"{u}: {len(inside)} complete spot days, futures volume (VWAP) on "
                     f"{with_vwap} ({share:.0%})")
        if share < MIN_VWAP_SHARE:
            problems.append(f"{u} futures volume is missing on {len(inside) - with_vwap} of "
                            f"{len(inside)} days, so Setup B could not run on them")
        row = next((r for r in rows if r.judged and r.underlying == u), None)
        if row is not None:
            missing = row.skips.get("NO_DATA", 0) + row.skips.get("NO_CONTRACT", 0)
            tried = missing + len(row.trades)
            lines.append(f"{u}: {len(row.trades)} entries priced, {missing} without option candles")
            if tried and missing / tried > MAX_MISSING_LEGS:
                problems.append(f"{u}: {missing} of {tried} entries had no option candles stored")
    if inp.market.vix_915:
        lines.append(f"India VIX 1-minute: {len(inp.market.vix_915)} days")
    else:
        lines.append("India VIX 1-minute: none stored (the previous daily close is used)")
    return problems, lines


def run_all(inp: Inputs) -> Results:
    prepared = {u: E.prepare(u, ix.spot, ix.fut, ix.closes) for u, ix in inp.indices.items()}
    instruments = {u: E.instrument_for(u, ix.options, PARAMS[u]) for u, ix in inp.indices.items()}
    rows: list[Row] = []
    for u in UNDERLYINGS:
        if u not in inp.indices:
            continue
        p = PARAMS[u]
        rows.append(run_row(f"{u} MR-4 full (A+B)", prepared, inp, {u: p}, instruments, True))
        rows.append(run_row(f"{u} Setup A only (gap fill)", prepared, inp,
                            {u: replace(p, setups=("A",))}, instruments))
        rows.append(run_row(f"{u} Setup B only (confluence fade)", prepared, inp,
                            {u: replace(p, setups=("B",))}, instruments))
        if u == "BANKNIFTY" and "NIFTY" in inp.indices:
            rows.append(run_row(f"{u} MR-4 full + R29", prepared, inp, {u: replace(p, r29=True)},
                                instruments))
        rows.append(run_row(f"{u} full, no-bias days at full size (info)", prepared, inp,
                            {u: replace(p, half_size=1.0)}, instruments))
    if all(u in inp.indices for u in UNDERLYINGS):
        both = {u: PARAMS[u] for u in UNDERLYINGS}
        rows.append(run_row("BOTH combined account (R27-R30)", prepared, inp, both, instruments))
        both29 = {**both, "BANKNIFTY": replace(PARAMS["BANKNIFTY"], r29=True)}
        rows.append(run_row("BOTH combined account + R29", prepared, inp, both29, instruments))
    wf = {u: walk_forward(u, prepared, inp, instruments) for u in UNDERLYINGS if u in inp.indices}
    days = sorted({d for u in prepared for d in prepared[u] if inp.start <= d <= inp.end})
    fallback = sum(1 for d in days if d not in inp.market.vix_915)
    iv = Counter(t.iv_source for r in rows for t in r.trades)
    problems, lines = data_check(prepared, inp, rows)
    return Results(inp, rows, wf, fallback, iv, problems, lines)


# ----------------------------------------------------------------------------- verdicts
def verdict(r: Row, wf: WalkForward | None, start: date) -> tuple[bool, list[str]]:
    s = score(r.trades, start)
    checks = bar_checks(s)
    if wf is not None:
        checks["walk-forward holds"] = wf.holds(start)
    failed = [k for k, ok in checks.items() if not ok]
    return not failed, failed


def by_index(r: Row, u: str) -> list[E.Trade]:
    return [t for t in r.trades if t.underlying == u]


# ----------------------------------------------------------------------------- formatting
def rs(x: float | None) -> str:
    if x is None:
        return "-"
    return f"-₹{abs(x):,.0f}" if x < 0 else f"₹{x:,.0f}"


def nv(x) -> str:
    return "-" if x is None else f"{x:g}"


HEADER = ("| Row | Trades | Win % | Gross | Costs + slippage | Net | PF | Max DD | "
          "Year 1 | Year 2 | Year 3 | R26 |")


def _line(name: str, s: Score, v: str) -> str:
    ys = " | ".join(f"{rs(y)} ({n})" for y, n in zip(s.years, s.years_n, strict=True))
    return (f"| {name} | {s.trades} | {nv(s.win_rate)} | {rs(s.gross)} | {rs(s.friction)} | "
            f"{rs(s.net)} | {nv(s.pf)} | {s.max_dd_pct:g}% | {ys} | {v} |")


def markdown(res: Results) -> str:
    inp = res.inputs
    st = inp.start
    yb = year_bounds(st)
    out = [f"# {VERSION} backtest: NIFTY + BANKNIFTY mean reversion ({inp.start} to {inp.end})", "",
           "Rules: backend/app/meanrev/__init__.py (R1-R31, fixed 2026-11-20 before this run). "
           "Option BUY at ~0.7 delta, 1 lot (NIFTY 65 / BANKNIFTY 30), fills at the option's "
           "1-minute open, slippage 0.5% + 0.5 pt a side, full cost stack. Backtest only, no orders.",
           "", "Years: " + ", ".join(f"Y{i + 1} {a} to {b}" for i, (a, b) in enumerate(yb)) + ".",
           "", "## Data check", ""] + [f"- {x}" for x in res.data_lines]
    if res.data_problems:
        out += ["", "**DATA INCOMPLETE: the verdicts below are NOT JUDGED.** Run "
                "`deploy/tradex.sh mr4-load` until it finishes without errors, then "
                "`deploy/tradex.sh mr4-backtest` again. Problems:"]
        out += [f"- {x}" for x in res.data_problems]
    out += ["", "## Verdict per index (R26, R31)", ""]
    for u in UNDERLYINGS:
        row = next((r for r in res.rows if r.judged and r.underlying == u), None)
        if row is None:
            out.append(f"- {u}: no data.")
            continue
        ok, failed = verdict(row, res.wf.get(u), st)
        s = score(row.trades, st)
        word = "NOT JUDGED (data incomplete)" if res.data_problems else ("PASS" if ok else "FAIL")
        out.append(f"- **{u}: {word}** — {s.trades} trades, net {rs(s.net)}, "
                   f"PF {nv(s.pf)}, max DD {s.max_dd_pct:g}%"
                   + ("" if ok else f"; fails: {', '.join(failed)}") + ".")
    comb = next((r for r in res.rows if r.name.startswith("BOTH combined account (")), None)
    if comb is not None:
        parts = []
        for u in UNDERLYINGS:
            ok, failed = verdict(replace(comb, trades=by_index(comb, u)), None, st)
            parts.append(f"{u} {'passes' if ok else 'fails'}"
                         + ("" if ok else f" ({', '.join(failed)})"))
        out.append("- Combined account, each index on its own (R31, without walk-forward): "
                   + "; ".join(parts) + ".")
    out += ["", "## All rows", "", HEADER, "|" + "---|" * 12]
    for r in res.rows:
        s = score(r.trades, st)
        if r.judged:
            ok, failed = verdict(r, res.wf.get(r.underlying), st)
            v = "NOT JUDGED" if res.data_problems else ("**PASS**" if ok else "FAIL")
        else:
            ok = all(bar_checks(s).values())
            v = ("meets bar" if ok else "below bar") + " (not judged)"
        out.append(_line(r.name, s, v))
        if r.underlying == "BOTH":
            for u in UNDERLYINGS:
                out.append(_line(f"&nbsp;&nbsp;of which {u}", score(by_index(r, u), st), ""))
    out += ["", "Year columns: net (trades). PF = sum of net wins / sum of net losses. "
            "Max DD = peak-to-trough of the net equity curve on ₹1L.", ""]

    out += ["## Walk-forward (gap band x trend-day block multipliers, anchored)", ""]
    for u, wf in res.wf.items():
        out += [f"### {u}", "", "| Train | Picked gap x | Picked trend x | Train trades | Train PF "
                "| Test year | Test trades | Test net | Test PF |", "|---|---|---|---|---|---|---|---|---|"]
        for f in wf.folds:
            ts = score(f.test_trades, st)
            out.append(f"| {f.train[0]} to {f.train[1]} | {f.gap:g} | {f.trend:g} | "
                       f"{f.train_score.trades} | {nv(f.train_score.pf)} | {f.test[0]} to "
                       f"{f.test[1]} | {ts.trades} | {rs(ts.net)} | {nv(ts.pf)} |")
        o = score(wf.oos, st)
        out += ["", f"Out-of-sample (years 2+3 with the picked values): {o.trades} trades, net "
                f"{rs(o.net)}, PF {nv(o.pf)} → **{'holds' if wf.holds(st) else 'does not hold'}**.",
                "", "Whole grid over all 3 years (net / PF / trades), for stability:", "",
                "| gap x \\ trend x | " + " | ".join(f"{t:g}" for t in GRID) + " |",
                "|---|" + "---|" * len(GRID)]
        for g in GRID:
            cells = []
            for t in GRID:
                s = score(wf.grid[(g, t)], st)
                cells.append(f"{rs(s.net)} / {nv(s.pf)} / {s.trades}")
            out.append(f"| {g:g} | " + " | ".join(cells) + " |")
        out.append("")

    out += ["## Why entries did not happen", ""]
    for r in res.rows:
        if not (r.judged or r.underlying == "BOTH"):
            continue
        out.append(f"- {r.name}: day/signal filters "
                   + ", ".join(f"{k} {v}" for k, v in r.days.most_common())
                   + "; account/option skips "
                   + (", ".join(f"{k} {v}" for k, v in r.skips.most_common()) or "none") + ".")
    full = [t for r in res.rows if r.judged for t in r.trades]
    ex = Counter(t.exit_reason for t in full)
    out += ["", "Exits (judged rows): " + ", ".join(f"{k} {v}" for k, v in ex.most_common()) + ".",
            f"Strike delta from the ATM option's implied volatility: {res.iv_sources.get('ATM', 0)} "
            f"trades; from India VIX (ATM had no price): {res.iv_sources.get('VIX', 0)}.",
            f"Days that used the previous daily VIX close because Groww had no 1-minute VIX: "
            f"{res.vix_fallback_days}.",
            f"Trades with a stale exit price (no option candle at the exit minute): "
            f"{sum(1 for t in full if t.stale)}.",
            "", "## Caveats", "",
            "- Half size (no-bias days) and the 50/50 Setup B exits are simulated as fractions of "
            "1 lot. Trading them literally needs 2-4 lots; brokerage is still charged ₹20 an order.",
            "- Lot sizes are today's (NIFTY 65, BANKNIFTY 30) for every date, as in every earlier "
            "backtest. RBI/Budget dates before 2026 are NOT_VERIFIED (app/data/mr4_event_days.csv).",
            "- Many rows and a 9-point grid are shown, so one PASS can be luck. The verdict row uses "
            "the posted values; the walk-forward is the out-of-sample check.", ""]
    return "\n".join(out)


def write_csv(res: Results, path: Path) -> int:
    n = 0
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["row", "underlying", "setup", "day", "direction", "pattern", "size", "strike",
                    "type", "expiry", "delta", "iv", "iv_source", "entry_time", "entry_spot",
                    "stop", "entry_price", "exits", "gross_pnl", "friction", "net_pnl", "stale"])
        for r in res.rows:
            if not (r.judged or r.underlying == "BOTH"):
                continue
            for t in r.trades:
                exits = "; ".join(f"{f.fraction:g}@{f.time:%H:%M} {f.reason} {f.price}"
                                  for f in t.exits)
                w.writerow([r.name, t.underlying, t.setup, t.day, t.direction, t.pattern, t.size,
                            t.strike, t.option_type, t.expiry, t.delta, t.iv, t.iv_source,
                            f"{t.entry_time:%Y-%m-%d %H:%M}", t.entry_spot, t.stop, t.entry_price,
                            exits, t.gross_pnl, t.friction, t.net_pnl, t.stale])
                n += 1
    return n
