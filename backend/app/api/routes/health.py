from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_session

router = APIRouter()


@router.get("/health")
def health(session: Session = Depends(get_session)) -> dict:
    """Is the API up and can it reach the database?"""
    try:
        session.execute(text("SELECT 1"))
        db = "ok"
    except Exception:
        db = "unreachable"  # no internal details are exposed
    settings = get_settings()
    return {
        "status": "ok" if db == "ok" else "degraded",
        "database": db,
        "data_provider": settings.data_provider,
        "sample_mode": settings.data_provider.lower() == "sample",
        "order_execution": "DISABLED (Version 1 is analysis only)",
    }
