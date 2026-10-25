"""Domain data structures shared by services.

SQLite persistence is handled with explicit SQL in database.py; these
dataclasses keep service boundaries readable and serialisable.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class IngestSummary:
    batch_id: str
    total: int = 0
    accepted: int = 0
    rejected: int = 0
    duplicate_ids: int = 0
    duplicate_content: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    status: str = "completed"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class QualityIssueDraft:
    issue_id: str
    issue_type: str
    severity: str
    explanation: str
    event_id: str | None
    device_id: str | None
    detected_at: str
    suggested_action: str
    details: dict[str, Any] = field(default_factory=dict)
    status: str = "open"

    def to_row(self) -> tuple:
        import json

        return (
            self.issue_id,
            self.issue_type,
            self.severity,
            self.explanation,
            self.event_id,
            self.device_id,
            self.detected_at,
            self.suggested_action,
            json.dumps(self.details, sort_keys=True, default=str),
            self.status,
        )


@dataclass
class IncidentDraft:
    incident_id: str
    title: str
    category: str
    severity: str
    confidence: float
    status: str
    created_at: str
    updated_at: str
    created_epoch: int
    updated_epoch: int
    event_ids: list[str]
    devices: list[str]
    zones: list[str]
    sources: list[str]
    explanation: str
    missing_info: list[str]
    contradictions: list[str]
    recommended_actions: list[str]
    is_possible_association: bool = False

    def to_row(self) -> tuple:
        import json

        return (
            self.incident_id,
            self.title,
            self.category,
            self.severity,
            self.confidence,
            self.status,
            self.created_at,
            self.updated_at,
            self.created_epoch,
            self.updated_epoch,
            json.dumps(self.event_ids),
            json.dumps(self.devices),
            json.dumps(self.zones),
            json.dumps(self.sources),
            self.explanation,
            json.dumps(self.missing_info),
            json.dumps(self.contradictions),
            json.dumps(self.recommended_actions),
            len(self.event_ids),
            int(self.is_possible_association),
        )
