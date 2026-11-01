"""Backtest ST-1.0, the 09:20 hedged short straddle on NIFTY weekly options (rules in
backend/app/straddle/__init__.py). Writes reports/straddle_backtest.md and every leg to
reports/straddle_trades.csv. Needs no Groww access (load first with straddle_load.py).

Usage (from the repository root):
    python scripts/straddle_backtest.py                    # the last 2 years, default slippage
    python scripts/straddle_backtest.py --slippage-pct 0 --slippage-points 0
On the server: deploy/tradex.sh straddle-backtest [options]
"""

import argparse
import sys
from datetime import date
from pathlib import Path

import _bootstrap  # noqa: F401
import pandas as pd

from app.core.config import REPO_ROOT, get_settings
from app.db.session import get_sessionmaker
from app.ema921 import loader as EL
from app.ema921 import store as S
from app.services.metrics import daily_frame
from app.services.vix_warning import vix_instrument
from app.straddle import engine as E
from app.straddle import report as R


def vix_closes(session) -> pd.Series | None:
    inst = vix_instrument(session, get_settings().data_provider)
    if inst is None:
        return None
    df = daily_frame(session, inst.id)
    return pd.Series(df["close"].to_numpy(), index=[t.date() for t in df.index])


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--years", type=int, default=2)
    p.add_argument("--from", dest="start_day")
    p.add_argument("--to", dest="end_day")
    p.add_argument("--slippage-pct", type=float, default=0.5,
                   help="slippage per side as a percent of the price (0.5 means 0.5%%)")
    p.add_argument("--slippage-points", type=float, default=0.5, help="plus these points per side")
    p.add_argument("--no-vix-filter", action="store_true", help="trade VIX-jump days too")
    a = p.parse_args()
    start, end = EL.default_range(EL.ist_today(), a.years)
    start = date.fromisoformat(a.start_day) if a.start_day else start
    end = date.fromisoformat(a.end_day) if a.end_day else end
    cfg = E.Config(slippage_pct=a.slippage_pct / 100, slippage_points=a.slippage_points,
                   vix_filter=not a.no_vix_filter)

    with get_sessionmaker()() as session:
        spot = S.load_spot(session, start, end, 0)
        if spot.empty:
            print("No NIFTY 1-minute data stored yet. Run deploy/tradex.sh straddle-load first.")
            return 1
        vix = vix_closes(session)
        if cfg.vix_filter and (vix is None or vix.empty):
            print("No India VIX history stored, so every day would be NO TRADE. "
                  "Run deploy/tradex.sh vix-study once to load it, or use --no-vix-filter.")
            return 1
        res = E.run(spot, S.DbOptionData(session), start, end, cfg, vix)

    out = Path(REPO_ROOT) / "reports"
    out.mkdir(exist_ok=True)
    md = R.to_markdown(res, start, end)
    (out / "straddle_backtest.md").write_text(md, encoding="utf-8")
    n = R.write_csv(res, out / "straddle_trades.csv")
    print("\n" + md)
    print(f"Saved reports/straddle_backtest.md and {n} legs to reports/straddle_trades.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
