"""Backtest the four core direction readings (D1-D4) on stored daily candles.

Read-only: it only reads candles from the database. Writes
reports/signal_study.md (the summary) and reports/signal_study_episodes.csv (every signal).

Usage (from the repository root):
    python scripts/signal_study.py
    python scripts/signal_study.py --symbols NIFTY,BANKNIFTY
On the server: deploy/tradex.sh study
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
from app.services import signal_study as study
from app.services.metrics import daily_frame


def main() -> int:
    s = get_settings()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--symbols", default=",".join(s.underlying_list))
    args = p.parse_args()
    symbols = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]

    frames = {}
    with get_sessionmaker()() as session:
        for sym in symbols:
            # The underlying itself: the index, or the stock's cash price (spec §5).
            inst = session.scalar(select(Instrument).where(
                Instrument.symbol == sym, Instrument.instrument_type.in_(("INDEX", "STOCK")),
                Instrument.provider == s.data_provider.lower()))
            if inst is None:
                print(f"  {sym}: no index/stock row, skipped")
                continue
            frames[sym] = daily_frame(session, inst.id)
            print(f"  {sym}: {len(frames[sym])} daily candles")

    episodes, baseline, info = study.run(frames)
    report = study.to_markdown(episodes, baseline, info, datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"))
    if s.data_provider.lower() == "sample":
        report = "**SAMPLE DATA: synthetic prices, results mean nothing.**\n\n" + report
    out = Path(REPO_ROOT) / "reports"
    out.mkdir(exist_ok=True)
    (out / "signal_study.md").write_text(report, encoding="utf-8")
    episodes.to_csv(out / "signal_study_episodes.csv", index=False)
    print("\n" + report)
    print(f"\nSaved {out / 'signal_study.md'} and {out / 'signal_study_episodes.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
