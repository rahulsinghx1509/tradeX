"""Study VS-1.0: selling protected monthly options, on NSE's real option closes (fo_daily).

Why: OS-1.0 found options were usually overpriced (realised volatility below ATM IV on 59–74%
of development days) and one timing metric survived (call_wall_pct: a far-away biggest call-OI
strike came before calmer months than options priced). This study turns that into trades
with a fixed, limited worst case, and asks whether they made money after costs.

Trade (per underlying, once per monthly expiry E), all decided at a day's close:
- Entry: the first trading day after the previous monthly expiry, if E is 20+ days away.
- Structure, both with a long "wing" on each side so the worst loss is known at entry:
  - fly:    sell the ATM call and put, buy the call and put 1 standard deviation (SD) away
  - condor: sell the call and put 0.5 SD away, buy the ones 1.5 SD away
  SD = ATM IV × √(days to E / 365) × forward; strikes are the nearest stored ones.
  All four legs must have traded that day (a contract with no trades has a stale close).
- Exit: held to expiry, settled on the underlying's close on E (NSE settles at expiry on the
  underlying's closing price). No stop, no early exit: the wings already cap the loss.
- Result in R: profit / worst possible loss (wing width − credit). −1R is the worst case;
  a month that ends calm keeps the whole credit (credit / worst loss, e.g. +0.4R).

Variants scored on development data only (expiry before VALIDATION_FROM):
  A every month; B skip when the VIX-jump warning (VJ-1.0) was on the day before entry;
  C = B and only names whose call_wall_pct is in the top half of the 12 that day.

Costs (estimates, NOT_VERIFIED against a contract note): per leg, slippage of 1% of the
premium for indices and 3% for stocks (at least 0.05), plus charges of 0.15% of premium
turnover (STT on the sold legs, exchange fees, GST, stamp duty) and ₹20 brokerage per leg
spread over the lot size. Held to expiry, so no exit costs (STT on exercised in-the-money
long legs is ignored; it is small next to the wing's value).

Also here: check that call_wall_pct is not just repeating the IV level or the last month's
move (`residual_check`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from app.services import feature_screen as fs
from app.services import vix_warning

STUDY_VERSION = "VS-1.0"
MIN_ENTRY_DTE = 20
STRUCTURES = {"fly": (0.0, 1.0), "condor": (0.5, 1.5)}  # (short, long) distance in SD
INDICES = {"NIFTY", "BANKNIFTY", "FINNIFTY"}
SLIPPAGE = {"index": 0.01, "stock": 0.03}
MIN_SLIPPAGE = 0.05
CHARGES = 0.0015
BROKERAGE_PER_LEG = 20.0
VARIANTS = {"A": "every month", "B": "skip VIX-jump warning days",
            "C": "skip warning days, call wall in the top half"}


@dataclass
class Trade:
    symbol: str
    structure: str
    entry: date
    expiry: date
    forward: float
    short_call: float
    short_put: float
    long_call: float
    long_put: float
    credit: float      # after entry costs, per unit
    max_loss: float    # per unit
    settle: float
    pnl: float         # per unit
    r: float
    vix_warning: bool = False
    call_wall_top: bool | None = None


def _px(legs: pd.DataFrame, kind: str, strike: float) -> float | None:
    r = legs[(legs.instrument == kind) & (legs.strike == strike)]
    if r.empty:
        return None
    r = r.iloc[0]
    ok = (r["volume"] or 0) > 0 and np.isfinite(r["close"]) and r["close"] > 0
    return float(r["close"]) if ok else None


def _nearest(strikes: np.ndarray, target: float) -> float | None:
    return None if len(strikes) == 0 else float(strikes[np.argmin(np.abs(strikes - target))])


def entry_days(frame: pd.DataFrame) -> list[tuple[date, date]]:
    """(entry day, expiry) pairs: first trading day after each monthly expiry, for the next one."""
    futs = sorted(frame.loc[frame.instrument == "FUT", "expiry_date"].unique())
    days = np.array(sorted(frame.trade_date.unique()))
    out = []
    for prev, nxt in zip(futs, futs[1:], strict=False):
        after = days[days > prev]
        if len(after) and (nxt - after[0]).days >= MIN_ENTRY_DTE:
            out.append((after[0], nxt))
    return out


def open_trade(day: pd.DataFrame, symbol: str, entry: date, expiry: date, atm_iv: float,
               structure: str, lot: float | None) -> dict | None:
    """Strikes, prices and credit for one structure, or None when any leg did not trade."""
    fut = day[(day.instrument == "FUT") & (day.expiry_date == expiry)]
    if fut.empty or not np.isfinite(atm_iv) or atm_iv <= 0:
        return None
    f = fut.iloc[0]
    fwd = f["close"] if (f["volume"] or 0) > 0 and np.isfinite(f["close"]) else f["settle"]
    if not np.isfinite(fwd) or fwd <= 0:
        return None
    sd = atm_iv / 100 * math.sqrt((expiry - entry).days / 365) * fwd
    legs = day[(day.instrument != "FUT") & (day.expiry_date == expiry)]
    calls = np.sort(legs.loc[legs.instrument == "CE", "strike"].dropna().unique())
    puts = np.sort(legs.loc[legs.instrument == "PE", "strike"].dropna().unique())
    near, far = STRUCTURES[structure]
    sc, sp = _nearest(calls, fwd + near * sd), _nearest(puts, fwd - near * sd)
    lc, lp = _nearest(calls, fwd + far * sd), _nearest(puts, fwd - far * sd)
    if None in (sc, sp, lc, lp) or not (lc > sc and lp < sp):
        return None  # wings collapsed onto the short strikes: not enough strikes stored
    prices = [_px(legs, "CE", sc), _px(legs, "PE", sp), _px(legs, "CE", lc), _px(legs, "PE", lp)]
    if any(p is None for p in prices):
        return None
    slip = SLIPPAGE["index" if symbol in INDICES else "stock"]
    costs = sum(max(MIN_SLIPPAGE, slip * p) + CHARGES * p for p in prices)
    if lot and lot > 0:
        costs += 4 * BROKERAGE_PER_LEG / lot
    credit = prices[0] + prices[1] - prices[2] - prices[3] - costs
    width = max(lc - sc, sp - lp)
    if credit <= 0 or credit >= width:
        return None
    return dict(forward=float(fwd), short_call=sc, short_put=sp, long_call=lc, long_put=lp,
                credit=float(credit), max_loss=float(width - credit))


def settle_value(t: dict, s: float) -> float:
    """What the four legs pay the seller at expiry (≤ 0)."""
    return (-max(s - t["short_call"], 0) + max(s - t["long_call"], 0)
            - max(t["short_put"] - s, 0) + max(t["long_put"] - s, 0))


def trades_for(symbol: str, frame: pd.DataFrame, spot_close: pd.Series, metrics: pd.DataFrame,
               lot: float | None = None) -> list[Trade]:
    """All trades of both structures for one underlying."""
    out = []
    by_day = {d: g for d, g in frame.groupby("trade_date")}
    for entry, expiry in entry_days(frame):
        m = metrics.loc[pd.Timestamp(entry)] if pd.Timestamp(entry) in metrics.index else None
        if m is None or not np.isfinite(m.get("atm_iv", np.nan)):
            continue
        settle = spot_close[spot_close.index <= pd.Timestamp(expiry)]
        if settle.empty or settle.index[-1] < pd.Timestamp(expiry) - pd.Timedelta(days=4):
            continue  # no underlying close at expiry: can't settle, never guessed
        s = float(settle.iloc[-1])
        for structure in STRUCTURES:
            t = open_trade(by_day[entry], symbol, entry, expiry, float(m["atm_iv"]), structure, lot)
            if t is None:
                continue
            pnl = t["credit"] + settle_value(t, s)
            out.append(Trade(symbol, structure, entry, expiry, settle=s, pnl=pnl,
                             r=pnl / t["max_loss"], **t))
    return out


def tag(trades: pd.DataFrame, vix_close: pd.Series | None, call_wall_rank: pd.DataFrame) -> pd.DataFrame:
    """Add the two filters, using only what was known at the entry close.

    vix_warning: VJ-1.0's 5-day VIX rise above its threshold on the day BEFORE entry.
    call_wall_top: the name's call_wall_pct ranked in the top half of the names that day.
    """
    t = trades.copy()
    t["vix_warning"] = False
    if vix_close is not None and not vix_close.empty:
        chg = vix_warning._change(vix_close).shift(1)  # known before the entry day's close
        w = chg.reindex(pd.DatetimeIndex(t["entry"]))
        t["vix_warning"] = (w > vix_warning.JUMP_PCT).to_numpy()
    rk = call_wall_rank.stack() if not call_wall_rank.empty else pd.Series(dtype=float)
    keys = list(zip(pd.DatetimeIndex(t["entry"]), t["symbol"], strict=True))
    vals = [rk.get(k, np.nan) for k in keys]
    t["call_wall_top"] = [None if not np.isfinite(v) else bool(v > 0.5) for v in vals]
    return t


def variant(t: pd.DataFrame, name: str) -> pd.DataFrame:
    if name == "A":
        return t
    t = t[~t["vix_warning"].astype(bool)]
    if name == "B":
        return t
    return t[t["call_wall_top"].map(lambda v: v is True)]


def summary(t: pd.DataFrame) -> dict:
    if t.empty:
        return {"trades": 0}
    r = t["r"]
    wins, losses = r[r > 0].sum(), -r[r < 0].sum()
    monthly = t.groupby(pd.to_datetime(t["expiry"]).dt.to_period("M"))["r"].mean()
    eq = monthly.cumsum()
    return {"trades": len(t), "avg_r": r.mean(), "median_r": r.median(),
            "win": float((r > 0).mean()), "pf": wins / losses if losses > 0 else np.inf,
            "worst_r": r.min(), "months": len(monthly), "month_avg_r": monthly.mean(),
            "losing_months": float((monthly < 0).mean()),
            "max_dd_r": float((eq.cummax() - eq).max()) if len(eq) else 0.0,
            "years_positive": float((t.groupby(pd.to_datetime(t["expiry"]).dt.year)["r"].mean() > 0).mean())}


def dev_trades(t: pd.DataFrame) -> pd.DataFrame:
    """Only trades whose whole life ended before validation starts."""
    return t[pd.to_datetime(t["expiry"]) < pd.Timestamp(fs.VALIDATION_FROM)]


# --------------------------------------------------------------------------- call-wall check
def residual_check(dev: pd.DataFrame) -> pd.DataFrame:
    """Re-score call_wall_pct after removing what IV level, IV rank and the last 20 days' move
    explain of it, each day across the names. If the link survives this, the call wall says
    something the IV level does not."""
    cols = ["atm_iv", "iv_rank_1y", "ret_20d"]
    d = dev.copy()
    d.index.name = "day"
    d = d.reset_index()
    resid = pd.Series(np.nan, index=d.index)
    for _, g in d.groupby("day"):
        g = g.dropna(subset=["call_wall_pct", *cols])
        if len(g) < fs.MIN_NAMES:
            continue
        x = np.column_stack([np.ones(len(g)), g[cols].to_numpy(float)])
        beta, *_ = np.linalg.lstsq(x, g["call_wall_pct"].to_numpy(float), rcond=None)
        resid.loc[g.index] = g["call_wall_pct"].to_numpy(float) - x @ beta
    d["call_wall_resid"] = resid.to_numpy()
    d = d.set_index("day")
    feats = {"call_wall_pct": ("", ""), "call_wall_resid": ("", "")}
    return fs.screen(d, feats, ["vol_surprise", "rv_vs_iv"], (), set(), fs.MIN_ABS_T_CS, 99.0)


# --------------------------------------------------------------------------- report
def to_markdown(trades: pd.DataFrame, check: pd.DataFrame, skipped: dict[str, int],
                generated: str) -> str:
    f = _f
    lines = [f"# Protected option selling {STUDY_VERSION} (development period only)", "",
             f"Generated {generated}. Monthly, per underlying, entered the day after the previous "
             f"monthly expiry, held to expiry. Real NSE option closes (fo_daily). Expiries before "
             f"{fs.VALIDATION_FROM} only; validation is held back. Costs are estimates (see "
             "`backend/app/services/option_selling.py`). R = profit ÷ worst possible loss.", ""]
    lines += ["## 1. Is the call wall more than the IV level?", "",
              "call_wall_pct re-scored after removing what ATM IV, IV rank and the last 20 days' "
              "move explain of it (each day, across names). Same scoring as OS-1.0.", "",
              "| Metric | vol_surprise | rv_vs_iv |", "|---|---|---|"]
    for feat in ("call_wall_pct", "call_wall_resid"):
        cells = []
        for tgt in ("vol_surprise", "rv_vs_iv"):
            r = check[(check.feature == feat) & (check.target == tgt)]
            cells.append("–" if r.empty or "ic" not in r or pd.isna(r["ic"].iloc[0]) else
                         f"{f(r['ic'].iloc[0], 3)} (t {f(r['t'].iloc[0], 1)})")
        lines.append(f"| {feat} | " + " | ".join(cells) + " |")
    lines += ["", "Read: if call_wall_resid keeps a |t| near 4 or more with the same sign, the call "
              "wall carries its own information; if it drops towards 0, it was the IV level.", ""]
    lines += ["## 2. Results by structure and filter", "",
              "| Structure | Variant | Trades | Win % | Avg R | Median R | PF | Worst R | Months "
              "| Avg R per month | Losing months | Max drawdown (R) | Years positive |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for st in STRUCTURES:
        for v, meaning in VARIANTS.items():
            s = summary(variant(trades[trades.structure == st], v))
            if not s["trades"]:
                lines.append(f"| {st} | {v}: {meaning} | 0 |" + " – |" * 10)
                continue
            lines.append(
                f"| {st} | {v}: {meaning} | {s['trades']} | {f(s['win'] * 100, 0)} | "
                f"{f(s['avg_r'], 3)} | {f(s['median_r'], 3)} | {f(s['pf'])} | {f(s['worst_r'])} | "
                f"{s['months']} | {f(s['month_avg_r'], 3)} | {f(s['losing_months'] * 100, 0)}% | "
                f"{f(s['max_dd_r'])} | {f(s['years_positive'] * 100, 0)}% |")
    lines += ["", "## 3. By underlying (variant B)", "",
              "| Underlying | fly trades | fly avg R | condor trades | condor avg R |",
              "|---|---|---|---|---|"]
    b = variant(trades, "B")
    for sym in sorted(trades.symbol.unique()):
        cells = []
        for st in STRUCTURES:
            x = b[(b.symbol == sym) & (b.structure == st)]
            cells += [str(len(x)), f(x["r"].mean(), 3) if len(x) else "–"]
        lines.append(f"| {sym} | " + " | ".join(cells) + " |")
    lines += ["", "## 4. By year (variant B, all names)", "",
              "| Year | fly trades | fly avg R | condor trades | condor avg R |", "|---|---|---|---|---|"]
    for yr, g in b.groupby(pd.to_datetime(b["expiry"]).dt.year):
        cells = []
        for st in STRUCTURES:
            x = g[g.structure == st]
            cells += [str(len(x)), f(x["r"].mean(), 3) if len(x) else "–"]
        lines.append(f"| {yr} | " + " | ".join(cells) + " |")
    lines += ["", "## Notes", "",
              "- Months skipped because a leg did not trade or strikes were missing: "
              + (", ".join(f"{k} {v}" for k, v in sorted(skipped.items())) or "none") + ".",
              "- Held to expiry with no stop: the long wings cap every loss at −1R.",
              "- Entry uses the day's closing prices; real fills will differ (that is what the "
              "slippage estimate is for).",
              "- Nothing here is a final test. A rule chosen from these results gets one final "
              "test on the held-back period, with its pass bar fixed in code first.", ""]
    return "\n".join(lines) + "\n"


def _f(x, nd=2) -> str:
    try:
        return "–" if x is None or not np.isfinite(x) else f"{x:.{nd}f}"
    except TypeError:
        return "–"
