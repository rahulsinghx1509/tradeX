"""One-shot screen of every catalog metric that has history (study FS-1.0).

Read-only: it only reads candles from the database. For the fullest screen, first load daily
history back to 2020 (deploy/tradex.sh backfill --timeframes 1d --days-1d 2400), the expired
futures (deploy/tradex.sh futures-history) and India VIX (deploy/tradex.sh vix-study).
Writes reports/feature_screen.md (the report), reports/feature_screen_results.csv (every
metric × target score) and reports/feature_screen_table.csv (the full daily table).
Development period only; validation is held back.

Usage (from the repository root):
    python scripts/feature_screen.py
On the server: deploy/tradex.sh screen
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

import _bootstrap  # noqa: F401
import pandas as pd
from sqlalchemy import select

from app.core.config import REPO_ROOT, get_settings
from app.db.models import Instrument
from app.db.session import get_sessionmaker
from app.services import feature_screen as fs
from app.services.data_quality import VIX_SYMBOL
from app.services.metrics import daily_frame


def main() -> int:
    s = get_settings()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--symbols", default=",".join(s.underlying_list))
    args = p.parse_args()
    symbols = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
    provider = s.data_provider.lower()

    tables, coverage = [], {}
    with get_sessionmaker()() as session:
        def underlying(sym: str) -> Instrument | None:
            return session.scalar(select(Instrument).where(
                Instrument.symbol == sym, Instrument.instrument_type.in_(("INDEX", "STOCK")),
                Instrument.provider == provider))

        nifty_inst, vix_inst = underlying("NIFTY"), underlying(VIX_SYMBOL)
        nifty = daily_frame(session, nifty_inst.id) if nifty_inst else None
        vix = daily_frame(session, vix_inst.id) if vix_inst else None
        print(f"NIFTY: {0 if nifty is None else len(nifty)} daily candles; "
              f"India VIX: {0 if vix is None else len(vix)} daily candles")
        for sym in symbols:
            inst = underlying(sym)
            if inst is None:
                print(f"  {sym}: no index/stock row, skipped")
                continue
            spot = daily_frame(session, inst.id)
            if len(spot) < 250:
                print(f"  {sym}: only {len(spot)} daily candles, skipped")
                continue
            fut = fs.futures_long(session, provider, sym)
            t = fs.build_table(sym, spot, fut, nifty, vix)
            tables.append(t)
            coverage[sym] = {"days": len(t), "first": f"{t.index[0]:%Y-%m-%d}",
                             "last": f"{t.index[-1]:%Y-%m-%d}",
                             "fut": int(t["basis_pct"].notna().sum()),
                             "vix": int(t["vix"].notna().sum())}
            print(f"  {sym}: {len(t)} days, {fut['contract'].nunique() if len(fut) else 0} "
                  f"futures contracts")

    if not tables:
        print("No instruments with enough daily history: run deploy/tradex.sh backfill first.")
        return 1
    table = pd.concat(tables)
    dev = fs.dev_only(table)
    res = fs.screen(dev)
    report = fs.to_markdown(dev, res, coverage, datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"))
    if provider == "sample":
        report = "**SAMPLE DATA: synthetic prices, results mean nothing.**\n\n" + report
    out = Path(REPO_ROOT) / "reports"
    out.mkdir(exist_ok=True)
    (out / "feature_screen.md").write_text(report, encoding="utf-8")
    res.to_csv(out / "feature_screen_results.csv", index=False)
    dev.to_csv(out / "feature_screen_table.csv", index_label="day")
    print("\n" + report)
    print(f"\nSaved {out / 'feature_screen.md'}, feature_screen_results.csv and "
          "feature_screen_table.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
