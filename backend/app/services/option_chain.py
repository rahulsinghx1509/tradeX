"""Daily option-chain snapshots: TradeX's own history of implied volatility, put-call ratio and
open interest by strike, because Groww keeps no option-chain history.

Once per trading day, shortly before the close, the collector stores the full chain of the next
CHAIN_EXPIRIES expiries for every tracked underlying, exactly as Groww returned it. The field
meanings are NOT_VERIFIED until checked against real snapshots (`deploy/tradex.sh chain-fields`);
studies will parse the stored payload once they are.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.market_time import to_ist, utc_now
from app.db.models import Instrument, OptionChainSnapshot
from app.providers.base import MarketDataProvider, ProviderError
from app.services.ingestion import _finish, _insert, _start_run

log = logging.getLogger(__name__)

CHAIN_EXPIRIES = 2
CHAIN_TIME_IST = time(15, 15)  # snapshot from this time on, once per day


@dataclass
class ChainResult:
    symbol: str
    expiry: str | None
    status: str  # SUCCESS / FAILED / EMPTY
    strikes: int = 0
    error: str | None = None


def supports_chains(provider: MarketDataProvider) -> bool:
    return hasattr(provider, "option_chain") and hasattr(provider, "upcoming_expiries")


def underlyings(session: Session, provider: MarketDataProvider) -> list[str]:
    """Tracked underlyings: the active index and stock rows."""
    return list(session.scalars(
        select(Instrument.symbol).where(Instrument.provider == provider.name, Instrument.is_active,
                                        Instrument.instrument_type.in_(("INDEX", "STOCK")))
        .order_by(Instrument.symbol)).all())


def snapshot(session: Session, provider: MarketDataProvider, symbols: list[str],
             now: datetime) -> list[ChainResult]:
    """Store today's chain for the next CHAIN_EXPIRIES expiries of each symbol."""
    today = to_ist(now).date()
    run = _start_run(session, provider, "option_chain")
    out: list[ChainResult] = []
    for sym in symbols:
        try:
            expiries = provider.upcoming_expiries(sym, today, CHAIN_EXPIRIES)
        except ProviderError as exc:
            out.append(ChainResult(sym, None, "FAILED", error=str(exc)))
            continue
        for exp in expiries:
            try:
                payload = provider.option_chain(sym, exp)
            except ProviderError as exc:
                out.append(ChainResult(sym, exp.isoformat(), "FAILED", error=str(exc)))
                continue
            strikes = len(payload.get("strikes") or {})
            if not strikes:
                out.append(ChainResult(sym, exp.isoformat(), "EMPTY"))
                continue
            stmt = _insert(session)(OptionChainSnapshot).values(
                provider=provider.name, symbol=sym, expiry_date=exp, trade_date=today,
                taken_at=utc_now(), strikes=strikes, payload=payload,
                is_sample=provider.is_sample)
            stmt = stmt.on_conflict_do_update(
                index_elements=["provider", "symbol", "expiry_date", "trade_date"],
                set_={"taken_at": stmt.excluded.taken_at, "strikes": stmt.excluded.strikes,
                      "payload": stmt.excluded.payload})
            session.execute(stmt)
            out.append(ChainResult(sym, exp.isoformat(), "SUCCESS", strikes))
    ok = [r for r in out if r.status == "SUCCESS"]
    run.rows_received = run.rows_written = len(ok)
    failed = [r for r in out if r.status != "SUCCESS"]
    _finish(session, run, "FAILED" if failed and not ok else "SUCCESS",
            "; ".join(f"{r.symbol} {r.expiry}: {r.error or r.status}" for r in failed)[:500] or None)
    return out


def field_names(payload: dict) -> dict[str, list[str]]:
    """Top-level keys, and the keys of one CE leg and its greeks: to verify what Groww sends."""
    strikes = payload.get("strikes") or {}
    leg = next((v.get("CE") for v in strikes.values() if isinstance(v, dict) and v.get("CE")), {})
    return {"top": sorted(payload.keys()), "leg": sorted(leg.keys()),
            "greeks": sorted((leg.get("greeks") or {}).keys())}
