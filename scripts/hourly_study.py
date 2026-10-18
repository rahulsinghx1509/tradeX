"""Hourly entries inside the daily trend: does reading the hourly chart improve entries?

Read-only: it only reads candles from the database. Needs several years of hourly candles
first (deploy/tradex.sh backfill --timeframes 1h --days-1h 2400). Writes
reports/hourly_study.md and reports/hourly_study_trades.csv. Development period only;
validation is held back.
With --final it runs the one-time final test of variant D on the held-back period and writes
reports/hourly_final.md and reports/hourly_final_trades.csv.

Usage (from the repository root):
    python scripts/hourly_study.py
    python scripts/hourly_study.py --final
On the server: deploy/tradex.sh hourly-study  (or: deploy/tradex.sh hourly-final)
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

import _bootstrap  # noqa: F401
from sqlalchemy import select

from app.core.config import REPO_ROOT, get_settings
from app.db.models import Instrument
from app.db.session import get_sessionmaker
from app.services import hourly_study
from app.services.metrics import daily_frame, hourly_frame


def main() -> int:
    s = get_settings()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--symbols", default=",".join(s.underlying_list))
    p.add_argument("--final", action="store_true", help="one-time final test of variant D")
    args = p.parse_args()
    symbols = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]

    daily, hourly = {}, {}
    with get_sessionmaker()() as session:
        for sym in symbols:
            # The underlying itself: the index, or the stock's cash price.
            inst = session.scalar(select(Instrument).where(
                Instrument.symbol == sym, Instrument.instrument_type.in_(("INDEX", "STOCK")),
                Instrument.provider == s.data_provider.lower()))
            if inst is None:
                print(f"  {sym}: no index/stock row, skipped")
                continue
            daily[sym], hourly[sym] = daily_frame(session, inst.id), hourly_frame(session, inst.id)
            first = hourly[sym].index[0].date() if len(hourly[sym]) else "-"
            print(f"  {sym}: {len(daily[sym])} daily, {len(hourly[sym])} hourly candles (from {first})")

    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    if args.final:
        trades, info = hourly_study.final_test(daily, hourly)
        report, name = hourly_study.final_markdown(trades, info, generated), "hourly_final"
    else:
        trades, info = hourly_study.run(daily, hourly)
        report, name = hourly_study.to_markdown(trades, info, generated), "hourly_study"
    if s.data_provider.lower() == "sample":
        report = "**SAMPLE DATA: synthetic prices, results mean nothing.**\n\n" + report
    out = Path(REPO_ROOT) / "reports"
    out.mkdir(exist_ok=True)
    (out / f"{name}.md").write_text(report, encoding="utf-8")
    trades.to_csv(out / f"{name}_trades.csv", index=False)
    print("\n" + report)
    print(f"\nSaved {out / (name + '.md')} and {out / (name + '_trades.csv')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
