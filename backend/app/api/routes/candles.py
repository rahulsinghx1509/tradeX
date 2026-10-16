from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.serialize import instrument_dict, num, ts
from app.core.market_time import SUPPORTED_TIMEFRAMES, to_utc
from app.db.models import Instrument, MarketCandle
from app.db.session import get_session

router = APIRouter(prefix="/api")
MAX_LIMIT = 5000


@router.get("/candles")
def get_candles(
    instrument_id: int = Query(..., ge=1),
    timeframe: str = Query("1d", pattern="^(" + "|".join(SUPPORTED_TIMEFRAMES) + ")$"),
    start: datetime | None = Query(None, description="ISO time; no timezone = IST"),
    end: datetime | None = Query(None, description="ISO time; no timezone = IST"),
    limit: int = Query(500, ge=1, le=MAX_LIMIT, description="most recent N candles"),
    session: Session = Depends(get_session),
) -> dict:
    inst = session.get(Instrument, instrument_id)
    if inst is None:
        raise HTTPException(404, "Instrument not found")
    q = select(MarketCandle).where(MarketCandle.instrument_id == instrument_id,
                                   MarketCandle.timeframe == timeframe)
    if start:
        q = q.where(MarketCandle.candle_time >= to_utc(start))
    if end:
        q = q.where(MarketCandle.candle_time <= to_utc(end))
    rows = list(session.scalars(q.order_by(MarketCandle.candle_time.desc()).limit(limit)))
    rows.reverse()
    return {
        "instrument": instrument_dict(inst),
        "timeframe": timeframe,
        "is_sample": inst.is_sample,
        "count": len(rows),
        "candles": [
            {
                "time": ts(r.candle_time),
                "open": num(r.open), "high": num(r.high), "low": num(r.low), "close": num(r.close),
                "volume": r.volume, "open_interest": r.open_interest,
                "quality_status": r.quality_status, "quality_reason": r.quality_reason,
            }
            for r in rows
        ],
    }
