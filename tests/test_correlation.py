"""Explainable correlation and incident-separation tests."""
from __future__ import annotations

from datetime import datetime


def _traffic_event(make_event, event_id, source, device, occurred, **overrides):
    return make_event(
        event_id=event_id,
        source_type=source,
        device_id=device,
        event_type="speed_violation" if source == "anpr" else "speed_detection",
        occurred_at=occurred,
        received_at=overrides.pop("received_at", occurred),
        severity="high",
        confidence=0.9,
        zone=overrides.pop("zone", "North Gate"),
        **overrides,
    )


def test_related_events_form_one_explained_incident(client, make_event):
    records = [
        make_event(
            event_id="EVT-REL-3", source_type="cctv", device_id="CAM-REL-1",
            event_type="vehicle_motion", occurred_at="2026-03-12T10:04:00Z",
            received_at="2026-03-12T10:05:00Z", severity="high", confidence=0.84,
            zone="North Gate",
        ),
        make_event(
            event_id="EVT-REL-2", source_type="radar", device_id="RAD-REL-1",
            event_type="speed_detection", occurred_at="2026-03-12T10:02:00Z",
            received_at="2026-03-12T10:06:00Z", severity="high", confidence=0.91,
            zone="North Gate", payload={"speed_kmh": 88},
        ),
        make_event(
            event_id="EVT-REL-1", source_type="anpr", device_id="ANPR-REL-1",
            event_type="speed_violation", occurred_at="2026-03-12T10:00:00Z",
            received_at="2026-03-12T10:07:00Z", severity="high", confidence=0.93,
            zone="North Gate", payload={"plate": "SYN-1", "speed_kmh": 87},
        ),
    ]
    assert client.post("/ingest", json=records).status_code == 200
    listing = client.get("/incidents").json()
    assert listing["total"] == 1
    incident = listing["items"][0]
    assert incident["event_count"] == 3
    assert set(incident["source_event_ids"]) == {"EVT-REL-1", "EVT-REL-2", "EVT-REL-3"}
    assert incident["severity"] == "high"
    assert 0 < incident["confidence"] <= 1
    assert "share" in incident["correlation_explanation"]
    assert "Arrival time was not used" in incident["correlation_explanation"]
    assert incident["is_possible_association"] is False

    detail = client.get(f"/incidents/{incident['incident_id']}").json()
    assert [row["event_id"] for row in detail["timeline"]] == [
        "EVT-REL-1",
        "EVT-REL-2",
        "EVT-REL-3",
    ]
    assert detail["recommended_actions"]
    assert detail["locations"] == ["North Gate"]


def test_unrelated_locations_and_categories_stay_separate(client, make_event):
    records = [
        make_event(
            event_id="EVT-SEP-1", source_type="anpr", event_type="speed_violation",
            device_id="ANPR-1", zone="North Gate", severity="high",
            occurred_at="2026-03-12T10:00:00Z",
        ),
        make_event(
            event_id="EVT-SEP-2", source_type="anpr", event_type="speed_violation",
            device_id="ANPR-2", zone="South Lot", severity="high",
            occurred_at="2026-03-12T10:01:00Z",
        ),
        make_event(
            event_id="EVT-SEP-3", source_type="access_control", event_type="access_decision",
            device_id="DOOR-1", zone="Main Entrance", severity="medium",
            occurred_at="2026-03-12T10:00:00Z", payload={"result": "denied"},
        ),
        make_event(
            event_id="EVT-SEP-4", source_type="weather", event_type="temperature_high",
            device_id="WX-1", zone="Main Entrance", severity="low",
            occurred_at="2026-03-12T10:00:00Z", payload={"temperature_c": 32},
        ),
    ]
    assert client.post("/ingest", json=records).status_code == 200
    listing = client.get("/incidents").json()
    assert listing["total"] == 4
    clusters = [set(item["source_event_ids"]) for item in listing["items"]]
    assert {"EVT-SEP-1"} in clusters
    assert {"EVT-SEP-2"} in clusters
    assert {"EVT-SEP-3"} in clusters
    assert {"EVT-SEP-4"} in clusters


def test_arrival_proximity_alone_does_not_merge(client, make_event):
    records = [
        _traffic_event(
            make_event, "EVT-ARR-1", "anpr", "ANPR-A", "2026-03-12T10:00:00Z",
            received_at="2026-03-12T10:41:00Z",
        ),
        _traffic_event(
            make_event, "EVT-ARR-2", "radar", "RAD-A", "2026-03-12T10:40:00Z",
            received_at="2026-03-12T10:41:30Z",
        ),
    ]
    assert client.post("/ingest", json=records).status_code == 200
    listing = client.get("/incidents").json()
    assert listing["total"] == 2


