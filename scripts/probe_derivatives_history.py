"""How far back Groww keeps futures and options history, and whether it has open interest.

Needs Groww credentials in .env. Read-only: it never places, modifies or cancels orders.
Writes reports/derivatives_probe.md (counts only, no prices).

Usage (from the repository root):
    python scripts/probe_derivatives_history.py
    python scripts/probe_derivatives_history.py --symbols NIFTY,SBIN --first-year 2022
On the server: deploy/tradex.sh probe
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

import _bootstrap  # noqa: F401

from app.core.config import REPO_ROOT, get_settings
from app.providers.base import ProviderError
from app.providers.groww import GrowwProvider
from app.services import derivatives_probe as dp


def main() -> int:
    s = get_settings()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--symbols", default=",".join(s.underlying_list))
    p.add_argument("--first-year", type=int, default=2020)
    args = p.parse_args()
    symbols = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]

    provider = GrowwProvider(s)
    try:
        provider._connect()
    except ProviderError as exc:
        print(f"Cannot log in to Groww: {exc}")
        return 1
    now = datetime.now(UTC)
    years = dp.default_years(now, args.first_year)
    print(f"Probing {len(symbols)} underlyings x {len(years)} years; this takes a few minutes...")
    rows = []
    for sym in symbols:  # client() spaces calls out, so Groww's rate limit isn't hit
        rows += dp.probe(provider.client(), [sym], years)
        print(f"  {sym} done")
    report = dp.to_markdown(rows, now)
    out = Path(REPO_ROOT) / "reports" / "derivatives_probe.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print("\n" + report)
    print(f"Saved to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
