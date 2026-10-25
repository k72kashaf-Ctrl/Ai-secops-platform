"""Grounded incident reasoning with a deterministic offline provider.

The provider boundary is intentionally small: a future local or hosted model
can implement the same contract, while the deterministic implementation keeps
the platform usable without a GPU, network connection, or paid API key.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Protocol

from app.database import Database
from app.utils import parse_json


class ReasoningProvider(Protocol):
    name: str

    def explain_incident(
        self,
        incident: dict[str, Any],
        events: list[dict[str, Any]],
        quality_issues: list[dict[str, Any]],
    ) -> dict[str, Any]:
        ...


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


class DeterministicReasoningProvider:
    """Produce structured, evidence-grounded reasoning from stored rows."""

    name = "deterministic"

    def explain_incident(
        self,
        incident: dict[str, Any],
        events: list[dict[str, Any]],
        quality_issues: list[dict[str, Any]],
    ) -> dict[str, Any]:
        event_ids = [event["event_id"] for event in events]
        facts = [
            f"Incident {incident['incident_id']} contains {len(events)} stored source event(s).",
            f"Category is {incident['category']} with severity {incident['severity']}.",
            f"Stored sources: {', '.join(sorted({event['source_type'] for event in events})) or 'none'}.",
            f"Stored devices: {', '.join(sorted({event['device_id'] for event in events})) or 'none'}.",
        ]
        if events:
            facts.append(
                f"Occurrence window is {events[0]['occurred_at']} to {events[-1]['occurred_at']}."
            )

        inferences = [incident["explanation"]]
        if incident.get("is_possible_association"):
            inferences.append(
                "The association is marked possible because the stored confidence or corroboration is limited."
            )

        uncertainty = list(parse_json(incident.get("contradictions"), []))
        # Contradictions are stored as "<issue_type>: <explanation>"; skip issues already listed.
        uncertainty.extend(
            issue["explanation"]
            for issue in quality_issues
            if issue.get("severity") in {"high", "critical"}
            and not any(str(item).endswith(issue["explanation"]) for item in uncertainty)
        )
        missing_information = list(parse_json(incident.get("missing_info"), []))
        actions = list(parse_json(incident.get("recommended_actions"), []))

        return {
            "answer": (
                f"{incident['title']} is {incident['status']} with "
                f"{incident['severity']} severity and confidence {float(incident['confidence']):.2f}."
            ),
            "facts": _unique(facts),
            "inferences": _unique(inferences),
            "uncertainty": _unique(uncertainty),
            "missing_information": _unique(missing_information),
            "recommended_actions": _unique(actions),
            "incident_ids": [incident["incident_id"]],
            "evidence_event_ids": _unique(event_ids),
            "quality_issue_ids": _unique([issue.get("issue_id") for issue in quality_issues]),
            "confidence": float(incident["confidence"]),
            "provider": self.name,
            "grounded": True,
        }


FALLBACK_PROVIDER_NAME = "deterministic (fallback)"
_NARRATIVE_LIST_FIELDS = (
    "facts",
    "inferences",
    "uncertainty",
    "missing_information",
    "recommended_actions",
)


def _post_json(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    """POST JSON with the stdlib so the optional provider adds no dependency."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - local URL
        return json.loads(response.read().decode("utf-8"))


