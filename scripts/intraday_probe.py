"""Check what Groww gives for intraday (1/5/15-minute candles, history depth, quote fields)
and whether the server can download NSE's NIFTY 50 / Next 50 member lists and delivery file.

Needs Groww credentials in .env. Read-only: it never places, modifies or cancels orders.
Takes a few minutes (calls are spaced out). Writes reports/intraday_probe.md (no prices).

Usage (from the repository root):
    python scripts/intraday_probe.py
    python scripts/intraday_probe.py --stock SBIN
On the server: deploy/tradex.sh intraday-probe
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

import _bootstrap  # noqa: F401
import httpx

from app.core.config import REPO_ROOT, get_settings
from app.providers.base import ProviderError
from app.providers.groww import GrowwProvider
from app.services import intraday_probe as ip


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--stock", default="RELIANCE", help="the stock to probe (default RELIANCE)")
    args = p.parse_args()
    stock = args.stock.strip().upper()

    provider = GrowwProvider(get_settings())
    try:
        provider._connect()
        client = provider.client()  # spaced out and retried, so Groww's rate limit isn't hit
        targets = ip.target_instruments(provider.list_instruments(["NIFTY", stock], 1), stock)
        instruments = client.get_all_instruments()
    except ProviderError as exc:
        print(f"Cannot reach Groww: {exc}")
        return 1
    if not targets:
        print(f"Neither NIFTY nor {stock} found in Groww's instrument list.")
        return 1

    now = datetime.now(UTC)
    print(f"Probing {', '.join(targets)}; this takes a few minutes...")
    res = ip.run_groww(client, targets, now)
    print("Checking NSE files...")
    with httpx.Client() as http:
        ip.run_nse(res, http, instruments)
    report = ip.to_markdown(res)
    out = Path(REPO_ROOT) / "reports" / "intraday_probe.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print("\n" + report)
    print(f"Saved to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
