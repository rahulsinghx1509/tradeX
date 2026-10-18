"""Backtest of the four core direction readings (strategy spec §5) on the underlying.

This is a *signal study*, the first step of the backtest plan (spec §12). It answers: when all
four readings agree, what did the underlying do next? It does not trade options, so it says
nothing yet about spreads, premiums, costs or slippage.

Timing follows spec §2, with no look-ahead:
- The readings for day D use only completed candles up to and including D's close
  (EMA / RSI / ADX / ATR only ever look backwards; the weekly reading uses the last week
  that had ended by D).
- Entry is at the close of day D+1. The spec enters on D+1 between 09:45 and 11:30, but Groww
  sends no 09:15 open for stocks and 1h history is short, so D+1's close is the first price
  that is available for every instrument and every year.
- Only the first day of a run of identical signals counts as a new signal ("episode"),
  otherwise one long trend would be counted dozens of times.

Two outcomes are measured for each episode:
1. Forward return after 5 / 10 / 20 trading days, in % and signed so that a gain means the
   signal was right.
2. A simple trade on the underlying using the spec's §9 levels: stop = lowest low of the last
   10 sessions − 0.25 ATR, kept between 1 and 2 ATR from entry; target = entry + 2R; exit at the
   close after 10 trading days otherwise. If a day touches both stop and target, the stop is
   assumed to come first. The result is in R (1R = the distance to the stop).

Episodes from the first 60% of the period are "development", the rest "validation" (spec §12).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from app.services import indicators as ind
from app.services.metrics import ADX_TREND_MIN, MIN_DAILY_BARS, MIN_WEEKLY_BARS

STUDY_VERSION = "SS-1.0"
HORIZONS = (5, 10, 20)
DEV_SHARE = 0.6
STOP_LOOKBACK = 10
STOP_BUFFER_ATR = 0.25
STOP_MIN_ATR, STOP_MAX_ATR = 1.0, 2.0
TARGET_R = 2.0
MAX_HOLD_DAYS = 10
CHECKS = ("D1", "D2", "D3", "D4")


def _vote(up: pd.Series, down: pd.Series, known: pd.Series) -> pd.Series:
    """+1 / -1 / 0 per day, NaN where the inputs are not available yet."""
    out = pd.Series(np.where(up, 1.0, np.where(down, -1.0, 0.0)), index=up.index)
    return out.where(known)


def daily_readings(df: pd.DataFrame) -> pd.DataFrame:
    """D1-D4 (+1 up, -1 down, 0 neutral) and the combined signal for every day.

    `df` has high, low, close indexed by trading date (oldest first). Days before the warm-up
    (MIN_DAILY_BARS daily, MIN_WEEKLY_BARS completed weeks) get signal NaN.
    """
    close, high, low = df["close"], df["high"], df["low"]
    ema20, ema50 = ind.ema(close, 20), ind.ema(close, 50)
    adx, plus_di, minus_di = ind.adx(high, low, close, 14)
    rsi = ind.rsi(close, 14)
    out = pd.DataFrame(index=df.index)
    out["close"], out["high"], out["low"] = close, high, low
    out["atr"] = ind.atr(high, low, close, 14)
    out["D1"] = _vote((close > ema50) & (ema20 > ema50), (close < ema50) & (ema20 < ema50),
                      ema50.notna())
    out["D3"] = _vote((adx >= ADX_TREND_MIN) & (plus_di > minus_di),
                      (adx >= ADX_TREND_MIN) & (minus_di > plus_di), adx.notna())
    out["D4"] = _vote((rsi >= 50) & (rsi <= 70), (rsi >= 30) & (rsi < 50), rsi.notna())

    # Weekly: a week counts from the day it ends (its Friday), never earlier.
    weekly = close.groupby(close.index.to_period("W-FRI")).last()
    w_ema = ind.ema(weekly, 20)
    wk = pd.DataFrame({
        "available_from": [p.end_time.normalize() for p in weekly.index],
        "D2": _vote(weekly > w_ema, weekly < w_ema, w_ema.notna()).to_numpy(),
        "weeks": np.arange(1, len(weekly) + 1),
    })
    days = pd.DataFrame({"day": out.index})
    joined = pd.merge_asof(days, wk, left_on="day", right_on="available_from", direction="backward")
    out["D2"] = joined["D2"].to_numpy()
    out["weeks"] = joined["weeks"].fillna(0).to_numpy()

    warm = (np.arange(len(out)) >= MIN_DAILY_BARS - 1) & (out["weeks"] >= MIN_WEEKLY_BARS)
    votes = out[list(CHECKS)]
    known = votes.notna().all(axis=1) & warm
    signal = np.where((votes == 1).all(axis=1), 1.0, np.where((votes == -1).all(axis=1), -1.0, 0.0))
    out["signal"] = pd.Series(signal, index=out.index).where(known)
    return out


def _trade(r: pd.DataFrame, i: int, direction: int) -> dict | None:
    """Simulate one trade entered at the close of day i+1 (see module docstring)."""
    e = i + 1
    if e >= len(r) or pd.isna(r["atr"].iat[i]):
        return None
    entry, atr = r["close"].iat[e], r["atr"].iat[i]
    window = r.iloc[max(0, i - STOP_LOOKBACK + 1): i + 1]
    if direction == 1:
        dist = entry - (window["low"].min() - STOP_BUFFER_ATR * atr)
    else:
        dist = (window["high"].max() + STOP_BUFFER_ATR * atr) - entry
    dist = min(max(dist, STOP_MIN_ATR * atr), STOP_MAX_ATR * atr)
    stop = entry - direction * dist
    target = entry + direction * TARGET_R * dist
    last = min(e + MAX_HOLD_DAYS, len(r) - 1)
    if last == e:
        return None  # no days after entry yet
    mfe = mae = 0.0
    for j in range(e + 1, last + 1):
        hi, lo = r["high"].iat[j], r["low"].iat[j]
        fav = (hi - entry) if direction == 1 else (entry - lo)
        adv = (entry - lo) if direction == 1 else (hi - entry)
        mfe, mae = max(mfe, fav / dist), max(mae, adv / dist)
        hit_stop = lo <= stop if direction == 1 else hi >= stop
        hit_target = hi >= target if direction == 1 else lo <= target
        if hit_stop:  # stop first when both are touched the same day (conservative)
            return {"exit_day": r.index[j], "exit_reason": "STOP", "r": -1.0,
                    "mfe_r": mfe, "mae_r": mae}
        if hit_target:
            return {"exit_day": r.index[j], "exit_reason": "TARGET", "r": TARGET_R,
                    "mfe_r": mfe, "mae_r": mae}
    complete = last == e + MAX_HOLD_DAYS
    pnl = direction * (r["close"].iat[last] - entry) / dist
    return {"exit_day": r.index[last], "exit_reason": "TIME" if complete else "OPEN",
            "r": pnl, "mfe_r": mfe, "mae_r": mae}


def episodes(symbol: str, df: pd.DataFrame) -> pd.DataFrame:
    """One row per new LONG / SHORT signal for one instrument, with its outcomes."""
    r = daily_readings(df)
    sig = r["signal"]
    onset = sig.notna() & (sig != 0) & (sig != sig.shift(1))
    rows = []
    for i in np.flatnonzero(onset.to_numpy()):
        d = int(sig.iat[i])
        row = {"symbol": symbol, "signal_day": r.index[i].date(),
               "direction": "LONG" if d == 1 else "SHORT"}
        e = i + 1
        row["entry_day"] = r.index[e].date() if e < len(r) else None
        row["entry"] = r["close"].iat[e] if e < len(r) else None
        for h in HORIZONS:
            x = e + h
            row[f"ret_{h}d_pct"] = (d * (r["close"].iat[x] / r["close"].iat[e] - 1) * 100
                                    if x < len(r) else None)
        t = _trade(r, i, d)
        row.update({"exit_day": t["exit_day"].date(), "exit_reason": t["exit_reason"], "r": t["r"],
                    "mfe_r": t["mfe_r"], "mae_r": t["mae_r"]} if t else
                   {"exit_day": None, "exit_reason": "OPEN", "r": None, "mfe_r": None, "mae_r": None})
        rows.append(row)
    return pd.DataFrame(rows)


def baseline(symbol: str, df: pd.DataFrame) -> pd.DataFrame:
    """Every eligible day's forward returns (from D+1 close), to compare signals against."""
    r = daily_readings(df)
    out = pd.DataFrame({"symbol": symbol, "day": r.index.date, "eligible": r["signal"].notna()})
    entry = r["close"].shift(-1)
    for h in HORIZONS:
        out[f"ret_{h}d_pct"] = ((r["close"].shift(-1 - h) / entry - 1) * 100).to_numpy()
    return out[out["eligible"]].drop(columns="eligible")


