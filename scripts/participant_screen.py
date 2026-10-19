"""One-shot screen of FII / retail / Pro positioning (study PS-1.0), development period only.

Reads NIFTY's daily candles and the participant OI history (load it first with
deploy/tradex.sh participant-history). Scores each positioning reading against NIFTY's next
5/10/20 days and next month's volatility with the same rules as the earlier screens. Read-only.
Writes reports/participant_screen.md and reports/participant_screen_results.csv.
Validation stays held back.

Usage (from the repository root):
    python scripts/participant_screen.py
On the server: deploy/tradex.sh participant-screen
"""

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
from app.services import participant_oi as po
from app.services import participant_screen as ps
from app.services.metrics import daily_frame


def main() -> int:
    provider = get_settings().data_provider.lower()
    with get_sessionmaker()() as session:
        raw = po.load_frame(session)
        if raw.empty:
            print("participant_oi is empty: run deploy/tradex.sh participant-history first.")
            return 1
        nifty = session.scalar(select(Instrument).where(
            Instrument.symbol == fs.MARKET_SYMBOL, Instrument.instrument_type == "INDEX",
            Instrument.provider == provider))
        if nifty is None:
            print("No NIFTY index row for this provider.")
            return 1
        base = fs.build_table(fs.MARKET_SYMBOL, daily_frame(session, nifty.id), pd.DataFrame(),
                              None, None)
    days = raw["trade_date"].nunique()
    print(f"Participant OI: {days} days, {raw['trade_date'].min()} to {raw['trade_date'].max()}.")
    dev = fs.dev_only(ps.build_table(base, po.features(raw)))
    res = ps.screen(dev)
    report = ps.to_markdown(dev, res, datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"), days)
    if provider == "sample":
        report = "**SAMPLE DATA: synthetic prices, results mean nothing.**\n\n" + report
    out = Path(REPO_ROOT) / "reports"
    out.mkdir(exist_ok=True)
    (out / "participant_screen.md").write_text(report, encoding="utf-8")
    res.to_csv(out / "participant_screen_results.csv", index=False)
    print("\n" + report)
    print(f"Saved {out / 'participant_screen.md'} and {out / 'participant_screen_results.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
