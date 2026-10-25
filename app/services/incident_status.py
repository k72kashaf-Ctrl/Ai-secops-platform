"""Single write path for operator-driven incident status / assignment changes."""
from __future__ import annotations

from typing import Any

from app.database import Database
from app.utils import now_iso


def apply_incident_update(
    db: Database,
    incident_id: str,
    status: str | None = None,
    assigned_to: str | None = None,
) -> dict[str, Any]:
    """Persist a status and/or assignee change.

    The incident_status table outlives correlation recomputes, so both the
    status and the assignee survive re-analysis. Returns {} if not found.
    """
    timestamp = now_iso()
    with db.transaction() as connection:
        row = connection.execute(
            "SELECT status FROM incidents WHERE incident_id = ?", (incident_id,)
        ).fetchone()
        if row is None:
            return {}
        new_status = status or row["status"]
        connection.execute(
            "UPDATE incidents SET status = ?, updated_at = ? WHERE incident_id = ?",
            (new_status, timestamp, incident_id),
        )
        connection.execute(
            """
            INSERT INTO incident_status (incident_id, status, updated_at, assigned_to)
            VALUES (?,?,?,?)
            ON CONFLICT(incident_id) DO UPDATE SET
                status = excluded.status,
                updated_at = excluded.updated_at,
                assigned_to = COALESCE(excluded.assigned_to, incident_status.assigned_to)
            """,
            (incident_id, new_status, timestamp, assigned_to),
        )
        current = connection.execute(
            "SELECT assigned_to FROM incident_status WHERE incident_id = ?", (incident_id,)
        ).fetchone()
    return {
        "incident_id": incident_id,
        "status": new_status,
        "assigned_to": current["assigned_to"] if current else None,
        "updated_at": timestamp,
    }
