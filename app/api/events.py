"""Event ingestion and exploration endpoints."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.config import Settings
from app.database import Database
from app.deps import get_db, get_settings
from app.security import require_ingest_rate_limit
from app.services.ingestion import ingest_records
from app.utils import parse_json, to_iso

router = APIRouter(tags=["events"])


def _epoch(value: datetime | None) -> int | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp())


def serialize_event(row: dict[str, Any], include_raw: bool = False) -> dict[str, Any]:
    result = {
        "event_id": row["event_id"],
        "source_type": row["source_type"],
        "device_id": row["device_id"],
        "event_type": row["event_type"],
        "occurred_at": row["occurred_at"],
        "received_at": row["received_at"],
        "latitude": row["latitude"],
        "longitude": row["longitude"],
        "zone": row["zone"],
        "severity": row["severity"],
        "confidence": row["confidence"],
        "payload": parse_json(row.get("payload"), {}),
        "validation_status": row["validation_status"],
        "duplicate_of": row.get("duplicate_of"),
        "ingested_at": row.get("ingested_at"),
        "batch_id": row.get("batch_id"),
    }
    if include_raw:
        result["raw_record"] = parse_json(row.get("raw_record"), row.get("raw_record"))
        result["content_hash"] = row.get("content_hash")
    return result


@router.post("/ingest")
def ingest_endpoint(
    request: Request,
    payload: Any = Body(...),
    analyze: Literal["sync", "deferred"] | None = Query(
        default=None,
        description=(
            "sync: recompute quality + incidents before responding. deferred: insert "
            "only; a debounced background worker recomputes (see /health "
            "analysis.analysis_pending). Defaults to ZENITH_ANALYSIS_MODE."
        ),
    ),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Ingest one event, a JSON array, or {"events": [...]}.

    A single malformed record returns 422, but its raw content and validation
    errors are still preserved in rejected_events before the response is sent.
    Batch submissions return per-record results (200 for partial success).
    The endpoint is metered per record (ZENITH_INGEST_RECORDS_PER_MINUTE), not
    by the generic per-request write limit.
    """
    single = False
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        if isinstance(payload.get("events"), list):
            records = payload["events"]
        elif isinstance(payload.get("event"), dict):
            records = [payload["event"]]
            single = True
        else:
            records = [payload]
            single = True
    else:
        raise HTTPException(status_code=422, detail="Body must be a JSON object or array")

    if not records:
        raise HTTPException(status_code=400, detail="No records supplied")

    require_ingest_rate_limit(request, len(records))

    worker = request.app.state.analysis_worker
    mode = analyze or settings.analysis_mode
    if mode == "deferred" and worker is None:
        mode = "sync"
    deferred = mode == "deferred"
    summary = ingest_records(
        db, records, settings, run_quality=not deferred, run_correlation=not deferred
    )
    if deferred and (summary.accepted or summary.rejected):
        worker.mark_dirty()
    body = summary.to_dict()
    body["source"] = "rest"
    body["analysis"] = "deferred" if deferred else "completed"
    if summary.total == 1 and summary.accepted == 0:
        return JSONResponse(status_code=422, content=body)
    return JSONResponse(status_code=200, content=body)


@router.get("/events")
def list_events(
    request: Request,
    db: Database = Depends(get_db),
    search: str | None = Query(default=None, description="Match event_id or device_id"),
    event_id: str | None = None,
    device_id: str | None = None,
    source_type: str | None = None,
    event_type: str | None = None,
    severity: str | None = None,
    zone: str | None = None,
    validation_status: str | None = None,
    start: datetime | None = Query(default=None, description="Occurrence time lower bound (ISO-8601)"),
    end: datetime | None = Query(default=None, description="Occurrence time upper bound (ISO-8601)"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
):
    where: list[str] = []
    params: list[Any] = []
    if search:
        where.append("(event_id LIKE ? OR device_id LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    if event_id:
        where.append("event_id = ?")
        params.append(event_id)
    if device_id:
        where.append("device_id = ?")
        params.append(device_id)
    if source_type:
        where.append("source_type = ?")
        params.append(source_type.lower())
    if event_type:
        where.append("event_type = ?")
        params.append(event_type.lower())
    if severity:
        where.append("severity = ?")
        params.append(severity.lower())
    if zone:
        where.append("zone = ?")
        params.append(zone)
    if validation_status:
        where.append("validation_status = ?")
        params.append(validation_status)
    start_epoch, end_epoch = _epoch(start), _epoch(end)
    if start_epoch is not None:
        where.append("occurred_epoch >= ?")
        params.append(start_epoch)
    if end_epoch is not None:
        where.append("occurred_epoch <= ?")
        params.append(end_epoch)
    clause = f"WHERE {' AND '.join(where)}" if where else ""

    total = db.scalar(f"SELECT COUNT(*) FROM events {clause}", params, default=0)
    offset = (page - 1) * page_size
    rows = db.fetchall(
        f"SELECT * FROM events {clause} ORDER BY occurred_epoch DESC, event_id ASC "
        f"LIMIT ? OFFSET ?",
        [*params, page_size, offset],
    )
    return {
        "items": [serialize_event(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total else 0,
        "filters": {
            "search": search,
            "source_type": source_type,
            "severity": severity,
            "zone": zone,
            "start": to_iso(start) if start else None,
            "end": to_iso(end) if end else None,
        },
    }


@router.get("/events/facets")
def event_facets(db: Database = Depends(get_db)):
    """Distinct values used by dashboard filters (registered before /events/{id})."""
    def distinct(column: str) -> list[str]:
        rows = db.fetchall(
            f"SELECT DISTINCT {column} AS value FROM events WHERE {column} IS NOT NULL "
            f"AND {column} != '' ORDER BY {column}"
        )
        return [row["value"] for row in rows]

    return {
        "source_types": distinct("source_type"),
        "event_types": distinct("event_type"),
        "severities": ["critical", "high", "medium", "low"],
        "zones": distinct("zone"),
        "devices": distinct("device_id"),
        "validation_statuses": distinct("validation_status"),
    }


@router.get("/events/{event_id}")
def get_event(event_id: str, db: Database = Depends(get_db)):
    row = db.fetchone("SELECT * FROM events WHERE event_id = ?", (event_id,))
    if not row:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")
    event = serialize_event(row, include_raw=True)
    issues = db.fetchall(
        "SELECT issue_id, issue_type, severity, explanation, suggested_action, detected_at "
        "FROM quality_issues WHERE event_id = ? "
        "ORDER BY CASE severity WHEN 'critical' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END DESC",
        (event_id,),
    )
    incidents = db.fetchall(
        "SELECT incident_id FROM incident_events WHERE event_id = ? ORDER BY incident_id",
        (event_id,),
    )
    event["quality_issues"] = issues
    event["incident_ids"] = [item["incident_id"] for item in incidents]
    return event
