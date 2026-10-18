"""Check, from this machine, which free outside data sources download and how far back they go.

Covers NSE archive files (F&O bhavcopy, participant OI and volume, ban list, MWPL) and FRED
global series. Needs no credentials. Downloads one small file per source and period, reads
the header, and discards it; nothing is stored. Takes about 2 minutes (requests are spaced).
Writes reports/data_sources.md.

Usage (from the repository root):
    python scripts/check_data_sources.py
    python scripts/check_data_sources.py --no-fred
On the server: deploy/tradex.sh sources
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

import _bootstrap  # noqa: F401
import httpx

from app.core.config import REPO_ROOT
from app.services import source_check as sc


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--no-fred", action="store_true", help="skip the FRED global series")
    args = p.parse_args()
    now = datetime.now(UTC)
    print("Checking NSE archives and FRED; this takes about 2 minutes...")
    with httpx.Client() as client:
        checks, note = sc.run(client, now, fred=not args.no_fred)
    report = sc.to_markdown(checks, note, now)
    out = Path(REPO_ROOT) / "reports" / "data_sources.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print("\n" + report)
    print(f"Saved to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
