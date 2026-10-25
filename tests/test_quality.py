"""Deterministic data-quality checks."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def issues_for(client, issue_type: str, **params):
    response = client.get(
        "/quality-issues", params={"issue_type": issue_type, "page_size": 500, **params}
    )
    assert response.status_code == 200
    return response.json()


def test_delayed_event_flagged(client, make_event):
    event = make_event(
        event_id="EVT-DELAY-1",
        occurred_at="2026-03-12T10:00:00Z",
        received_at="2026-03-12T10:12:00Z",
    )
    assert client.post("/ingest", json=event).status_code == 200
    result = issues_for(client, "delayed_event", event_id="EVT-DELAY-1")
    assert result["total"] == 1
    issue = result["items"][0]
    assert issue["issue_id"].startswith("QI-")
    assert issue["severity"] in {"medium", "high"}
    assert "12.0 minutes" in issue["explanation"] or "later" in issue["explanation"]
    assert issue["suggested_action"]
    assert issue["detected_at"]
    assert issue["details"]["delay_seconds"] == 720


def test_out_of_order_arrival_uses_event_time(client, make_event):
    records = [
        make_event(
            event_id="EVT-OOO-A",
            device_id="CAM-ORDER-01",
            occurred_at="2026-03-12T10:00:00Z",
            received_at="2026-03-12T10:00:02Z",
        ),
        make_event(
            event_id="EVT-OOO-B",
            device_id="CAM-ORDER-01",
            occurred_at="2026-03-12T09:40:00Z",
            received_at="2026-03-12T10:05:00Z",
        ),
    ]
    assert client.post("/ingest", json=records).status_code == 200
    result = issues_for(client, "out_of_order_arrival")
    assert result["total"] == 1
    issue = result["items"][0]
    assert issue["event_id"] == "EVT-OOO-B"
    assert issue["details"]["previous_event_id"] == "EVT-OOO-A"
    assert "Arrival order is not chronological" in issue["explanation"]


def test_conflicting_sensor_results_flagged(client, make_event):
    records = [
        make_event(
            event_id="EVT-CONF-1",
            source_type="access_control",
            device_id="DOOR-CONF-1",
            event_type="access_decision",
            occurred_at="2026-03-12T10:00:00Z",
            confidence=0.92,
            payload={"result": "granted", "subject": "visitor-1"},
        ),
        make_event(
            event_id="EVT-CONF-2",
            source_type="face_recognition",
            device_id="FACE-CONF-1",
            event_type="access_decision",
            occurred_at="2026-03-12T10:01:00Z",
            confidence=0.90,
            payload={"result": "denied", "subject": "visitor-1"},
        ),
    ]
    assert client.post("/ingest", json=records).status_code == 200
    result = issues_for(client, "conflicting_sensors")
    assert result["total"] == 1
    issue = result["items"][0]
    assert issue["severity"] == "high"
    assert "granted" in issue["details"]["reason"] or "denied" in issue["details"]["reason"]
    assert issue["details"]["first_event_id"] in {"EVT-CONF-1", "EVT-CONF-2"}


def test_conflicting_location_flagged(client, make_event):
    records = [
        make_event(
            event_id="EVT-LOC-1",
            device_id="CAM-LOC-1",
            occurred_at="2026-03-12T10:00:00Z",
            longitude=-0.1278,
            latitude=51.5074,
        ),
        make_event(
            event_id="EVT-LOC-2",
            device_id="CAM-LOC-1",
            occurred_at="2026-03-12T10:01:00Z",
            zone="Remote Yard",
            latitude=51.7000,
            longitude=-0.4000,
        ),
    ]
    assert client.post("/ingest", json=records).status_code == 200
    result = issues_for(client, "conflicting_location")
    assert result["total"] == 1
    assert "km apart" in result["items"][0]["explanation"]


def test_low_confidence_detection(client, make_event):
    event = make_event(event_id="EVT-LOW-1", confidence=0.21)
    assert client.post("/ingest", json=event).status_code == 200
    result = issues_for(client, "low_confidence", event_id="EVT-LOW-1")
    assert result["total"] == 1
    issue = result["items"][0]
    assert issue["details"]["confidence"] == 0.21
    assert "below" in issue["explanation"]


def test_repeated_events_from_same_device(client, make_event):
    base = datetime(2026, 3, 12, 10, 0, tzinfo=timezone.utc)
    records = [
        make_event(
            event_id=f"EVT-REP-{index}",
            device_id="CAM-REPEAT-01",
            event_type="motion_detected",
            occurred_at=(base + timedelta(seconds=index * 20)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            received_at=(base + timedelta(seconds=index * 20 + 2)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            payload={"object": "person", "frame": index},
        )
        for index in range(9)
    ]
    assert client.post("/ingest", json=records).status_code == 200
    result = issues_for(client, "repeated_events")
    assert result["total"] == 1
    issue = result["items"][0]
    assert issue["device_id"] == "CAM-REPEAT-01"
    assert issue["details"]["count"] == 9


def test_event_spike_across_devices(client, make_event):
    base = datetime(2026, 3, 12, 10, 0, tzinfo=timezone.utc)
    records = []
    for index in range(16):
        records.append(
            make_event(
                event_id=f"EVT-SPIKE-T-{index}",
                source_type="radar",
                device_id=f"RAD-SPIKE-{index % 4}",
                event_type="speed_detection",
                zone="East Junction",
                occurred_at=(base + timedelta(seconds=index * 12)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                received_at=(base + timedelta(seconds=index * 12 + 2)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                payload={"speed_kmh": 60 + index},
            )
        )
    assert client.post("/ingest", json=records).status_code == 200
    result = issues_for(client, "event_spike")
    assert result["total"] >= 1
    issue = result["items"][0]
    assert issue["details"]["zone"] == "East Junction"
    assert issue["details"]["count"] >= 15
    assert "not proof of an attack" in issue["explanation"]


def test_unhealthy_device_report(client, make_event):
    event = make_event(
        event_id="EVT-HEALTH-1",
        source_type="device_health",
        device_id="HEALTH-CAM-09",
        event_type="heartbeat",
        confidence=0.99,
        payload={"status": "offline", "battery_pct": 2},
    )
    assert client.post("/ingest", json=event).status_code == 200
    result = issues_for(client, "unhealthy_device", device_id="HEALTH-CAM-09")
    assert result["total"] == 1
    issue = result["items"][0]
    assert issue["severity"] == "high"
    assert "explicit device report" in issue["explanation"]


def test_missing_and_invalid_records_become_quality_issues(client, make_event):
    missing = make_event(event_id="EVT-MISS-1")
    del missing["device_id"]
    invalid_time = make_event(event_id="EVT-INVALID-TIME", occurred_at="not-a-date")
    assert client.post("/ingest", json=missing).status_code == 422
    assert client.post("/ingest", json=invalid_time).status_code == 422
    missing_issues = issues_for(client, "missing_mandatory_field", event_id="EVT-MISS-1")
    assert missing_issues["total"] == 1
    assert "device_id" in missing_issues["items"][0]["explanation"]
    invalid_issues = issues_for(client, "invalid_record", event_id="EVT-INVALID-TIME")
    assert invalid_issues["total"] == 1


def test_rejected_raw_evidence_is_not_lost(client, db):
    response = client.post("/ingest", json={"event_id": "EVT-RAW-1", "source_type": "cctv"})
    assert response.status_code == 422
    row = db.fetchone("SELECT * FROM rejected_events WHERE source_event_id = 'EVT-RAW-1'")
    assert row is not None
    assert "cctv" in row["raw_record"]
    assert row["reason"] == "validation_error"


def test_uncorroborated_high_severity_event(client, make_event):
    event = make_event(
        event_id="EVT-SOLO-HIGH",
        source_type="anpr",
        event_type="speed_violation",
        severity="critical",
        occurred_at="2026-03-12T10:00:00Z",
    )
    assert client.post("/ingest", json=event).status_code == 200
    result = issues_for(client, "uncorroborated_event", event_id="EVT-SOLO-HIGH")
    assert result["total"] == 1
    assert "unresolved" in result["items"][0]["explanation"]


def test_quality_analysis_is_idempotent_and_fields_complete(client, make_event):
    assert client.post(
        "/ingest",
        json=[
            make_event(event_id="EVT-IDEM-1", confidence=0.2),
            make_event(event_id="EVT-IDEM-2", severity="high"),
        ],
    ).status_code == 200
    first = client.get("/quality-issues", params={"page_size": 500}).json()
    assert first["total"] > 0
    for issue in first["items"]:
        assert issue["issue_id"]
        assert issue["issue_type"]
        assert issue["severity"] in {"low", "medium", "high", "critical"}
        assert issue["explanation"]
        assert issue["suggested_action"]
        assert issue["detected_at"]
    analysis = client.post("/quality/analyze")
    assert analysis.status_code == 200
    second = client.get("/quality-issues", params={"page_size": 500}).json()
    assert second["total"] == first["total"]
    ids = [issue["issue_id"] for issue in second["items"]]
    assert len(ids) == len(set(ids))
