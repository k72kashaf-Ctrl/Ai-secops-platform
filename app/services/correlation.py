"""Explainable, deterministic incident correlation.

Events are partitioned by location (zone, else geocell, else device) and swept
in event-time order. An event joins an open incident at the same location when
(a) the gap to that incident's latest event is within the correlation window
and (b) its category is compatible with *every* category already in the
incident (``CATEGORY_COMPATIBILITY``). This links corroborating sources such as
a perimeter breach and the CCTV covering it without chaining unrelated
categories together. Incidents are capped by span and event count and split
with an explanation when a cap is reached. Arrival time is never evidence.

Incident IDs are derived from the anchor (earliest) event and the location
key, so an incident keeps its ID when later events join it. When an analyst
has set a status and the anchor changes (e.g. a late-arriving earlier event),
the new incident inherits the old ID, and so its status, through maximum
event overlap.

The sweep is O(n log n) for the sort plus O(n * k), where k is the (small)
number of incidents open at one location at the same time.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

from app.config import Settings
from app.database import Database
from app.models import IncidentDraft
from app.utils import (
    flow_key,
    max_severity,
    now_iso,
    parse_json,
    severity_rank,
    sha1_short,
    to_iso,
)

CATEGORY_LABELS = {
    "intrusion": "Perimeter intrusion",
    "traffic": "Traffic / vehicle violation",
    "access": "Access-control anomaly",
    "device_health": "Device health degradation",
    "environmental": "Environmental condition",
    "cyber": "Network security event",
    "fleet": "Fleet / GPS anomaly",
    "signals": "Mobile-signal detection",
    "surveillance": "Video surveillance detection",
    "emergency": "Emergency alarm",
    "operator": "Operator report",
    "general": "Unclassified security event",
}

RECOMMENDED_ACTIONS = {
    "intrusion": [
        "Verify the perimeter breach with CCTV or a patrol before dispatching.",
        "Preserve sensor and camera evidence for the affected zone.",
        "Check whether multiple sensors corroborate the same entry point.",
    ],
    "traffic": [
        "Review the plate/radar evidence and preserve the original detections.",
        "Confirm vehicle identity with a second source before issuing action.",
        "Check for repeated violations at the same location.",
    ],
    "access": [
        "Validate the badge/identity against access-control logs.",
        "Check whether a denied and a granted decision refer to the same subject.",
        "Review CCTV for tailgating or forced entry.",
    ],
    "device_health": [
        "Dispatch maintenance for the affected device.",
        "Confirm whether missing data creates a surveillance or sensor gap.",
        "Re-test the device after repair before trusting its detections.",
    ],
    "environmental": [
        "Check whether the environmental condition affects other sensors.",
        "Correlate with weather alerts and site operating limits.",
    ],
    "cyber": [
        "Review the affected host and network logs.",
        "Check for related authentication or malware events on the same segment.",
        "Preserve logs before containment actions.",
    ],
    "fleet": [
        "Verify the vehicle's planned route and last known GPS fix.",
        "Check for a telematics outage before treating the location as confirmed.",
    ],
    "signals": [
        "Corroborate the signal detection with onsite verification; it is not an identity.",
        "Check whether the detection repeats across multiple sensors.",
    ],
    "surveillance": [
        "Review the camera clip and preserve it as evidence.",
        "Seek a second sensor before escalating a single camera detection.",
    ],
    "emergency": [
        "Confirm the alarm with the site operator and dispatch per protocol.",
        "Preserve alarm and communication logs.",
    ],
    "operator": [
        "Validate the report against sensor evidence.",
        "Record the operator identity and time of observation.",
    ],
    "general": [
        "Review the raw evidence and classify the event source.",
        "Seek corroboration before escalating.",
    ],
}

# Categories that may be linked into one incident when they occur at the same
# location inside the correlation window. The relation is symmetric and
# deliberately sparse: a category joins an incident only if it is compatible
# with every category already present, so A<->B and B<->C never chain A with C.
_COMPATIBILITY_PAIRS = {
    ("intrusion", "surveillance"),   # perimeter/motion alarm + CCTV coverage
    ("intrusion", "access"),         # breach near a door / forced entry
    ("intrusion", "operator"),       # patrol confirms or clears an alarm
    ("intrusion", "emergency"),      # panic alarm during a breach
    ("intrusion", "signals"),        # unknown handset near the perimeter
    ("access", "surveillance"),      # tailgating / badge decision vs video
    ("access", "operator"),
    ("access", "cyber"),             # credential misuse, physical + logical
    ("traffic", "surveillance"),     # ANPR/radar + CCTV vehicle footage
    ("traffic", "fleet"),            # violation by a tracked fleet vehicle
    ("emergency", "surveillance"),
    ("emergency", "operator"),
    ("emergency", "environmental"),  # fire alarm + heat/smoke reading
    ("surveillance", "operator"),
}
CATEGORY_COMPATIBILITY: dict[str, frozenset[str]] = {
    category: frozenset(
        {b for a, b in _COMPATIBILITY_PAIRS if a == category}
        | {a for a, b in _COMPATIBILITY_PAIRS if b == category}
    )
    for category in CATEGORY_LABELS
}

# Most specific category first; it names a mixed-source incident.
_CATEGORY_PRIORITY = [
    "emergency",
    "intrusion",
    "access",
    "cyber",
    "traffic",
    "fleet",
    "signals",
    "device_health",
    "environmental",
    "operator",
    "surveillance",
    "general",
]

# Categories where a missing CCTV/video source is a meaningful evidence gap.
_VIDEO_EXPECTED = {"intrusion", "access", "emergency", "operator"}
_VIDEO_SOURCES = {"cctv", "camera", "video"}

# Used when Settings does not define the incident caps.
DEFAULT_MAX_SPAN_MINUTES = 60
DEFAULT_MAX_EVENTS = 50

_CONFLICT_TYPES = {
    "conflicting_sensors",
    "conflicting_location",
    "low_confidence",
    "delayed_event",
    "out_of_order_arrival",
    "uncorroborated_event",
    "conflicting_timestamps",
}


def classify_event(source_type: str, event_type: str) -> str:
    source = (source_type or "").lower().replace("-", "_")
    event = (event_type or "").lower().replace("-", "_")
    text = f"{source} {event}"

    if "health" in text or "heartbeat" in text:
        return "device_health"
    if source in {"gps", "fleet", "gps_fleet", "telematics"} or "telematics" in event:
        return "fleet"
    if source in {"access_control", "face_recognition", "badge_reader", "door_controller"}:
        return "access"
    if any(word in text for word in ("access", "badge", "door_", "tailgat", "credential")):
        return "access"
    if source in {"anpr", "radar", "red_light", "seatbelt_detection", "traffic", "speed_camera"}:
        return "traffic"
    if any(
        word in text
        for word in (
            "traffic",
            "speed",
            "red_light",
            "seatbelt",
            "anpr",
            "plate",
            "vehicle",
            "violation",
            "parking",
            "toll",
        )
    ):
        return "traffic"
    if any(
        word in text
        for word in (
            "intrusion",
            "perimeter",
            "fence",
            "tamper",
            "breach",
            "prowler",
            "motion",
            "person_detected",
        )
    ):
        return "intrusion"
    if source in {"cctv", "camera", "video"}:
        return "surveillance"
    if any(word in text for word in ("emergency", "panic", "sos", "fire", "medical")):
        return "emergency"
    if source in {"operator_report", "operator", "guard"} or "operator" in event:
        return "operator"
    if any(
        word in text
        for word in (
            "firewall",
            "malware",
            "brute",
            "login",
            "network",
            "packet",
            "dns",
            "auth",
            "vulnerability",
            "ids",
            "ips",
            "cyber",
            "threat",
        )
    ):
        return "cyber"
    if any(word in text for word in ("imsi", "cell", "phone", "mobile", "wifi", "bluetooth", "signal")):
        return "signals"
    if any(
        word in text
        for word in ("weather", "temperature", "wind", "rain", "flood", "humidity", "environmental", "air_quality")
    ):
        return "environmental"
    return "general"


def _location_key(event: dict[str, Any], settings: Settings) -> str:
    key = flow_key(event, settings.geo_cell_degrees)
    if key == "unknown":
        return f"device:{event['device_id']}"
    return key


def _load_events(db: Database, settings: Settings) -> list[dict[str, Any]]:
    rows = db.fetchall(
        """
        SELECT event_id, source_type, device_id, event_type, occurred_at,
               occurred_epoch, received_at, received_epoch, latitude, longitude,
               zone, severity, confidence, payload
        FROM events
        WHERE validation_status = 'valid'
        ORDER BY occurred_epoch ASC, event_id ASC
        """
    )
    for row in rows:
        row["payload_obj"] = parse_json(row.get("payload"), {}) or {}
        row["category"] = classify_event(row["source_type"], row["event_type"])
        row["location_key"] = _location_key(row, settings)
    return rows


def _load_conflict_map(db: Database) -> dict[str, list[dict[str, Any]]]:
    rows = db.fetchall(
        """
        SELECT issue_id, issue_type, severity, explanation, event_id, details
        FROM quality_issues
        WHERE issue_type IN ({})
        """.format(",".join("?" for _ in _CONFLICT_TYPES)),
        tuple(sorted(_CONFLICT_TYPES)),
    )
    mapping: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        details = parse_json(row.get("details"), {}) or {}
        event_ids = {row.get("event_id")}
        for key in ("first_event_id", "second_event_id"):
            if details.get(key):
                event_ids.add(details[key])
        for event_id in event_ids:
            if event_id:
                mapping[event_id].append(row)
    return mapping


def _format_location(events: list[dict[str, Any]]) -> str:
    zones = Counter(event["zone"] for event in events if event.get("zone"))
    if zones:
        common = [zone for zone, _ in zones.most_common(2)]
        if len(common) == 1:
            return common[0]
        return f"{common[0]} (+{len(common) - 1} nearby zone)"
    if all(event.get("latitude") is not None for event in events):
        return f"geo {events[0]['latitude']:.4f},{events[0]['longitude']:.4f}"
    return "location not provided"


def _primary_category(categories: set[str] | list[str]) -> str:
    present = set(categories)
    for category in _CATEGORY_PRIORITY:
        if category in present:
            return category
    return sorted(present)[0] if present else "general"


def _incident_id(anchor: dict[str, Any]) -> str:
    """Stable ID: the anchor (earliest) event plus the location partition."""
    return f"INC-{sha1_short(anchor['event_id'] + '|' + anchor['location_key'], 14)}"


def _max_span_seconds(settings: Settings) -> int:
    return int(getattr(settings, "incident_max_span_minutes", DEFAULT_MAX_SPAN_MINUTES)) * 60


def _max_events(settings: Settings) -> int:
    return int(getattr(settings, "incident_max_events", DEFAULT_MAX_EVENTS))


class _Cluster:
    __slots__ = ("events", "categories", "first_epoch", "last_epoch", "split_from", "split_reason", "incident_id")

    def __init__(self, event: dict[str, Any], split_from: "_Cluster | None" = None, split_reason: str = ""):
        self.events = [event]
        self.categories = {event["category"]}
        self.first_epoch = event["occurred_epoch"]
        self.last_epoch = event["occurred_epoch"]
        self.split_from = split_from
        self.split_reason = split_reason
        self.incident_id = ""

    def accepts(self, category: str) -> bool:
        compatible = CATEGORY_COMPATIBILITY.get(category, frozenset())
        return all(existing == category or existing in compatible for existing in self.categories)

    def add(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        self.categories.add(event["category"])
        self.last_epoch = event["occurred_epoch"]


def _cluster_events(events: list[dict[str, Any]], settings: Settings) -> list[_Cluster]:
    by_location: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_location[event["location_key"]].append(event)

    window = settings.correlation_window_minutes * 60
    max_span = _max_span_seconds(settings)
    max_events = _max_events(settings)
    clusters: list[_Cluster] = []
    for group in by_location.values():
        group.sort(key=lambda item: (item["occurred_epoch"], item["event_id"]))
        active: list[_Cluster] = []
        for event in group:
            epoch = event["occurred_epoch"]
            category = event["category"]
            active = [cluster for cluster in active if epoch - cluster.last_epoch <= window]
            candidates = [cluster for cluster in active if cluster.accepts(category)]
            if not candidates:
                cluster = _Cluster(event)
                active.append(cluster)
                clusters.append(cluster)
                continue
            # Prefer an incident that already holds this category, then the most recent.
            best = max(candidates, key=lambda item: (category in item.categories, item.last_epoch))
            if epoch - best.first_epoch > max_span or len(best.events) >= max_events:
                reason = (
                    f"exceeded the {max_span // 60}-minute maximum incident span"
                    if epoch - best.first_epoch > max_span
                    else f"reached the {max_events}-event maximum incident size"
                )
                active.remove(best)
                cluster = _Cluster(event, split_from=best, split_reason=reason)
                active.append(cluster)
                clusters.append(cluster)
                continue
            best.add(event)
    return clusters


def _assign_incident_ids(
    clusters: list[_Cluster],
    status_map: dict[str, Any],
    previous_membership: dict[str, str],
) -> None:
    """Give every cluster an incident ID.

    The default ID is derived from the anchor event. An incident that an analyst
    has touched (it has a status row) keeps its ID, and so its status, even if
    its anchor changes: the new cluster with the largest event overlap inherits
    it. A carried ID cannot collide with a derived one, because derived IDs that
    already have a status are claimed first.
    """
    for cluster in clusters:
        cluster.incident_id = _incident_id(cluster.events[0])
    claimed = {cluster.incident_id for cluster in clusters if cluster.incident_id in status_map}

    candidates: list[tuple[int, str, int]] = []
    for index, cluster in enumerate(clusters):
        if cluster.incident_id in status_map:
            continue
        overlap = Counter(
            previous_membership[event["event_id"]]
            for event in cluster.events
            if event["event_id"] in previous_membership
        )
        for old_id, count in overlap.items():
            if old_id not in claimed:
                candidates.append((count, old_id, index))

    reassigned: set[int] = set()
    for _, old_id, index in sorted(candidates, key=lambda item: (-item[0], item[1], item[2])):
        if old_id in claimed or index in reassigned:
            continue
        clusters[index].incident_id = old_id
        claimed.add(old_id)
        reassigned.add(index)


def _build_incident(
    cluster: _Cluster,
    settings: Settings,
    status_map: dict[str, str],
    conflict_map: dict[str, list[dict[str, Any]]],
) -> IncidentDraft:
    ordered = sorted(cluster.events, key=lambda item: (item["occurred_epoch"], item["event_id"]))
    categories = sorted({event["category"] for event in ordered})
    category = _primary_category(categories)
    event_ids = [event["event_id"] for event in ordered]
    incident_id = cluster.incident_id
    zones = sorted({event["zone"] for event in ordered if event.get("zone")})
    devices = sorted({event["device_id"] for event in ordered})
    sources = sorted({event["source_type"] for event in ordered})
    location = _format_location(ordered)
    average_confidence = sum(event["confidence"] for event in ordered) / len(ordered)

    related_issues: list[dict[str, Any]] = []
    seen_issue_ids: set[str] = set()
    for event in ordered:
        for issue in conflict_map.get(event["event_id"], []):
            if issue["issue_id"] not in seen_issue_ids:
                seen_issue_ids.add(issue["issue_id"])
                related_issues.append(issue)

    contradictions = []
    low_confidence_present = False
    for issue in related_issues:
        if issue["issue_type"] == "low_confidence":
            low_confidence_present = True
        if issue["severity"] in {"high", "medium", "critical"}:
            contradictions.append(f"{issue['issue_type']}: {issue['explanation'][:240]}")
    contradictions = contradictions[:6]

    possible_association = len(ordered) == 1 or low_confidence_present
    confidence = average_confidence
    if len(categories) > 1:
        # Independent source types corroborating each other.
        confidence += 0.05
    if contradictions:
        confidence -= 0.1
    if possible_association:
        confidence *= settings.possible_association_confidence_factor
    confidence = round(max(0.05, min(0.99, confidence)), 2)

    gaps = [
        (ordered[index + 1]["occurred_epoch"] - ordered[index]["occurred_epoch"]) / 60
        for index in range(len(ordered) - 1)
    ]
    max_gap = max(gaps) if gaps else 0.0
    span_minutes = (ordered[-1]["occurred_epoch"] - ordered[0]["occurred_epoch"]) / 60
    event_type_counts = Counter(event["event_type"] for event in ordered)

    if len(categories) > 1:
        link_rule = (
            f"share the location ({location}) and belong to compatible categories "
            f"({', '.join(categories)}) that corroborate each other"
        )
    else:
        link_rule = f"share the '{category}' category and location ({location})"
    explanation = (
        f"{len(ordered)} event(s) from {', '.join(sources)} were linked because they {link_rule}, "
        f"and their event times form a chain with a maximum gap of {max_gap:.1f} minutes inside the "
        f"configured {settings.correlation_window_minutes}-minute window (span {span_minutes:.1f} "
        f"minutes, capped at {_max_span_seconds(settings) // 60}). Arrival time was not used as "
        f"evidence. Event types: {', '.join(f'{name} x{count}' for name, count in event_type_counts.most_common(4))}. "
        f"All {len(event_ids)} original event IDs are preserved."
    )
    if cluster.split_from is not None:
        explanation += (
            f" This incident continues activity from {cluster.split_from.incident_id}; it was split "
            f"because that chain {cluster.split_reason}, which keeps incidents reviewable instead of "
            "merging a long stream of activity into one record."
        )
    if len(ordered) == 1:
        explanation += (
            " No second event satisfied the compatible-category+location+time rule, so this is "
            "represented as a possible association / unresolved relationship rather than a confirmed incident."
        )
    if related_issues:
        explanation += (
            f" The incident carries {len(related_issues)} quality finding(s); confidence was reduced where "
            "contradictory or low-confidence evidence is present."
        )

    missing_info: list[str] = []
    if not zones:
        missing_info.append("No zone label is available for any event in this incident.")
    if all(event.get("latitude") is None or event.get("longitude") is None for event in ordered):
        missing_info.append("No complete latitude/longitude pair is available.")
    if (
        any(item in _VIDEO_EXPECTED for item in categories)
        and not any(event["source_type"] in _VIDEO_SOURCES for event in ordered)
    ):
        missing_info.append(
            f"No CCTV/video event at {location} was linked to this incident within the correlation "
            "window; review cameras covering the location to corroborate it."
        )
    if low_confidence_present:
        missing_info.append(
            "Contributing evidence is low-confidence: at least one detection is below the configured confidence threshold."
        )
    if len(ordered) == 1:
        missing_info.append("No corroborating event exists within the correlation window.")
    missing_info = missing_info[:6]

    actions: list[str] = []
    for item in [category] + [other for other in categories if other != category]:
        for action in RECOMMENDED_ACTIONS.get(item, RECOMMENDED_ACTIONS["general"]):
            if action not in actions:
                actions.append(action)
    if contradictions:
        actions.insert(0, "Resolve the documented contradictions with a second source before escalation.")
    if len(ordered) == 1:
        actions.insert(0, "Treat as a single-source lead; seek corroboration before committing resources.")

    severity = max_severity(event["severity"] for event in ordered)
    proposed_status = status_map.get(incident_id, "open")
    created_epoch = max(event["received_epoch"] for event in ordered)
    created_at = max(
        (event["received_at"] for event in ordered),
        key=lambda value: value or "",
    )
    label = CATEGORY_LABELS.get(category, CATEGORY_LABELS["general"])
    title = f"{label} at {location}"
    if len(ordered) > 1:
        if len(categories) > 1:
            title += f" ({len(ordered)} events, {len(sources)} sources)"
        else:
            title += f" ({len(ordered)} events)"
    else:
        title = f"Possible {label.lower()} at {location}"

    return IncidentDraft(
        incident_id=incident_id,
        title=title,
        category=category,
        severity=severity,
        confidence=confidence,
        status=proposed_status,
        created_at=created_at,
        updated_at=created_at,
        created_epoch=created_epoch,
        updated_epoch=created_epoch,
        event_ids=event_ids,
        devices=devices,
        zones=zones,
        sources=sources,
        explanation=explanation,
        missing_info=missing_info,
        contradictions=contradictions,
        recommended_actions=actions[:6],
        is_possible_association=possible_association,
    )


def run_correlation(db: Database, settings: Settings) -> int:
    """Recompute all incidents from valid events and persist atomically."""
    events = _load_events(db, settings)
    status_map = {
        row["incident_id"]: row["status"]
        for row in db.fetchall("SELECT incident_id, status FROM incident_status")
    }
    previous_membership = {
        row["event_id"]: row["incident_id"]
        for row in db.fetchall(
            """
            SELECT ie.event_id, ie.incident_id
            FROM incident_events ie
            JOIN incident_status s ON s.incident_id = ie.incident_id
            """
        )
    }
    conflict_map = _load_conflict_map(db)

    clusters = _cluster_events(events, settings)
    _assign_incident_ids(clusters, status_map, previous_membership)
    drafts = [_build_incident(cluster, settings, status_map, conflict_map) for cluster in clusters]
    drafts.sort(key=lambda item: (severity_rank(item.severity), item.created_epoch), reverse=True)
    event_by_id = {event["event_id"]: event for event in events}

    with db.transaction() as connection:
        connection.execute("DELETE FROM incident_events")
        connection.execute("DELETE FROM incidents")
        if drafts:
            connection.executemany(
                """
                INSERT INTO incidents (
                    incident_id, title, category, severity, confidence, status,
                    created_at, updated_at, created_epoch, updated_epoch,
                    event_ids, devices, zones, sources, explanation, missing_info,
                    contradictions, recommended_actions, event_count,
                    is_possible_association
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [draft.to_row() for draft in drafts],
            )
            incident_event_rows = []
            for draft in drafts:
                for sequence, event_id in enumerate(draft.event_ids):
                    event = event_by_id[event_id]
                    incident_event_rows.append(
                        (
                            draft.incident_id,
                            event_id,
                            event["occurred_at"],
                            event["occurred_epoch"],
                            sequence,
                        )
                    )
            connection.executemany(
                """
                INSERT INTO incident_events (
                    incident_id, event_id, occurred_at, occurred_epoch, sequence
                ) VALUES (?,?,?,?,?)
                """,
                incident_event_rows,
            )
    return len(drafts)
