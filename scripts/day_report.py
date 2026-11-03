"""Send the ONE PAPER day report (text + picture, every strategy) to Telegram now.

The signals service sends it by itself at 15:05. Use this to send it again, or for another day.

Usage (from the repository root):
    python scripts/day_report.py                    # today
    python scripts/day_report.py --date 2026-11-03
On the server: deploy/tradex.sh day-report [--date YYYY-MM-DD]
"""

import argparse
import sys
from datetime import date

import _bootstrap  # noqa: F401
from sqlalchemy import select

from app.alerts.telegram import get_notifier
from app.core.config import get_settings
from app.db.models import PaperTrade, SignalTrade
from app.db.session import get_sessionmaker
from app.ema921.loader import ist_today
from app.live import engine as LE
from app.live.report_card import render_png


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--date", help="YYYY-MM-DD (default today)")
    a = p.parse_args()
    day = date.fromisoformat(a.date) if a.date else ist_today()
    with get_sessionmaker()() as session:
        rows = list(session.scalars(select(SignalTrade).where(SignalTrade.trade_date == day)))
        ema = list(session.scalars(select(PaperTrade).where(PaperTrade.trade_date == day)))
    text = LE.summary_message(day, rows, ema)
    print(text)
    notifier = get_notifier(get_settings())
    notifier.send(text)
    notifier.send_photo(render_png(day, rows, ema),
                        f"PAPER day report {day:%d %b %Y}, all strategies. No order was placed.")
    print(f"\nSent to Telegram: {len(rows)} numbered trades, {len(ema)} 9/21 EMA paper legs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