def test_geographic_proximity_without_zone(client, make_event):
    records = [
        make_event(
            event_id="EVT-GEO-1", source_type="anpr", event_type="speed_violation",
            device_id="ANPR-GEO-1", zone=None, latitude=51.5050, longitude=-0.1250,
            occurred_at="2026-03-12T10:00:00Z",
        ),
        make_event(
            event_id="EVT-GEO-2", source_type="radar", event_type="speed_detection",
            device_id="RAD-GEO-1", zone=None, latitude=51.5051, longitude=-0.1255,
            occurred_at="2026-03-12T10:05:00Z",
        ),
        make_event(
            event_id="EVT-GEO-3", source_type="radar", event_type="speed_detection",
            device_id="RAD-GEO-2", zone=None, latitude=51.6000, longitude=-0.3000,
            occurred_at="2026-03-12T10:05:00Z",
        ),
    ]
    assert client.post("/ingest", json=records).status_code == 200
    listing = client.get("/incidents").json()
    assert listing["total"] == 2
    clusters = sorted([set(item["source_event_ids"]) for item in listing["items"]], key=len)
    assert clusters == [{"EVT-GEO-3"}, {"EVT-GEO-1", "EVT-GEO-2"}]


def test_incident_status_survives_recomputation(client, make_event):
    records = [
        _traffic_event(make_event, "EVT-STAT-1", "anpr", "ANPR-S1", "2026-03-12T10:00:00Z"),
        _traffic_event(make_event, "EVT-STAT-2", "radar", "RAD-S1", "2026-03-12T10:01:00Z"),
    ]
    assert client.post("/ingest", json=records).status_code == 200
    incident_id = client.get("/incidents").json()["items"][0]["incident_id"]
    assert client.patch(f"/incidents/{incident_id}", json={"status": "investigating"}).status_code == 200
    recompute = client.post("/admin/recompute")
    assert recompute.status_code == 200
    detail = client.get(f"/incidents/{incident_id}").json()
    assert detail["status"] == "investigating"


def test_contradictions_and_missing_information_are_exposed(client, make_event):
    records = [
        make_event(
            event_id="EVT-CX-1", source_type="access_control", device_id="DOOR-CX-1",
            event_type="access_decision", occurred_at="2026-03-12T10:00:00Z",
            severity="high", confidence=0.9, payload={"result": "denied"},
        ),
        make_event(
            event_id="EVT-CX-2", source_type="face_recognition", device_id="FACE-CX-1",
            event_type="access_decision", occurred_at="2026-03-12T10:01:00Z",
            severity="medium", confidence=0.27, payload={"result": "granted"},
        ),
    ]
    assert client.post("/ingest", json=records).status_code == 200
    incident = client.get("/incidents").json()["items"][0]
    assert incident["is_possible_association"] is True
    detail = client.get(f"/incidents/{incident['incident_id']}").json()
    assert detail["contradictions"]
    assert any("low-confidence" in item for item in detail["missing_info"])
    assert any("CCTV" in item for item in detail["missing_info"])
    assert "quality finding" in detail["correlation_explanation"]


def test_incident_filters_by_source_device_zone_and_severity(client, make_event):
    records = [
        _traffic_event(make_event, "EVT-FILT-1", "anpr", "ANPR-F1", "2026-03-12T10:00:00Z"),
        _traffic_event(make_event, "EVT-FILT-2", "radar", "RAD-F2", "2026-03-12T10:01:00Z"),
        make_event(
            event_id="EVT-FILT-3", source_type="network_log", event_type="failed_login",
            device_id="IDS-F3", zone="Data Center", severity="low",
            occurred_at="2026-03-12T10:00:00Z",
        ),
    ]
    assert client.post("/ingest", json=records).status_code == 200
    assert client.get("/incidents", params={"source": "anpr"}).json()["total"] == 1
    assert client.get("/incidents", params={"device": "IDS-F3"}).json()["total"] == 1
    assert client.get("/incidents", params={"zone": "Data Center"}).json()["total"] == 1
    assert client.get("/incidents", params={"severity": "high"}).json()["total"] == 1
    assert client.get("/incidents", params={"severity": "low"}).json()["total"] == 1
    search = client.get("/incidents", params={"search": "North Gate"}).json()
    assert search["total"] == 1
    facets = client.get("/incidents/facets").json()
    assert "anpr" in facets["source_types"]
    assert "North Gate" in facets["zones"]


def test_required_source_categories_are_classified():
    from app.services.correlation import classify_event

    sources = {
        "cctv": "surveillance",
        "anpr": "traffic",
        "face_recognition": "access",
        "mobile_phone_detection": "signals",
        "seatbelt_detection": "traffic",
        "red_light": "traffic",
        "radar": "traffic",
        "access_control": "access",
        "perimeter_sensor": "intrusion",
        "device_health": "device_health",
        "operator_report": "operator",
        "emergency_alarm": "emergency",
        "gps": "fleet",
        "network_log": "cyber",
        "weather": "environmental",
    }
    for source, expected in sources.items():
        assert classify_event(source, "detection") == expected


