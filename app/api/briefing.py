"""Operational briefing endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse

from app.config import Settings
from app.database import Database
from app.deps import get_db, get_settings
from app.services.briefing import generate_briefing

router = APIRouter(tags=["briefing"])


@router.get("/briefing")
def get_briefing(
    format: str = Query(default="json", pattern="^(json|markdown)$"),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    briefing = generate_briefing(db, settings)
    if format == "markdown":
        return PlainTextResponse(briefing["markdown"], media_type="text/markdown")
    return briefing
