"""API, ingestion and persistence tests."""
from __future__ import annotations

import json


def test_health_endpoint(client, db):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"]["database_connected"] is True
    assert body["database"]["database_integrity"] == "ok"
    assert body["statistics"]["valid_events"] == 0


def test_valid_event_ingestion_preserves_source_id(client, db, make_event):
    event = make_event(
        event_id="SRC-ANPR-7788",
        source_type="ANPR",
        device_id="ANPR-NG-09",
        event_type="Speed Violation",
        severity="HIGH",
        confidence=0.88,
    )
    response = client.post("/ingest", json=event)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accepted"] == 1
    assert body["rejected"] == 0
    assert body["status"] == "completed"

    detail = client.get("/events/SRC-ANPR-7788")
    assert detail.status_code == 200
    data = detail.json()
    assert data["event_id"] == "SRC-ANPR-7788"
    assert data["source_type"] == "anpr"
    assert data["event_type"] == "speed_violation"
    assert data["severity"] == "high"
    assert data["payload"] == {"object": "person"}
    assert data["raw_record"]["event_type"] == "Speed Violation"
    assert data["raw_record"]["source_type"] == "ANPR"
    assert db.stats()["valid_events"] == 1


def test_invalid_single_record_returns_422_and_preserves_raw(client, db):
    response = client.post(
        "/ingest",
        json={
            "event_id": "EVT-BAD-1",
            "source_type": "cctv",
            "event_type": "motion_detected",
            "occurred_at": "2026-03-12T10:00:00Z",
            # device_id intentionally missing
        },
    )
    assert response.status_code == 422
    body = response.json()
    assert body["accepted"] == 0
    assert body["rejected"] == 1
    assert body["errors"][0]["reason"] == "validation_error"
    rejected = db.fetchall("SELECT * FROM rejected_events")
    assert len(rejected) == 1
    raw = json.loads(rejected[0]["raw_record"])
    assert raw["event_id"] == "EVT-BAD-1"
    assert "device_id" in rejected[0]["errors"]


def test_batch_partial_success_and_malformed_record(client, db, make_event):
    records = [
        make_event(event_id="EVT-OK-1"),
        {"event_id": "EVT-BAD-2", "source_type": "cctv"},
        "not-an-object",
        make_event(event_id="EVT-BAD-3", occurred_at="12/03/2026 10:00"),
    ]
    response = client.post("/ingest", json=records)
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] == 1
    assert body["rejected"] == 3
    assert body["status"] == "partial"
    assert len(db.fetchall("SELECT * FROM rejected_events")) == 3
    reasons = {row["reason"] for row in db.fetchall("SELECT reason FROM rejected_events")}
    assert reasons == {"validation_error", "malformed_record"}


def test_duplicate_event_id_is_rejected_without_overwriting(client, db, make_event):
    first = make_event(event_id="EVT-DUP-ID")
    second = make_event(event_id="EVT-DUP-ID", payload={"object": "vehicle"})
    assert client.post("/ingest", json=first).status_code == 200
    response = client.post("/ingest", json=second)
    assert response.status_code == 422
    body = response.json()
    assert body["duplicate_ids"] == 1
    assert body["accepted"] == 0
    stored = db.fetchall("SELECT * FROM events WHERE event_id = 'EVT-DUP-ID'")
    assert len(stored) == 1
    assert json.loads(stored[0]["payload"]) == {"object": "person"}
    rejected = db.fetchall(
        "SELECT * FROM rejected_events WHERE reason = 'duplicate_event_id'"
    )
    assert len(rejected) == 1
    assert json.loads(rejected[0]["raw_record"])["payload"] == {"object": "vehicle"}


