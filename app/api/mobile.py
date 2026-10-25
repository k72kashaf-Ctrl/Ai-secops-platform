"""Mobile-friendly operator view (Bonus C).

A single self-contained, responsive HTML page that calls the existing JSON
APIs from the browser: incidents (with status updates), quality findings,
the operational briefing and the assistant (with optional push-to-talk).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["mobile"])

_PAGE = Path(__file__).resolve().parent.parent / "static" / "mobile.html"


@router.get("/mobile", response_class=HTMLResponse, include_in_schema=False)
def mobile_view() -> HTMLResponse:
    return HTMLResponse(_PAGE.read_text(encoding="utf-8"))
