"""Text operations assistant with confirmation-gated state changes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.database import Database
from app.config import Settings
from app.deps import get_db, get_settings, require_admin_access
from app.schemas import AssistantConfirm, AssistantQuery
from app.services.assistant import answer_query, confirm_request, history
from app.security import record_audit

router = APIRouter(prefix="/assistant", tags=["assistant"])


@router.post("/query")
def query_assistant(
    request: AssistantQuery,
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    return answer_query(db, request.query, settings, session_id=request.session_id)


@router.post("/confirm")
def confirm_assistant(
    request: Request,
    payload: AssistantConfirm,
    db: Database = Depends(get_db),
    _admin: bool = Depends(require_admin_access),
):
    result = confirm_request(db, payload.request_id, payload.confirmed)
    if not result:
        raise HTTPException(status_code=404, detail="Assistant request not found")
    if result.get("status") == "already_decided":
        raise HTTPException(
            status_code=409,
            detail=f"Assistant request {payload.request_id} was already {result.get('decision')}",
        )
    if result.get("status") == "confirmed":
        record_audit(
            db,
            request,
            "assistant.confirm",
            target=result["action"]["incident_id"],
            details=result["action"],
        )
    return result


@router.get("/history")
def assistant_history(
    limit: int = Query(default=50, ge=1, le=200),
    session_id: str | None = Query(default=None, max_length=100),
    db: Database = Depends(get_db),
):
    return {"items": history(db, limit, session_id)}
