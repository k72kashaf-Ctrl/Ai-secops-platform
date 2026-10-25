"""Grounded, structured reasoning endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.config import Settings
from app.database import Database
from app.deps import get_db, get_settings
from app.services.reasoning import generate_incident_reasoning

router = APIRouter(tags=["reasoning"])


@router.get("/incidents/{incident_id}/reasoning")
def incident_reasoning(
    incident_id: str,
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    result = generate_incident_reasoning(db, incident_id, settings.reasoning_provider)
    if not result:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")
    return result
