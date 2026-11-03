"""Backtest VF-1.0: EMA-921, ORB-1.0 and ET-1.0 on NIFTY options, each with and without the
NIFTY futures volume filter (RVOL >= 1.0 and >= 1.5), scored on the pass bar fixed before the
run (backend/app/volume/__init__.py, docs/01 §25).

Needs no Groww access: load first with `tradex.sh fut-load` (futures volume) on top of the
existing `ema-load` / `orb-load` data. Writes reports/volume_backtest.md and every trade to
reports/volume_trades.csv.

Usage (from the repository root):
    python scripts/volume_backtest.py                       # the 2 development years
    python scripts/volume_backtest.py --holdout "ORB VF>=1.5|ATM buy"   # ONCE, best passing row
On the server: deploy/tradex.sh volume-backtest [--holdout ROW]
"""

import argparse
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import _bootstrap  # noqa: F401

from app.core.config import REPO_ROOT
from app.core.market_time import trading_days
from app.db.session import get_sessionmaker
from app.ema921 import engine as E
from app.ema921 import loader as L
from app.ema921 import report as R
from app.ema921 import store as S
from app.ema921 import trend as T
from app.ema921.metrics import passes_dev_bar, summarize
from app.orb import engine as O
from app.volume import THRESHOLDS, VERSION
from app.volume import futures as VF
from app.volume import rvol as RV

HOLDOUT = (date(2023, 11, 1), date(2024, 10, 31))
REPORTS = Path(REPO_ROOT) / "reports"
MARKER = REPORTS / "volume_holdout_used.txt"
MIN_COVERAGE = 0.80  # below this share of bars with an RVOL, the filtered rows are not judged


def runs(spot, opts, start, end, base, lookup):
    """[(label, Result)]: each strategy unfiltered, then with each volume threshold."""
    ema_cfg = replace(base, timeframe=5)
    et_cfg = replace(base, timeframe=T.SIGNAL_TIMEFRAME, start=T.ENTRY_START,
                     cutoff=T.ENTRY_CUTOFF, stop_atr_mult=None, trend_filter=True)
    out = []
    for name, go in (("EMA-921", lambda f: E.run(spot, opts, start, end, ema_cfg, entry_filter=f)),
                     ("ORB", lambda f: O.run(spot, opts, start, end, base, entry_filter=f)),
                     ("ET", lambda f: E.run(spot, opts, start, end, et_cfg, entry_filter=f))):
        print(f"{name}: no filter...")
        out.append((f"{name} no filter", go(None)))
        for th in THRESHOLDS:
            print(f"{name}: volume >= {th:g}x...")
            out.append((f"{name} VF>={th:g}", go(lookup.filter(th))))
    return out


