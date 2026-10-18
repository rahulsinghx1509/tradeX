"""Hourly entries inside the daily trend: does reading the hourly chart improve entries?

The question from the owner: should the live (hourly) chart be read together with the daily
history to decide entries? This study answers it on past data, long side only (the side the
earlier studies favoured), and on the development period only.

Daily context for a day D uses only daily candles up to D-1's close (known before D opens):
"daily uptrend" = D1 up on D-1 (close above EMA50 and EMA20 above EMA50).

Variants differ only in *when* they buy. Each enters at the close of a completed 1h candle
(the moment an alert would fire), one trade per instrument at a time:

- A  Daily trend only: the first hourly candle of each day in a daily uptrend. No hourly
     reading, so this is the baseline every other variant is compared with.
- B  + hourly pullback: the candle's low touches the hourly EMA20 and it closes above it,
     with the hourly EMA20 above the hourly EMA50 (the dashboard's H3 reading).
- C  + hourly RSI dip: hourly RSI(14) closes back above 40 after being below it.
- D  + hourly breakout: the first hourly close above the previous day's high.
- E  Hourly pullback with no daily filter: is the daily history adding anything?

Exit is the same for all, so only the timing differs: stop = entry − 1 daily ATR (ATR of D-1),
target = +2R, otherwise out at the close of the 35th hourly candle (about 5 trading days).
Checked candle by candle; when one candle touches both stop and target the stop is assumed to
come first. Results are in R (1R = the distance to the stop).

Underlying only: **no options, costs or slippage**. Validation (from VALIDATION_FROM) is held
back and not reported, as in every earlier study.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from app.services import indicators as ind
from app.services import signal_study as ss
from app.services.metrics import MIN_HOURLY_BARS

HOURLY_STUDY_VERSION = "MTF-1.0"
VALIDATION_FROM = date(2025, 4, 1)  # same held-back period as every earlier study
STOP_ATR = 1.0
TARGET_R = 2.0
MAX_HOLD_BARS = 35
RSI_DIP = 40


@dataclass(frozen=True)
class Variant:
    key: str
    trigger: str  # "first", "pullback", "rsi_dip", "breakout"
    daily_filter: bool
    label: str


VARIANTS = (
    Variant("A", "first", True, "Daily trend only (baseline)"),
    Variant("B", "pullback", True, "Daily trend + hourly pullback"),
    Variant("C", "rsi_dip", True, "Daily trend + hourly RSI dip"),
    Variant("D", "breakout", True, "Daily trend + hourly breakout"),
    Variant("E", "pullback", False, "Hourly pullback, no daily filter"),
)


def prepare(daily: pd.DataFrame, hourly: pd.DataFrame) -> pd.DataFrame:
    """Hourly candles with hourly indicators and the previous day's daily context."""
    h = hourly.sort_index().copy()
    close = h["close"]
    h["ema20"], h["ema50"] = ind.ema(close, 20), ind.ema(close, 50)
    h["rsi"] = ind.rsi(close, 14)
    h["bar_no"] = np.arange(len(h))
    h["day"] = h.index.normalize()

    d = daily.sort_index()
    ctx = pd.DataFrame(index=d.index)
    ema20, ema50 = ind.ema(d["close"], 20), ind.ema(d["close"], 50)
    ctx["daily_up"] = ((d["close"] > ema50) & (ema20 > ema50)).where(ema50.notna())
    ctx["daily_atr"] = ind.atr(d["high"], d["low"], d["close"], 14)
    ctx["prev_high"] = d["high"]
    ctx["ctx_day"] = ctx.index
    # Each hourly candle gets the context of the last daily candle *before* its own day.
    joined = pd.merge_asof(pd.DataFrame({"day": h["day"].to_numpy()}), ctx.reset_index(drop=True),
                           left_on="day", right_on="ctx_day", direction="backward",
                           allow_exact_matches=False)
    for col in ("daily_up", "daily_atr", "prev_high"):
        h[col] = joined[col].to_numpy()
    return h


