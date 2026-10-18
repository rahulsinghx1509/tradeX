"""One-shot screen of the option metrics (study OS-1.0), development period only.

Reads daily candles and the F&O history (fo_daily; load it with deploy/tradex.sh fo-history).
Rebuilds the option metrics one underlying and one year at a time, then scores every metric
against what happened next with the same rules as the price/OI screen. Read-only.
Writes reports/option_screen.md and reports/option_screen_results.csv. Takes about 5-10
minutes on the small server. Validation stays held back.

Usage (from the repository root):
    python scripts/option_screen.py
On the server: deploy/tradex.sh option-screen
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
from app.services import feature_screen as fs
from app.services import option_metrics as om
from app.services import option_screen as osc
from app.services.metrics import daily_frame


def main() -> int:
    s = get_settings()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--symbols", default=",".join(s.underlying_list))
    args = p.parse_args()
    symbols = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
    provider = s.data_provider.lower()
    last_dev_year = fs.VALIDATION_FROM.year  # option rows after the cut are never needed

    tables = []
    with get_sessionmaker()() as session:
        first = session.scalar(select(func.min(FoDaily.trade_date)))
        if first is None:
            print("fo_daily is empty: run deploy/tradex.sh fo-history first.")
            return 1

        def underlying(sym: str) -> Instrument | None:
            return session.scalar(select(Instrument).where(
                Instrument.symbol == sym, Instrument.instrument_type.in_(("INDEX", "STOCK")),
                Instrument.provider == provider))

        for sym in symbols:
            inst = underlying(sym)
            if inst is None:
                print(f"  {sym}: no index/stock row, skipped")
                continue
            spot = daily_frame(session, inst.id)
            base = fs.build_table(sym, spot, pd.DataFrame(), None, None)
            parts = []
            for year in range(first.year, last_dev_year + 1):
                df = om.load_frame(session, sym, date(year, 1, 1), date(year, 12, 31))
                if not df.empty:
                    parts.append(om.compute(df))
            if not parts:
                print(f"  {sym}: no option rows, skipped")
                continue
            t = osc.build_table(base, pd.concat(parts))
            tables.append(t)
            print(f"  {sym}: {len(t)} days, {int(t['atm_iv'].notna().sum())} with ATM IV")

    if not tables:
        print("Nothing to screen.")
        return 1
    dev = osc.dev_only(pd.concat(tables))
    res = osc.screen(dev)
    report = osc.to_markdown(dev, res, datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"))
    if provider == "sample":
        report = "**SAMPLE DATA: synthetic prices, results mean nothing.**\n\n" + report
    out = Path(REPO_ROOT) / "reports"
    out.mkdir(exist_ok=True)
    (out / "option_screen.md").write_text(report, encoding="utf-8")
    res.to_csv(out / "option_screen_results.csv", index=False)
    print("\n" + report)
    print(f"Saved {out / 'option_screen.md'} and {out / 'option_screen_results.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
