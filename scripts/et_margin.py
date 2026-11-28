"""How much margin does Groww ask for the ET-1.0 trade (sell 1 lot of the ATM NIFTY option)?

Asks Groww's margin CALCULATOR (growwapi `get_order_margin_details`, POST /margins/detail/orders)
for a few orders and prints Groww's answer as it comes back. **It places no order**: that
endpoint only calculates. The answer's field names are NOT_VERIFIED, so the raw reply is shown.

Orders asked about (1 lot = 65, nearest weekly expiry, ATM from the last stored NIFTY price):
  1. sell the ATM call, intraday (MIS)        2. the same, carry-forward (NRML)
  3. sell the ATM put, intraday (MIS)         4. the same, carry-forward (NRML)
  5. sell the ATM call + buy the call 150 points higher, intraday (a hedged version, to compare)

Usage (from the repository root):  python scripts/et_margin.py [--spot 26150]
On the server: deploy/tradex.sh et-margin
"""

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import _bootstrap  # noqa: F401

from app.core.config import REPO_ROOT, get_settings
from app.db.session import get_sessionmaker
from app.ema921 import engine as EE
from app.ema921 import loader as L
from app.ema921 import store as S
from app.providers.base import ProviderError
from app.providers.groww import GrowwProvider

HEDGE = 150
REPORTS = Path(REPO_ROOT) / "reports"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--spot", type=float, help="NIFTY level to pick the ATM strike from")
    a = p.parse_args()
    cfg = L.config_for("NIFTY")
    today = L.ist_today()
    provider = GrowwProvider(get_settings())
    try:
        provider._connect()
    except ProviderError as exc:
        print(f"Cannot reach Groww: {exc}")
        return 1
    client = provider.client()
    rep = L.LoadReport()
    with get_sessionmaker()() as session:
        spot = a.spot
        if spot is None:
            df = S.load_spot(session, today - timedelta(days=10), today, 0, "NIFTY")
            if df.empty:
                print("No NIFTY price stored in the last 10 days. Run again with --spot 26150 "
                      "(today's NIFTY level).")
                return 1
            spot = float(df["close"].dropna().iloc[-1])
        L.load_expiries(session, client, today, today + timedelta(days=14), rep, print, "NIFTY")
        expiry = EE.pick_expiry(today, S.expiries(session, "NIFTY"), cfg.roll_on_expiry_day)
        if expiry is None:
            print("No upcoming NIFTY expiry found. " + "; ".join(rep.errors[:3]))
            return 1
        L.load_contracts(session, client, [expiry], rep, "NIFTY")
        data = S.DbOptionData(session, "NIFTY")
        atm = EE.atm_strike(spot, cfg.strike_step)

        def trading_symbol(strike: float, kind: str) -> str | None:
            g = data.contract(expiry, strike, kind) or L.guess_symbol(session, expiry, strike, kind)
            try:
                return str(client.get_instrument_by_groww_symbol(groww_symbol=g)["trading_symbol"])
            except Exception as exc:  # noqa: BLE001 - shown to the user, nothing else to do
                print(f"  could not find {g}: {str(exc)[:200]}")
                return None

        ce, pe = trading_symbol(atm, "CE"), trading_symbol(atm, "PE")
        wing = trading_symbol(atm + HEDGE, "CE")

    def order(ts, side, product):
        return {"trading_symbol": ts, "transaction_type": side, "quantity": cfg.qty,
                "price": 0, "order_type": "MARKET", "product": product, "exchange": "NSE"}

    asks = [(f"1. Sell {atm:g} CE, intraday (MIS)", [order(ce, "SELL", "MIS")]),
            (f"2. Sell {atm:g} CE, carry-forward (NRML)", [order(ce, "SELL", "NRML")]),
            (f"3. Sell {atm:g} PE, intraday (MIS)", [order(pe, "SELL", "MIS")]),
            (f"4. Sell {atm:g} PE, carry-forward (NRML)", [order(pe, "SELL", "NRML")]),
            (f"5. Sell {atm:g} CE + buy {atm + HEDGE:g} CE, intraday (MIS)",
             [order(ce, "SELL", "MIS"), order(wing, "BUY", "MIS")])]
    lines = [f"# ET-1.0 margin check ({date.today()})", "",
             f"NIFTY {spot:,.2f} → ATM {atm:g}, expiry {expiry}, 1 lot = {cfg.qty}. "
             "Groww's margin calculator only: **no order was placed**.", ""]
    for title, orders in asks:
        if any(o["trading_symbol"] is None for o in orders):
            lines += [f"## {title}", "", "Skipped: contract not found.", ""]
            continue
        try:
            reply = client.get_order_margin_details(segment="FNO", orders=orders)
            body = json.dumps(reply, indent=2, default=str)
        except Exception as exc:  # noqa: BLE001 - shown to the user
            body = f"ERROR: {str(exc)[:400]}"
        lines += [f"## {title}", "", "Symbols: " + ", ".join(o["trading_symbol"] for o in orders),
                  "", "```", body, "```", ""]
    text = "\n".join(lines)
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "et_margin.md").write_text(text, encoding="utf-8")
    print("\n" + text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
