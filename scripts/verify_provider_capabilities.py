"""Milestone 0: test what the Groww API really provides on your account.

Needs Groww credentials in .env (see docs/02_SETUP_WINDOWS.md). Read-only: it
never places, modifies or cancels orders.

Usage:
    python scripts/verify_provider_capabilities.py
Writes reports/provider_capability_report.md and prints the table.
"""

import sys
from datetime import UTC, datetime
from pathlib import Path

import _bootstrap  # noqa: F401

from app.core.config import REPO_ROOT, get_settings
from app.providers.base import ProviderError
from app.providers.groww import GrowwProvider
from app.services.capability_check import CapabilityChecker, to_markdown


def main() -> int:
    try:
        client = GrowwProvider(get_settings())._connect()
    except ProviderError as exc:
        print(f"Cannot log in to Groww: {exc}")
        return 1
    now = datetime.now(UTC)
    rows = CapabilityChecker(client, now).run_all()
    report = to_markdown(rows, now)
    out = Path(REPO_ROOT) / "reports" / "provider_capability_report.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"Saved to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
