"""Check how far back Groww keeps 1-minute candles for NIFTY spot and for EXPIRED NIFTY options.

Run this first: the 9/21 EMA backtest needs both. Needs Groww credentials in .env.
Read-only: it never places, modifies or cancels orders. About 30 Groww calls, under a minute.
Writes reports/ema921_probe.md (counts and times only, no prices).

Usage (from the repository root):
    python scripts/ema921_probe.py
On the server: deploy/tradex.sh ema-probe
"""

import argparse
import sys
from pathlib import Path

import _bootstrap  # noqa: F401

from app.core.config import REPO_ROOT, get_settings
from app.ema921 import loader as L
from app.ema921 import probe as P
from app.providers.base import ProviderError
from app.providers.groww import GrowwProvider


def main() -> int:
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter).parse_args()
    provider = GrowwProvider(get_settings())
    try:
        provider._connect()
    except ProviderError as exc:
        print(f"Cannot reach Groww: {exc}")
        return 1
    today = L.ist_today()
    print("Probing Groww 1-minute history for NIFTY spot and expired options...")
    report = P.to_markdown(P.run(provider.client(), today), today)
    out = Path(REPO_ROOT) / "reports" / "ema921_probe.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print("\n" + report)
    print(f"Saved to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
