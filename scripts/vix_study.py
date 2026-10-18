"""India VIX versus NIFTY's realised volatility: were option premiums usually too high?

First fetches India VIX daily candles since 2020 from Groww (read-only; safe to re-run), then
compares them with NIFTY's realised volatility over the following 21 trading days.
Writes reports/vix_study.md and reports/vix_study_days.csv. Development period only;
validation is held back. A study, not a trade.

Usage (from the repository root):
    python scripts/vix_study.py
    python scripts/vix_study.py --no-load     # use the VIX candles already stored
On the server: deploy/tradex.sh vix-study
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
from app.providers.factory import get_provider
from app.services import vix_study
from app.services.metrics import daily_frame


def main() -> int:
    s = get_settings()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--no-load", action="store_true", help="skip fetching VIX from the provider")
    args = p.parse_args()
    provider_name = s.data_provider.lower()

    with get_sessionmaker()() as session:
        if not args.no_load:
            provider = get_provider(s)
            res = vix_study.load_vix(session, provider, datetime.now(UTC))
            print(f"India VIX load: {res.status}, received {res.rows_received}, stored {res.rows_written}"
                  f"{' ERROR: ' + res.error if res.error else ''}")

        def underlying(sym: str) -> Instrument | None:
            return session.scalar(select(Instrument).where(
                Instrument.symbol == sym, Instrument.instrument_type == "INDEX",
                Instrument.provider == provider_name))

        nifty, vix = underlying("NIFTY"), underlying(vix_study.VIX_SYMBOL)
        if nifty is None:
            print("No NIFTY index row: run deploy/tradex.sh backfill first.")
            return 1
        nifty_df = daily_frame(session, nifty.id)
        vix_df = daily_frame(session, vix.id) if vix else nifty_df.iloc[0:0]
        print(f"NIFTY: {len(nifty_df)} daily candles; India VIX: {len(vix_df)} daily candles")

    days = vix_study.run(nifty_df, vix_df)
    report = vix_study.to_markdown(days, len(vix_df), datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"))
    if provider_name == "sample":
        report = "**SAMPLE DATA: synthetic prices, results mean nothing.**\n\n" + report
    out = Path(REPO_ROOT) / "reports"
    out.mkdir(exist_ok=True)
    (out / "vix_study.md").write_text(report, encoding="utf-8")
    days.to_csv(out / "vix_study_days.csv", index_label="day")
    print("\n" + report)
    print(f"\nSaved {out / 'vix_study.md'} and {out / 'vix_study_days.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
