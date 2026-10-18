"""Load daily candles (with open interest) of every expired monthly future since 2020.

Needs DATA_PROVIDER=groww and Groww credentials in .env. Read-only towards Groww: it never
places, modifies or cancels orders. Writes to the TradeX database only: expired contracts are
stored as inactive instruments, so the collector and dashboard ignore them.
Safe to stop and run again: months already loaded are skipped.

Usage (from the repository root):
    python scripts/load_futures_history.py
    python scripts/load_futures_history.py --symbols NIFTY,SBIN --first-year 2023
On the server: deploy/tradex.sh futures-history
"""

import argparse
import sys
from collections import Counter
from datetime import UTC, datetime

import _bootstrap  # noqa: F401

from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.providers.factory import get_provider
from app.services import futures_history as fh


def main() -> int:
    s = get_settings()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--symbols", default=",".join(s.underlying_list))
    p.add_argument("--first-year", type=int, default=2020)
    args = p.parse_args()
    symbols = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]

    provider = get_provider(s)
    if not hasattr(provider, "expired_future"):
        print(f"DATA_PROVIDER={s.data_provider} cannot list expired contracts; use groww.")
        return 1
    print(f"Loading expired futures for {len(symbols)} underlyings from {args.first_year}. "
          "This can take 20-40 minutes; it is safe to stop and re-run.")

    def show(r: fh.MonthResult) -> None:
        if r.status != "SKIPPED":
            extra = f" {r.contract} ({r.candles} candles)" if r.status == "LOADED" else ""
            print(f"  {r.symbol:<10} {r.month} {r.status}{extra}{' ' + r.error if r.error else ''}")

    with get_sessionmaker()() as session:
        results = fh.load(session, provider, symbols, args.first_year, datetime.now(UTC), show)

    print("\nSummary (months):")
    for sym in symbols:
        c = Counter(r.status for r in results if r.symbol == sym)
        print(f"  {sym:<10} " + ", ".join(f"{k} {v}" for k, v in sorted(c.items())))
    failed = sum(r.status == "FAILED" for r in results)
    print("\nDone." if not failed else f"\n{failed} month(s) failed - run the command again to retry them.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
