"""Load 5-minute candles for all NIFTY 50 + NIFTY Next 50 stocks (and NIFTY) from Groww.

Step 1 downloads NSE's two member lists and matches them to Groww (the last saved list is reused
if NSE can't be reached). Step 2 fetches each stock in 30-day windows, newest first, until its
history runs out. About 100 symbols x 36 windows for 3 years: allow an hour or two.
Needs Groww credentials in .env. Read-only: no orders. Safe to stop (Ctrl+C) and run again.

Usage (from the repository root):
    python scripts/stock_load.py                 # last 3 years (or as far as Groww goes)
    python scripts/stock_load.py --years 1 --only RELIANCE,SBIN
On the server: deploy/tradex.sh stock-load
"""

import argparse
import sys
from datetime import UTC, datetime

import _bootstrap  # noqa: F401
import httpx

from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.ema921 import loader as EL
from app.providers.base import ProviderError
from app.providers.groww import GrowwProvider
from app.services import intraday_probe as ip
from app.services import source_check as sc
from app.stocks import loader as L
from app.stocks import store as S


def refresh_members(session, client) -> str:
    with httpx.Client() as http:
        checker = sc.Checker(http)
        checker.warm_up()
        lists = [ip.fetch_list(checker, name, urls) for name, urls in ip.INDEX_LISTS.items()]
    got = [(s, lc.name) for lc in lists if lc.status == "OK" for s in lc.symbols]
    if len(lists) != sum(lc.status == "OK" for lc in lists):
        bad = ", ".join(f"{lc.name}: {lc.status} {lc.note}" for lc in lists if lc.status != "OK")
        if S.members(session):
            return f"NSE list download failed ({bad}); reusing the {len(S.members(session))} saved members."
        raise SystemExit(f"NSE list download failed ({bad}) and no saved list exists. Try again later.")
    found, missing = L.match_groww(got, client.get_all_instruments())
    S.save_members(session, found)
    session.commit()
    note = f"Members: {len(found)} matched to Groww"
    return note + (f"; not in Groww: {', '.join(missing)}" if missing else "")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--years", type=int, default=3, help="how many years back (default 3)")
    p.add_argument("--only", help="comma-separated symbols to load (default: all members + NIFTY)")
    p.add_argument("--skip-members", action="store_true", help="don't re-download the NSE lists")
    args = p.parse_args()
    start, _ = EL.default_range(EL.ist_today(), args.years)
    end = ip.last_full_day(datetime.now(UTC))

    provider = GrowwProvider(get_settings())
    try:
        provider._connect()
        client = provider.client()
    except ProviderError as exc:
        print(f"Cannot reach Groww: {exc}")
        return 1
    with get_sessionmaker()() as session:
        if not args.skip_members:
            print(refresh_members(session, client))
        only = [s.strip() for s in args.only.split(",")] if args.only else None
        print(f"Loading 5-minute candles {start} to {end}. Safe to stop and re-run.")
        rep = L.load_all(session, client, start, end, only)
    print(f"\nSymbols {rep.symbols}; windows fetched {rep.windows_fetched} "
          f"(empty {rep.windows_empty}); candles saved {rep.candles:,}.")
    if rep.first_day:
        oldest = sorted(rep.first_day.values())
        print(f"Earliest day found: {oldest[0]} (median first day {oldest[len(oldest) // 2]}).")
    if rep.errors:
        print(f"\n{len(rep.errors)} error(s); run the command again to retry. First few:")
        for e in rep.errors[:10]:
            print("  " + e)
        return 1
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