def trigger_rows(h: pd.DataFrame, v: Variant) -> np.ndarray:
    """Row numbers of hourly candles that trigger an entry, before overlap is removed."""
    warm = (h["bar_no"] >= MIN_HOURLY_BARS - 1) & h["daily_atr"].notna()
    if v.daily_filter:
        warm &= h["daily_up"].eq(True)
    if v.trigger == "first":
        hit = ~h["day"].duplicated()
    elif v.trigger == "pullback":
        hit = (h["low"] <= h["ema20"]) & (h["close"] > h["ema20"]) & (h["ema20"] > h["ema50"])
    elif v.trigger == "rsi_dip":
        hit = (h["rsi"] >= RSI_DIP) & (h["rsi"].shift(1) < RSI_DIP)
    else:  # breakout: first close above yesterday's high, once per day
        above = h["close"] > h["prev_high"]
        hit = above & ~(above.groupby(h["day"]).cumsum() > 1)
    return np.flatnonzero((hit & warm).to_numpy())


def _trade(h: pd.DataFrame, i: int) -> dict:
    entry, dist = h["close"].iat[i], STOP_ATR * h["daily_atr"].iat[i]
    stop, target = entry - dist, entry + TARGET_R * dist
    last = min(i + MAX_HOLD_BARS, len(h) - 1)
    for j in range(i + 1, last + 1):
        if h["low"].iat[j] <= stop:  # stop first when both are touched (conservative)
            return {"exit_row": j, "exit_reason": "STOP", "r": -1.0}
        if h["high"].iat[j] >= target:
            return {"exit_row": j, "exit_reason": "TARGET", "r": TARGET_R}
    reason = "TIME" if last == i + MAX_HOLD_BARS else "OPEN"
    return {"exit_row": last, "exit_reason": reason, "r": (h["close"].iat[last] - entry) / dist}


def trades(symbol: str, h: pd.DataFrame, v: Variant) -> pd.DataFrame:
    rows, busy_until = [], -1
    for i in trigger_rows(h, v):
        if i <= busy_until or i >= len(h) - 1:
            continue
        t = _trade(h, i)
        busy_until = t["exit_row"]
        rows.append({"variant": v.key, "symbol": symbol, "entry_time": h.index[i],
                     "entry": h["close"].iat[i], "exit_time": h.index[t["exit_row"]],
                     "exit_reason": t["exit_reason"], "r": t["r"],
                     "bars": t["exit_row"] - i})
    return pd.DataFrame(rows)


