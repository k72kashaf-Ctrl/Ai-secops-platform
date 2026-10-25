"""Camera wall: camera-like devices with latest event, local clip and stream."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.config import Settings
from app.database import Database
from app.deps import get_db, get_settings
from app.services.cameras import build_cameras

router = APIRouter(tags=["cameras"])


@router.get("/cameras")
def cameras(db: Database = Depends(get_db), settings: Settings = Depends(get_settings)):
    """Cameras (CCTV, ANPR, face, traffic enforcement) from valid events.

    Alert-first, then most recent. ``media_url`` is set only when the clip file
    exists in the media directory (served read-only at /media); ``stream`` comes
    from the optional operator file (ZENITH_CAMERAS_FILE).
    """
    return build_cameras(db, settings)
