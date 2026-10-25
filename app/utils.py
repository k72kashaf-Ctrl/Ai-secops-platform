"""Shared deterministic helpers used across services."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Iterable

SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3, "critical": 4}
VALID_SEVERITIES = tuple(SEVERITY_ORDER)
VALID_STATUSES = (
    "open",
    "acknowledged",
    "investigating",
    "false_positive",
    "resolved",
    "closed",
)
# Statuses that still need operator attention (counted as "unresolved").
ACTIVE_STATUSES = ("open", "acknowledged", "investigating")
ACTIVE_STATUS_SQL = ",".join(f"'{status}'" for status in ACTIVE_STATUSES)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(value: datetime) -> str:
    """Normalise a datetime to a lexicographically sortable UTC string."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc)
    return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def from_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def iso_to_epoch(value: str) -> int:
    return int(from_iso(value).timestamp())


def now_iso() -> str:
    return to_iso(utc_now())


def severity_rank(value: str | None) -> int:
    return SEVERITY_ORDER.get((value or "low").lower(), 0)


def max_severity(values: Iterable[str]) -> str:
    ranked = sorted(values, key=severity_rank)
    return ranked[-1] if ranked else "low"


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def parse_json(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def sha1_short(text: str, length: int = 12) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:length].upper()


def event_content_hash(
    source_type: str,
    device_id: str,
    event_type: str,
    occurred_at: str,
    latitude: float | None,
    longitude: float | None,
    zone: str | None,
    payload: dict[str, Any],
) -> str:
    """Hash the evidentiary content of an event (excluding transport metadata).

    Two records with the same source, device, type, occurrence time, location
    and payload are treated as duplicate content even when their event IDs or
    arrival times differ.
    """
    fingerprint = canonical_json(
        {
            "source_type": source_type,
            "device_id": device_id,
            "event_type": event_type,
            "occurred_at": occurred_at,
            "latitude": round(latitude, 6) if latitude is not None else None,
            "longitude": round(longitude, 6) if longitude is not None else None,
            "zone": zone,
            "payload": payload,
        }
    )
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    radius = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def geocell(lat: float | None, lon: float | None, size: float = 0.01) -> str | None:
    if lat is None or lon is None:
        return None
    return f"{math.floor(lat / size):+d}:{math.floor(lon / size):+d}"


def normalize_zone(value: Any) -> str:
    """Comparable zone key: case-insensitive, ignoring spaces, '-' and '_'."""
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def flow_key(event: dict[str, Any], geo_cell_degrees: float = 0.01) -> str:
    """Location key used for candidate selection (zone first, then geocell)."""
    zone = (event.get("zone") or "").strip().lower()
    if zone:
        return f"zone:{zone}"
    cell = geocell(event.get("latitude"), event.get("longitude"), geo_cell_degrees)
    if cell:
        return f"cell:{cell}"
    return "unknown"


def chunked(items: list[Any], size: int) -> Iterable[list[Any]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def clean_text(value: Any, max_length: int = 120) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:max_length] if text else None