def run(daily: dict[str, pd.DataFrame], hourly: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, dict]:
    """Development-period trades of every variant, plus the hourly coverage per instrument."""
    coverage, parts = {}, []
    for sym, hf in hourly.items():
        if hf.empty or sym not in daily or daily[sym].empty:
            continue
        coverage[sym] = (hf.index[0].date(), hf.index[-1].date(), len(hf))
        h = prepare(daily[sym], hf)
        parts += [trades(sym, h, v) for v in VARIANTS]
    parts = [p for p in parts if len(p)]
    tr = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if len(tr):
        cut = pd.Timestamp(VALIDATION_FROM)
        # Keep trades that both start and finish before validation begins.
        tr = tr[(tr["entry_time"] < cut) & (tr["exit_time"] < cut)].copy()
        tr.sort_values(["variant", "entry_time", "symbol"], inplace=True)
    days = sorted(tr["entry_time"].dt.normalize().unique()) if len(tr) else []
    mid = days[len(days) // 2] if days else None
    return tr, {"coverage": coverage, "mid": mid}


def to_markdown(tr: pd.DataFrame, info: dict, generated: str) -> str:
    f = ss._fmt
    lines = [f"# Hourly entries inside the daily trend {HOURLY_STUDY_VERSION} "
             "(development period only)", "",
             f"Generated {generated}. Long only. Entry at the close of a completed hourly candle; "
             f"stop {STOP_ATR:g} daily ATR, target {TARGET_R:g}R, otherwise out after "
             f"{MAX_HOLD_BARS} hourly candles. Development: before {VALIDATION_FROM}; validation "
             "is held back. **Underlying only: no options, costs or slippage.**", "",
             "## Hourly history used", "", "| Instrument | First hourly candle | Last | Candles |",
             "|---|---|---|---|"]
    for sym, (first, last, n) in sorted(info["coverage"].items()):
        lines.append(f"| {sym} | {first} | {last} | {n} |")
    closed = tr[tr["exit_reason"] != "OPEN"] if len(tr) else tr
    if closed.empty:
        return "\n".join(lines + ["", "No closed development trades: hourly history does not "
                                  "reach far enough before the validation period. Load more "
                                  "with `deploy/tradex.sh backfill --timeframes 1h --days-1h 2400`."])
    base = ss.trade_summary(closed.loc[closed["variant"] == "A", "r"]).get("expectancy_r")
    mid = info["mid"]
    lines += ["", "## 1. Results by variant", "",
              "Edge = expectancy minus variant A's (daily trend only). Hourly timing helps only if "
              "the edge is positive in both halves.", "",
              "| Variant | What it adds | Trades | Win % | Expectancy (R) | Edge vs A (R) | "
              "Profit factor | Longest losing streak | Total R | 1st half (R) | 2nd half (R) |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for v in VARIANTS:
        sub = closed[closed["variant"] == v.key]
        t = ss.trade_summary(sub["r"])
        if not t["trades"]:
            lines.append(f"| {v.key} | {v.label} | 0 | – | – | – | – | – | – | – | – |")
            continue
        edge = t["expectancy_r"] - base if base is not None else None
        first = ss.trade_summary(sub.loc[sub["entry_time"] < mid, "r"]).get("expectancy_r")
        second = ss.trade_summary(sub.loc[sub["entry_time"] >= mid, "r"]).get("expectancy_r")
        lines.append(f"| {v.key} | {v.label} | {t['trades']} | {f(t['win_pct'], 0)} | "
                     f"{f(t['expectancy_r'])} | {f(edge)} | {f(t['profit_factor'])} | "
                     f"{t['max_losing_streak']} | {f(t['total_r'], 1)} | {f(first)} | {f(second)} |")
    lines += ["", "## 2. Expectancy (R) by instrument", "",
              "| Instrument | " + " | ".join(v.key for v in VARIANTS) + " |",
              "|---|" + "---|" * len(VARIANTS)]
    for sym in sorted(closed["symbol"].unique()):
        cells = []
        for v in VARIANTS:
            t = ss.trade_summary(closed.loc[(closed["variant"] == v.key) & (closed["symbol"] == sym), "r"])
            cells.append(f"{f(t.get('expectancy_r'))} ({t['trades']})" if t["trades"] else "–")
        lines.append(f"| {sym} | " + " | ".join(cells) + " |")
    lines += ["", "Cells show expectancy and (trades).", "", "## 3. Expectancy (R) by year", "",
              "| Year | " + " | ".join(v.key for v in VARIANTS) + " |",
              "|---|" + "---|" * len(VARIANTS)]
    for yr, g in closed.groupby(closed["entry_time"].dt.year):
        cells = [f(ss.trade_summary(g.loc[g["variant"] == v.key, "r"]).get("expectancy_r"))
                 for v in VARIANTS]
        lines.append(f"| {yr} | " + " | ".join(cells) + " |")
    lines += ["", "## Notes", "",
              "- Entry is the close of the signal candle, about when an alert would arrive; a real "
              "fill would be a little worse.",
              "- Groww's hourly candles run 09:15, 10:00 … 15:00, so the first is 45 minutes and the "
              "last 30 minutes long.",
              "- A variant worth a final test beats A in both halves, in most years and on most "
              "instruments, with enough trades. A big total from a few trades is luck.", ""]
    return "\n".join(lines)


# Final test (spec §12). Proposed to the owner on 2026-10-18 *before* any validation result of
# variant D was seen. Run once; do not change these numbers after looking at the result.
FINAL_VARIANT = "D"
FINAL_BAR = {"min_trades": 100, "min_expectancy_r": 0.10, "min_profit_factor": 1.2,
             "max_symbol_share": 0.5}


def final_test(daily: dict[str, pd.DataFrame], hourly: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, dict]:
    """Variants D and A on the validation period only: closed trades entered on or after the cut."""
    keep = [v for v in VARIANTS if v.key in (FINAL_VARIANT, "A")]
    parts = []
    for sym, hf in hourly.items():
        if hf.empty or sym not in daily or daily[sym].empty:
            continue
        h = prepare(daily[sym], hf)
        parts += [trades(sym, h, v) for v in keep]
    parts = [p for p in parts if len(p)]
    tr = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if len(tr):
        tr = tr[tr["entry_time"] >= pd.Timestamp(VALIDATION_FROM)].sort_values(["variant", "entry_time"])
    closed = tr[tr["exit_reason"] != "OPEN"] if len(tr) else tr
    d = closed[closed["variant"] == FINAL_VARIANT] if len(closed) else closed
    a = closed[closed["variant"] == "A"] if len(closed) else closed
    summary = ss.trade_summary(d["r"]) if len(d) else {"trades": 0}
    base = ss.trade_summary(a["r"]) if len(a) else {"trades": 0}
    by_symbol = d.groupby("symbol")["r"].sum() if len(d) else pd.Series(dtype=float)
    total = summary.get("total_r")
    share = (by_symbol.max() / total) if total and total > 0 else None
    b = FINAL_BAR
    checks = {
        f"At least {b['min_trades']} closed trades": summary["trades"] >= b["min_trades"],
        f"Expectancy at least +{b['min_expectancy_r']}R":
            (summary.get("expectancy_r") or -9) >= b["min_expectancy_r"],
        f"Profit factor at least {b['min_profit_factor']}":
            (summary.get("profit_factor") or 0) >= b["min_profit_factor"],
        "Beats A (daily trend only) on the same period":
            summary.get("expectancy_r") is not None and base.get("expectancy_r") is not None
            and summary["expectancy_r"] > base["expectancy_r"],
        f"No instrument above {b['max_symbol_share']:.0%} of total R":
            share is not None and share <= b["max_symbol_share"],
    }
    return tr, {"summary": summary, "baseline": base, "by_symbol": by_symbol, "share": share,
                "checks": checks, "passed": all(checks.values()),
                "open": int(((tr["exit_reason"] == "OPEN") & (tr["variant"] == FINAL_VARIANT)).sum())
                if len(tr) else 0}


def final_markdown(tr: pd.DataFrame, info: dict, generated: str) -> str:
    """The one-time final test report: the pre-agreed bar, the result and the verdict."""
    f = ss._fmt
    verdict = "PASSED" if info["passed"] else "FAILED"
    lines = [f"# Final test {HOURLY_STUDY_VERSION}: variant {FINAL_VARIANT} (daily trend + hourly "
             "breakout) on held-back data", "",
             f"Generated {generated}. Validation period: {VALIDATION_FROM} onwards. Underlying "
             "hourly candles only; **no options, costs or slippage**.", "",
             f"## Verdict: **{verdict}**", "", "| Pre-agreed check | Result |", "|---|---|"]
    lines += [f"| {name} | {'pass' if ok else 'FAIL'} |" for name, ok in info["checks"].items()]
    lines += ["", "| Variant | Trades | Win % | Expectancy (R) | Profit factor | "
              "Longest losing streak | Total R |", "|---|---|---|---|---|---|---|"]
    for key, s in ((FINAL_VARIANT, info["summary"]), ("A", info["baseline"])):
        if s["trades"]:
            lines.append(f"| {key} | {s['trades']} | {f(s['win_pct'], 0)} | {f(s['expectancy_r'])} | "
                         f"{f(s['profit_factor'])} | {s['max_losing_streak']} | {f(s['total_r'], 1)} |")
        else:
            lines.append(f"| {key} | 0 | – | – | – | – | – |")
    share = info["share"]
    lines += ["", f"Biggest instrument share of D's total R: "
              f"{f(share * 100 if share is not None else None, 0, '%')}. "
              f"D trades still open: {info['open']}."]
    if len(info["by_symbol"]):
        closed = tr[(tr["exit_reason"] != "OPEN") & (tr["variant"] == FINAL_VARIANT)]
        lines += ["", "| Instrument | Trades | Total R |", "|---|---|---|"]
        for sym, total in info["by_symbol"].sort_values(ascending=False).items():
            lines.append(f"| {sym} | {int((closed['symbol'] == sym).sum())} | {f(total, 1)} |")
    lines += ["", "This test is run once. If it failed, the rules are not changed and re-tested on "
              "the same data; that would be fitting to luck.", ""]
    return "\n".join(lines)
