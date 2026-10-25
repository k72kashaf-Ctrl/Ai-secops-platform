"""GET /situation: operations-center snapshot (event time, SQL aggregates)."""
from __future__ import annotations

KPI_KEYS = {
    "valid_events", "received", "rejected", "duplicates", "open_incidents", "by_severity",
    "quality_issues", "conflicts", "uncorroborated", "spikes", "unhealthy_devices",
    "window_minutes", "incidents_last", "incidents_prev", "crit_high_last", "crit_high_prev",
    "events_last", "events_prev", "incidents_series", "crit_high_series", "events_series",
}
TOP_KEYS = {
    "latest_occurred_at", "kpis", "zones", "activity", "quality_trend", "sensors", "priority",
    "watchlist_count", "feed", "brief", "routes", "campaign",
}


def _seed(client):
    assert client.post("/admin/seed-sample", params={"reset": "true"}).status_code == 200
    response = client.get("/situation")
    assert response.status_code == 200
    return response.json()


def test_situation_empty_database(client):
    response = client.get("/situation")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == TOP_KEYS
    assert set(body["kpis"]) == KPI_KEYS
    kpis = body["kpis"]
    assert body["latest_occurred_at"] is None
    assert kpis["valid_events"] == 0 and kpis["open_incidents"] == 0
    assert kpis["by_severity"] == {"critical": 0, "high": 0, "medium": 0, "low": 0}
    assert kpis["incidents_series"] == [0] * 8
    assert kpis["crit_high_series"] == [0] * 8
    assert kpis["events_series"] == [0] * 16
    assert body["zones"] == [] and body["priority"] == [] and body["feed"] == []
    assert body["activity"]["buckets"] == []
    assert body["activity"]["bucket_minutes"] == 15
    assert [s["group"] for s in body["sensors"]] == [
        "Cameras & ANPR", "Access control", "Perimeter & radar", "Alarms & reports", "Telemetry",
    ]
    assert body["watchlist_count"] == 0
    assert body["brief"]["headline"] == "No open incidents. 0 events analysed."
    assert body["brief"]["chips"] == []
    assert body["routes"] == [] and body["campaign"] is None


def test_situation_sample_kpis(client):
    body = _seed(client)
    kpis = body["kpis"]
    assert set(kpis) == KPI_KEYS
    assert kpis["valid_events"] == 73
    assert kpis["received"] == kpis["valid_events"] + kpis["duplicates"] + kpis["rejected"]
    assert kpis["open_incidents"] == 44
    assert kpis["by_severity"]["critical"] == 2
    assert sum(kpis["by_severity"].values()) == kpis["open_incidents"]
    assert body["watchlist_count"] == 36
    assert body["latest_occurred_at"].startswith("2026-03-12T10:24")
    assert len(kpis["incidents_series"]) == 8 and len(kpis["events_series"]) == 16
    assert kpis["incidents_last"] == kpis["incidents_series"][-1]
    assert kpis["events_last"] <= kpis["valid_events"]
    assert kpis["conflicts"] == 6


def test_situation_sample_zones_priority_feed(client):
    body = _seed(client)
    assert body["priority"]
    assert body["priority"][0]["severity"] == "critical"
    assert all(item["status"] in {"open", "acknowledged", "investigating"} for item in body["priority"])
    assert len(body["priority"]) <= 6

    zones = {zone["zone"]: zone for zone in body["zones"]}
    assert zones["Perimeter North"]["worst_severity"] == "critical"
    events = [zone["events"] for zone in body["zones"]]
    assert events == sorted(events, reverse=True)

    assert 0 < len(body["feed"]) <= 10
    for item in body["feed"]:
        assert item["at"] and item["text"] and item["ref"]
        assert item["kind"] in {"linked", "conflict", "spike", "device", "delay", "offline"}
    stamps = [item["at"] for item in body["feed"]]
    assert stamps == sorted(stamps, reverse=True)

    assert body["brief"]["headline"]
    labels = [chip["label"] for chip in body["brief"]["chips"]]
    assert "2 critical" in labels


def test_situation_sample_activity_and_sensors(client):
    body = _seed(client)
    buckets = body["activity"]["buckets"]
    assert 0 < len(buckets) <= 96
    total = sum(sum(bucket["counts"].values()) for bucket in buckets)
    assert total == body["kpis"]["valid_events"]

    sensors = {sensor["group"]: sensor for sensor in body["sensors"]}
    assert sensors["Telemetry"]["flagged"] >= 1
    assert len(sensors["Telemetry"]["notes"]) <= 3
    assert sum(sensor["devices"] for sensor in body["sensors"]) > 0


def test_situation_respects_status_override(client):
    body = _seed(client)
    target = body["priority"][0]["incident_id"]
    response = client.patch(f"/incidents/{target}", json={"status": "resolved"})
    assert response.status_code == 200, response.text
    after = client.get("/situation").json()
    assert after["kpis"]["open_incidents"] == body["kpis"]["open_incidents"] - 1
    assert target not in {item["incident_id"] for item in after["priority"]}


