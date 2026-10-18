"""Long-only variants of the four core readings, scored on the development period only.

The signal study (SS-1.0) found shorts lost money in both periods and longs had a small but
consistent edge. This module tests a few changes to the LONG side, one at a time and combined,
so the best one can be chosen *before* it is tested on data it has not seen (spec §12).

Every variant uses the same timing as the signal study: readings from completed candles up to
the signal day's close, entry at the next day's close, and the §9 starting stop (lowest low of
10 sessions − 0.25 ATR, kept between 1 and 2 ATR). The building blocks:

- Entry "onset": the first day all four readings turn up (as in the study).
- Entry "pullback": while all four readings are up, a day whose low touches EMA20 after the
  previous day stayed above it. Buys a dip inside an uptrend instead of the breakout day.
- Exit "fixed": 2R target, otherwise out at the close after 10 days (as in the study).
- Exit "trail": no target. After each close the stop moves up to the highest close since entry
  − 3 ATR (a chandelier stop) and never moves down; out at the close after 40 days at most.
- Strength filter: take the trade only if the instrument rose more than NIFTY over the last
  20 days (the relative strength shown on the dashboard). NIFTY itself skips this filter.
  Missing NIFTY data means no trade (missing stays missing).

One trade per instrument at a time: a signal while a trade is still open is skipped.
Validation-period trades are not reported; that period stays held back for the final check.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.services import indicators as ind
from app.services import signal_study as ss
from app.services.metrics import RS_DAYS

VARIANTS_VERSION = "LV-1.0"
TRAIL_ATR = 3.0
TRAIL_MAX_HOLD = 40
REFERENCE = "NIFTY"


@dataclass(frozen=True)
class Variant:
    key: str
    entry: str  # "onset" or "pullback"
    exit: str  # "fixed" or "trail"
    strength: bool
    label: str


VARIANTS = (
    Variant("A", "onset", "fixed", False, "Current rules, long only"),
    Variant("B", "pullback", "fixed", False, "Pullback entry"),
    Variant("C", "onset", "trail", False, "Trailing stop"),
    Variant("D", "onset", "fixed", True, "Strength filter"),
    Variant("E", "pullback", "trail", False, "Pullback + trailing stop"),
    Variant("F", "pullback", "trail", True, "Pullback + trailing stop + strength"),
    Variant("G", "onset", "trail", True, "Trailing stop + strength"),
)


def prepare(df: pd.DataFrame, reference: pd.DataFrame | None) -> pd.DataFrame:
    """Readings plus EMA20 and the 20-day strength versus NIFTY for each day."""
    r = ss.daily_readings(df)
    r["ema20"] = ind.ema(r["close"], 20)
    mine = r["close"] / r["close"].shift(RS_DAYS)
    if reference is not None and len(reference):
        ref = reference["close"].reindex(r.index)  # no fill: a missing NIFTY day stays missing
        r["strength"] = (mine - ref / ref.shift(RS_DAYS)) * 100
    else:
        r["strength"] = np.nan
    return r


def signal_days(r: pd.DataFrame, entry: str) -> np.ndarray:
    """Row numbers of the days that trigger an entry, before filters and overlap."""
    up = (r["signal"] == 1).to_numpy()
    if entry == "onset":
        prev = np.r_[False, up[:-1]]
        return np.flatnonzero(up & ~prev)
    touch = (r["low"] <= r["ema20"]).to_numpy()
    above_before = np.r_[False, (r["low"] > r["ema20"]).to_numpy()[:-1]]
    return np.flatnonzero(up & touch & above_before)


def _trail_trade(r: pd.DataFrame, i: int) -> dict | None:
    """Long entered at the close of day i+1 with the §9 stop, then a 3 ATR chandelier trail."""
    e = i + 1
    if e >= len(r) or pd.isna(r["atr"].iat[i]):
        return None
    entry, atr = r["close"].iat[e], r["atr"].iat[i]
    window = r.iloc[max(0, i - ss.STOP_LOOKBACK + 1): i + 1]
    dist = entry - (window["low"].min() - ss.STOP_BUFFER_ATR * atr)
    dist = min(max(dist, ss.STOP_MIN_ATR * atr), ss.STOP_MAX_ATR * atr)
    stop = entry - dist
    best_close = entry
    last = min(e + TRAIL_MAX_HOLD, len(r) - 1)
    if last == e:
        return None
    mfe = mae = 0.0
    for j in range(e + 1, last + 1):
        hi, lo, cl = r["high"].iat[j], r["low"].iat[j], r["close"].iat[j]
        mfe, mae = max(mfe, (hi - entry) / dist), max(mae, (entry - lo) / dist)
        if lo <= stop:  # filled at the stop; a gap below it would be worse in real life
            return {"exit_day": r.index[j], "exit_reason": "STOP", "r": (stop - entry) / dist,
                    "mfe_r": mfe, "mae_r": mae, "days": j - e}
        best_close = max(best_close, cl)
        atr_j = r["atr"].iat[j]
        if not pd.isna(atr_j):  # known at today's close, used from tomorrow
            stop = max(stop, best_close - TRAIL_ATR * atr_j)
    complete = last == e + TRAIL_MAX_HOLD
    return {"exit_day": r.index[last], "exit_reason": "TIME" if complete else "OPEN",
            "r": (r["close"].iat[last] - entry) / dist, "mfe_r": mfe, "mae_r": mae,
            "days": last - e}


def trades(symbol: str, r: pd.DataFrame, v: Variant) -> pd.DataFrame:
    """Every trade of one variant on one instrument (all periods)."""
    rows, busy_until = [], -1
    for i in signal_days(r, v.entry):
        if i <= busy_until:
            continue  # still in the previous trade
        if v.strength and symbol != REFERENCE:
            s = r["strength"].iat[i]
            if pd.isna(s) or s <= 0:
                continue
        if v.exit == "trail":
            t = _trail_trade(r, i)
        else:
            t = ss._trade(r, i, 1)
            if t:
                t["days"] = r.index.get_loc(t["exit_day"]) - (i + 1)
        if t is None:
            continue
        busy_until = r.index.get_loc(t["exit_day"])
        rows.append({"variant": v.key, "symbol": symbol, "signal_day": r.index[i].date(),
                     "entry_day": r.index[i + 1].date(), "entry": r["close"].iat[i + 1],
                     "exit_day": t["exit_day"].date(), "exit_reason": t["exit_reason"],
                     "r": t["r"], "mfe_r": t["mfe_r"], "mae_r": t["mae_r"], "days": t["days"]})
    return pd.DataFrame(rows)


def run(frames: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, dict]:
    """Development-period trades for every variant, plus the split dates used."""
    frames = {s: df for s, df in frames.items() if len(df)}
    reference = frames.get(REFERENCE)
    prepared = {s: prepare(df, reference) for s, df in frames.items()}
    days = [d for s, df in frames.items() for d in ss.baseline(s, df)["day"]]
    cut = ss.split_date(days)
    parts = [trades(s, r, v) for v in VARIANTS for s, r in prepared.items()]
    parts = [p for p in parts if len(p)]
    tr = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if len(tr):
        # Keep trades that both start and finish before validation begins.
        tr = tr[(tr["signal_day"] < cut) & (tr["exit_day"] < cut)].copy()
        tr.sort_values(["variant", "entry_day", "symbol"], inplace=True)
    dev_days = sorted({d for d in days if cut and d < cut})
    mid = dev_days[len(dev_days) // 2] if dev_days else None
    return tr, {"split_date": cut, "dev_start": dev_days[0] if dev_days else None, "dev_mid": mid}


def to_markdown(tr: pd.DataFrame, info: dict, generated: str) -> str:
    """Side-by-side comparison of the variants on development data."""
    f = ss._fmt
    lines = [f"# Long-only variants {VARIANTS_VERSION} (development period only)", "",
             f"Generated {generated}. Underlying daily candles only; **no options, costs or slippage**.",
             f"Development: {info['dev_start']} to before {info['split_date']}. "
             "Validation is held back and not shown.", ""]
    closed = tr[tr["exit_reason"] != "OPEN"] if len(tr) else tr
    if closed.empty:
        return "\n".join(lines + ["No closed trades: not enough history after the warm-up."])
    mid = info["dev_mid"]
    lines += ["| Variant | What changes | Trades | Win % | Expectancy (R) | Profit factor | "
              "Longest losing streak | Total R | Avg days held | 1st half (R) | 2nd half (R) |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for v in VARIANTS:
        sub = closed[closed["variant"] == v.key]
        t = ss.trade_summary(sub["r"])
        if not t["trades"]:
            lines.append(f"| {v.key} | {v.label} | 0 | – | – | – | – | – | – | – | – |")
            continue
        first = ss.trade_summary(sub.loc[sub["signal_day"] < mid, "r"]).get("expectancy_r")
        second = ss.trade_summary(sub.loc[sub["signal_day"] >= mid, "r"]).get("expectancy_r")
        lines.append(f"| {v.key} | {v.label} | {t['trades']} | {f(t['win_pct'], 0)} | "
                     f"{f(t['expectancy_r'])} | {f(t['profit_factor'])} | {t['max_losing_streak']} | "
                     f"{f(t['total_r'], 1)} | {f(sub['days'].mean(), 1)} | {f(first)} | {f(second)} |")
    lines += ["", "A variant is worth carrying forward only if it is positive in **both** halves "
              "and has enough trades; a big total from a handful of trades is luck until shown "
              "otherwise.", "", "## Expectancy (R) by instrument", ""]
    syms = sorted(closed["symbol"].unique())
    lines += ["| Instrument | " + " | ".join(v.key for v in VARIANTS) + " |",
              "|---|" + "---|" * len(VARIANTS)]
    for sym in syms:
        cells = []
        for v in VARIANTS:
            t = ss.trade_summary(closed.loc[(closed["variant"] == v.key) & (closed["symbol"] == sym), "r"])
            cells.append(f"{f(t.get('expectancy_r'))} ({t['trades']})" if t["trades"] else "–")
        lines.append(f"| {sym} | " + " | ".join(cells) + " |")
    lines += ["", "Cells show expectancy and (trades).", "", "## Notes", "",
              "- Entry is the next day's close; stops are assumed filled at the stop price, though "
              "a gap below it would lose more.",
              "- Trades still open when validation begins are left out, so long-hold variants "
              "lose a few trades at the end.",
              f"- Trailing stop = highest close since entry − {TRAIL_ATR:g} ATR, at most "
              f"{TRAIL_MAX_HOLD} days. Strength = 20-day return minus NIFTY's.", ""]
    return "\n".join(lines)


# Final test (spec §12). Agreed with the owner on 2026-10-18 *before* any validation result of
# variant C was seen. Run once; do not change these numbers after looking at the result.
FINAL_VARIANT = "C"
FINAL_BAR = {"min_trades": 30, "min_expectancy_r": 0.15, "min_profit_factor": 1.3,
             "max_symbol_share": 0.5}


def final_test(frames: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, dict]:
    """Variant C on the validation period only: closed trades that started on or after the split."""
    frames = {s: df for s, df in frames.items() if len(df)}
    reference = frames.get(REFERENCE)
    v = next(x for x in VARIANTS if x.key == FINAL_VARIANT)
    days = [d for s, df in frames.items() for d in ss.baseline(s, df)["day"]]
    cut = ss.split_date(days)
    parts = [trades(s, prepare(df, reference), v) for s, df in frames.items()]
    parts = [p for p in parts if len(p)]
    tr = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if len(tr):
        tr = tr[tr["signal_day"] >= cut].sort_values(["entry_day", "symbol"])
    closed = tr[tr["exit_reason"] != "OPEN"] if len(tr) else tr
    summary = ss.trade_summary(closed["r"]) if len(closed) else {"trades": 0}
    by_symbol = closed.groupby("symbol")["r"].sum() if len(closed) else pd.Series(dtype=float)
    total = summary.get("total_r")
    share = (by_symbol.max() / total) if total and total > 0 else None
    b = FINAL_BAR
    checks = {
        f"At least {b['min_trades']} closed trades": summary["trades"] >= b["min_trades"],
        f"Expectancy at least +{b['min_expectancy_r']}R":
            (summary.get("expectancy_r") or -9) >= b["min_expectancy_r"],
        f"Profit factor at least {b['min_profit_factor']}":
            (summary.get("profit_factor") or 0) >= b["min_profit_factor"],
        f"No instrument above {b['max_symbol_share']:.0%} of total R":
            share is not None and share <= b["max_symbol_share"],
    }
    return tr, {"split_date": cut, "summary": summary, "by_symbol": by_symbol, "share": share,
                "checks": checks, "passed": all(checks.values()),
                "open": int((tr["exit_reason"] == "OPEN").sum()) if len(tr) else 0}


def final_markdown(tr: pd.DataFrame, info: dict, generated: str) -> str:
    """The one-time final test report: the pre-agreed bar, the result and the verdict."""
    f, s = ss._fmt, info["summary"]
    verdict = "PASSED" if info["passed"] else "FAILED"
    lines = [f"# Final test {VARIANTS_VERSION}: variant {FINAL_VARIANT} (trailing stop) on held-back data",
             "", f"Generated {generated}. Validation period: {info['split_date']} onwards. "
             "Underlying daily candles only; **no options, costs or slippage**.", "",
             f"## Verdict: **{verdict}**", "", "| Pre-agreed check | Result |", "|---|---|"]
    lines += [f"| {name} | {'pass' if ok else 'FAIL'} |" for name, ok in info["checks"].items()]
    lines += ["", "| Trades | Win % | Expectancy (R) | Profit factor | Longest losing streak | "
              "Total R | Biggest instrument share | Still open |", "|---|---|---|---|---|---|---|---|"]
    if s["trades"]:
        lines.append(f"| {s['trades']} | {f(s['win_pct'], 0)} | {f(s['expectancy_r'])} | "
                     f"{f(s['profit_factor'])} | {s['max_losing_streak']} | {f(s['total_r'], 1)} | "
                     f"{f(info['share'] * 100 if info['share'] is not None else None, 0, '%')} | "
                     f"{info['open']} |")
    else:
        lines.append(f"| 0 | – | – | – | – | – | – | {info['open']} |")
    if len(info["by_symbol"]):
        lines += ["", "| Instrument | Trades | Total R |", "|---|---|---|"]
        closed = tr[tr["exit_reason"] != "OPEN"]
        for sym, total in info["by_symbol"].sort_values(ascending=False).items():
            lines.append(f"| {sym} | {int((closed['symbol'] == sym).sum())} | {f(total, 1)} |")
    lines += ["", "This test is run once. If it failed, the rules are not changed and re-tested on "
              "the same data; that would be fitting to luck.", ""]
    return "\n".join(lines)
