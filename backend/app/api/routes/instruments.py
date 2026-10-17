from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.serialize import instrument_dict
from app.core.config import get_settings
from app.db.models import Instrument
from app.db.session import get_session

router = APIRouter(prefix="/api")


@router.get("/instruments")
def list_instruments(
    symbol: str | None = Query(None, max_length=32, description="e.g. NIFTY"),
    instrument_type: str | None = Query(None, pattern="^(INDEX|STOCK|FUTURE|OPTION)$"),
    active_only: bool = True,
    provider: str | None = Query(None, max_length=20, description="default: DATA_PROVIDER in .env"),
    session: Session = Depends(get_session),
) -> list[dict]:
    q = select(Instrument).order_by(Instrument.symbol, Instrument.instrument_type.desc(),
                                    Instrument.expiry_date)
    if symbol:
        q = q.where(Instrument.symbol == symbol.upper())
    if instrument_type:
        q = q.where(Instrument.instrument_type == instrument_type)
    q = q.where(Instrument.provider == (provider or get_settings().data_provider).lower())
    if active_only:
        q = q.where(Instrument.is_active)
    return [instrument_dict(i) for i in session.scalars(q)]