def test_events_on_correlation_boundary_are_related_but_beyond_boundary_are_not(
    client, make_event
):
    boundary = [
        _traffic_event(make_event, "EVT-BOUND-1", "anpr", "ANPR-B1", "2026-03-12T10:00:00Z"),
        _traffic_event(make_event, "EVT-BOUND-2", "radar", "RAD-B1", "2026-03-12T10:15:00Z"),
    ]
    assert client.post("/ingest", json=boundary).status_code == 200
    assert client.get("/incidents").json()["total"] == 1

    assert client.post("/admin/reset").status_code == 200
    beyond = [
        _traffic_event(make_event, "EVT-BEYOND-1", "anpr", "ANPR-B2", "2026-03-12T10:00:00Z"),
        _traffic_event(make_event, "EVT-BEYOND-2", "radar", "RAD-B2", "2026-03-12T10:16:00Z"),
    ]
    assert client.post("/ingest", json=beyond).status_code == 200
    assert client.get("/incidents").json()["total"] == 2


def _single_incident_containing(client, event_id):
    matches = [
        item
        for item in client.get("/incidents", params={"page_size": 200}).json()["items"]
        if event_id in item["source_event_ids"]
    ]
    assert len(matches) == 1
    return matches[0]


def test_status_and_id_survive_when_a_new_event_joins(client, make_event):
    first = [
        _traffic_event(make_event, "EVT-JOIN-1", "anpr", "ANPR-J1", "2026-03-12T10:00:00Z"),
        _traffic_event(make_event, "EVT-JOIN-2", "radar", "RAD-J1", "2026-03-12T10:02:00Z"),
    ]
    assert client.post("/ingest", json=first).status_code == 200
    incident_id = _single_incident_containing(client, "EVT-JOIN-1")["incident_id"]
    assert client.patch(f"/incidents/{incident_id}", json={"status": "investigating"}).status_code == 200

    joining = [_traffic_event(make_event, "EVT-JOIN-3", "radar", "RAD-J2", "2026-03-12T10:05:00Z")]
    assert client.post("/ingest", json=joining).status_code == 200

    incident = _single_incident_containing(client, "EVT-JOIN-3")
    assert incident["incident_id"] == incident_id
    assert incident["event_count"] == 3
    assert client.get(f"/incidents/{incident_id}").json()["status"] == "investigating"


def test_status_survives_when_a_late_earlier_event_changes_the_anchor(client, make_event):
    first = [
        _traffic_event(make_event, "EVT-ANCH-2", "anpr", "ANPR-A1", "2026-03-12T10:05:00Z"),
        _traffic_event(make_event, "EVT-ANCH-3", "radar", "RAD-A1", "2026-03-12T10:07:00Z"),
    ]
    assert client.post("/ingest", json=first).status_code == 200
    incident_id = _single_incident_containing(client, "EVT-ANCH-2")["incident_id"]
    assert client.patch(f"/incidents/{incident_id}", json={"status": "resolved"}).status_code == 200

    # Occurred earlier than the current anchor, delivered late.
    late = [
        _traffic_event(
            make_event, "EVT-ANCH-1", "radar", "RAD-A2", "2026-03-12T10:01:00Z",
            received_at="2026-03-12T10:40:00Z",
        )
    ]
    assert client.post("/ingest", json=late).status_code == 200

    incident = _single_incident_containing(client, "EVT-ANCH-1")
    assert incident["incident_id"] == incident_id
    assert set(incident["source_event_ids"]) == {"EVT-ANCH-1", "EVT-ANCH-2", "EVT-ANCH-3"}
    assert incident["status"] == "resolved"


def test_intrusion_and_cctv_in_same_zone_are_linked_across_sources(client, make_event):
    records = [
        make_event(
            event_id="EVT-XS-1", source_type="perimeter", device_id="PER-XS-1",
            event_type="fence_vibration", occurred_at="2026-03-12T10:00:00Z",
            zone="Perimeter North", severity="high", payload={"sector": "P-3"},
        ),
        make_event(
            event_id="EVT-XS-2", source_type="cctv", device_id="CAM-XS-1",
            event_type="camera_clip", occurred_at="2026-03-12T10:03:00Z",
            zone="Perimeter North", severity="medium",
        ),
        make_event(
            event_id="EVT-XS-3", source_type="operator_report", device_id="OP-XS-1",
            event_type="patrol_report", occurred_at="2026-03-12T10:09:00Z",
            zone="Perimeter North", severity="medium",
        ),
    ]
    assert client.post("/ingest", json=records).status_code == 200
    listing = client.get("/incidents").json()
    assert listing["total"] == 1
    incident = listing["items"][0]
    assert incident["category"] == "intrusion"
    assert set(incident["sources"]) == {"perimeter", "cctv", "operator_report"}
    assert "compatible categories" in incident["correlation_explanation"]
    detail = client.get(f"/incidents/{incident['incident_id']}").json()
    assert not any("CCTV" in item for item in detail["missing_info"])


