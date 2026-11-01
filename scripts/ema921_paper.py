"""9/21 EMA PAPER engine: runs the backtest rules live, minute by minute, and sends PAPER
entry/exit messages to Telegram. It NEVER places orders (no order code exists in TradeX).

Needs Groww credentials in .env (for live candles) and Telegram settings for messages
(without Telegram it prints the messages instead). Quiet outside market hours.

Usage (from the repository root):
    python scripts/ema921_paper.py                          # 5-minute bars, 09:30-14:30, all 3 modes
    python scripts/ema921_paper.py --tf 15 --cutoff 13:00 --modes ATM_BUY,ATM_SELL
    python scripts/ema921_paper.py --once                   # one step, then exit
On the server it runs by itself as the "ema-paper" service. Settings in .env:
    EMA921_PAPER_TF=5  EMA921_PAPER_START=09:30  EMA921_PAPER_CUTOFF=14:30  EMA921_PAPER_MODES=all
    EMA921_PAPER_OFF=1   keeps it switched off
"""

import argparse
import logging
import os
import time as _time
from datetime import time

import _bootstrap  # noqa: F401

from app.alerts.telegram import AlertError, get_notifier
from app.core.config import get_settings
from app.core.log_setup import setup_logging
from app.db.session import get_sessionmaker
from app.ema921 import engine as E
from app.ema921 import paper as P
from app.providers.base import ProviderError
from app.providers.groww import GrowwProvider

setup_logging()
log = logging.getLogger("ema921-paper")


class SafeNotifier:
    """A failed Telegram message must never stop the paper engine; it is logged instead."""

    def __init__(self, inner):
        self.inner = inner

    def send(self, text: str) -> None:
        log.info("message:\n%s", text)
        try:
            self.inner.send(text)
        except AlertError as exc:
            log.error("Message not sent: %s", exc)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--tf", type=int, default=5, choices=(5, 15, 30))
    p.add_argument("--start", default="09:30")
    p.add_argument("--cutoff", default="14:30")
    p.add_argument("--cross", default="", help="only enter on these crosses of the day, e.g. 1 or 1,2")
    p.add_argument("--modes", default="all", help="all, or e.g. ATM_BUY,ATM_SELL")
    p.add_argument("--stop-atr", type=float, default=2.0,
                   help="trailing stop distance in ATR(14) of the signal bars; 0 turns it off")
    p.add_argument("--once", action="store_true")
    a = p.parse_args()
    modes = E.MODES if a.modes.strip().lower() == "all" else tuple(
        m.strip().upper() for m in a.modes.split(",") if m.strip())
    bad = [m for m in modes if m not in E.MODES]
    if bad:
        p.error(f"unknown mode(s) {bad}; choose from {E.MODES}")
    start, cutoff = time.fromisoformat(a.start), time.fromisoformat(a.cutoff)
    if start not in E.START_CHOICES or cutoff not in E.CUTOFF_CHOICES:
        p.error("start must be 09:30/10:00/10:30 and cutoff 11:30 ... 14:30 (half hours)")
    crosses = frozenset(int(x) for x in a.cross.split(",") if x.strip()) or None
    cfg = E.Config(timeframe=a.tf, start=start, cutoff=cutoff, cross_numbers=crosses,
                   stop_atr_mult=a.stop_atr or None)

    if os.environ.get("EMA921_PAPER_OFF", "").strip() in ("1", "true", "yes"):
        log.info("EMA921_PAPER_OFF is set: the paper engine stays idle.")
        while True:  # idle rather than exit, so Docker doesn't keep restarting it
            _time.sleep(3600)

    s = get_settings()
    provider = GrowwProvider(s)
    notifier = SafeNotifier(get_notifier(s))
    state = P.PaperState()
    Session = get_sessionmaker()
    log.info("PAPER engine started: %sm bars, entries %s-%s, modes %s. No orders, ever.",
             a.tf, a.start, a.cutoff, ",".join(modes))
    while True:
        try:
            client = provider.client()
            with Session() as session:
                log.info(P.step(session, client, notifier, cfg, modes, state, P.utc_now()))
        except ProviderError as exc:
            log.error("Groww unavailable: %s", exc)
        except Exception:
            log.exception("paper step crashed; will retry next minute")
        if a.once:
            return 0
        # Wake 5 seconds after each minute, when the previous 1-minute candle has closed.
        now = _time.time()
        _time.sleep(60 - (now % 60) + 5)


if __name__ == "__main__":
    raise SystemExit(main())
