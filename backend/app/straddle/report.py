"""Markdown report and CSV for the ST-1.0 straddle backtest."""

from __future__ import annotations

import csv
from collections import Counter
from datetime import date
from pathlib import Path

from app.straddle import engine as E


def _money(x: float | None) -> str:
    return "–" if x is None else (f"-₹{abs(x):,.0f}" if x < 0 else f"₹{x:,.0f}")


def _num(x, suffix: str = "") -> str:
    return "–" if x is None else f"{x:g}{suffix}"


def to_markdown(res: E.Result, start: date, end: date) -> str:
    c = res.config
    s = E.summarize(res.trades, c.capital)
    ok, why = E.passes_dev_bar(res.trades, c.capital)
    lines = [
        f"# {E.VERSION} 09:20 hedged short straddle, NIFTY weekly options", "",
        f"Range {start} to {end}. Sell ATM CE + PE at {c.entry:%H:%M}, buy wings ±{c.wing_points} pts, "
        f"stop {c.stop_pct * 100:g}% on each sold leg, exit {c.exit:%H:%M}. {c.lot_size} units a leg. "
        f"Slippage {c.slippage_pct * 100:g}% + {c.slippage_points:g} pts a side. VIX-jump filter "
        f"{'on' if c.vix_filter else 'OFF'}. Costs: brokerage, STT, exchange, SEBI, stamp, GST "
        f"(rates NOT_VERIFIED). Drawdown % on ₹{c.capital:,.0f}.", "",
        "| Days traded | Win % | Gross | Friction | Net | Profit factor | Avg day | Max DD | "
        "Max DD % | Worst day | Best day | Sold legs stopped | Both stopped |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        f"| {s.days} | {_num(s.win_rate)} | {_money(s.gross_pnl)} | {_money(s.friction)} | "
        f"{_money(s.net_pnl)} | {_num(s.profit_factor)} | {_money(s.expectancy)} | "
        f"{_money(s.max_drawdown)} | {_num(s.max_drawdown_pct, '%')} | {_money(s.worst_day)} | "
        f"{_money(s.best_day)} | {_num(s.legs_stopped_pct, '%')} | {s.both_stopped_days} |", "",
        f"Pass bar for the held-back year (rule 8): **{'PASS' if ok else 'FAIL'}** ({why}). "
        "Only meaningful at the default slippage.", "",
        "## By year", "", "| Year starting | Days | Net | Profit factor | Max DD |", "|---|---|---|---|---|"]
    by_year: dict[int, list[E.DayTrade]] = {}
    for t in res.trades:
        k = (t.day - start).days // 365
        by_year.setdefault(k, []).append(t)
    for k in sorted(by_year):
        ys = E.summarize(by_year[k], c.capital)
        first = min(t.day for t in by_year[k])
        lines.append(f"| {first} | {ys.days} | {_money(ys.net_pnl)} | {_num(ys.profit_factor)} | "
                     f"{_money(ys.max_drawdown)} |")
    lines += ["", "## Days not traded", "", "| Reason | Days |", "|---|---|"]
    reasons = Counter(r.split(" (")[0] for r in res.skipped.values())
    for r, n in reasons.most_common():
        lines.append(f"| {r} | {n} |")
    if not reasons:
        lines.append("| none | 0 |")
    lines += ["", "## Read before trusting these numbers", "",
              "- Stops are checked on 1-minute highs and filled at the stop (or the open after a gap); "
              "in fast markets a real stop-loss order can fill worse.",
              "- Margin for a hedged straddle (roughly ₹1L a lot set, NOT_VERIFIED) is not modelled.",
              "- Lot size is a flat 65 for every date (it was 25, then 75, in the past).",
              "- Past results are not proof of a future edge."]
    return "\n".join(lines) + "\n"


CSV_FIELDS = ["day", "expiry", "dte", "spot", "atm", "role", "side", "option_type", "strike", "symbol",
              "entry_time", "entry_price", "entry_fill", "exit_time", "exit_price", "exit_fill",
              "exit_reason", "stale_exit", "gross_pnl", "slippage", "charges", "net_pnl"]


def write_csv(res: E.Result, path: Path) -> int:
    n = 0
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        w.writeheader()
        for t in res.trades:
            for leg in t.legs:
                w.writerow({"day": t.day, "expiry": t.expiry, "dte": t.dte, "spot": t.spot,
                            "atm": t.atm, **{k: getattr(leg, k) for k in CSV_FIELDS[5:]}})
                n += 1
    return n
