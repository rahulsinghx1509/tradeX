"""Rebuild option metrics (ATM IV, skew, PCR, max pain, OI walls) from NSE's daily F&O file.

Reads the fo_daily table (load it first with deploy/tradex.sh fo-history). Works one underlying
and one year at a time, so it fits in the small server's memory. Writes
reports/option_metrics.md (coverage plus two data checks) and reports/option_metrics.csv
(one row per underlying and day). A data check, not a study.

Usage (from the repository root):
    python scripts/option_metrics.py
    python scripts/option_metrics.py --symbols NIFTY,BANKNIFTY
On the server: deploy/tradex.sh option-metrics
"""

import argparse
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import _bootstrap  # noqa: F401
import pandas as pd
from sqlalchemy import func, select

from app.core.config import REPO_ROOT, get_settings
from app.db.models import FoDaily, Instrument
from app.db.session import get_sessionmaker
from app.services import option_metrics as om
from app.services.data_quality import VIX_SYMBOL
from app.services.metrics import daily_frame


def main() -> int:
    s = get_settings()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--symbols", default=",".join(s.underlying_list))
    args = p.parse_args()
    symbols = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]

    per_symbol, units = {}, {}
    with get_sessionmaker()() as session:
        first, last = session.execute(select(func.min(FoDaily.trade_date),
                                             func.max(FoDaily.trade_date))).one()
        if first is None:
            print("fo_daily is empty: run deploy/tradex.sh fo-history first.")
            return 1
        print(f"fo_daily holds {first} to {last}.")
        for sym in symbols:
            parts = []
            for year in range(first.year, last.year + 1):
                df = om.load_frame(session, sym, date(year, 1, 1), date(year, 12, 31))
                if df.empty:
                    continue
                parts.append(om.compute(df))
                if year == 2024:
                    units[sym] = om.units_check(df)
            per_symbol[sym] = pd.concat(parts) if parts else pd.DataFrame(columns=om.METRICS)
            print(f"  {sym:<10} {len(per_symbol[sym])} days")
        vix_inst = session.scalar(select(Instrument).where(
            Instrument.symbol == VIX_SYMBOL, Instrument.provider == s.data_provider.lower()))
        vix = daily_frame(session, vix_inst.id)["close"] if vix_inst else None

    report = om.to_markdown(per_symbol, units, vix, datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"))
    out = Path(REPO_ROOT) / "reports"
    out.mkdir(exist_ok=True)
    (out / "option_metrics.md").write_text(report, encoding="utf-8")
    long = pd.concat({k: v for k, v in per_symbol.items() if not v.empty}, names=["symbol", "day"])
    long.round(4).to_csv(out / "option_metrics.csv")
    print("\n" + report)
    print(f"Saved {out / 'option_metrics.md'} and {out / 'option_metrics.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
