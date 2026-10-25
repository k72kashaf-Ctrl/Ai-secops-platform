"""FastAPI dependencies."""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from app.config import Settings
from app.database import Database


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def require_admin_access(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> bool:
    """Protect mutating/demo-admin routes when an API key is configured.

    Local development remains backwards-compatible when ZENITH_ADMIN_API_KEY is
    unset. Deployments can opt in without changing the API surface.
    """
    if settings.admin_api_key and request.headers.get("X-Admin-Key") != settings.admin_api_key:
        raise HTTPException(status_code=401, detail="Admin API key required")
    return True
