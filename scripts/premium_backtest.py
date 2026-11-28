"""PR-1.0: backtest two hedged NIFTY premium-selling candidates on NSE's daily option file.

A (WT-1.0): iron fly sold at Friday's close, closed at the next trading day's close.
B (WV-1.0): iron fly sold the day after each weekly expiry, held to expiry, only when
India VIX is above NIFTY's 20-day realised volatility. Both skip VIX-jump days (VJ-1.0).
Rules and pass bar are fixed in backend/app/premium/__init__.py (docs/01 §29).

Read-only: reads fo_daily, NIFTY and India VIX daily candles. Writes reports/premium_backtest.md
and reports/premium_trades.csv. Takes a few minutes. No orders, ever.

Usage (from the repository root):
    python scripts/premium_backtest.py
On the server: ~/tradex/deploy/tradex.sh premium-backtest
"""

import sys
from datetime import UTC, date, datetime
from pathlib import Path

import _bootstrap  # noqa: F401
import pandas as pd
from sqlalchemy import func, select

from app import premium as P
from app.core.config import REPO_ROOT, get_settings
from app.db.models import FoDaily, Instrument
from app.db.session import get_sessionmaker
from app.premium import engine as E
from app.premium import report as R
from app.services import option_metrics as om
from app.services.data_quality import VIX_SYMBOL
from app.services.metrics import daily_frame


def main() -> int:
    s = get_settings()
    provider = s.data_provider.lower()
    with get_sessionmaker()() as session:
        first, last = session.execute(
            select(func.min(FoDaily.trade_date), func.max(FoDaily.trade_date))
            .where(FoDaily.symbol == P.SYMBOL)).one()
        if first is None:
            print("fo_daily has no NIFTY rows: run ~/tradex/deploy/tradex.sh fo-history first.")
            return 1

        def inst(sym: str, kinds: tuple[str, ...] | None = None) -> Instrument | None:
            q = select(Instrument).where(Instrument.symbol == sym, Instrument.provider == provider)
            if kinds:
                q = q.where(Instrument.instrument_type.in_(kinds))
            return session.scalar(q.limit(1))

        und, vix_inst = inst(P.SYMBOL, ("INDEX",)), inst(VIX_SYMBOL)
        if und is None:
            print("No NIFTY index row: run the daily backfill first.")
            return 1
        spot = daily_frame(session, und.id)["close"]
        vix = daily_frame(session, vix_inst.id)["close"] if vix_inst else None
        frames = []
        for year in range(max(first.year, P.FIRST_DAY.year), last.year + 1):
            df = om.load_frame(session, P.SYMBOL, date(year, 1, 1), date(year, 12, 31))
            df = E.trim(df)
            print(f"  {year}: {len(df):,} NIFTY option rows")
            frames.append(df)
    frame = pd.concat(frames, ignore_index=True)
    book = E.Book(frame)
    skipped: dict[str, int] = {}
    trades = E.build_a(book, spot, vix, skipped) + E.build_b(book, spot, vix, skipped)
    print(f"  built {sum(t.candidate == 'A' for t in trades)} A and "
          f"{sum(t.candidate == 'B' for t in trades)} B trades (before filters)")
    coverage = {"first": first, "last": last,
                "spot_last": spot.index.max().date() if len(spot) else None,
                "vix_last": vix.index.max().date() if vix is not None and len(vix) else "MISSING"}
    report = R.to_markdown(trades, skipped, coverage, datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"))
    if vix is None:
        report = "**India VIX missing: VJ-1.0 blocks every entry (NO_DATA = NO TRADE).**\n\n" + report
    if provider == "sample":
        report = "**SAMPLE DATA: synthetic prices, results mean nothing.**\n\n" + report
    out = Path(REPO_ROOT) / "reports"
    out.mkdir(exist_ok=True)
    (out / "premium_backtest.md").write_text(report, encoding="utf-8")
    E.to_frame(trades).to_csv(out / "premium_trades.csv", index=False)
    print("\n" + report)
    print(f"Saved {out / 'premium_backtest.md'} and {out / 'premium_trades.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
