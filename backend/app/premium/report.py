"""PR-1.0 report: plain tables RK can read, verdict per candidate against the fixed bar."""

from __future__ import annotations

import math

from app import premium as P
from app.premium import engine as E

CHECK_NAMES = {
    "trades": f"at least {P.MIN_TRADES} trades",
    "pf": f"profit factor >= {P.PF_MIN:.2f}",
    "dd": f"max drawdown < {P.DD_MAX:.0%} of ₹1L",
    "years": "every calendar year net > 0",
    "slip2x": "net > 0 at 2x slippage",
    "drop5": f"net > 0 without the best {P.DROP_BEST} trades",
}


def _r(x: float) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "–" if x is None or math.isnan(x) else "∞"
    return f"₹{x:,.0f}" if x >= 0 else f"-₹{-x:,.0f}"


def _pf(x: float) -> str:
    return "∞" if math.isinf(x) else f"{x:.2f}"


def to_markdown(trades: list[E.Trade], skipped: dict[str, int], coverage: dict, generated: str) -> str:
    rows = {k: E.summarize(E.variant(trades, k)) for k in (*P.CANDIDATES, *P.REFERENCES)}
    L = [f"# Theta Harvest (TH-1.0 = {P.VERSION}): hedged NIFTY premium selling, held overnight", "",
         f"Generated {generated}. NSE daily option closes (fo_daily) from "
         f"{coverage.get('first')} to {coverage.get('last')}; NIFTY close to "
         f"{coverage.get('spot_last')}; India VIX to {coverage.get('vix_last')}. "
         f"1 lot = {P.LOT}, ₹1L account. Rules and bar fixed before this run "
         "(`backend/app/premium/__init__.py`, docs/01 §29).", ""]
    L += ["## Verdict", "", "| Candidate | Verdict | Trades | Net | PF | Max DD | Net at 2x slippage "
          f"| Net without best {P.DROP_BEST} | Every year > 0? |", "|---|---|---|---|---|---|---|---|---|"]
    for k, meaning in P.CANDIDATES.items():
        s = rows[k]
        if not s["trades"]:
            L.append(f"| {k}: {meaning} | **FAIL** (no trades) |" + " – |" * 7)
            continue
        L.append(f"| {k}: {meaning} | **{s['verdict']}** | {s['trades']} | {_r(s['net'])} | "
                 f"{_pf(s['pf'])} | {_r(s['max_dd'])} | {_r(s['net_2x'])} | {_r(s['net_drop'])} | "
                 f"{'yes' if s['checks']['years'] else 'no'} |")
    L += ["", "Bar (all six needed): " + "; ".join(CHECK_NAMES.values()) + ".", ""]
    for k in P.CANDIDATES:
        s = rows[k]
        if s["trades"]:
            fails = [CHECK_NAMES[c] for c, ok in s["checks"].items() if not ok]
            msg = "all checks passed." if not fails else "failed: " + "; ".join(fails) + "."
            L.append(f"- {k}: {msg}")
    L += ["", "## Detail (judged rows and unfiltered references)", "",
          "| Row | Trades | Win % | Gross (before costs) | Costs | Net | Avg / trade | PF | Max DD "
          "| Worst trade | Best trade | Net 2x slip | PF 2x | Net w/o best 5 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for k in (*P.CANDIDATES, *P.REFERENCES):
        s = rows[k]
        if not s["trades"]:
            L.append(f"| {k} | 0 |" + " – |" * 12)
            continue
        L.append(f"| {k} | {s['trades']} | {s['win'] * 100:.0f} | {_r(s['gross'])} | {_r(s['costs'])} | "
                 f"{_r(s['net'])} | {_r(s['avg'])} | {_pf(s['pf'])} | {_r(s['max_dd'])} | "
                 f"{_r(s['worst'])} | {_r(s['best'])} | {_r(s['net_2x'])} | {_pf(s['pf_2x'])} | "
                 f"{_r(s['net_drop'])} |")
    L += ["", "## Net by calendar year (trades in brackets)", ""]
    years = sorted({y for s in rows.values() if s["trades"] for y in s["years"]})
    L += ["| Row | " + " | ".join(str(y) for y in years) + " |", "|---|" + "---|" * len(years)]
    for k in (*P.CANDIDATES, *P.REFERENCES):
        s = rows[k]
        cells = []
        for y in years:
            v = s.get("years", {}).get(y) if s["trades"] else None
            cells.append("–" if v is None else f"{_r(v[0])} ({v[1]})")
        L.append(f"| {k} | " + " | ".join(cells) + " |")
    L += ["", "## Notes", "",
          "- A0 / B0 are the same trades without filters, to show what the filters did. They are "
          "never judged and never replace A or B.",
          f"- Exit legs valued at NSE's settle price because they did not trade that day: "
          f"{rows['A0'].get('fallback', 0)} (candidate A).",
          "- Days skipped (counted, never guessed): "
          + (", ".join(f"{k} {v}" for k, v in sorted(skipped.items())) or "none") + ".",
          "- Prices are day closes; real fills near 15:25 differ (that is what slippage covers). "
          "Charges use `ema921/costs.py` (NOT_VERIFIED rates). In-the-money legs at expiry pay "
          "0.125% STT (long legs) and an assumed ₹20 each.",
          "- Fixed lot of 65 in every year. Drawdown is on closed trades.",
          "- Iron fly margin at Groww is NOT_VERIFIED; checked only if a candidate passes.",
          "- PASS here means: worth PAPER trading after RK approves. Never real orders.", ""]
    return "\n".join(L) + "\n"
