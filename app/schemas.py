"""Pydantic input models and validation helpers."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Severity = Literal["low", "medium", "high", "critical"]


def _coerce_datetime(value: Any, field_name: str) -> datetime | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if field_name == "occurred_at":
            raise ValueError("occurred_at is required and must be an ISO-8601 timestamp")
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, (int, float)):
        # Accept epoch seconds or milliseconds.
        numeric = float(value)
        if abs(numeric) > 10_000_000_000:
            numeric /= 1000.0
        result = datetime.fromtimestamp(numeric, tz=timezone.utc)
    elif isinstance(value, str):
        text = value.strip()
        try:
            result = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                f"{field_name} must be an ISO-8601 timestamp (received {text!r})"
            ) from exc
    else:
        raise ValueError(f"{field_name} has an unsupported timestamp type")
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _coerce_payload(value: Any) -> dict[str, Any]:
    if value is None or (isinstance(value, float) and value != value):  # NaN from CSV
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except ValueError:
            return {"raw": text}
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}
    return {"value": value}


class EventIn(BaseModel):
    """Validated, normalised event accepted by POST /ingest.

    Only event_id, source_type, device_id, event_type and occurred_at are
    mandatory. Optional fields are tolerated when absent. Extra source fields
    are preserved in the raw evidence copy.
    """

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    event_id: str = Field(min_length=1, max_length=200)
    source_type: str = Field(min_length=1, max_length=64)
    device_id: str = Field(min_length=1, max_length=128)
    event_type: str = Field(min_length=1, max_length=128)
    occurred_at: datetime
    received_at: datetime | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    zone: str | None = Field(default=None, max_length=128)
    severity: Severity = "medium"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("occurred_at", "received_at", mode="before")
    @classmethod
    def validate_datetime(cls, value: Any, info) -> datetime | None:
        return _coerce_datetime(value, info.field_name)

    @field_validator("latitude", "longitude", mode="before")
    @classmethod
    def validate_coordinate(cls, value: Any) -> float | None:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        if isinstance(value, float) and value != value:
            return None
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("coordinate must be numeric") from exc

    # severity, confidence and received_at are accepted when absent but then
    # carry an assumed value; quality analysis raises `incomplete_record` for them.
    @staticmethod
    def defaulted_fields(raw: dict[str, Any]) -> list[str]:
        """Names of defaultable fields that are missing, null or blank in ``raw``."""
        missing = []
        for name in ("severity", "confidence", "received_at"):
            value = raw.get(name)
            if value is None or (isinstance(value, str) and not value.strip()) or (
                isinstance(value, float) and value != value
            ):
                missing.append(name)
        return missing

    @field_validator("confidence", mode="before")
    @classmethod
    def validate_confidence(cls, value: Any) -> float:
        if value is None or (isinstance(value, str) and not value.strip()):
            return 0.5
        if isinstance(value, float) and value != value:
            return 0.5
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("confidence must be a number between 0 and 1") from exc

    @field_validator("severity", mode="before")
    @classmethod
    def validate_severity(cls, value: Any) -> str:
        if value is None or (isinstance(value, str) and not value.strip()):
            return "medium"
        text = str(value).strip().lower()
        if text not in ("low", "medium", "high", "critical"):
            raise ValueError("severity must be one of low, medium, high, critical")
        return text

    @field_validator("source_type", "event_type", mode="before")
    @classmethod
    def validate_token(cls, value: Any) -> str:
        if value is None:
            raise ValueError("field is required")
        text = str(value).strip().lower().replace(" ", "_").replace("-", "_")
        if not text:
            raise ValueError("field must not be empty")
        return text

    @field_validator("zone", mode="before")
    @classmethod
    def validate_zone(cls, value: Any) -> str | None:
        if value is None or (isinstance(value, float) and value != value):
            return None
        text = str(value).strip()
        return text or None

    @field_validator("payload", mode="before")
    @classmethod
    def validate_payload(cls, value: Any) -> dict[str, Any]:
        return _coerce_payload(value)

    @field_validator("event_id", "device_id", mode="before")
    @classmethod
    def validate_identifier(cls, value: Any) -> str:
        if value is None:
            raise ValueError("field is required")
        if isinstance(value, float) and value != value:
            raise ValueError("field is required")
        text = str(value).strip()
        if not text:
            raise ValueError("field must not be empty")
        return text

    def normalised_payload(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            include={
                "event_id",
                "source_type",
                "device_id",
                "event_type",
                "occurred_at",
                "received_at",
                "latitude",
                "longitude",
                "zone",
                "severity",
                "confidence",
                "payload",
            },
        )


IncidentStatus = Literal[
    "open", "acknowledged", "investigating", "false_positive", "resolved", "closed"
]


class IncidentStatusUpdate(BaseModel):
    status: IncidentStatus | None = None
    assigned_to: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def _require_change(self) -> "IncidentStatusUpdate":
        if self.status is None and self.assigned_to is None:
            raise ValueError("provide status and/or assigned_to")
        return self


class AssistantQuery(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, min_length=1, max_length=100)


class AssistantConfirm(BaseModel):
    request_id: str = Field(min_length=1, max_length=100)
    confirmed: bool = True


class IngestEnvelope(BaseModel):
    """Optional wrapper for batch submissions: {"events": [...]}."""

    event: EventIn | None = None
    events: list[dict[str, Any]] | None = None