def test_situation_quality_trend_accounts_for_every_finding(client):
    body = _seed(client)
    trend = body["quality_trend"]
    assert trend["bucket_minutes"] == 15
    assert len(trend["buckets"]) == len(body["activity"]["buckets"])
    timed = sum(sum(b["counts"].values()) for b in trend["buckets"])
    # Every finding is either placed on its event's time bucket or counted as untimed.
    assert timed + trend["untimed"] == body["kpis"]["quality_issues"]
    assert "Contradictions" in trend["families"] and "Devices" in trend["families"]


ROUTE_KEYS = {"kind", "from_zone", "to_zone", "severity", "label", "refs", "at"}


def test_situation_sample_routes(client):
    body = _seed(client)
    routes = body["routes"]
    assert routes and all(set(route) == ROUTE_KEYS for route in routes)
    assert all(route["from_zone"] != route["to_zone"] for route in routes)
    stamps = [route["at"] for route in routes]
    assert stamps == sorted(stamps, reverse=True)

    patterns = [r for r in routes if r["kind"] == "pattern"]
    assert all("possible" in r["label"].lower() and len(r["refs"]) == 2 for r in patterns)
    traffic = {(r["from_zone"], r["to_zone"]) for r in patterns if "traffic" in r["label"]}
    assert traffic == {("South Lot", "West Road"), ("West Road", "South Lot"), ("South Lot", "North Gate")}
    first = next(r for r in patterns if (r["from_zone"], r["to_zone"]) == ("South Lot", "West Road"))
    assert first["label"] == "Possible traffic pattern · 8 min apart"
    intrusion = [r for r in patterns if "intrusion" in r["label"]]
    assert len(intrusion) == 1
    assert (intrusion[0]["from_zone"], intrusion[0]["to_zone"]) == ("Perimeter North", "Data Center")
    assert intrusion[0]["label"].endswith("44 min apart") and intrusion[0]["severity"] == "critical"

    conflicts = [r for r in routes if r["kind"] == "location_conflict"]
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert (conflict["from_zone"], conflict["to_zone"]) == ("Data Center", "Remote Yard")
    assert conflict["label"] == "Location jump · CAM-DC-01"
    assert conflict["severity"] == "high" and conflict["refs"][0].startswith("QI-")
    assert conflict["at"].startswith("2026-03-12T09:51")

    campaign = body["campaign"]
    assert campaign["category"] == "traffic"
    assert campaign["label"] == "Possible traffic pattern"
    assert set(campaign["zones"]) == {"South Lot", "West Road", "North Gate"}
    assert len(campaign["incidents"]) == 4
    assert campaign["severity"] == "high"
    assert campaign["events"] > 0


def test_situation_routes_ignore_resolved_incidents(client):
    body = _seed(client)
    intrusion = next(r for r in body["routes"] if "intrusion" in r["label"])
    response = client.patch(f"/incidents/{intrusion['refs'][1]}", json={"status": "resolved"})
    assert response.status_code == 200, response.text
    after = client.get("/situation").json()
    assert not [r for r in after["routes"] if "intrusion" in r["label"]]


def test_situation_entity_routes(client, ingest, make_event):
    records = [
        make_event(event_id="EVT-ENT-1", device_id="ANPR-A", source_type="anpr",
                   event_type="plate_read", zone="North Gate", severity="low",
                   occurred_at="2026-03-12T10:00:00Z", received_at="2026-03-12T10:00:02Z",
                   payload={"plate": "ABC123"}),
        make_event(event_id="EVT-ENT-2", device_id="ANPR-B", source_type="anpr",
                   event_type="plate_read", zone="South Lot", severity="high",
                   latitude=51.52, longitude=-0.10,
                   occurred_at="2026-03-12T10:05:00Z", received_at="2026-03-12T10:05:02Z",
                   payload={"plate": "ABC123"}),
        make_event(event_id="EVT-ENT-3", device_id="ANPR-C", source_type="anpr",
                   event_type="plate_read", zone="South Lot", severity="low",
                   latitude=51.52, longitude=-0.10,
                   occurred_at="2026-03-12T10:07:00Z", received_at="2026-03-12T10:07:02Z",
                   payload={"plate": "ABC123"}),
        # Placeholder identities never link zones.
        make_event(event_id="EVT-ENT-4", device_id="FACE-A", source_type="face_recognition",
                   event_type="face_match", zone="North Gate",
                   occurred_at="2026-03-12T10:01:00Z", received_at="2026-03-12T10:01:02Z",
                   payload={"subject": "unknown"}),
        make_event(event_id="EVT-ENT-5", device_id="FACE-B", source_type="face_recognition",
                   event_type="face_match", zone="South Lot",
                   latitude=51.52, longitude=-0.10,
                   occurred_at="2026-03-12T10:02:00Z", received_at="2026-03-12T10:02:02Z",
                   payload={"subject": "unknown"}),
    ]
    assert ingest(records).status_code in (200, 201, 202)
    body = client.get("/situation").json()
    entity = [r for r in body["routes"] if r["kind"] == "entity"]
    assert entity == [
        {
            "kind": "entity",
            "from_zone": "North Gate",
            "to_zone": "South Lot",
            "severity": "high",
            "label": "Same plate ABC123 seen in 2 zones",
            "refs": ["EVT-ENT-1", "EVT-ENT-2"],
            "at": entity[0]["at"],
        }
    ]
    assert entity[0]["at"].startswith("2026-03-12T10:05")
