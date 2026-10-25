"""Assistant safety, follow-up context, structure and status workflow (G8)."""
from __future__ import annotations

import pytest

from app.services.assistant import parse_command


STRUCTURED_FIELDS = ("facts", "inferences", "missing_information", "conflicts", "recommended_actions")


def _seed_incident(client, make_event, **overrides) -> str:
    assert client.post("/ingest", json=make_event(**overrides)).status_code == 200
    return client.get("/incidents").json()["items"][0]["incident_id"]


@pytest.mark.parametrize(
    "query",
    [
        "show open incidents",
        "what is open in Gate A",
        "list unresolved incidents near North Gate",
        "which incidents are resolved?",
        "is INC-ABC open?",
        "summarise this incident",
    ],
)
def test_read_only_questions_are_not_commands(query):
    assert parse_command(query) is None


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Resolve INC-1", {"action": "update_incident_status", "status": "resolved"}),
        ("please acknowledge INC-1", {"action": "update_incident_status", "status": "acknowledged"}),
        ("mark INC-1 as false positive", {"action": "update_incident_status", "status": "false_positive"}),
        ("set INC-1 to under investigation", {"action": "update_incident_status", "status": "investigating"}),
        ("close INC-1", {"action": "update_incident_status", "status": "closed"}),
        ("reopen it", {"action": "update_incident_status", "status": "open"}),
        ("assign INC-1 to Sam Lee", {"action": "assign_incident", "assigned_to": "Sam Lee"}),
    ],
)
def test_commands_are_parsed(query, expected):
    assert parse_command(query) == expected


def test_show_open_incidents_requires_no_confirmation(client, make_event):
    _seed_incident(client, make_event, event_id="EVT-RO-1")
    body = client.post("/assistant/query", json={"query": "show open incidents"}).json()
    assert body["requires_confirmation"] is False
    assert body["pending_action"] is None
    assert body["incident_ids"]
    for field in STRUCTURED_FIELDS:
        assert isinstance(body[field], list)
    assert body["facts"]


def test_zone_matching_is_normalised(client, make_event):
    incident_id = _seed_incident(client, make_event, event_id="EVT-ZONE-1", zone="Gate-A")
    for query in ("what is open in gate a", "incidents near GATE_A", "open incidents at GateA"):
        body = client.post("/assistant/query", json={"query": query}).json()
        assert body["incident_ids"] == [incident_id], query
        assert body["requires_confirmation"] is False


def test_follow_up_context_resolves_this_incident(client, make_event):
    incident_id = _seed_incident(client, make_event, event_id="EVT-CTX-1", severity="high")
    first = client.post(
        "/assistant/query", json={"query": f"show {incident_id}", "session_id": "S1"}
    ).json()
    assert first["incident_ids"] == [incident_id]

    follow_up = client.post(
        "/assistant/query", json={"query": "summarise this incident", "session_id": "S1"}
    ).json()
    assert follow_up["used_session_context"] is True
    assert follow_up["incident_ids"] == [incident_id]
    assert follow_up["intent"] == "incident_summary"
    assert follow_up["evidence_event_ids"] == ["EVT-CTX-1"]
    assert follow_up["facts"] and follow_up["inferences"]

    # A different session has no context.
    other = client.post(
        "/assistant/query", json={"query": "summarise this incident", "session_id": "S2"}
    ).json()
    assert other["used_session_context"] is False

    command = client.post(
        "/assistant/query", json={"query": "acknowledge it", "session_id": "S1"}
    ).json()
    assert command["pending_action"]["incident_id"] == incident_id
    assert command["pending_action"]["status"] == "acknowledged"


def test_confidence_is_derived_from_incidents(client, make_event):
    incident_id = _seed_incident(client, make_event, event_id="EVT-CONF-1", confidence=0.9)
    stored = client.get(f"/incidents/{incident_id}").json()["confidence"]
    body = client.post("/assistant/query", json={"query": f"explain {incident_id}"}).json()
    assert body["confidence"] == pytest.approx(stored, abs=1e-3)

    empty = client.post("/assistant/query", json={"query": "show critical incidents near Nowhere Land"}).json()
    if not empty["incident_ids"]:
        assert empty["confidence"] == 0.0


