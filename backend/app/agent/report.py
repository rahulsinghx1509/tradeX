"""AD-1.0 report: verdict against the fixed bar, then what the agent picked each month in words."""

from __future__ import annotations

import math

from app import agent as A

ENTRY_WORDS = {
    "ORB": "opening-range breakout", "EMA": "9/21 EMA cross", "GAPFADE": "gap fade at 09:20",
    "GAPGO": "gap-and-go at 09:20", "RSI": "RSI(14) bounce from 25/75", "BB": "Bollinger re-entry",
    "TRENDPB": "trend pullback to EMA21", "SUPERTREND": "Supertrend(10,3) flip",
    "MACD": "MACD(12,26,9) cross", "PDHL": "previous-day high/low breakout",
    "CPR": "CPR (central pivot range) breakout", "IB30": "30-minute initial-balance breakout"}
STRUCT_WORDS = {
    "BUY_ATM": "buy ATM option", "BUY_ITM": "buy 1-strike ITM option",
    "DEBIT": "debit spread (buy ATM, sell 2 strikes out)",
    "CREDIT": "hedged credit spread (sell ATM other side, buy 4 strikes out)",
    "FLY": "iron fly", "CONDOR": "iron condor (100/300 out)",
    "LONG": "buy ATM option"}
HOLD_WORDS = {"OVERNIGHT": "overnight", "WEEKEND": "over the weekend", "WEEKLY": "to expiry (weekly)"}
COND_WORDS = {"ALWAYS": "every day", "VRP": "when VIX > 20-day volatility", "RANGE": "when ADX < 20",
              "TREND": "with the EMA20 trend", "REVERT": "against RSI(2) extremes"}
REGIME_WORDS = {"ANY": "any day", "TREND": "trend days (ADX >= 25)", "RANGE": "range days (ADX < 20)",
                "HIGHVOL": "high-VIX days", "LOWVOL": "low-VIX days",
                "LIVE_TREND": "only when NIFTY is already 0.4%+ from the open (live check)",
                "LIVE_FLAT": "only when NIFTY is within 0.2% of the open (live check)"}
EXIT_WORDS = {"2R": "target 2x stop", "NONE": "no target", "TRAIL": "trailing stop after 1x stop profit"}
CHECKS = {
    "trades": f"at least {A.MIN_TRADES} trades", "pf": f"profit factor >= {A.PF_MIN:.2f}",
    "dd": f"max drawdown < {A.DD_MAX:.0%} of ₹1L", "years": "every calendar year net > 0",
    "slip2x": "net > 0 at 2x slippage", "drop5": f"net > 0 without the best {A.DROP_BEST} trades",
    "months": "more than half of traded months net > 0"}


def describe(base: str, regime: str) -> str:
    p = base.split("|")
    if p[0] == "I":
        _, e, stop, ex, te, su = p
        t = EXIT_WORDS[ex]
        x = "exit after 60 min" if te == "60m" else "exit 15:15"
        s = f"Intraday {ENTRY_WORDS[e]}, {STRUCT_WORDS[su]}, stop {stop}, {t}, {x}"
    else:
        _, h, cond, su = p
        s = f"Swing {HOLD_WORDS[h]}, {STRUCT_WORDS[su]} {COND_WORDS[cond]}"
    return f"{s}; {REGIME_WORDS[regime]}"


def _r(x) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    if isinstance(x, float) and math.isinf(x):
        return "∞"
    return f"₹{x:,.0f}" if x >= 0 else f"-₹{-x:,.0f}"


def _pf(x: float) -> str:
    return "∞" if math.isinf(x) else f"{x:.2f}"


def _track_cell(bt: dict):
    return lambda k: f"{_r(bt[k]['sum'])} ({int(bt[k]['count'])})" if k in bt else "–"


