"""Slippage and the Indian options cost stack.

Every rate here is NOT_VERIFIED against the current exchange and SEBI circulars and Groww's
brokerage page. They sit in one dated table so a change is a one-line edit with a date.
Rules doc #15 and #16.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

TICK = 0.05


@dataclass(frozen=True)
class CostRates:
    effective_from: date
    brokerage_per_order: float  # ₹ per executed order (Groww F&O flat fee)
    stt_sell: float  # share of SELL premium (options)
    exchange_txn: float  # NSE transaction charge, share of premium (both sides)
    sebi_per_crore: float  # ₹ per ₹1 crore of turnover (both sides)
    stamp_buy: float  # share of BUY premium
    gst: float  # on brokerage + exchange charge + SEBI fee


# NOT_VERIFIED. Oldest first; the last row whose date is on or before the trade date applies.
RATES: tuple[CostRates, ...] = (
    CostRates(date(2023, 4, 1), 20.0, 0.000625, 0.000495, 10.0, 0.00003, 0.18),
    CostRates(date(2024, 10, 1), 20.0, 0.001, 0.0003503, 10.0, 0.00003, 0.18),
)


def rates_for(day: date) -> CostRates:
    usable = [r for r in RATES if r.effective_from <= day]
    return usable[-1] if usable else RATES[0]


def _round_tick(price: float, up: bool) -> float:
    # Small epsilon so 100.05 stays 100.05 instead of becoming 100.10 through float noise.
    steps = price / TICK
    steps = math.ceil(steps - 1e-9) if up else math.floor(steps + 1e-9)
    return round(steps * TICK, 2)


def buy_fill(price: float, pct: float, points: float) -> float:
    """What a buy really costs: price + 0.5% + fixed points, rounded UP to the tick."""
    return _round_tick(price * (1 + pct) + points, up=True)


def sell_fill(price: float, pct: float, points: float) -> float:
    """What a sell really gets: price - 0.5% - fixed points, rounded DOWN, never below 0.05."""
    return max(TICK, _round_tick(price * (1 - pct) - points, up=False))


def charges(buy_value: float, sell_value: float, day: date, orders: int = 2) -> dict[str, float]:
    """Regulatory and broker charges for one round trip (₹), by component."""
    r = rates_for(day)
    turnover = buy_value + sell_value
    brokerage = r.brokerage_per_order * orders
    exchange = turnover * r.exchange_txn
    sebi = turnover * r.sebi_per_crore / 1e7
    out = {
        "brokerage": brokerage,
        "stt": sell_value * r.stt_sell,
        "exchange": exchange,
        "sebi": sebi,
        "stamp": buy_value * r.stamp_buy,
        "gst": (brokerage + exchange + sebi) * r.gst,
    }
    out = {k: round(v, 2) for k, v in out.items()}
    out["total"] = round(sum(out.values()), 2)
    return out
