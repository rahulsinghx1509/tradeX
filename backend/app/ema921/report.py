"""Markdown report and trade CSV for a 9/21 EMA backtest run."""

from __future__ import annotations

import csv
from collections import Counter
from datetime import date
from pathlib import Path

from app.ema921 import engine as E
from app.ema921.metrics import summarize


def _money(x: float | None) -> str:
    if x is None:
        return "–"
    return f"-₹{-x:,.0f}" if x < 0 else f"₹{x:,.0f}"


def _num(x: float | None, suffix: str = "") -> str:
    return "–" if x is None else f"{x:g}{suffix}"


def to_markdown(results: list[E.Result], start: date, end: date) -> str:
    cfg0 = results[0].config
    lines = [
        "# 9/21 EMA NIFTY options backtest", "",
        f"Range {start} to {end}. Entry window {cfg0.start:%H:%M}–{cfg0.cutoff:%H:%M}, square-off "
        f"{cfg0.square_off:%H:%M}. 1 lot of {cfg0.lot_size}. Slippage {cfg0.slippage_pct * 100:g}% + "
        f"{cfg0.slippage_points:g} pts a side. Costs: brokerage, STT, exchange, SEBI, stamp, GST "
        "(rates NOT_VERIFIED). Drawdown % on ₹{:,.0f} starting capital.".format(cfg0.capital),
        "", "Rules: https://claude.ai/artifact/PiDc6FGqdqS6xnc1z6sZq4", "",
        "| Bars | Mode | Trades | Win % | Gross | Friction | Net | Profit factor | Payoff | "
        "Expectancy | Max DD | Max DD % | Worst trade | Worst open loss | Whipsaws |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for res in results:
        for mode, trades in res.trades.items():
            s = summarize(trades, res.config.capital)
            lines.append(
                f"| {res.config.timeframe}m | {E.MODE_LABELS[mode]} | {s.trades} | {_num(s.win_rate)} | "
                f"{_money(s.gross_pnl)} | {_money(s.slippage + s.charges)} | {_money(s.net_pnl)} | "
                f"{_num(s.profit_factor)} | {_num(s.payoff_ratio)} | {_money(s.expectancy)} | "
                f"{_money(s.max_drawdown)} | {_num(s.max_drawdown_pct, '%')} | {_money(s.worst_trade)} | "
                f"{_money(s.worst_mae)} | {s.whipsaws} |")
    lines += ["", "## Days without trades", "", "| Bars | Reason | Days |", "|---|---|---|"]
    for res in results:
        counts = Counter(r.split(" (")[0] for r in res.no_trade_days.values())
        for reason, n in counts.most_common():
            lines.append(f"| {res.config.timeframe}m | {reason} | {n} |")
    lines += ["", "## Entries skipped for missing option data", "",
              "| Bars | Mode | Reason | Count |", "|---|---|---|---|"]
    any_skip = False
    for res in results:
        for (mode, reason), n in Counter((s.mode, s.reason) for s in res.skips).most_common():
            any_skip = True
            lines.append(f"| {res.config.timeframe}m | {E.MODE_LABELS[mode]} | {reason} | {n} |")
    if not any_skip:
        lines.append("| – | – | none | 0 |")
    lines += ["", "## Read before trusting these numbers", "",
              "- ATM sell has no stop loss (as specified) and needs roughly ₹1L+ margin per lot, which "
              "is not modelled. 'Worst open loss' is the deepest loss seen inside a trade.",
              "- Deep ITM spreads can be wider than 0.5%; results there may flatter reality.",
              "- Lot size is a flat 65 for every date, as specified (it was 25, then 75, in the past).",
              "- Past results on one strategy over two years are not proof of a future edge."]
    return "\n".join(lines) + "\n"


CSV_FIELDS = ["timeframe", "mode", "day", "direction", "side", "option_type", "strike", "expiry", "dte",
              "symbol", "cross_number", "signal_time", "entry_time", "entry_price", "entry_fill",
              "exit_time", "exit_price", "exit_fill", "exit_reason", "nifty_entry", "nifty_exit",
              "bars_held", "whipsaw", "stale_exit", "gross_pnl", "slippage", "charges", "net_pnl", "mae"]


def write_csv(results: list[E.Result], path: Path) -> int:
    n = 0
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        w.writeheader()
        for res in results:
            for trades in res.trades.values():
                for t in trades:
                    row = {k: getattr(t, k, None) for k in CSV_FIELDS}
                    row["timeframe"] = f"{res.config.timeframe}m"
                    row["charges"] = t.charges.get("total")
                    w.writerow(row)
                    n += 1
    return n