def to_markdown(rows: dict[str, dict], meta: dict, upcoming: list[dict], generated: str) -> str:
    L = [f"# {A.VERSION} adaptive agent: walk-forward backtest (NIFTY options, PAPER only)", "",
         f"Generated {generated}. Intraday data {meta.get('i_first')} to {meta.get('i_last')} "
         f"({meta.get('i_days')} complete days); daily option file {meta.get('s_first')} to "
         f"{meta.get('s_last')}. {meta.get('n_candidates')} candidates (cap {A.MAX_CANDIDATES}), "
         f"re-picked every month from the previous {A.TRAIN_MONTHS} months, after costs. "
         "Rules and bar fixed before this run (`backend/app/agent/__init__.py`, docs/01 §30).", ""]
    L += ["## Verdict", "", "| Row | Months | Verdict | Trades | Net | PF | Max DD | Net 2x slip "
          f"| Net w/o best {A.DROP_BEST} | Every year > 0? | Months > 0 |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, s in rows.items():
        label = f"**{name}**" if name == "FULL" else name
        if not s["trades"]:
            L.append(f"| {label} | {s.get('span', '')} | **FAIL** (no trades) |" + " – |" * 8)
            continue
        verdict = f"**{s['verdict']}**" if name == "FULL" else f"{s['verdict']} (info)"
        L.append(f"| {label} | {s.get('span', '')} | {verdict} | {s['trades']} | {_r(s['net'])} | "
                 f"{_pf(s['pf'])} | {_r(s['max_dd'])} | {_r(s['net_2x'])} | {_r(s['net_drop'])} | "
                 f"{'yes' if s['checks']['years'] else 'no'} | {s['months_pos']}/{s['months_traded']} |")
    L += ["", "FULL = intraday + swing together, the only judged row. Bar (all seven needed): "
          + "; ".join(CHECKS.values()) + ".", ""]
    full = rows.get("FULL", {})
    if full.get("trades"):
        fails = [CHECKS[c] for c, ok in full["checks"].items() if not ok]
        L.append("FULL: " + ("all checks passed." if not fails else "failed: " + "; ".join(fails) + "."))
        L.append("")
    L += ["## Detail", "", "| Row | Win % | Gross (before costs) | Costs | Net | Avg / trade | "
          "No-trade months | Kill switch | Intraday net (trades) | Swing net (trades) |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for name, s in rows.items():
        if not s["trades"]:
            continue
        cell = _track_cell(s["by_track"])
        L.append(f"| {name} | {s['win'] * 100:.0f} | {_r(s['gross'])} | {_r(s['costs'])} | {_r(s['net'])} | "
                 f"{_r(s['avg'])} | {s['months_none']} | {s['kills']} | {cell('I')} | {cell('S')} |")
    L += ["", "## Net by calendar year (trades)", ""]
    years = sorted({y for s in rows.values() if s["trades"] for y in s["years"]})
    L += ["| Row | " + " | ".join(map(str, years)) + " |", "|---|" + "---|" * len(years)]
    for name, s in rows.items():
        if s["trades"]:
            cells = [f"{_r(s['years'][y][0])} ({s['years'][y][1]})" if y in s["years"] else "–"
                     for y in years]
            L.append(f"| {name} | " + " | ".join(cells) + " |")
    if full.get("months"):
        L += ["", "## What the agent picked each month (FULL row)", "",
              "| Month | Picks (trained on the 6 months before) | Trades | Net |", "|---|---|---|---|"]
        for m in full["months"]:
            picks = "<br>".join(f"{describe(p['base'], p['regime'])} (train {p['train_n']} trades, "
                                f"{_r(p['train_net'])}, PF {_pf(p['train_pf'])})" for p in m["picks"]) \
                or "NO TRADE (nothing passed its checks)"
            L.append(f"| {m['month']} | {picks} | {m['trades']} | {_r(m['net'])} |")
    L += ["", "## If PAPER-traded next month, the agent would use", ""]
    L += [f"- {describe(p['base'], p['regime'])} (train {p['train_n']} trades, {_r(p['train_net'])}, "
          f"PF {_pf(p['train_pf'])})" for p in upcoming] or ["- nothing (NO TRADE)"]
    blocked = full.get("blocked") or {}
    L += ["", "## Notes", "",
          "- Every month is out-of-sample for the agent: it only sees the months before. But this "
          "same data was used by earlier tests, which shaped the building blocks, so only live "
          "PAPER trading is truly new data.",
          "- Picked trades the account rules refused (FULL): "
          + (", ".join(f"{k} {v}" for k, v in sorted(blocked.items())) or "none") + ".",
          "- Trades skipped because data was missing (counted, never guessed): "
          + (", ".join(f"{k} {v}" for k, v in sorted(meta.get("skipped", {}).items())) or "none") + ".",
          "- Swing entries use the day's closing prices; real fills near 15:25 differ (slippage "
          "covers it). Charges: `ema921/costs.py` (NOT_VERIFIED rates). Fixed lot 65.",
          "- Margin for hedged structures is about ₹50k (ET-1.0 check); iron fly / condor margin "
          "is NOT_VERIFIED.",
          "- PASS = worth PAPER trading after RK approves. Never real orders.", ""]
    return "\n".join(L) + "\n"