def test_incompatible_categories_in_one_zone_are_not_chained(client, make_event):
    records = [
        make_event(
            event_id="EVT-NC-1", source_type="perimeter", device_id="PER-NC-1",
            event_type="perimeter_breach", occurred_at="2026-03-12T10:00:00Z", zone="Depot",
        ),
        # Surveillance is compatible with both intrusion and traffic ...
        make_event(
            event_id="EVT-NC-2", source_type="cctv", device_id="CAM-NC-1",
            event_type="clip_recorded", occurred_at="2026-03-12T10:02:00Z", zone="Depot",
        ),
        # ... but traffic is not compatible with intrusion, so it must not chain in.
        _traffic_event(make_event, "EVT-NC-3", "anpr", "ANPR-NC-1", "2026-03-12T10:04:00Z", zone="Depot"),
        make_event(
            event_id="EVT-NC-4", source_type="network_log", device_id="IDS-NC-1",
            event_type="port_scan", occurred_at="2026-03-12T10:05:00Z", zone="Depot",
        ),
        make_event(
            event_id="EVT-NC-5", source_type="weather", device_id="WX-NC-1",
            event_type="wind_gust", occurred_at="2026-03-12T10:06:00Z", zone="Depot",
        ),
    ]
    assert client.post("/ingest", json=records).status_code == 200
    clusters = sorted(
        (sorted(item["source_event_ids"]) for item in client.get("/incidents").json()["items"]),
    )
    assert clusters == [["EVT-NC-1", "EVT-NC-2"], ["EVT-NC-3"], ["EVT-NC-4"], ["EVT-NC-5"]]


def test_continuous_activity_is_split_at_the_maximum_span(client, make_event):
    # One event every 10 minutes for 6 hours: every gap is inside the 15-minute
    # window, so without a span cap it would become a single 6-hour incident.
    records = []
    for index in range(37):
        minutes = index * 10
        records.append(
            _traffic_event(
                make_event, f"EVT-SPAN-{index:02d}", "radar", "RAD-SPAN-1",
                f"2026-03-12T{10 + minutes // 60:02d}:{minutes % 60:02d}:00Z",
            )
        )
    assert client.post("/ingest", json=records).status_code == 200
    listing = client.get("/incidents", params={"page_size": 200}).json()
    assert listing["total"] >= 5
    covered = [event_id for item in listing["items"] for event_id in item["source_event_ids"]]
    assert sorted(covered) == sorted(record["event_id"] for record in records)
    for item in listing["items"]:
        timeline = client.get(f"/incidents/{item['incident_id']}").json()["timeline"]
        times = [datetime.fromisoformat(row["occurred_at"].replace("Z", "+00:00")) for row in timeline]
        assert (max(times) - min(times)).total_seconds() <= 60 * 60
    split = [item for item in listing["items"] if "was split because" in item["correlation_explanation"]]
    assert len(split) == listing["total"] - 1
    assert all("maximum incident span" in item["correlation_explanation"] for item in split)


def test_missing_cctv_line_only_where_video_is_expected(client, make_event):
    records = [
        _traffic_event(make_event, "EVT-CV-1", "anpr", "ANPR-CV-1", "2026-03-12T10:00:00Z"),
        make_event(
            event_id="EVT-CV-2", source_type="perimeter", device_id="PER-CV-1",
            event_type="perimeter_breach", occurred_at="2026-03-12T10:00:00Z", zone="Fence Line",
        ),
        make_event(
            event_id="EVT-CV-3", source_type="network_log", device_id="IDS-CV-1",
            event_type="failed_login", occurred_at="2026-03-12T10:00:00Z", zone="Data Center",
        ),
    ]
    assert client.post("/ingest", json=records).status_code == 200
    by_event = {
        item["source_event_ids"][0]: client.get(f"/incidents/{item['incident_id']}").json()
        for item in client.get("/incidents").json()["items"]
    }
    assert not any("CCTV" in line for line in by_event["EVT-CV-1"]["missing_info"])
    assert any("CCTV" in line for line in by_event["EVT-CV-2"]["missing_info"])
    assert not any("CCTV" in line for line in by_event["EVT-CV-3"]["missing_info"])
