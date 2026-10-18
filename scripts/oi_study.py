"""Open-interest study: forward returns after each kind of futures OI build-up.

Read-only: it only reads candles from the database. Needs the expired-futures history first
(deploy/tradex.sh futures-history). Writes reports/oi_study.md and reports/oi_study_days.csv.
Development period only; validation is held back.

Usage (from the repository root):
    python scripts/oi_study.py
On the server: deploy/tradex.sh oi-study
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
from app.services import oi_study
from app.services.metrics import daily_frame


def main() -> int:
    s = get_settings()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--symbols", default=",".join(s.underlying_list))
    args = p.parse_args()
    symbols = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
    provider = s.data_provider.lower()

    prices, oi = {}, {}
    with get_sessionmaker()() as session:
        for sym in symbols:
            inst = session.scalar(select(Instrument).where(
                Instrument.symbol == sym, Instrument.instrument_type.in_(("INDEX", "STOCK")),
                Instrument.provider == provider))
            if inst is None:
                print(f"  {sym}: no index/stock row, skipped")
                continue
            prices[sym] = daily_frame(session, inst.id)["close"]
            oi[sym] = oi_study.futures_oi(session, provider, sym)
            print(f"  {sym}: {len(prices[sym])} daily closes, {oi[sym]['contract'].nunique()} "
                  f"futures contracts with OI")

    days = oi_study.run(prices, oi)
    report = oi_study.to_markdown(days, datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"))
    if provider == "sample":
        report = "**SAMPLE DATA: synthetic prices, results mean nothing.**\n\n" + report
    out = Path(REPO_ROOT) / "reports"
    out.mkdir(exist_ok=True)
    (out / "oi_study.md").write_text(report, encoding="utf-8")
    days.to_csv(out / "oi_study_days.csv", index=False)
    print("\n" + report)
    print(f"\nSaved {out / 'oi_study.md'} and {out / 'oi_study_days.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