def test_second_confirm_is_rejected(client, make_event):
    incident_id = _seed_incident(client, make_event, event_id="EVT-REPLAY-1")
    body = client.post("/assistant/query", json={"query": f"mark {incident_id} as false positive"}).json()
    assert body["requires_confirmation"] is True
    request_id = body["request_id"]

    first = client.post("/assistant/confirm", json={"request_id": request_id, "confirmed": True})
    assert first.status_code == 200
    assert first.json()["status"] == "confirmed"
    assert client.get(f"/incidents/{incident_id}").json()["status"] == "false_positive"

    client.patch(f"/incidents/{incident_id}", json={"status": "open"})
    replay = client.post("/assistant/confirm", json={"request_id": request_id, "confirmed": True})
    assert replay.status_code == 409
    assert client.get(f"/incidents/{incident_id}").json()["status"] == "open"


def test_cancelled_request_cannot_be_confirmed_later(client, make_event):
    incident_id = _seed_incident(client, make_event, event_id="EVT-CANCEL-1")
    body = client.post("/assistant/query", json={"query": f"close {incident_id}"}).json()
    assert client.post(
        "/assistant/confirm", json={"request_id": body["request_id"], "confirmed": False}
    ).json()["status"] == "cancelled"
    late = client.post("/assistant/confirm", json={"request_id": body["request_id"], "confirmed": True})
    assert late.status_code == 409
    assert client.get(f"/incidents/{incident_id}").json()["status"] == "open"


def test_assign_via_assistant_and_patch(client, make_event):
    incident_id = _seed_incident(client, make_event, event_id="EVT-ASSIGN-1")
    body = client.post("/assistant/query", json={"query": f"assign {incident_id} to Sam Lee"}).json()
    assert body["pending_action"]["action"] == "assign_incident"
    confirmed = client.post("/assistant/confirm", json={"request_id": body["request_id"]}).json()
    assert confirmed["result"]["assigned_to"] == "Sam Lee"
    detail = client.get(f"/incidents/{incident_id}").json()
    assert detail["assigned_to"] == "Sam Lee"
    assert detail["status"] == "open"

    patched = client.patch(f"/incidents/{incident_id}", json={"status": "acknowledged"})
    assert patched.status_code == 200
    assert patched.json()["assigned_to"] == "Sam Lee"
    assert client.patch(f"/incidents/{incident_id}", json={}).status_code == 422


def test_new_statuses_and_briefing_counts(client, make_event):
    incident_id = _seed_incident(client, make_event, event_id="EVT-STATUS-1")
    facets = client.get("/incidents/facets").json()
    assert facets["statuses"] == [
        "open", "acknowledged", "investigating", "false_positive", "resolved", "closed",
    ]
    for status in ("acknowledged", "false_positive", "closed"):
        assert client.patch(f"/incidents/{incident_id}", json={"status": status}).status_code == 200
        assert client.get("/incidents", params={"status": status}).json()["total"] == 1

    briefing = client.get("/briefing").json()
    summary = briefing["sections"]["summary"]
    assert summary["unresolved_incidents"] == 0
    assert summary["resolved_incidents"] == 1  # closed counts as resolved

    assert client.patch(f"/incidents/{incident_id}", json={"status": "false_positive"}).status_code == 200
    summary = client.get("/briefing").json()["sections"]["summary"]
    assert summary["unresolved_incidents"] == 0
    assert summary["false_positive_incidents"] == 1

    # Status and assignee survive a full recompute.
    client.patch(f"/incidents/{incident_id}", json={"status": "acknowledged", "assigned_to": "Ops"})
    assert client.post("/admin/recompute").status_code == 200
    detail = client.get(f"/incidents/{incident_id}").json()
    assert detail["status"] == "acknowledged"
    assert detail["assigned_to"] == "Ops"