def reading_mix(df: pd.DataFrame) -> dict[str, float]:
    """Share of eligible days that were LONG / SHORT / NO TRADE."""
    sig = daily_readings(df)["signal"].dropna()
    n = len(sig)
    if not n:
        return {"days": 0}
    return {"days": n, "long_pct": (sig == 1).mean() * 100, "short_pct": (sig == -1).mean() * 100,
            "no_trade_pct": (sig == 0).mean() * 100}


def split_date(all_days: list[date]) -> date | None:
    """The first validation day: 60% of the way through the eligible calendar."""
    if not all_days:
        return None
    lo, hi = min(all_days), max(all_days)
    return lo + (hi - lo) * DEV_SHARE


@dataclass
class Stats:
    n: int
    hit_pct: float | None
    mean: float | None
    median: float | None
    t: float | None


def stats(values: pd.Series) -> Stats:
    """Count, % positive, mean, median and t-statistic (mean / standard error)."""
    v = pd.to_numeric(values, errors="coerce").dropna()
    n = len(v)
    if n == 0:
        return Stats(0, None, None, None, None)
    sd = v.std(ddof=1) if n > 1 else float("nan")
    t = v.mean() / (sd / math.sqrt(n)) if n > 1 and sd > 0 else None
    return Stats(n, (v > 0).mean() * 100, v.mean(), v.median(), t)


