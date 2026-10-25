"""Evidence-backed operational briefing tests."""
from __future__ import annotations


def test_empty_database_briefing_is_honest(client, db):
    response = client.get("/briefing")
    assert response.status_code == 200
    briefing = response.json()
    assert briefing["stats"]["valid_events"] == 0
    assert briefing["stats"]["incidents"] == 0
    assert briefing["sections"]["highest_priority_incidents"] == []
    assert briefing["sections"]["unresolved_incidents"] == []
    assert "No events have been ingested yet" in briefing["markdown"]
    assert "No incidents are stored" in briefing["markdown"]
    assert "INC-" not in briefing["evidence"].get("incident_ids", [])
    assert db.stats()["valid_events"] == 0


def test_seeded_briefing_matches_database_and_cites_evidence(client, db):
    seed = client.post("/admin/seed-sample", params={"reset": "true"})
    assert seed.status_code == 200, seed.text
    seed_body = seed.json()
    assert seed_body["ingestion"]["accepted"] > 30
    assert seed_body["ingestion"]["rejected"] >= 1
    assert seed_body["incidents"] > 0
    assert seed_body["quality_issues"] > 0

    response = client.get("/briefing")
    assert response.status_code == 200
    briefing = response.json()
    stats = db.stats()
    assert briefing["stats"] == stats
    assert briefing["sections"]["summary"]["unresolved_incidents"] == stats["open_incidents"]
    assert briefing["sections"]["highest_priority_incidents"]
    assert briefing["sections"]["quality_by_type"]
    assert briefing["sections"]["device_health"]
    assert briefing["sections"]["event_spikes"]
    assert briefing["sections"]["repeated_events"]

    incident_ids = briefing["evidence"]["incident_ids"]
    assert incident_ids
    markdown = briefing["markdown"]
    for incident_id in incident_ids:
        assert incident_id in markdown
    top = briefing["sections"]["highest_priority_incidents"][0]
    assert any(event_id in markdown for event_id in top["event_ids"])
    quality_ids = briefing["evidence"]["quality_issue_ids"]
    assert quality_ids
    for issue_id in quality_ids[:5]:
        assert issue_id in markdown
    assert "No incidents, statistics or sources were invented" in markdown
    assert "deterministic template" in markdown
    assert "Confidence values are deterministic" in markdown


def test_briefing_reflects_resolved_status(client):
    assert client.post("/admin/seed-sample", params={"reset": "true"}).status_code == 200
    first = client.get("/briefing").json()
    unresolved_before = first["sections"]["summary"]["unresolved_incidents"]
    incident = first["sections"]["highest_priority_incidents"][0]
    update = client.patch(f"/incidents/{incident['incident_id']}", json={"status": "resolved"})
    assert update.status_code == 200
    second = client.get("/briefing").json()
    assert second["sections"]["summary"]["unresolved_incidents"] == unresolved_before - 1
    assert second["stats"]["incidents"] == first["stats"]["incidents"]
    resolved_ids = {
        item["incident_id"] for item in second["sections"]["unresolved_incidents"]
    }
    assert incident["incident_id"] not in resolved_ids


def test_briefing_markdown_format(client):
    assert client.post("/admin/seed-sample", params={"reset": "true"}).status_code == 200
    response = client.get("/briefing", params={"format": "markdown"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    text = response.text
    assert "# Zenith SecOps" in text
    assert "## Executive summary" in text
    assert "## Recommended management actions" in text
    assert "## Confidence and uncertainty" in text


def test_briefing_recommended_actions_cite_sources(client):
    assert client.post("/admin/seed-sample", params={"reset": "true"}).status_code == 200
    briefing = client.get("/briefing").json()
    actions = briefing["sections"]["recommended_management_actions"]
    assert actions
    assert any("incident_id" in action or "issue_id" in action for action in actions)
    for action in actions:
        assert action["action"]
        assert action["reason"]
