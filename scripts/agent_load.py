"""Load what the AD-1.0 adaptive agent's intraday candidates need from Groww (NIFTY, 3 years).

NIFTY spot 1m, expiries and every option contract an intraday signal of each day could trade.
Read-only: no orders. Days already stored are skipped, so it is safe to stop (Ctrl+C) and run
again. Needs Groww credentials in .env. Rules: backend/app/agent/__init__.py.
Swing candidates use NSE's daily file instead: run `tradex.sh fo-history` for that.

Usage (from the repository root):
    python scripts/agent_load.py                  # 2023-11-01 to yesterday
    python scripts/agent_load.py --from 2024-11-01 --to 2026-10-31
On the server: ~/tradex/deploy/tradex.sh agent-load
"""

import argparse
import sys
import time
from datetime import date, timedelta

import _bootstrap  # noqa: F401

from app.agent import INTRADAY_FIRST as FIRST_DAY
from app.agent import loader as AL
from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.ema921.loader import ist_today
from app.providers.base import ProviderError
from app.providers.groww import GrowwProvider, is_rate_limited

# Groww refuses logins for a while after too many of them (seen 2026-11-20 right after
# `tradex.sh update`, when every service logs in again at once). Wait and try again instead of
# giving up: about 30 minutes in all. Its exact login limit is NOT_VERIFIED.
LOGIN_WAITS = (60, 120, 300, 600, 900)


def connect(provider: GrowwProvider) -> bool:
    for attempt in range(len(LOGIN_WAITS) + 1):
        try:
            provider._connect()
            return True
        except ProviderError as exc:
            if not is_rate_limited(exc) or attempt == len(LOGIN_WAITS):
                print(f"Cannot reach Groww: {exc}")
                return False
            wait = LOGIN_WAITS[attempt]
            print(f"Groww says its login rate limit is exceeded; waiting {wait // 60} min "
                  f"before trying again (try {attempt + 2} of {len(LOGIN_WAITS) + 1}). "
                  "Ctrl+C to stop.", flush=True)
            time.sleep(wait)
    return False


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--from", dest="start", help=f"first day, YYYY-MM-DD (default {FIRST_DAY})")
    p.add_argument("--to", dest="end", help="last day, YYYY-MM-DD (default yesterday)")
    args = p.parse_args()
    start = date.fromisoformat(args.start) if args.start else FIRST_DAY
    end = date.fromisoformat(args.end) if args.end else ist_today() - timedelta(days=1)

    provider = GrowwProvider(get_settings())
    if not connect(provider):
        print("Tip: run ~/tradex/deploy/tradex.sh pause-live first (stops the other Groww "
              "logins), then run ~/tradex/deploy/tradex.sh agent-load again.")
        return 1
    print(f"Loading AD-1.0 intraday data (NIFTY) {start} to {end}. Safe to stop and re-run.")
    with get_sessionmaker()() as session:
        rep = AL.load_all(session, provider.client(), start, end)
    errors = rep.errors
    fetched = rep.contract_days_fetched
    print(f"Option contract-days loaded this run: {fetched}.")
    if errors:
        print(f"\n{len(errors)} error(s); run the command again to retry. First few:")
        for e in errors[:10]:
            print("  " + e)
        return 1
    print("Done. Next: ~/tradex/deploy/tradex.sh agent-backtest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