class OllamaReasoningProvider:
    """Optional local-LLM narrative over the deterministic, stored evidence.

    The model only rewrites the narrative fields. Its output is validated
    (JSON shape, evidence IDs must be a non-empty subset of the incident's
    stored events); anything else returns the deterministic result marked as
    a fallback, so investigation never depends on the model being available.
    Confidence always comes from the stored incident, never from the model.
    """

    name = "ollama"

    def __init__(self, url: str, model: str, timeout: float = 20.0):
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.fallback = DeterministicReasoningProvider()

    @classmethod
    def from_env(cls) -> "OllamaReasoningProvider":
        return cls(
            url=os.getenv("ZENITH_OLLAMA_URL", "http://127.0.0.1:11434"),
            model=os.getenv("ZENITH_OLLAMA_MODEL", "llama3.2"),
            timeout=float(os.getenv("ZENITH_OLLAMA_TIMEOUT", "20")),
        )

    def _prompt(
        self,
        incident: dict[str, Any],
        events: list[dict[str, Any]],
        baseline: dict[str, Any],
    ) -> str:
        evidence = [
            {
                key: event.get(key)
                for key in ("event_id", "source_type", "device_id", "event_type", "occurred_at", "severity")
            }
            for event in events[:50]
        ]
        return (
            "You are a security operations analyst. Using ONLY the stored evidence below, "
            "return a JSON object with keys: answer (string), facts, inferences, uncertainty, "
            "missing_information, recommended_actions (arrays of strings) and evidence_event_ids "
            "(array of event_id values taken from the evidence). Separate facts from inferences. "
            "Do not invent events, devices or IDs.\n"
            f"Incident: {json.dumps({k: incident.get(k) for k in ('incident_id', 'title', 'category', 'severity', 'status', 'confidence', 'explanation')}, default=str)}\n"
            f"Evidence: {json.dumps(evidence, default=str)}\n"
            f"Deterministic analysis: {json.dumps({k: baseline[k] for k in _NARRATIVE_LIST_FIELDS})}"
        )

    def _validate(self, candidate: Any, valid_event_ids: set[str]) -> dict[str, Any] | None:
        if not isinstance(candidate, dict) or not isinstance(candidate.get("answer"), str):
            return None
        cleaned: dict[str, Any] = {"answer": candidate["answer"].strip()}
        if not cleaned["answer"]:
            return None
        for field in _NARRATIVE_LIST_FIELDS:
            values = candidate.get(field, [])
            if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
                return None
            cleaned[field] = _unique([item.strip() for item in values])
        evidence = candidate.get("evidence_event_ids")
        if (
            not isinstance(evidence, list)
            or not evidence
            or not all(isinstance(item, str) for item in evidence)
            or not set(evidence) <= valid_event_ids
        ):
            return None
        cleaned["evidence_event_ids"] = _unique(evidence)
        return cleaned

    def explain_incident(
        self,
        incident: dict[str, Any],
        events: list[dict[str, Any]],
        quality_issues: list[dict[str, Any]],
    ) -> dict[str, Any]:
        baseline = self.fallback.explain_incident(incident, events, quality_issues)
        try:
            raw = _post_json(
                f"{self.url}/api/generate",
                {
                    "model": self.model,
                    "prompt": self._prompt(incident, events, baseline),
                    "format": "json",
                    "stream": False,
                    "options": {"temperature": 0},
                },
                self.timeout,
            )
            candidate = json.loads(raw.get("response", ""))
            reason = "model output failed validation"
        except Exception as exc:  # network, timeout, bad JSON: never break investigation
            candidate, reason = None, f"model unavailable: {type(exc).__name__}"
        validated = (
            self._validate(candidate, {event["event_id"] for event in events})
            if candidate is not None
            else None
        )
        if validated is None:
            return {**baseline, "provider": FALLBACK_PROVIDER_NAME, "fallback_reason": reason}
        return {**baseline, **validated, "provider": f"ollama:{self.model}", "grounded": True}


def get_reasoning_provider(name: str = "deterministic") -> ReasoningProvider:
    """Return the configured provider.

    Unknown providers fail closed to the deterministic provider so an optional
    model configuration can never break core incident investigation.
    """
    if name.strip().lower() == "ollama":
        return OllamaReasoningProvider.from_env()
    return DeterministicReasoningProvider()


def generate_incident_reasoning(
    db: Database,
    incident_id: str,
    provider_name: str = "deterministic",
) -> dict[str, Any]:
    incident = db.fetchone("SELECT * FROM incidents WHERE incident_id = ?", (incident_id,))
    if not incident:
        return {}
    events = db.fetchall(
        """
        SELECT e.event_id, e.source_type, e.device_id, e.event_type,
               e.occurred_at, e.severity, e.confidence
        FROM incident_events ie
        JOIN events e ON e.event_id = ie.event_id
        WHERE ie.incident_id = ?
        ORDER BY e.occurred_epoch ASC, e.event_id ASC
        """,
        (incident_id,),
    )
    issues = db.fetchall(
        """
        SELECT DISTINCT q.issue_id, q.issue_type, q.severity, q.explanation
        FROM quality_issues q
        JOIN incident_events ie ON ie.event_id = q.event_id
        WHERE ie.incident_id = ?
        ORDER BY q.issue_id
        """,
        (incident_id,),
    )
    result = get_reasoning_provider(provider_name).explain_incident(incident, events, issues)
    valid_event_ids = {event["event_id"] for event in events}
    result["evidence_event_ids"] = [
        event_id for event_id in result["evidence_event_ids"] if event_id in valid_event_ids
    ]
    result["incident_ids"] = [incident_id]
    return result