def test_duplicate_content_is_preserved_but_excluded_from_valid_metrics(
    client, db, make_event
):
    first = make_event(event_id="EVT-CONTENT-1")
    duplicate = make_event(event_id="EVT-CONTENT-2", received_at="2026-03-12T10:01:00Z")
    response = client.post("/ingest", json=[first, duplicate])
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] == 2
    assert body["duplicate_content"] == 1
    assert db.stats()["valid_events"] == 1
    assert db.stats()["duplicate_content_events"] == 1
    stored = db.fetchall("SELECT event_id, validation_status, duplicate_of FROM events ORDER BY event_id")
    duplicate_row = next(row for row in stored if row["event_id"] == "EVT-CONTENT-2")
    assert duplicate_row["validation_status"] == "duplicate"
    assert duplicate_row["duplicate_of"] == "EVT-CONTENT-1"
    issues = client.get("/quality-issues", params={"issue_type": "duplicate_content"}).json()
    assert issues["total"] == 1


def test_events_filters_search_and_pagination(client, make_event):
    records = [
        make_event(
            event_id="EVT-FILTER-1", source_type="anpr", severity="high",
            device_id="ANPR-X", zone="North Gate",
            occurred_at="2026-03-12T09:00:00Z",
        ),
        make_event(
            event_id="EVT-FILTER-2", source_type="cctv", severity="low",
            device_id="CAM-Y", zone="South Lot",
            occurred_at="2026-03-12T09:10:00Z",
        ),
        make_event(
            event_id="EVT-FILTER-3", source_type="anpr", severity="medium",
            device_id="ANPR-Z", zone="North Gate",
            occurred_at="2026-03-12T09:20:00Z",
        ),
    ]
    assert client.post("/ingest", json=records).status_code == 200
    assert client.get("/events", params={"source_type": "anpr"}).json()["total"] == 2
    assert client.get("/events", params={"severity": "high"}).json()["total"] == 1
    assert client.get("/events", params={"zone": "North Gate"}).json()["total"] == 2
    assert client.get("/events", params={"search": "ANPR-Z"}).json()["total"] == 1
    page = client.get("/events", params={"page_size": 2, "page": 1}).json()
    assert page["total"] == 3
    assert len(page["items"]) == 2
    assert page["pages"] == 2
    assert page["items"][0]["occurred_at"] >= page["items"][1]["occurred_at"]
    date_filtered = client.get(
        "/events",
        params={"start": "2026-03-12T09:05:00Z", "end": "2026-03-12T09:25:00Z"},
    ).json()
    assert date_filtered["total"] == 2
    facets = client.get("/events/facets").json()
    assert set(facets["source_types"]) == {"anpr", "cctv"}
    assert "North Gate" in facets["zones"]