def trade_summary(r_values: pd.Series) -> dict[str, float | int | None]:
    """Expectancy, win rate, profit factor and longest losing streak, all in R."""
    v = pd.to_numeric(r_values, errors="coerce").dropna()
    if v.empty:
        return {"trades": 0}
    wins, losses = v[v > 0], v[v <= 0]
    streak = best = 0
    for x in v:
        streak = streak + 1 if x <= 0 else 0
        best = max(best, streak)
    return {"trades": len(v), "win_pct": len(wins) / len(v) * 100, "expectancy_r": v.mean(),
            "avg_win_r": wins.mean() if len(wins) else None,
            "avg_loss_r": losses.mean() if len(losses) else None,
            "profit_factor": wins.sum() / -losses.sum() if losses.sum() < 0 else None,
            "max_losing_streak": best, "total_r": v.sum()}


def _fmt(x, digits: int = 2, suffix: str = "") -> str:
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{digits}f}{suffix}"


def run(frames: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Episodes and baseline days for every instrument, tagged development / validation."""
    eps = [episodes(s, df) for s, df in frames.items() if len(df)]
    base = [baseline(s, df) for s, df in frames.items() if len(df)]
    ep = pd.concat(eps, ignore_index=True) if eps else pd.DataFrame()
    bl = pd.concat(base, ignore_index=True) if base else pd.DataFrame()
    cut = split_date(list(bl["day"])) if len(bl) else None
    for t, col in ((ep, "signal_day"), (bl, "day")):
        if len(t):
            t["period"] = np.where(t[col] < cut, "development", "validation")
    if len(ep):
        ep.sort_values(["entry_day", "symbol"], inplace=True, na_position="last")
    mix = {s: reading_mix(df) for s, df in frames.items()}
    return ep, bl, {"split_date": cut, "mix": mix}


def to_markdown(ep: pd.DataFrame, bl: pd.DataFrame, info: dict, generated: str) -> str:
    """The study report, written for reading on GitHub or in a terminal."""
    lines = [f"# Signal study {STUDY_VERSION}: the four core readings (D1–D4)", "",
             f"Generated {generated}. Underlying daily candles only; **no options, costs or slippage**.",
             "Past results are not proof of future results.", ""]
    if ep.empty:
        return "\n".join(lines + ["No signals: not enough history after the warm-up."])
    cut = info["split_date"]
    lines += [f"Development: signals before {cut}. Validation: {cut} onwards (untouched, spec §12).",
              "Entry = close of the day after the signal. Only the first day of each signal run counts.",
              ""]

    lines += ["## 1. Forward return after the signal (% move in the signal's favour)", "",
              "Baseline = the same move from *every* eligible day, as if you went long blindly. "
              "A signal is only useful if it beats the baseline (shorts beat its negative).", "",
              "| Period | Direction | Days | Signals | Right % | Avg % | Median % | t | "
              "Baseline right % | Baseline avg % |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for period in ("development", "validation"):
        for direction in ("LONG", "SHORT"):
            sub = ep[(ep["period"] == period) & (ep["direction"] == direction)]
            for h in HORIZONS:
                s = stats(sub[f"ret_{h}d_pct"])
                b = bl.loc[bl["period"] == period, f"ret_{h}d_pct"].dropna()
                b = b if direction == "LONG" else -b
                bs = stats(b)
                lines.append(f"| {period} | {direction} | {h} | {s.n} | {_fmt(s.hit_pct, 0)} | "
                             f"{_fmt(s.mean)} | {_fmt(s.median)} | {_fmt(s.t, 1)} | "
                             f"{_fmt(bs.hit_pct, 0)} | {_fmt(bs.mean)} |")
    lines += ["", "*t* above about 2 means the average is unlikely to be luck; below 1 it is noise. "
              "Signals on different instruments often fire together (banks move together), so the "
              "real evidence is weaker than the count suggests.", ""]

    closed = ep[ep["exit_reason"] != "OPEN"]
    lines += ["## 2. Simple trade on the underlying (spec §9 stop, 2R target, 10-day exit)", "",
              "| Period | Direction | Trades | Win % | Expectancy (R) | Avg win (R) | Avg loss (R) | "
              "Profit factor | Longest losing streak | Total R |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for period in ("development", "validation"):
        for direction in ("LONG", "SHORT", "BOTH"):
            sub = closed[closed["period"] == period]
            if direction != "BOTH":
                sub = sub[sub["direction"] == direction]
            t = trade_summary(sub["r"])
            if not t["trades"]:
                lines.append(f"| {period} | {direction} | 0 | – | – | – | – | – | – | – |")
                continue
            lines.append(f"| {period} | {direction} | {t['trades']} | {_fmt(t['win_pct'], 0)} | "
                         f"{_fmt(t['expectancy_r'])} | {_fmt(t['avg_win_r'])} | {_fmt(t['avg_loss_r'])} | "
                         f"{_fmt(t['profit_factor'])} | {t['max_losing_streak']} | {_fmt(t['total_r'], 1)} |")
    reasons = closed.groupby(["period", "exit_reason"]).size().unstack(fill_value=0)
    lines += ["", "Exit reasons: " + "; ".join(
        f"{p}: " + ", ".join(f"{k} {v}" for k, v in reasons.loc[p].items()) for p in reasons.index), ""]

    lines += ["## 3. By instrument (all periods)", "",
              "| Instrument | Days after warm-up | LONG days % | SHORT days % | Signals | "
              "10-day right % | 10-day avg % | Trades | Expectancy (R) |",
              "|---|---|---|---|---|---|---|---|---|"]
    for sym, mix in info["mix"].items():
        sub = ep[ep["symbol"] == sym]
        s = stats(sub["ret_10d_pct"]) if len(sub) else Stats(0, None, None, None, None)
        t = trade_summary(closed.loc[closed["symbol"] == sym, "r"])
        lines.append(f"| {sym} | {mix.get('days', 0)} | {_fmt(mix.get('long_pct'), 0)} | "
                     f"{_fmt(mix.get('short_pct'), 0)} | {len(sub)} | {_fmt(s.hit_pct, 0)} | "
                     f"{_fmt(s.mean)} | {t['trades']} | {_fmt(t.get('expectancy_r'))} |")
    lines += ["", "## 4. Limits of this study", "",
              "- Underlying only. Real trades are option spreads: premiums, time decay, costs and "
              "slippage will lower these numbers.",
              "- Entry at the next day's close, not 09:45–11:30 as in the spec (no reliable open).",
              "- The gates (G1–G17) and the confirmation score (S1–S9) are not applied yet; they "
              "only remove trades, so this is the raw edge of the four readings.",
              "- Daily candles cannot show whether the stop or the target came first on a day "
              "that touched both; the stop is assumed (worst case).",
              f"- Stocks have about 5 years of history and FINNIFTY less; after the {MIN_DAILY_BARS}-day "
              "warm-up that leaves few independent signals, so treat small groups with care.", ""]
    return "\n".join(lines)
