"""RT-1.0 report: runs the rule and the stress grid (retest.py) and writes the Markdown.

Kept apart from scripts/et_retest.py so the tests can run it on synthetic data.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import date

from app.ema921 import costs as C
from app.ema921 import engine as EE
from app.spreads import engine as E
from app.spreads import retest as RT
from app.spreads import risk as RK
from app.spreads.report import nv, rs


def pct(x) -> str:
    return "–" if x is None else f"{x:g}%"


def period_table(title: str, rows: list[RT.PeriodRow]) -> list[str]:
    out = [f"### {title}", "",
           "| Period | Full period? | Trades | Win % | Gross | Costs + slippage | Net | PF | Max DD |",
           "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {r.label} | {'yes' if r.complete else 'part'} | {r.trades} | "
                   f"{pct(r.win_rate)} | {rs(r.gross)} | {rs(r.costs)} | **{rs(r.net)}** | "
                   f"{nv(r.pf)} | {pct(r.max_dd_pct)} |")
    return out + [""]


def run_rule(spot, opts, start, end, cfg, stops, mins):
    """{stop: (kept positions, skipped by limits)} plus the no-stop reference and data checks."""
    res = EE.run(spot, opts, start, end, E.et_config(cfg), (E.CS_SHORT,))
    closed = [t for t in res.trades[E.CS_SHORT] if t.exit_time is not None]
    out = {}
    for stop in stops:
        lv = RT.level(stop)
        trades = [RK.apply_exit_rule(t, mins(t), lv, cfg) for t in closed]
        ps = [p for p in E.single("ET ATM sell", trades) if not p.expiry_day]
        out[stop] = RK.apply_day_week(ps, lv)
    ref = [p for p in E.single("ET", closed) if not p.expiry_day]
    return out, ref, res


def build(spot, opts, start: date, end: date, base: EE.Config, last_spot=None,
          log=print) -> tuple[str, list]:
    """(report Markdown, the rule's positions). `opts` is the engine's OptionData."""
    E.set_expiry_days(opts.expiries())
    cache: dict = {}

    def mins(t):
        if (t.symbol, t.day) not in cache:
            cache[(t.symbol, t.day)] = EE._Minutes(opts.minutes(t.symbol, t.day))
        return cache[(t.symbol, t.day)]

    grid, main_res, main_ref = {}, None, None
    for name, sp, pts in RT.SLIPPAGE:
        log(f"ET ATM sell, slippage {name}...")
        cfg = replace(base, slippage_pct=sp, slippage_points=pts)
        out, ref, res = run_rule(spot, opts, start, end, cfg, RT.STOPS, mins)
        for stop, v in out.items():
            grid[(name, stop)] = v
        if sp == base.slippage_pct and pts == base.slippage_points:
            main_res, main_ref = res, ref
    if main_res is None:
        raise ValueError("the engine's default slippage must be the 1x row of RT.SLIPPAGE")

    cap = base.capital
    one_x = RT.SLIPPAGE[0][0]
    kept, skipped = grid[(one_x, 2000.0)]
    kept = sorted(kept, key=lambda p: (p.day, str(p.entry_time)))
    s = E.summarize(kept, cap, start)
    v = RT.verdict(kept, cap, start, end)
    first_day = kept[0].day if kept else start
    legs = [p.legs[0] for p in kept]
    slip = sum(t.slippage for t in legs)
    chg = sum(t.charges.get("total", 0.0) for t in legs)
    n = max(1, len(legs))

    lines = [f"# RT-1.0: ET-1.0 option sell re-test ({start} to {end})", "",
             "Rule (unchanged since 2026-11-05, backend/app/spreads/risk.py): ET-1.0 ATM sell, "
             "no trades on weekly expiry days, ₹2,000 1R trailing stop on the premium, ₹3,000 day "
             "loss, ₹6,000 day profit lock, ₹6,000 week loss. NIFTY, 1 lot of 65, ₹1L account.", "",
             "**This is a re-check, not a new out-of-sample test.** Nov 2023 - Oct 2024 was the "
             "held-back year (used once on 2026-11-05) and Nov 2024 - Oct 2026 chose the rule. "
             f"Only trades from {RT.NEW_FROM} on are truly new.", "",
             f"Bar (fixed before the run): PF >= {RT.PF_MIN}, max DD < {RT.DD_MAX_PCT:g}%, every "
             f"complete Nov - Oct year net > 0 → **{'PASS' if v.ok else 'FAIL'}**"
             + ("" if v.ok else f" ({'; '.join(v.reasons)})") + ".", "",
             "## The rule, whole period", "",
             "| Trades | Win % | Gross | Slippage | Charges | Net | PF | Avg / trade | Max DD | "
             "Worst trade | Worst day | Worst week | Skipped by day/week limits |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
             f"| {s.trades} | {pct(s.win_rate)} | {rs(s.gross_pnl)} | {rs(slip)} | {rs(chg)} | "
             f"**{rs(s.net_pnl)}** | {nv(s.profit_factor)} | {rs(s.expectancy)} | "
             f"{pct(s.max_drawdown_pct)} | {rs(s.worst_trade)} | "
             f"{rs(RK.worst_period(kept, False))} | {rs(RK.worst_period(kept, True))} | {skipped} |",
             "", f"Exits: {RK.exits(kept)}. Costs a trade: slippage {rs(slip / n)}, charges "
             f"{rs(chg / n)}. First trade {first_day}, last trade "
             f"{kept[-1].day if kept else '–'}; newest NIFTY day stored {last_spot}.", ""]
    lines += period_table("By test year (Nov - Oct; the years earlier results used)",
                          RT.by_period(kept, RT.nov_oct_year, start, end, cap))
    lines += period_table("By calendar year",
                          RT.by_period(kept, RT.calendar_year, start, end, cap))
    lines += period_table("By financial year (April - March)",
                          RT.by_period(kept, RT.financial_year, start, end, cap))

    new = [p for p in kept if p.day >= RT.NEW_FROM]
    ns = E.summarize(new, cap, RT.NEW_FROM)
    lines += [f"## New days only (from {RT.NEW_FROM}, never seen by any decision)", "",
              f"{ns.trades} trades, net {rs(ns.net_pnl)}, PF {nv(ns.profit_factor)}, win "
              f"{pct(ns.win_rate)}. Far too few to judge on their own; shown so they can be "
              "followed.", ""]
    if new:
        lines += ["| Day | Entry | Sold | Entry ₹ | Exit ₹ | Exit | Net |", "|---|---|---|---|---|---|---|"]
        for p in new:
            t = p.legs[0]
            lines.append(f"| {t.day} | {t.entry_time:%H:%M} | {t.strike:g} {t.option_type} | "
                         f"{t.entry_price:g} | {nv(t.exit_price)} | {t.exit_reason} | {rs(t.net_pnl)} |")
        lines.append("")

    lines += ["## Stress test 1: stop size x slippage", "",
              "Day/week limits stay ₹3k / ₹6k / ₹6k. Robustness only: a better-looking row does "
              "NOT replace the rule.", "",
              "| Trailing stop | Slippage a side | Trades | Net | PF | Max DD | "
              + " | ".join(r.label for r in RT.by_period(kept, RT.nov_oct_year, start, end, cap))
              + " | Bar |", "|---|---|---|---|---|---|"
              + "---|" * len(RT.by_period(kept, RT.nov_oct_year, start, end, cap)) + "---|"]
    for name, _, _ in RT.SLIPPAGE:
        for stop in RT.STOPS:
            ps, _ = grid[(name, stop)]
            gs = E.summarize(ps, cap, start)
            gv = RT.verdict(ps, cap, start, end)
            yrs = RT.by_period(ps, RT.nov_oct_year, start, end, cap)
            mark = " (the rule)" if (name, stop) == (one_x, 2000.0) else ""
            lines.append(f"| ₹{stop:,.0f}{mark} | {name} | {gs.trades} | {rs(gs.net_pnl)} | "
                         f"{nv(gs.profit_factor)} | {pct(gs.max_drawdown_pct)} | "
                         + " | ".join(rs(y.net) for y in yrs)
                         + f" | {'pass' if gv.ok else 'fail'} |")
    lines.append("")

    lines += ["## Stress test 2: without the best trades", "",
              "The rule's own trades (₹2,000 stop, 1x slippage) with the most profitable ones "
              "removed. Shows how much of the profit rests on a few big days.", "",
              "| Best trades removed | Trades | Net | PF | Max DD | Bar |", "|---|---|---|---|---|---|"]
    for k in (0, *RT.DROP_BEST):
        ps = RT.drop_best(kept, k)
        ds = E.summarize(ps, cap, start)
        dv = RT.verdict(ps, cap, start, end)
        lines.append(f"| {k} | {ds.trades} | {rs(ds.net_pnl)} | {nv(ds.profit_factor)} | "
                     f"{pct(ds.max_drawdown_pct)} | {'pass' if dv.ok else 'fail'} |")
    top = sorted(kept, key=lambda p: -p.net_pnl)[:max(RT.DROP_BEST)]
    lines += ["", "Best trades: " + ", ".join(f"{p.day} {rs(p.net_pnl)}" for p in top) + ".", ""]

    rf = E.summarize(main_ref, cap, start)
    skips = Counter(sk.reason for sk in main_res.skips if sk.mode == E.CS_SHORT)
    gaps = Counter(r.split(" ")[0] for r in main_res.no_trade_days.values())
    lines += ["## Reference and data check", "",
              f"- Same ET signals, no expiry days, **no stop and no limits**: {rf.trades} trades, "
              f"net {rs(rf.net_pnl)}, PF {nv(rf.profit_factor)}, max DD {pct(rf.max_drawdown_pct)}.",
              f"- Days tested {len(main_res.days_tested)}; days not traded: "
              + (", ".join(f"{k} {c}" for k, c in gaps.most_common()) or "none") + ".",
              "- ET signals with no option price (skipped, never priced at zero): "
              + (", ".join(f"{k} {c}" for k, c in skips.most_common()) or "none")
              + ". If the newest days show up here, run spread-load again.",
              f"- Slippage {base.slippage_pct:.1%} + {base.slippage_points:g} point a side (engine "
              "default since PR #45). Charges (NOT_VERIFIED, backend/app/ema921/costs.py): "
              + "; ".join(f"from {r.effective_from}: ₹{r.brokerage_per_order:g} an order, STT "
                          f"{r.stt_sell:.4%} of sell premium, NSE {r.exchange_txn:.5%}, stamp "
                          f"{r.stamp_buy:.3%} of buy, GST {r.gst:.0%}" for r in C.RATES) + ".",
              "- 1 lot = 65 units in every year (today's lot), so years compare like for like; "
              "the real lot size was different in earlier years.",
              "- Margin for the naked sell is not in these numbers: see `tradex.sh et-margin`.", ""]
    return "\n".join(lines), kept
