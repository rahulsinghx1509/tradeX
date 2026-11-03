"""IS-1.0 backtest: 3 intraday setups x LONG/SHORT on the NIFTY 50 + Next 50 stocks.

Uses the 5-minute candles `stock-load` saved. Scores each of the 6 rows on the development
period against the pass bar fixed in app/stocks/rules.py, and writes
reports/stock_backtest.md and reports/stock_trades.csv. The oldest third of the days is held
back: `--holdout ROW` (e.g. ORB-LONG) runs it once, only for the best passing row.
Read-only: no orders.

Usage (from the repository root):
    python scripts/stock_backtest.py
    python scripts/stock_backtest.py --holdout ORB-LONG
On the server: deploy/tradex.sh stock-backtest
"""

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

import _bootstrap  # noqa: F401

from app.core.config import REPO_ROOT
from app.db.session import get_sessionmaker
from app.stocks import engine as E
from app.stocks import rules as R
from app.stocks import store as S

ROWS = [(s, d) for s in R.SETUPS for d in R.DIRECTIONS]
REPORTS = Path(REPO_ROOT) / "reports"
MARKER = REPORTS / "stock_holdout_used.txt"


def money(x: float) -> str:
    return f"-₹{abs(x):,.0f}" if x < 0 else f"₹{x:,.0f}"


def collect(session, symbols, nifty, log=print):
    cands = {row: [] for row in ROWS}
    for i, sym in enumerate(symbols, 1):
        df = S.load_candles(session, sym)
        if len(df) < 75 * 25:
            log(f"  {sym}: only {len(df)} candles, skipped")
            continue
        r = E.readings(df, nifty)
        for setup, dname in ROWS:
            cands[(setup, dname)] += E.candidates(sym, r, setup, R.DIRECTIONS[dname])
        if i % 10 == 0:
            log(f"  {i}/{len(symbols)} stocks read")
    return cands


