"""Multi-strategy PAPER signal engine: EMA-921, ORB-1.0 and ET-1.0 run live on NIFTY options.

Every trade gets a number. Telegram gets one ENTRY message per trade (strategy, contract,
quantity, price, exit plan) and one EXIT message quoting the same trade number, plus a 15:05
day report. PAPER only: no order is ever placed (no order code exists in TradeX).

Usage (from the repository root):
    python scripts/signals_paper.py                         # all 3 strategies, ATM buy
    python scripts/signals_paper.py --strategies ORB-1.0,ET-1.0 --mode ATM_SELL
    python scripts/signals_paper.py --once                  # one step, then exit
On the server it runs by itself as the "signals" service. Settings in .env:
    SIGNALS_STRATEGIES=all  SIGNALS_MODE=ATM_BUY   SIGNALS_OFF=1 keeps it switched off
"""

import argparse
import logging
import os
import time as _time

import _bootstrap  # noqa: F401
from ema921_paper import SafeNotifier

from app.alerts.telegram import get_notifier
from app.core.config import get_settings
from app.core.log_setup import setup_logging
from app.db.session import get_sessionmaker
from app.ema921 import engine as E
from app.ema921.paper import utc_now
from app.live import engine as LE
from app.providers.base import ProviderError
from app.providers.groww import GrowwProvider

setup_logging()
log = logging.getLogger("signals")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--strategies", default="all", help=f"all, or some of {list(LE.STRATEGIES)}")
    p.add_argument("--mode", default="ATM_BUY", choices=E.MODES)
    p.add_argument("--once", action="store_true")
    a = p.parse_args()
    try:
        strategies = LE.parse_strategies(a.strategies)
    except ValueError as exc:
        p.error(str(exc))

    if os.environ.get("SIGNALS_OFF", "").strip() in ("1", "true", "yes"):
        log.info("SIGNALS_OFF is set: the signal engine stays idle.")
        while True:  # idle rather than exit, so Docker doesn't keep restarting it
            _time.sleep(3600)

    s = get_settings()
    provider = GrowwProvider(s)
    notifier = SafeNotifier(get_notifier(s))
    state = LE.LiveState()
    Session = get_sessionmaker()
    log.info("PAPER signal engine started: %s, mode %s. No orders, ever.", ",".join(strategies), a.mode)
    while True:
        try:
            client = provider.client()
            with Session() as session:
                log.info(LE.step(session, client, notifier, strategies, a.mode, state, utc_now()))
        except ProviderError as exc:
            log.error("Groww unavailable: %s", exc)
        except Exception:
            log.exception("signal step crashed; will retry next minute")
        if a.once:
            return 0
        # 5 seconds after each minute, and also at :20 and :35 on 5-minute closes, when the
        # signals and exits are decided (a second look catches a candle Groww sent late).
        _time.sleep(LE.seconds_to_next_look(_time.time()))


if __name__ == "__main__":
    raise SystemExit(main())
