"""Optional Ollama reasoning provider: validated output, deterministic fallback (G11).

No network is used: ``_post_json`` is monkeypatched in every test.
"""
from __future__ import annotations

import json
import socket

import pytest

from app.services import reasoning
from app.services.reasoning import (
    FALLBACK_PROVIDER_NAME,
    DeterministicReasoningProvider,
    OllamaReasoningProvider,
    get_reasoning_provider,
)


@pytest.fixture
def incident_id(client, make_event):
    assert client.post(
        "/ingest",
        json=[
            make_event(event_id="EVT-LLM-1"),
            make_event(
                event_id="EVT-LLM-2",
                device_id="CAM-TEST-02",
                occurred_at="2026-03-12T10:02:00Z",
                received_at="2026-03-12T10:02:05Z",
            ),
        ],
    ).status_code == 200
    return client.get("/incidents").json()["items"][0]["incident_id"]


@pytest.fixture
def ollama_app(settings):
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(settings=settings.derive(reasoning_provider="ollama"))
    with TestClient(app) as test_client:
        yield test_client


def _model_reply(payload) -> dict:
    return {"response": json.dumps(payload) if not isinstance(payload, str) else payload}


def _valid_payload(evidence):
    return {
        "answer": "Two cameras observed motion at North Gate within two minutes.",
        "facts": ["CAM-TEST-01 and CAM-TEST-02 reported motion."],
        "inferences": ["Likely the same person moving past the gate."],
        "uncertainty": [],
        "missing_information": ["No access-control record for the window."],
        "recommended_actions": ["Review footage from both cameras."],
        "evidence_event_ids": evidence,
    }


def test_default_provider_is_deterministic(monkeypatch):
    monkeypatch.delenv("ZENITH_REASONING_PROVIDER", raising=False)
    assert isinstance(get_reasoning_provider("deterministic"), DeterministicReasoningProvider)
    assert isinstance(get_reasoning_provider("something-unknown"), DeterministicReasoningProvider)


def test_ollama_provider_reads_env(monkeypatch):
    monkeypatch.setenv("ZENITH_OLLAMA_URL", "http://ollama.local:1234/")
    monkeypatch.setenv("ZENITH_OLLAMA_MODEL", "qwen2.5")
    provider = get_reasoning_provider("ollama")
    assert isinstance(provider, OllamaReasoningProvider)
    assert provider.url == "http://ollama.local:1234"
    assert provider.model == "qwen2.5"


def test_valid_model_output_is_used(ollama_app, monkeypatch):
    client = ollama_app
    assert client.post(
        "/ingest",
        json={
            "event_id": "EVT-LLM-OK", "source_type": "cctv", "device_id": "CAM-1",
            "event_type": "motion_detected", "occurred_at": "2026-03-12T10:00:00Z",
            "received_at": "2026-03-12T10:00:05Z", "zone": "North Gate",
            "severity": "medium", "confidence": 0.8, "payload": {},
        },
    ).status_code == 200
    incident_id = client.get("/incidents").json()["items"][0]["incident_id"]
    calls = []

    def fake_post(url, payload, timeout):
        calls.append((url, payload))
        return _model_reply(_valid_payload(["EVT-LLM-OK"]))

    monkeypatch.setattr(reasoning, "_post_json", fake_post)
    body = client.get(f"/incidents/{incident_id}/reasoning").json()
    assert calls and calls[0][0].endswith("/api/generate")
    assert calls[0][1]["format"] == "json"
    assert body["provider"].startswith("ollama:")
    assert body["answer"].startswith("Two cameras")
    assert body["evidence_event_ids"] == ["EVT-LLM-OK"]
    assert body["grounded"] is True
    stored = client.get(f"/incidents/{incident_id}").json()["confidence"]
    assert body["confidence"] == pytest.approx(stored)  # never taken from the model


def _reason(db, incident_id):
    return reasoning.generate_incident_reasoning(db, incident_id, "ollama")


def test_invented_evidence_falls_back(db, incident_id, monkeypatch):
    monkeypatch.setattr(
        reasoning, "_post_json",
        lambda url, payload, timeout: _model_reply(_valid_payload(["EVT-LLM-1", "EVT-MADE-UP"])),
    )
    body = _reason(db, incident_id)
    assert body["provider"] == FALLBACK_PROVIDER_NAME
    assert "validation" in body["fallback_reason"]
    assert set(body["evidence_event_ids"]) <= {"EVT-LLM-1", "EVT-LLM-2"}
    assert body["facts"]


@pytest.mark.parametrize(
    "reply",
    [
        "not json at all",
        {"answer": "x", "facts": "should be a list", "evidence_event_ids": ["EVT-LLM-1"]},
        {"answer": "", "evidence_event_ids": ["EVT-LLM-1"]},
        {"answer": "no evidence", "evidence_event_ids": []},
    ],
)
def test_malformed_output_falls_back(db, incident_id, monkeypatch, reply):
    monkeypatch.setattr(reasoning, "_post_json", lambda url, payload, timeout: _model_reply(reply))
    assert _reason(db, incident_id)["provider"] == FALLBACK_PROVIDER_NAME


@pytest.mark.parametrize("error", [socket.timeout("timed out"), ConnectionRefusedError("refused")])
def test_unavailable_model_falls_back(db, incident_id, monkeypatch, error):
    def boom(url, payload, timeout):
        raise error

    monkeypatch.setattr(reasoning, "_post_json", boom)
    body = _reason(db, incident_id)
    assert body["provider"] == FALLBACK_PROVIDER_NAME
    assert "unavailable" in body["fallback_reason"]
    assert body["grounded"] is True
    assert body["evidence_event_ids"]