def row_name(label: str, mode: str) -> str:
    return f"{label}|{E.MODE_LABELS[mode]}"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--years", type=int, default=2)
    p.add_argument("--holdout", help='run the held-back year ONCE for this row, e.g. "ORB VF>=1.5|ATM buy"')
    a = p.parse_args()
    start, end = L.default_range(L.ist_today(), a.years)
    if a.holdout:
        if MARKER.exists():
            print(f"The held-back year was already used ({MARKER.read_text().strip()}). It is run once only.")
            return 1
        start, end = HOLDOUT
    with get_sessionmaker()() as session:
        spot = S.load_spot(session, start, end, L.WARMUP_CALENDAR_DAYS)
        if spot.empty:
            print("No 1-minute NIFTY spot data stored. Run deploy/tradex.sh ema-load first.")
            return 1
        vol = RV.load_volume(session, VF.history_start(start), end)
        if vol.empty:
            print("No NIFTY futures 1-minute data stored. Run deploy/tradex.sh fut-load first.")
            return 1
        lookup = RV.RvolLookup(vol)
        bars = RV.rvol(RV.bar_volumes(vol))
        in_range = bars[(bars["day"] >= start) & (bars["day"] <= end)]
        covered = int(in_range["rvol"].notna().sum())
        expected = len(trading_days(start, end)) * 75  # 75 five-minute bars a day
        coverage = covered / expected if expected else 0.0
        opts = S.DbOptionData(session, "NIFTY")
        base = L.config_for("NIFTY")
        results = runs(spot, opts, start, end, base, lookup)

    REPORTS.mkdir(exist_ok=True)
    if a.holdout:
        return holdout(a.holdout, results, start, end, coverage)

    verdicts = []
    for label, res in results:
        for mode, trades in res.trades.items():
            ok, why = passes_dev_bar(trades, res.config.capital, start)
            judged = "VF" not in label or coverage >= MIN_COVERAGE
            verdicts.append((label, mode, ok and judged and "VF" in label, why, len(trades)))
    head = [f"# {VERSION}: NIFTY futures volume filter, pass bar", "",
            f"Range {start} to {end} (2 development years). Bar fixed 2026-11-03 before any run: "
            "net > 0, profit factor >= 1.10, each year net > 0, max drawdown < 50% of ₹1L, default "
            "slippage. Rows without the filter are shown for comparison only.", "",
            f"Volume coverage: {covered:,} of {expected:,} 5-minute bars have an RVOL "
            f"({coverage:.0%}).", ""]
    if coverage < MIN_COVERAGE:
        head += [f"**Less than {MIN_COVERAGE:.0%} of bars have futures volume, so the filtered rows "
                 "are not judged.** Check `fut-load`: Groww may not serve old 1-minute futures.", ""]
    head += ["| Row | Mode | Trades | Pass | Numbers |", "|---|---|---|---|---|"]
    head += [f"| {lbl} | {E.MODE_LABELS[m]} | {n} | {'PASS' if ok else ('–' if 'VF' not in lbl else 'fail')} "
             f"| {why} |" for lbl, m, ok, why, n in verdicts]
    passed = [(lbl, m) for lbl, m, ok, _, _ in verdicts if ok]
    if passed:
        best = max(passed, key=lambda lm: summarize(
            dict(results)[lm[0]].trades[lm[1]], base.capital).profit_factor or 0)
        head += ["", f"Best passing row: **{row_name(*best)}**. Next, once only: "
                 f'`deploy/tradex.sh volume-backtest --holdout "{row_name(*best)}"`.']
    else:
        head += ["", "**No filtered row passed.** The volume filter does not rescue these strategies; "
                 "the live filter stays off (alerts still show volume as information)."]
    head += ["", "18 filtered rows are scored, so one PASS can be luck: that is why only the best one "
             "gets the single held-back-year check.", ""]
    labels = [lab for lab, _ in results]
    md = "\n".join(head) + "\n" + R.to_markdown([r for _, r in results], start, end,
                                                 title=f"{VERSION} detail", labels=labels,
                                                 rules="Rules: backend/app/volume/__init__.py")
    (REPORTS / "volume_backtest.md").write_text(md, encoding="utf-8")
    n = R.write_csv([r for _, r in results], REPORTS / "volume_trades.csv", [lab for lab, _ in results])
    print("\n" + "\n".join(head))
    print(f"Saved reports/volume_backtest.md and {n} trades to reports/volume_trades.csv")
    return 0


def holdout(name: str, results, start, end, coverage: float) -> int:
    label, _, mode_label = name.partition("|")
    modes = {v: k for k, v in E.MODE_LABELS.items()}
    res = dict(results).get(label.strip())
    mode = modes.get(mode_label.strip())
    if res is None or mode is None:
        print(f'Unknown row "{name}". Use the exact text from reports/volume_backtest.md.')
        return 1
    trades = res.trades[mode]
    s = summarize(trades, res.config.capital)
    ok = bool(trades) and s.net_pnl > 0 and (s.profit_factor or 0) >= 1.05
    MARKER.write_text(f"{name} on {date.today()}: {'PASS' if ok else 'FAIL'}\n", encoding="utf-8")
    text = (f"# {VERSION} held-back year ({start} to {end}), row {name}\n\n"
            f"Volume coverage {coverage:.0%}. Trades {len(trades)}, net ₹{s.net_pnl:,.0f}, "
            f"PF {s.profit_factor}. Bar: net > 0 and PF >= 1.05 → **{'PASS' if ok else 'FAIL'}**.\n")
    (REPORTS / "volume_holdout.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
