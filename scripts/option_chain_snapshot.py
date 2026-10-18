"""Option-chain snapshots: check what Groww sends, or store today's chains by hand.

The collector stores the chains automatically once a day from 15:15 IST. This script is for
checking. Read-only towards Groww: it never places, modifies or cancels orders.

Usage (from the repository root):
    python scripts/option_chain_snapshot.py --fields          # print field names, store nothing
    python scripts/option_chain_snapshot.py --store           # store today's chains now
On the server: deploy/tradex.sh chain-fields
"""

import argparse
import json
import sys
from datetime import UTC, datetime

import _bootstrap  # noqa: F401

from app.core.config import get_settings
from app.core.market_time import is_trading_day, to_ist
from app.db.session import get_sessionmaker
from app.providers.factory import get_provider
from app.services import option_chain as oc


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--symbol", default="NIFTY", help="underlying for --fields")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--fields", action="store_true")
    g.add_argument("--store", action="store_true")
    args = p.parse_args()

    provider = get_provider(get_settings())
    if not oc.supports_chains(provider):
        print("This data provider has no option chains; use DATA_PROVIDER=groww.")
        return 1
    now = datetime.now(UTC)
    if args.fields:
        exp = provider.upcoming_expiries(args.symbol, to_ist(now).date(), 1)
        if not exp:
            print(f"No upcoming expiry for {args.symbol}.")
            return 1
        payload = provider.option_chain(args.symbol, exp[0])
        print(f"{args.symbol} expiry {exp[0]}: {len(payload.get('strikes') or {})} strikes")
        print(json.dumps(oc.field_names(payload), indent=2))
        return 0
    if not is_trading_day(to_ist(now).date()):
        print("Not a trading day: nothing stored (a snapshot would carry stale prices).")
        return 1
    with get_sessionmaker()() as session:
        res = oc.snapshot(session, provider, oc.underlyings(session, provider), now)
    for r in res:
        print(f"  {r.symbol:<10} {r.expiry or '-':<10} {r.status:<7} {r.strikes} strikes"
              f"{' ' + r.error if r.error else ''}")
    return 0 if all(r.status == "SUCCESS" for r in res) else 1


if __name__ == "__main__":
    sys.exit(main())
