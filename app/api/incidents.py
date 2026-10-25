"""Incident listing, investigation detail and status updates."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.database import Database
from app.deps import get_db, require_admin_access
from app.schemas import IncidentStatusUpdate
from app.services.incident_status import apply_incident_update
from app.utils import VALID_STATUSES, parse_json
from app.security import record_audit

router = APIRouter(tags=["incidents"])

SEVERITY_SQL = (
    "CASE severity WHEN 'critical' THEN 4 WHEN 'high' THEN 3 "
    "WHEN 'medium' THEN 2 ELSE 1 END"
)
LIST_COLUMNS = (
    "incident_id, title, category, severity, confidence, status, created_at, "
    "updated_at, event_ids, devices, zones, sources, explanation, missing_info, "
    "contradictions, recommended_actions, event_count, is_possible_association"
)


def _epoch(value: datetime | None) -> int | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp())


def serialize_incident(row: dict[str, Any], include_explanation: bool = True) -> dict[str, Any]:
    result = {
        "incident_id": row["incident_id"],
        "title": row["title"],
        "category": row["category"],
        "severity": row["severity"],
        "confidence": row["confidence"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "event_count": row["event_count"],
        "source_event_ids": parse_json(row.get("event_ids"), []),
        "devices": parse_json(row.get("devices"), []),
        "locations": parse_json(row.get("zones"), []),
        "sources": parse_json(row.get("sources"), []),
        "missing_info": parse_json(row.get("missing_info"), []),
        "contradictions": parse_json(row.get("contradictions"), []),
        "recommended_actions": parse_json(row.get("recommended_actions"), []),
        "is_possible_association": bool(row.get("is_possible_association")),
    }
    if include_explanation:
        result["correlation_explanation"] = row["explanation"]
    return result


@router.get("/incidents")
def list_incidents(
    db: Database = Depends(get_db),
    severity: str | None = None,
    status: str | None = None,
    category: str | None = None,
    search: str | None = Query(default=None, description="Match incident ID or title"),
    source: str | None = Query(default=None, description="Only incidents containing this source type"),
    device: str | None = Query(default=None, description="Only incidents containing this device"),
    zone: str | None = Query(default=None, description="Only incidents containing this zone"),
    start: datetime | None = Query(default=None, description="Occurrence-time lower bound (incident overlaps window)"),
    end: datetime | None = Query(default=None, description="Occurrence-time upper bound (incident overlaps window)"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
):
    where: list[str] = []
    params: list[Any] = []
    if severity:
        where.append("i.severity = ?")
        params.append(severity.lower())
    if status:
        where.append("i.status = ?")
        params.append(status.lower())
    if category:
        where.append("i.category = ?")
        params.append(category.lower())
    if search:
        where.append("(i.incident_id LIKE ? OR i.title LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    if source:
        where.append(
            "EXISTS (SELECT 1 FROM incident_events ie JOIN events e ON e.event_id = ie.event_id "
            "WHERE ie.incident_id = i.incident_id AND e.source_type = ?)"
        )
        params.append(source.lower())
    if device:
        where.append(
            "EXISTS (SELECT 1 FROM incident_events ie JOIN events e ON e.event_id = ie.event_id "
            "WHERE ie.incident_id = i.incident_id AND e.device_id = ?)"
        )
        params.append(device)
    if zone:
        where.append(
            "EXISTS (SELECT 1 FROM incident_events ie JOIN events e ON e.event_id = ie.event_id "
            "WHERE ie.incident_id = i.incident_id AND e.zone = ?)"
        )
        params.append(zone)
    start_epoch, end_epoch = _epoch(start), _epoch(end)
    # Time filters use occurrence time: an incident matches when its span
    # [first occurred_epoch, last occurred_epoch] overlaps the requested window.
    if start_epoch is not None:
        where.append(
            "(SELECT MAX(ie.occurred_epoch) FROM incident_events ie "
            "WHERE ie.incident_id = i.incident_id) >= ?"
        )
        params.append(start_epoch)
    if end_epoch is not None:
        where.append(
            "(SELECT MIN(ie.occurred_epoch) FROM incident_events ie "
            "WHERE ie.incident_id = i.incident_id) <= ?"
        )
        params.append(end_epoch)
    clause = f"WHERE {' AND '.join(where)}" if where else ""

    total = db.scalar(f"SELECT COUNT(*) FROM incidents i {clause}", params, default=0)
    offset = (page - 1) * page_size
    rows = db.fetchall(
        f"SELECT {', '.join('i.' + column.strip() for column in LIST_COLUMNS.split(','))} "
        f"FROM incidents i {clause} ORDER BY {SEVERITY_SQL.replace('severity', 'i.severity')} DESC, "
        f"i.confidence DESC, i.created_epoch DESC LIMIT ? OFFSET ?",
        [*params, page_size, offset],
    )
    return {
        "items": [serialize_incident(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total else 0,
    }


@router.get("/incidents/facets")
def incident_facets(db: Database = Depends(get_db)):
    """Distinct values used by dashboard filters (registered before /incidents/{id})."""
    sources = db.fetchall(
        "SELECT DISTINCT e.source_type AS value FROM incident_events ie "
        "JOIN events e ON e.event_id = ie.event_id ORDER BY e.source_type"
    )
    zones = db.fetchall(
        "SELECT DISTINCT e.zone AS value FROM incident_events ie "
        "JOIN events e ON e.event_id = ie.event_id WHERE e.zone IS NOT NULL "
        "AND e.zone != '' ORDER BY e.zone"
    )
    devices = db.fetchall(
        "SELECT DISTINCT e.device_id AS value FROM incident_events ie "
        "JOIN events e ON e.event_id = ie.event_id ORDER BY e.device_id"
    )
    categories = db.fetchall("SELECT DISTINCT category AS value FROM incidents ORDER BY category")
    return {
        "source_types": [row["value"] for row in sources],
        "zones": [row["value"] for row in zones],
        "devices": [row["value"] for row in devices],
        "categories": [row["value"] for row in categories],
        "severities": ["critical", "high", "medium", "low"],
        "statuses": list(VALID_STATUSES),
    }


@router.get("/incidents/{incident_id}")
def get_incident(incident_id: str, db: Database = Depends(get_db)):
    row = db.fetchone("SELECT * FROM incidents WHERE incident_id = ?", (incident_id,))
    if not row:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")
    incident = serialize_incident(row)
    event_rows = db.fetchall(
        """
        SELECT e.*, ie.sequence
        FROM incident_events ie JOIN events e ON e.event_id = ie.event_id
        WHERE ie.incident_id = ?
        ORDER BY ie.sequence ASC, e.occurred_epoch ASC
        """,
        (incident_id,),
    )
    timeline = []
    for event_row in event_rows:
        timeline.append(
            {
                "sequence": event_row["sequence"],
                "event_id": event_row["event_id"],
                "source_type": event_row["source_type"],
                "device_id": event_row["device_id"],
                "event_type": event_row["event_type"],
                "occurred_at": event_row["occurred_at"],
                "received_at": event_row["received_at"],
                "zone": event_row["zone"],
                "severity": event_row["severity"],
                "confidence": event_row["confidence"],
                "payload": parse_json(event_row.get("payload"), {}),
            }
        )
    event_ids = [item["event_id"] for item in timeline]
    issues: list[dict[str, Any]] = []
    for start in range(0, len(event_ids), 500):
        batch = event_ids[start : start + 500]
        if not batch:
            continue
        placeholders = ",".join("?" for _ in batch)
        issues.extend(
            db.fetchall(
                f"SELECT issue_id, issue_type, severity, explanation, event_id, device_id, "
                f"suggested_action, detected_at FROM quality_issues "
                f"WHERE event_id IN ({placeholders}) ORDER BY issue_id",
                batch,
            )
        )
    assignment = db.fetchone(
        "SELECT assigned_to FROM incident_status WHERE incident_id = ?", (incident_id,)
    )
    incident["assigned_to"] = assignment["assigned_to"] if assignment else None
    incident["timeline"] = timeline
    incident["related_event_ids"] = event_ids
    incident["quality_issues"] = issues
    return incident


@router.patch("/incidents/{incident_id}")
def update_incident_status(
    request: Request,
    incident_id: str,
    update: IncidentStatusUpdate,
    db: Database = Depends(get_db),
    _admin: bool = Depends(require_admin_access),
):
    existing = db.fetchone("SELECT incident_id, status FROM incidents WHERE incident_id = ?", (incident_id,))
    if not existing:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")
    result = apply_incident_update(db, incident_id, update.status, update.assigned_to)
    record_audit(
        db,
        request,
        "incident.status_update",
        target=incident_id,
        details={
            key: value
            for key, value in {"status": update.status, "assigned_to": update.assigned_to}.items()
            if value is not None
        },
    )
    return result
