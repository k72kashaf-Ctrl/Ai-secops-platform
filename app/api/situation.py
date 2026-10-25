"""Read-only operations-center situation snapshot."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.database import Database
from app.deps import get_db
from app.services.situation import build_situation

router = APIRouter(tags=["situation"])


@router.get("/situation")
def situation(db: Database = Depends(get_db)):
    """KPIs, zones, activity, sensors, priority incidents, feed and a brief.

    All windows use event time; "now" is the latest valid event occurrence.
    """
    return build_situation(db)
