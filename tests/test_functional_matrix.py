"""Functional requirements matrix: every public workflow and required endpoint."""
from __future__ import annotations

REQUIRED_PATHS = {
    "POST /ingest", "GET /events", "GET /incidents", "GET /incidents/{incident_id}",
    "GET /incidents/{incident_id}/reasoning", "GET /quality-issues", "GET /briefing",
    "GET /health", "POST /assistant/query", "POST /assistant/confirm", "GET /assistant/history",
}

def test_required_api_contract_is_published(client):
    openapi = client.get("/openapi.json")
    assert openapi.status_code == 200
    paths = openapi.json()["paths"]
    published = {f"{method.upper()} {path}" for path, operations in paths.items() for method in operations}
    assert REQUIRED_PATHS <= published


def test_root_overview_and_event_explorer_workflow(client, make_event):
    assert client.get("/").status_code == 200
    assert client.post("/ingest", json={"event": make_event(event_id="EVT-MATRIX-1")}).status_code == 200
    listing = client.get("/events", params={
        "event_id": "EVT-MATRIX-1", "device_id": "CAM-TEST-01", "source_type": "cctv",
        "event_type": "motion_detected", "severity": "medium", "zone": "North Gate",
        "validation_status": "valid", "start": "2026-03-12T09:00:00Z",
        "end": "2026-03-12T11:00:00Z",
    })
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert client.get("/events/facets").status_code == 200
    detail = client.get("/events/EVT-MATRIX-1")
    assert detail.status_code == 200
    assert detail.json()["raw_record"]["event_id"] == "EVT-MATRIX-1"
    assert client.get("/overview").status_code == 200


def test_quality_endpoints_and_analysis_workflow(client, make_event):
    assert client.post("/ingest", json=make_event(event_id="EVT-MATRIX-QUALITY", confidence=0.1, severity="high")).status_code == 200
    assert client.post("/quality/analyze").status_code == 200
    assert client.get("/quality-issues/summary").status_code == 200
    issues = client.get("/quality-issues")
    assert issues.status_code == 200
    assert issues.json()["total"] >= 1
    issue_id = issues.json()["items"][0]["issue_id"]
    assert client.get(f"/quality-issues/{issue_id}").status_code == 200
    assert client.get(f"/quality-issues/{issue_id}-missing").status_code == 404


def test_admin_seed_recompute_reset_and_audit_workflow(client):
    assert client.post("/admin/seed-sample", params={"reset": "true"}).status_code == 200
    assert client.post("/admin/recompute").status_code == 200
    assert client.get("/admin/audit").json()["items"]
    reset = client.post("/admin/reset")
    assert reset.status_code == 200
    assert reset.json()["stats"]["valid_events"] == 0


def test_assistant_cancel_does_not_change_incident_state(client, make_event):
    assert client.post("/ingest", json=make_event(event_id="EVT-MATRIX-ASST")).status_code == 200
    incident_id = client.get("/incidents").json()["items"][0]["incident_id"]
    body = client.post("/assistant/query", json={"query": f"Resolve {incident_id}"}).json()
    assert body["requires_confirmation"] is True
    cancelled = client.post("/assistant/confirm", json={"request_id": body["request_id"], "confirmed": False})
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert client.get(f"/incidents/{incident_id}").json()["status"] == "open"
    assert client.get("/assistant/history").json()["items"]
