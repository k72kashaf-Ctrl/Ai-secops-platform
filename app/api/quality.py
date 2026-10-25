"""Data-quality issue exploration and on-demand analysis."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import Settings
from app.database import Database
from app.deps import get_db, get_settings
from app.services.analysis import run_full_analysis
from app.utils import parse_json

router = APIRouter(tags=["quality"])
SEVERITY_SQL = (
    "CASE severity WHEN 'critical' THEN 4 WHEN 'high' THEN 3 "
    "WHEN 'medium' THEN 2 ELSE 1 END"
)


def serialize_issue(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "issue_id": row["issue_id"],
        "issue_type": row["issue_type"],
        "severity": row["severity"],
        "explanation": row["explanation"],
        "event_id": row["event_id"],
        "device_id": row["device_id"],
        "detected_at": row["detected_at"],
        "suggested_action": row["suggested_action"],
        "status": row["status"],
        "details": parse_json(row.get("details"), {}),
    }


@router.get("/quality-issues")
def list_quality_issues(
    issue_type: str | None = None,
    severity: str | None = None,
    event_id: str | None = None,
    device_id: str | None = None,
    search: str | None = Query(default=None, description="Match issue explanation or ID"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    db: Database = Depends(get_db),
):
    where: list[str] = []
    params: list[Any] = []
    if issue_type:
        where.append("issue_type = ?")
        params.append(issue_type)
    if severity:
        where.append("severity = ?")
        params.append(severity.lower())
    if event_id:
        where.append("event_id = ?")
        params.append(event_id)
    if device_id:
        where.append("device_id = ?")
        params.append(device_id)
    if search:
        where.append("(issue_id LIKE ? OR explanation LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    total = db.scalar(f"SELECT COUNT(*) FROM quality_issues {clause}", params, default=0)
    offset = (page - 1) * page_size
    rows = db.fetchall(
        f"SELECT * FROM quality_issues {clause} ORDER BY {SEVERITY_SQL} DESC, "
        f"detected_at DESC LIMIT ? OFFSET ?",
        [*params, page_size, offset],
    )
    return {
        "items": [serialize_issue(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total else 0,
    }


@router.get("/quality-issues/summary")
def quality_summary(db: Database = Depends(get_db)):
    by_type = db.fetchall(
        "SELECT issue_type, COUNT(*) AS count, "
        "CASE MAX(CASE severity WHEN 'critical' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 WHEN 'low' THEN 1 ELSE 0 END) WHEN 4 THEN 'critical' WHEN 3 THEN 'high' WHEN 2 THEN 'medium' WHEN 1 THEN 'low' ELSE 'unknown' END AS max_severity "
        "FROM quality_issues GROUP BY issue_type ORDER BY count DESC"
    )
    by_severity = db.fetchall(
        "SELECT severity, COUNT(*) AS count FROM quality_issues GROUP BY severity"
    )
    total = db.scalar("SELECT COUNT(*) FROM quality_issues", default=0)
    return {"total": total, "by_type": by_type, "by_severity": by_severity}


@router.get("/quality-issues/{issue_id}")
def get_quality_issue(issue_id: str, db: Database = Depends(get_db)):
    row = db.fetchone("SELECT * FROM quality_issues WHERE issue_id = ?", (issue_id,))
    if not row:
        raise HTTPException(status_code=404, detail=f"Quality issue {issue_id} not found")
    return serialize_issue(row)


@router.post("/quality/analyze")
def analyze_quality(
    db: Database = Depends(get_db), settings: Settings = Depends(get_settings)
):
    """Recompute correlation and every deterministic quality check."""
    result = run_full_analysis(db, settings)
    return {
        "status": "completed",
        "quality_issues": result["quality_issues"],
        "incidents": result["incidents"],
        "stats": db.stats(),
    }