def test_not_found_and_invalid_json(client):
    assert client.get("/events/DOES-NOT-EXIST").status_code == 404
    assert client.get("/incidents/DOES-NOT-EXIST").status_code == 404
    response = client.post(
        "/ingest", content="{not valid json", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert "errors" in response.json()


def test_incident_list_detail_and_status_update(client, make_event):
    records = [
        make_event(
            event_id="EVT-INC-1", source_type="anpr", event_type="speed_violation",
            device_id="ANPR-INC-1", severity="high", confidence=0.9,
            occurred_at="2026-03-12T10:00:00Z",
        ),
        make_event(
            event_id="EVT-INC-2", source_type="radar", event_type="speed_detection",
            device_id="RAD-INC-1", severity="high", confidence=0.85,
            occurred_at="2026-03-12T10:02:00Z",
        ),
    ]
    assert client.post("/ingest", json=records).status_code == 200
    listing = client.get("/incidents").json()
    assert listing["total"] == 1
    incident = listing["items"][0]
    incident_id = incident["incident_id"]
    assert set(incident["source_event_ids"]) == {"EVT-INC-1", "EVT-INC-2"}
    detail = client.get(f"/incidents/{incident_id}").json()
    assert [row["event_id"] for row in detail["timeline"]] == ["EVT-INC-1", "EVT-INC-2"]
    assert "share" in detail["correlation_explanation"]
    assert detail["recommended_actions"]

    updated = client.patch(f"/incidents/{incident_id}", json={"status": "investigating"})
    assert updated.status_code == 200
    assert client.get(f"/incidents/{incident_id}").json()["status"] == "investigating"
    assert client.get("/incidents", params={"status": "investigating"}).json()["total"] == 1
    assert client.get("/incidents", params={"status": "resolved"}).json()["total"] == 0

    invalid_status = client.patch(f"/incidents/{incident_id}", json={"status": "unknown"})
    assert invalid_status.status_code == 422


def test_incident_reasoning_is_structured_and_grounded(client, make_event):
    response = client.post(
        "/ingest",
        json=[
            make_event(event_id="EVT-REASON-1"),
            make_event(
                event_id="EVT-REASON-2",
                source_type="radar",
                event_type="speed_violation",
                occurred_at="2026-03-12T10:05:00Z",
            ),
        ],
    )
    assert response.status_code == 200
    incident_id = client.get("/incidents").json()["items"][0]["incident_id"]

    reasoning = client.get(f"/incidents/{incident_id}/reasoning")
    assert reasoning.status_code == 200
    body = reasoning.json()
    assert body["provider"] == "deterministic"
    assert body["grounded"] is True
    assert body["incident_ids"] == [incident_id]
    assert set(body["evidence_event_ids"]).issubset(
        {"EVT-REASON-1", "EVT-REASON-2"}
    )
    assert body["facts"]


def test_admin_api_key_protects_mutations(settings):
    from fastapi.testclient import TestClient

    from app.main import create_app

    protected_app = create_app(settings=settings.derive(admin_api_key="secret-key"))
    with TestClient(protected_app) as protected_client:
        assert protected_client.post("/admin/reset").status_code == 401
        assert protected_client.post(
            "/admin/reset",
            headers={"X-Admin-Key": "secret-key"},
        ).status_code == 200


def test_assistant_query_is_grounded_and_confirmation_gated(client, make_event):
    response = client.post(
        "/ingest",
        json=make_event(
            event_id="EVT-ASST-1",
            severity="high",
            zone="North Gate",
        ),
    )
    assert response.status_code == 200
    incident_id = client.get("/incidents").json()["items"][0]["incident_id"]

    query = client.post(
        "/assistant/query",
        json={"query": f"Resolve {incident_id}"},
    )
    assert query.status_code == 200
    body = query.json()
    assert body["requires_confirmation"] is True
    assert body["pending_action"]["status"] == "resolved"
    assert body["evidence_event_ids"] == ["EVT-ASST-1"]

    denied = client.post(
        "/assistant/confirm",
        json={"request_id": body["request_id"], "confirmed": True},
    )
    assert denied.status_code == 200
    assert denied.json()["status"] == "confirmed"
    assert client.get(f"/incidents/{incident_id}").json()["status"] == "resolved"


def test_assistant_briefing_query_and_history(client, make_event):
    assert client.post("/ingest", json=make_event(event_id="EVT-ASST-BRIEF")).status_code == 200
    result = client.post(
        "/assistant/query",
        json={"query": "Prepare a briefing for the current shift"},
    )
    assert result.status_code == 200
    body = result.json()
    assert body["provider"] == "deterministic"
    assert body["grounded"] is True
    history = client.get("/assistant/history").json()
    assert history["items"]


def test_rate_limit_and_audit_log(settings):
    from fastapi.testclient import TestClient

    from app.main import create_app

    protected_app = create_app(settings=settings.derive(rate_limit_per_minute=1))
    with TestClient(protected_app) as protected_client:
        assert protected_client.post("/admin/reset").status_code == 200
        assert protected_client.post("/admin/recompute").status_code == 429
        audit = protected_client.get("/admin/audit").json()
        assert audit["items"][0]["action"] == "admin.reset"


def test_overview_reports_real_data(client, make_event):
    assert client.post("/ingest", json=make_event(event_id="EVT-OV-1")).status_code == 200
    overview = client.get("/overview").json()
    assert overview["stats"]["valid_events"] == 1
    assert overview["stats"]["incidents"] == 1
    assert len(overview["recent_events"]) == 1
    assert overview["latest_event_at"] is not None
