"""Small helpers that turn database rows into JSON-friendly dicts."""

from datetime import datetime
from decimal import Decimal

from app.core.market_time import from_db
from app.db.models import Instrument


def num(v):
    return float(v) if isinstance(v, Decimal) else v


def ts(v: datetime | None) -> str | None:
    v = from_db(v)
    return v.isoformat() if v else None


def instrument_dict(i: Instrument) -> dict:
    return {
        "id": i.id,
        "symbol": i.symbol,
        "instrument_type": i.instrument_type,
        "exchange": i.exchange,
        "segment": i.segment,
        "expiry_date": i.expiry_date.isoformat() if i.expiry_date else None,
        "lot_size": i.lot_size,
        "provider": i.provider,
        "provider_symbol": i.provider_symbol,
        "trading_symbol": i.trading_symbol,
        "is_sample": i.is_sample,
        "is_active": i.is_active,
    }
