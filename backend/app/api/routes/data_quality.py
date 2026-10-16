from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.serialize import ts
from app.db.models import DataQualityLog, IngestionRun
from app.db.session import get_session

router = APIRouter(prefix="/api")


@router.get("/data-quality")
def data_quality(
    instrument_id: int | None = Query(None, ge=1),
    limit: int = Query(50, ge=1, le=500),
    session: Session = Depends(get_session),
) -> dict:
    """Latest ingestion runs and the data-quality checks they produced."""
    runs_q = select(IngestionRun).order_by(IngestionRun.id.desc()).limit(limit)
    checks_q = select(DataQualityLog).order_by(DataQualityLog.id.desc()).limit(limit)
    if instrument_id:
        runs_q = runs_q.where(IngestionRun.instrument_id == instrument_id)
        checks_q = checks_q.where(DataQualityLog.instrument_id == instrument_id)
    return {
        "runs": [
            {"id": r.id, "provider": r.provider, "job": r.job, "instrument_id": r.instrument_id,
             "timeframe": r.timeframe, "status": r.status, "started_at": ts(r.started_at),
             "finished_at": ts(r.finished_at), "rows_received": r.rows_received,
             "rows_written": r.rows_written, "error_message": r.error_message}
            for r in session.scalars(runs_q)
        ],
        "checks": [
            {"id": c.id, "run_id": c.run_id, "instrument_id": c.instrument_id,
             "timeframe": c.timeframe, "check_name": c.check_name, "status": c.status,
             "details": c.details, "checked_at": ts(c.checked_at)}
            for c in session.scalars(checks_q)
        ],
    }
