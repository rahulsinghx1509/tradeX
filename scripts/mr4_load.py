"""Load what the MR-4 mean-reversion backtest needs from Groww (NIFTY + BANKNIFTY, 3 years).

Spot 1m, expiries, India VIX 1m, near-month futures 1m (for VWAP volume) and the option legs the
rules trade. Read-only: no orders. Days already stored are skipped, so it is safe to stop
(Ctrl+C) and run again. Needs Groww credentials in .env. Rules: backend/app/meanrev/__init__.py.

Usage (from the repository root):
    python scripts/mr4_load.py                    # 2023-11-01 to 2026-10-31
    python scripts/mr4_load.py --from 2024-11-01 --to 2026-10-31
On the server: deploy/tradex.sh mr4-load
"""

import argparse
import sys
import time
from datetime import date

import _bootstrap  # noqa: F401

from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.meanrev import FIRST_DAY, LAST_DAY
from app.meanrev import loader as ML
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
    p.add_argument("--to", dest="end", help=f"last day, YYYY-MM-DD (default {LAST_DAY})")
    args = p.parse_args()
    start = date.fromisoformat(args.start) if args.start else FIRST_DAY
    end = date.fromisoformat(args.end) if args.end else LAST_DAY

    provider = GrowwProvider(get_settings())
    if not connect(provider):
        print("Tip: stop the services that also log in to Groww while this runs (see "
              "docs/01 §27), then run deploy/tradex.sh mr4-load again.")
        return 1
    print(f"Loading MR-4 data (NIFTY + BANKNIFTY) {start} to {end}. Safe to stop and re-run.")
    with get_sessionmaker()() as session:
        reps = ML.load_all(session, provider.client(), start, end)
    errors = [e for r in reps for e in r.errors]
    fetched = sum(r.contract_days_fetched for r in reps)
    print(f"Option contract-days loaded this run: {fetched}.")
    if errors:
        print(f"\n{len(errors)} error(s); run the command again to retry. First few:")
        for e in errors[:10]:
            print("  " + e)
        return 1
    print("Done. Next: deploy/tradex.sh mr4-backtest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
