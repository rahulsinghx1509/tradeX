"""Study VS-1.0: protected monthly option selling on real NSE option prices, development only.

Reads daily candles, India VIX and the F&O history (fo_daily). For each underlying and each
monthly expiry before the validation period it sells a fly and a condor (both with long wings,
so the worst loss is known), holds to expiry, and scores the results with and without the
VIX-jump warning and the call-wall filter. Also checks that the call wall is not just the IV
level. Read-only. Writes reports/option_selling.md and reports/option_selling_trades.csv.
Takes about 5-10 minutes on the small server. Validation stays held back.

Usage (from the repository root):
    python scripts/option_selling.py
On the server: deploy/tradex.sh option-selling
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
from app.services import option_selling as vs
from app.services.data_quality import VIX_SYMBOL
from app.services.metrics import daily_frame


def main() -> int:
    s = get_settings()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--symbols", default=",".join(s.underlying_list))
    args = p.parse_args()
    symbols = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
    provider = s.data_provider.lower()
    last_year = fs.VALIDATION_FROM.year  # rows after the cut are never needed

    trades, tables, walls, skipped = [], [], {}, {}
    with get_sessionmaker()() as session:
        first = session.scalar(select(func.min(FoDaily.trade_date)))
        if first is None:
            print("fo_daily is empty: run deploy/tradex.sh fo-history first.")
            return 1

        def inst(sym: str, kinds: tuple[str, ...]) -> Instrument | None:
            return session.scalar(select(Instrument).where(
                Instrument.symbol == sym, Instrument.instrument_type.in_(kinds),
                Instrument.provider == provider).limit(1))

        for sym in symbols:
            und = inst(sym, ("INDEX", "STOCK"))
            if und is None:
                print(f"  {sym}: no index/stock row, skipped")
                continue
            fut = inst(sym, ("FUTURE",))
            spot = daily_frame(session, und.id)
            frames, metrics = [], []
            for year in range(first.year, last_year + 1):
                df = om.load_frame(session, sym, date(year, 1, 1), date(year, 12, 31))
                if not df.empty:
                    frames.append(df)
                    metrics.append(om.compute(df))
            if not frames:
                print(f"  {sym}: no option rows, skipped")
                continue
            frame, m = pd.concat(frames), pd.concat(metrics)
            got = vs.trades_for(sym, frame, spot["close"], m, fut.lot_size if fut else None)
            months = len(vs.entry_days(frame))
            skipped[sym] = sum(months - sum(t.structure == st for t in got) for st in vs.STRUCTURES)
            trades += got
            base = fs.build_table(sym, spot, pd.DataFrame(), None, None)
            t = osc.build_table(base, m)
            t["ret_20d"] = base["ret_20d"]
            tables.append(t)
            walls[sym] = m["call_wall_pct"]
            print(f"  {sym}: {months} months, {len(got)} trades")
        vix_inst = session.scalar(select(Instrument).where(
            Instrument.symbol == VIX_SYMBOL, Instrument.provider == provider))
        vix = daily_frame(session, vix_inst.id)["close"] if vix_inst else None

    if not trades:
        print("No trades could be built.")
        return 1
    wall = pd.DataFrame(walls)
    rank = wall.rank(axis=1, pct=True).where(wall.notna().sum(axis=1) >= fs.MIN_NAMES, axis=0)
    t = vs.dev_trades(vs.tag(pd.DataFrame([vars(x) for x in trades]), vix, rank))
    check = vs.residual_check(osc.dev_only(pd.concat(tables)))
    report = vs.to_markdown(t, check, skipped, datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"))
    if vix is None:
        report = "**India VIX missing: variant B and C filters could not be applied.**\n\n" + report
    if provider == "sample":
        report = "**SAMPLE DATA: synthetic prices, results mean nothing.**\n\n" + report
    out = Path(REPO_ROOT) / "reports"
    out.mkdir(exist_ok=True)
    (out / "option_selling.md").write_text(report, encoding="utf-8")
    t.to_csv(out / "option_selling_trades.csv", index=False)
    print("\n" + report)
    print(f"Saved {out / 'option_selling.md'} and {out / 'option_selling_trades.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