def row_name(row) -> str:
    return f"{row[0]}-{row[1]}"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--holdout", help="run the held-back period once for this row, e.g. ORB-LONG")
    p.add_argument("--only", help="comma-separated symbols (testing)")
    args = p.parse_args()

    with get_sessionmaker()() as session:
        nifty = S.load_candles(session, S.INDEX_SYMBOL)
        if nifty.empty:
            print("No NIFTY 5-minute candles. Run deploy/tradex.sh stock-load first.")
            return 1
        symbols = [m[0] for m in S.members(session)] or [s for s in S.stored_symbols(session)
                                                        if s != S.INDEX_SYMBOL]
        if args.only:
            symbols = [s.strip().upper() for s in args.only.split(",")]
        nifty_days = sorted(set(nifty["day"]))
        split = E.split_days(nifty_days)
        dev_days = [d for d in nifty_days if d >= split]
        mid = dev_days[len(dev_days) // 2]
        print(f"{len(symbols)} stocks, NIFTY days {nifty_days[0]}..{nifty_days[-1]}; held back before "
              f"{split}. Reading candles...")
        cands = collect(session, symbols, nifty)

    results, verdicts = {}, {}
    for row in ROWS:
        dev = [c for c in cands[row] if c.day >= split]
        trades = E.portfolio(dev)
        results[row] = (trades, E.score(trades, mid))
        verdicts[row] = E.passes(results[row][1])

    passing = [r for r in ROWS if verdicts[r][0]]
    best = max(passing, key=lambda r: results[r][1].pf) if passing else None

    out = [f"# {R.VERSION} intraday stock backtest", "",
           f"- Stocks: {len(symbols)} (today's NIFTY 50 + Next 50; survivorship bias).",
           f"- Development period: {split} to {nifty_days[-1]} ({len(dev_days)} days; "
           f"halves split at {mid}).",
           f"- Held back (not scored here): {nifty_days[0]} to {split} (oldest third).",
           f"- Risk ₹{R.RISK_PER_TRADE:,.0f}/trade, max {R.MAX_OPEN} open, "
           f"slippage {R.SLIPPAGE_PCT}% a side, full equity intraday charges. "
           "Rules: backend/app/stocks/rules.py.", "",
           "| Row | Trades | Win % | Gross | Costs | Net | PF | Avg/trade | Max DD | Half 1 | Half 2 "
           "| Verdict |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for row in ROWS:
        s = results[row][1]
        ok, why = verdicts[row]
        dd = f"{money(s.max_dd)} ({s.max_dd_pct:.0f}%)"
        out.append(f"| {row_name(row)} | {s.trades} | {s.win_pct:.1f} | {money(s.gross)} | "
                   f"{money(s.costs)} | {money(s.net)} | {s.pf:.2f} | {money(s.avg)} | {dd} | "
                   f"{money(s.half1)} | {money(s.half2)} | {'PASS' if ok else 'fail'}: {why} |")
    out += ["", "## How trades ended", "", "| Row | " + " | ".join(
        ["TARGET", "STOP", "SQUARE_OFF", "DATA_END"]) + " |", "|---|---|---|---|---|"]
    for row in ROWS:
        cnt = Counter(t.c.exit_reason for t in results[row][0])
        out.append(f"| {row_name(row)} | " + " | ".join(
            str(cnt.get(k, 0)) for k in ("TARGET", "STOP", "SQUARE_OFF", "DATA_END")) + " |")
    out += ["", "## Verdict", ""]
    if best:
        out.append(f"Best passing row: **{row_name(best)}** (PF {results[best][1].pf:.2f}). "
                   f"Next, once only: `deploy/tradex.sh stock-backtest --holdout {row_name(best)}`.")
    else:
        out.append("No row passed. Per the rules, nothing is re-tuned on this data and the held-back "
                   "period stays unused.")
    out += ["", "6 rows are scored, so one PASS can be luck: only the best gets the held-back check.",
            "Past results are not proof of a future edge."]

    if args.holdout:
        name = args.holdout.strip().upper()
        if MARKER.exists():
            print(f"The held-back period was already used ({MARKER.read_text().strip()}). Refusing.")
            return 1
        if best is None or name != row_name(best):
            print(f"{name} is not the best passing row ({row_name(best) if best else 'none passed'}). "
                  "Refusing to spend the held-back period.")
            return 1
        held = [c for c in cands[best] if c.day < split]
        s = E.score(E.portfolio(held))
        ok = s.net > 0 and s.pf >= R.HOLDOUT_PF
        line = (f"HELD-BACK {name} {nifty_days[0]}..{split}: {s.trades} trades, net {money(s.net)}, "
                f"PF {s.pf:.2f}, max DD {s.max_dd_pct:.0f}% -> {'PASS' if ok else 'FAIL'} "
                f"(bar: net > 0, PF >= {R.HOLDOUT_PF})")
        out += ["", "## Held-back check (run once)", "", line]
        REPORTS.mkdir(exist_ok=True)
        MARKER.write_text(line + "\n", encoding="utf-8")
        print(line)

    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "stock_backtest.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    with (REPORTS / "stock_trades.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["row", "symbol", "day", "signal_time", "entry_time", "entry", "stop", "target", "qty",
                    "exit_time", "exit_price", "exit_reason", "gross", "slippage", "charges", "net"])
        for row in ROWS:
            for t in results[row][0]:
                c = t.c
                w.writerow([row_name(row), c.symbol, c.day, c.signal_time, c.entry_time, c.entry, c.stop,
                            round(c.target, 2), t.qty, c.exit_time, c.exit_price, c.exit_reason,
                            round(t.gross, 2), round(t.slippage, 2), round(t.charges, 2), round(t.net, 2)])
    print("\n" + "\n".join(out))
    print("\nSaved reports/stock_backtest.md and reports/stock_trades.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
