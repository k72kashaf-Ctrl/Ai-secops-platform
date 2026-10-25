"""System, dashboard overview and demo-administration endpoints."""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.api.events import serialize_event
from app.api.incidents import serialize_incident
from app.api.quality import serialize_issue
from app.config import Settings
from app.database import Database
from app.deps import get_db, get_settings, require_admin_access
from app.services.analysis import run_full_analysis
from app.services.sample import seed_sample
from app.security import record_audit
from app.utils import now_iso

router = APIRouter(tags=["system"])


def analysis_status(request: Request) -> dict[str, Any]:
    worker = request.app.state.analysis_worker
    if worker is None:
        return {"mode": "sync", "analysis_pending": False}
    return worker.status()


@router.get("/health")
def health(request: Request, db: Database = Depends(get_db)):
    database = db.health()
    stats: dict[str, Any] | None = None
    error = None
    try:
        stats = db.stats()
    except Exception as exc:  # pragma: no cover - defensive runtime path
        error = str(exc)
    analysis = analysis_status(request)
    healthy = bool(database.get("database_connected")) and database.get("database_integrity") == "ok"
    body = {
        "status": "ok" if healthy else "degraded",
        "app": "Zenith AI-Native SecOps Platform",
        "version": __version__,
        "timestamp": now_iso(),
        "database": database,
        "statistics": stats,
        "analysis_pending": analysis["analysis_pending"],
        "analysis": analysis,
        "error": error,
    }
    return JSONResponse(status_code=200 if healthy else 503, content=body)


@router.get("/overview")
def overview(db: Database = Depends(get_db)):
    stats = db.stats()
    recent_events = [
        serialize_event(row)
        for row in db.fetchall(
            "SELECT * FROM events ORDER BY received_epoch DESC, event_id ASC LIMIT 10"
        )
    ]
    top_incidents = [
        serialize_incident(row)
        for row in db.fetchall(
            "SELECT * FROM incidents ORDER BY "
            "CASE severity WHEN 'critical' THEN 4 WHEN 'high' THEN 3 "
            "WHEN 'medium' THEN 2 ELSE 1 END DESC, confidence DESC, created_epoch DESC LIMIT 5"
        )
    ]
    quality_by_type = db.fetchall(
        "SELECT issue_type, COUNT(*) AS count, "
        "CASE MAX(CASE severity WHEN 'critical' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 WHEN 'low' THEN 1 ELSE 0 END) WHEN 4 THEN 'critical' WHEN 3 THEN 'high' WHEN 2 THEN 'medium' WHEN 1 THEN 'low' ELSE 'unknown' END AS max_severity "
        "FROM quality_issues GROUP BY issue_type ORDER BY count DESC"
    )
    health_issues = [
        serialize_issue(row)
        for row in db.fetchall(
            "SELECT * FROM quality_issues WHERE issue_type IN "
            "('unhealthy_device','offline_device') ORDER BY issue_id LIMIT 10"
        )
    ]
    recent_ingestions = db.fetchall(
        "SELECT batch_id, initiated_at, completed_at, total, accepted, rejected, "
        "duplicates, status FROM ingestion_runs ORDER BY initiated_at DESC LIMIT 5"
    )
    latest = db.fetchone(
        "SELECT MAX(received_at) AS latest_event_at, MAX(occurred_at) AS latest_occurred_at, "
        "MIN(occurred_at) AS earliest_occurred_at FROM events"
    ) or {}
    return {
        "stats": stats,
        "latest_event_at": latest.get("latest_event_at"),
        "latest_occurred_at": latest.get("latest_occurred_at"),
        "earliest_occurred_at": latest.get("earliest_occurred_at"),
        "recent_events": recent_events,
        "top_incidents": top_incidents,
        "quality_by_type": quality_by_type,
        "device_health_issues": health_issues,
        "recent_ingestions": recent_ingestions,
        "api_base_url": os.getenv("ZENITH_PUBLIC_API_URL", "http://localhost:8000"),
    }


@router.post("/admin/reset")
def reset_database(
    request: Request,
    db: Database = Depends(get_db),
    _admin: bool = Depends(require_admin_access),
):
    """Delete all demo data while retaining the schema. Local/demo use only."""
    db.reset()
    record_audit(db, request, "admin.reset")
    return {"status": "reset", "stats": db.stats()}


@router.post("/admin/seed-sample")
def load_sample_dataset(
    request: Request,
    reset: bool = Query(default=True, description="Clear existing data before loading"),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _admin: bool = Depends(require_admin_access),
):
    try:
        result = seed_sample(db, settings, reset=reset)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    record_audit(db, request, "admin.seed_sample", details={"reset": reset})
    return result


@router.post("/admin/recompute")
def recompute(
    request: Request,
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _admin: bool = Depends(require_admin_access),
):
    """Synchronous full recompute (serialised with the background worker)."""
    result = run_full_analysis(db, settings)
    record_audit(db, request, "admin.recompute")
    return {
        "status": "completed",
        "quality_issues": result["quality_issues"],
        "incidents": result["incidents"],
        "duration_seconds": result["duration_seconds"],
        "analysis": analysis_status(request),
        "stats": db.stats(),
    }


@router.get("/admin/audit")
def audit_log(
    limit: int = Query(default=100, ge=1, le=500),
    db: Database = Depends(get_db),
    _admin: bool = Depends(require_admin_access),
):
    return {
        "items": db.fetchall(
            "SELECT audit_id, action, actor, target, details, created_at "
            "FROM audit_log ORDER BY audit_id DESC LIMIT ?",
            (limit,),
        )
    }
