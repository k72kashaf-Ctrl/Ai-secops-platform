"""Silent-device, cross-type conflict and incomplete-record quality checks."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

BASE = datetime(2026, 3, 12, 8, 0, tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def issues_for(client, issue_type: str):
    response = client.get("/quality-issues", params={"issue_type": issue_type, "page_size": 500})
    assert response.status_code == 200
    return response.json()


def _heartbeats(make_event, device_id: str, offsets_minutes: list[int]):
    return [
        make_event(
            event_id=f"EVT-HB-{device_id}-{index:03d}",
            source_type="device_health",
            device_id=device_id,
            event_type="heartbeat",
            occurred_at=_iso(BASE + timedelta(minutes=offset)),
            received_at=_iso(BASE + timedelta(minutes=offset, seconds=2)),
            payload={"status": "ok"},
        )
        for index, offset in enumerate(offsets_minutes)
    ]


# --------------------------------------------------------------- G5 offline


def test_silent_heartbeat_gap_flags_offline_device(client, make_event):
    # Every minute for 10 minutes, then silent for 40 minutes, then resumes.
    offsets = list(range(0, 10)) + [50, 51, 52]
    assert client.post("/ingest", json=_heartbeats(make_event, "HB-GATE-01", offsets)).status_code == 200

    result = issues_for(client, "offline_device")
    assert result["total"] == 1
    issue = result["items"][0]
    assert issue["device_id"] == "HB-GATE-01"
    assert issue["details"]["last_event_before_gap"] == "EVT-HB-HB-GATE-01-009"
    assert issue["details"]["first_event_after_gap"] == "EVT-HB-HB-GATE-01-010"
    assert issue["details"]["gap_seconds"] == 41 * 60
    assert issue["details"]["inferred"] is True
    assert "inferred from missing reports" in issue["explanation"]


def test_device_silent_until_dataset_end_is_flagged(client, make_event):
    records = _heartbeats(make_event, "HB-SILENT-01", list(range(0, 6)))
    # Another device keeps reporting for an hour, defining the dataset end.
    records += _heartbeats(make_event, "HB-ALIVE-01", list(range(0, 61)))
    assert client.post("/ingest", json=records).status_code == 200

    result = issues_for(client, "offline_device")
    devices = {item["device_id"] for item in result["items"]}
    assert devices == {"HB-SILENT-01"}
    issue = result["items"][0]
    assert issue["details"]["first_event_after_gap"] is None
    assert "has not reported since" in issue["explanation"]


def test_regular_cadence_and_event_driven_sources_not_flagged(client, make_event):
    records = _heartbeats(make_event, "HB-OK-01", list(range(0, 305, 5)))
    # Event-driven CCTV with long gaps is not a health signal.
    records += [
        make_event(
            event_id=f"EVT-CCTV-GAP-{index}",
            device_id="CAM-QUIET-01",
            occurred_at=_iso(BASE + timedelta(minutes=offset)),
            received_at=_iso(BASE + timedelta(minutes=offset, seconds=1)),
        )
        for index, offset in enumerate([0, 1, 2, 3, 4, 300])
    ]
    assert client.post("/ingest", json=records).status_code == 200
    assert issues_for(client, "offline_device")["total"] == 0


def test_offline_device_counted_in_db_stats(client, db, make_event):
    offsets = list(range(0, 10)) + [60]
    assert client.post("/ingest", json=_heartbeats(make_event, "HB-COUNT-01", offsets)).status_code == 200
    assert db.stats()["unhealthy_devices"] >= 1


# ----------------------------------------------------------- G6 cross-type


def test_perimeter_alarm_vs_cctv_clear_is_cross_type_conflict(client, make_event):
    records = [
        make_event(
            event_id="EVT-XT-PER",
            source_type="perimeter",
            device_id="PER-NG-01",
            event_type="perimeter_breach",
            payload={"result": "alarm", "sector": "P-2"},
        ),
        make_event(
            event_id="EVT-XT-CAM",
            source_type="cctv",
            device_id="CAM-NG-02",
            event_type="scene_status",
            occurred_at="2026-03-12T10:02:00Z",
            received_at="2026-03-12T10:02:03Z",
            payload={"result": "clear"},
        ),
    ]
    assert client.post("/ingest", json=records).status_code == 200
    result = issues_for(client, "conflicting_sensors")
    assert result["total"] == 1
    issue = result["items"][0]
    assert issue["details"]["cross_type"] is True
    assert {issue["details"]["first_event_id"], issue["details"]["second_event_id"]} == {
        "EVT-XT-PER",
        "EVT-XT-CAM",
    }
    assert "intrusion detected vs area reported clear" in issue["explanation"]
    assert issue["severity"] == "high"


def test_access_granted_vs_face_no_match_is_cross_type_conflict(client, make_event):
    records = [
        make_event(
            event_id="EVT-XT-DOOR",
            source_type="access_control",
            device_id="DOOR-MAIN-01",
            event_type="access_granted",
            zone="Main Entrance",
            payload={"result": "granted", "badge": "B-100"},
        ),
        make_event(
            event_id="EVT-XT-FACE",
            source_type="face_recognition",
            device_id="FACE-MAIN-01",
            event_type="unknown_face",
            zone="Main Entrance",
            occurred_at="2026-03-12T10:00:20Z",
            received_at="2026-03-12T10:00:22Z",
            payload={"match": "no_match"},
        ),
    ]
    assert client.post("/ingest", json=records).status_code == 200
    result = issues_for(client, "conflicting_sensors")
    assert result["total"] == 1
    assert result["items"][0]["details"]["rule"] == "access granted vs face recognition not matching"


def test_cross_type_respects_window_zone_and_subject(client, make_event):
    records = [
        # Outside the 2-minute access window.
        make_event(
            event_id="EVT-XT-D1",
            source_type="access_control",
            device_id="DOOR-A-01",
            event_type="access_granted",
            zone="Gate A",
            payload={"result": "granted"},
        ),
        make_event(
            event_id="EVT-XT-F1",
            source_type="face_recognition",
            device_id="FACE-A-01",
            event_type="unknown_face",
            zone="Gate A",
            occurred_at="2026-03-12T10:04:00Z",
            received_at="2026-03-12T10:04:01Z",
            payload={"match": "no_match"},
        ),
        # Different zone.
        make_event(
            event_id="EVT-XT-P2",
            source_type="perimeter",
            device_id="PER-B-01",
            event_type="perimeter_breach",
            zone="Gate B",
            payload={"result": "alarm"},
        ),
        make_event(
            event_id="EVT-XT-C2",
            source_type="cctv",
            device_id="CAM-C-01",
            event_type="scene_status",
            zone="Gate C",
            payload={"result": "clear"},
        ),
        # Different subjects at the same door.
        make_event(
            event_id="EVT-XT-D3",
            source_type="access_control",
            device_id="DOOR-D-01",
            event_type="access_granted",
            zone="Gate D",
            payload={"result": "granted", "subject": "alice"},
        ),
        make_event(
            event_id="EVT-XT-F3",
            source_type="face_recognition",
            device_id="FACE-D-01",
            event_type="face_check",
            zone="Gate D",
            payload={"match": "no_match", "subject": "bob"},
        ),
        # Plain CCTV motion is not a contradiction of a perimeter "clear".
        make_event(
            event_id="EVT-XT-P4",
            source_type="perimeter",
            device_id="PER-E-01",
            event_type="perimeter_clear",
            zone="Gate E",
            payload={"result": "clear"},
        ),
        make_event(
            event_id="EVT-XT-C4",
            source_type="cctv",
            device_id="CAM-E-01",
            event_type="motion_detected",
            zone="Gate E",
            payload={"object": "person"},
        ),
    ]
    assert client.post("/ingest", json=records).status_code == 200
    assert issues_for(client, "conflicting_sensors")["total"] == 0


# ------------------------------------------------------- G7 incomplete


def test_missing_severity_confidence_flagged_as_incomplete(client, make_event, db):
    record = make_event(event_id="EVT-INCOMPLETE-1")
    for name in ("severity", "confidence", "received_at"):
        record.pop(name)
    complete = make_event(event_id="EVT-COMPLETE-1", device_id="CAM-TEST-02", occurred_at="2026-03-12T11:00:00Z", received_at="2026-03-12T11:00:02Z")
    blank = make_event(event_id="EVT-INCOMPLETE-2", device_id="CAM-TEST-03", severity="", occurred_at="2026-03-12T12:00:00Z", received_at="2026-03-12T12:00:02Z")
    response = client.post("/ingest", json=[record, complete, blank])
    assert response.status_code == 200

    # Still accepted, with defaults applied for downstream use.
    stored = db.fetchone("SELECT severity, confidence FROM events WHERE event_id = ?", ("EVT-INCOMPLETE-1",))
    assert stored["severity"] == "medium"
    assert stored["confidence"] == 0.5

    result = issues_for(client, "incomplete_record")
    by_event = {item["event_id"]: item for item in result["items"]}
    assert set(by_event) == {"EVT-INCOMPLETE-1", "EVT-INCOMPLETE-2"}
    first = by_event["EVT-INCOMPLETE-1"]
    assert first["details"]["defaulted_fields"] == ["confidence", "received_at", "severity"]
    assert first["details"]["applied_defaults"]["confidence"] == 0.5
    assert "assumed, not reported" in first["explanation"]
    assert by_event["EVT-INCOMPLETE-2"]["details"]["defaulted_fields"] == ["severity"]


def test_eventin_reports_defaulted_fields():
    from app.schemas import EventIn

    assert EventIn.defaulted_fields({"severity": "high", "confidence": 0.9, "received_at": "x"}) == []
    assert EventIn.defaulted_fields({"severity": " ", "confidence": None}) == [
        "severity",
        "confidence",
        "received_at",
    ]
