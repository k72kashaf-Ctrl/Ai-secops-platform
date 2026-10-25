"""Incident time filters use occurrence time, not arrival/creation time."""
from __future__ import annotations


def _late_traffic_events(make_event):
    # Events happen at 10:00-10:04 but arrive four hours later (14:00+).
    return [
        make_event(
            event_id=f"EVT-TF-{index}", source_type=source, device_id=device,
            event_type=event_type, occurred_at=f"2026-03-12T10:0{index * 2}:00Z",
            received_at=f"2026-03-12T14:0{index}:00Z", severity="high",
            confidence=0.9, zone="North Gate",
        )
        for index, (source, device, event_type) in enumerate(
            [
                ("anpr", "ANPR-TF-1", "speed_violation"),
                ("radar", "RAD-TF-1", "speed_detection"),
                ("cctv", "CAM-TF-1", "vehicle_motion"),
            ]
        )
    ]


def _incident_ids(client, **params):
    items = client.get("/incidents", params={"page_size": 200, **params}).json()["items"]
    return {
        item["incident_id"]
        for item in items
        if any(event_id.startswith("EVT-TF-") for event_id in item["source_event_ids"])
    }


def test_time_filter_matches_occurrence_window(client, make_event):
    assert client.post("/ingest", json=_late_traffic_events(make_event)).status_code == 200
    everything = _incident_ids(client)
    assert everything

    during = _incident_ids(client, start="2026-03-12T09:30:00Z", end="2026-03-12T10:30:00Z")
    assert during == everything

    # The arrival window (when created_epoch falls) must NOT match.
    arrival = _incident_ids(client, start="2026-03-12T13:30:00Z", end="2026-03-12T15:00:00Z")
    assert arrival == set()


def test_time_filter_matches_partial_overlap(client, make_event):
    assert client.post("/ingest", json=_late_traffic_events(make_event)).status_code == 200
    everything = _incident_ids(client)
    # A window that only covers the tail of the incident still overlaps it.
    tail = _incident_ids(client, start="2026-03-12T10:03:00Z", end="2026-03-12T12:00:00Z")
    assert tail and tail <= everything
    # A window entirely before the first event excludes it.
    assert _incident_ids(client, end="2026-03-12T09:00:00Z") == set()
    # A window entirely after the last event excludes it.
    assert _incident_ids(client, start="2026-03-12T11:00:00Z") == set()
