"""Deterministic data-quality and anomaly detection.

Every rule is an explicit, configurable check. When a condition is merely
suspicious (for example a burst of events), the explanation says so rather
than asserting malicious activity. Original evidence is never modified.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Iterable

from app.config import Settings
from app.database import Database
from app.models import QualityIssueDraft
from app.utils import (
    haversine_km,
    now_iso,
    parse_json,
    sha1_short,
)

HEALTHY_STATUSES = {"ok", "online", "healthy", "nominal", "good", "up", "operational"}
UNHEALTHY_STATUSES = {
    "offline",
    "error",
    "degraded",
    "unhealthy",
    "fault",
    "faulty",
    "failed",
    "failure",
    "down",
    "critical",
    "disconnected",
}
CONTRADICTION_PAIRS = [
    {"granted", "denied"},
    {"allow", "deny"},
    {"allowed", "denied"},
    {"open", "closed"},
    {"detected", "clear"},
    {"detected", "not_detected"},
    {"alarm", "normal"},
    {"online", "offline"},
    {"valid", "invalid"},
    {"authorized", "unauthorized"},
    {"present", "absent"},
    {"approved", "rejected"},
    {"inside", "outside"},
]
RESULT_KEYS = ("result", "status", "state", "outcome", "decision", "alarm")
MAX_CONFLICT_COMPARISONS = 250_000


def _issue_id(issue_type: str, *parts: Any) -> str:
    raw = "|".join([issue_type, *[str(part) for part in parts]])
    return f"QI-{sha1_short(raw, 14)}"


def _status_value(payload: dict[str, Any]) -> str | None:
    for key in ("status", "state", "health", "device_status", "condition"):
        value = payload.get(key)
        if value is not None:
            return str(value).strip().lower().replace(" ", "_")
    return None


def _result_value(payload: dict[str, Any]) -> str | None:
    for key in RESULT_KEYS:
        value = payload.get(key)
        if value is not None:
            return str(value).strip().lower().replace(" ", "_")
    return None


def _numeric(payload: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _values_contradict(left: str, right: str) -> bool:
    if not left or not right or left == right:
        return False
    for pair in CONTRADICTION_PAIRS:
        if left in pair and right in pair:
            return True
    return False


def _flow_key(row: dict[str, Any], cell_size: float) -> str:
    zone = (row.get("zone") or "").strip().lower()
    if zone:
        return f"zone:{zone}"
    lat, lon = row.get("latitude"), row.get("longitude")
    if lat is not None and lon is not None:
        import math

        return f"cell:{math.floor(lat / cell_size):+d}:{math.floor(lon / cell_size):+d}"
    return "unknown"


class _Collector:
    def __init__(self, detected_at: str):
        self.detected_at = detected_at
        self.issues: dict[str, QualityIssueDraft] = {}

    def add(
        self,
        issue_type: str,
        severity: str,
        explanation: str,
        suggested_action: str,
        *,
        event_id: str | None = None,
        device_id: str | None = None,
        key: Any = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        issue_id = _issue_id(issue_type, event_id or "", device_id or "", key)
        if issue_id in self.issues:
            return
        self.issues[issue_id] = QualityIssueDraft(
            issue_id=issue_id,
            issue_type=issue_type,
            severity=severity,
            explanation=explanation,
            event_id=event_id,
            device_id=device_id,
            detected_at=self.detected_at,
            suggested_action=suggested_action,
            details=details or {},
        )


def _load_events(db: Database) -> list[dict[str, Any]]:
    rows = db.fetchall(
        """
        SELECT event_id, source_type, device_id, event_type, occurred_at,
               occurred_epoch, received_at, received_epoch, latitude, longitude,
               zone, severity, confidence, payload, validation_status, duplicate_of
        FROM events
        ORDER BY occurred_epoch ASC, event_id ASC
        """
    )
    for row in rows:
        row["payload_obj"] = parse_json(row.get("payload"), {}) or {}
    return rows


def _duplicate_content_issues(events: Iterable[dict[str, Any]], collector: _Collector) -> None:
    for event in events:
        if event["validation_status"] != "duplicate":
            continue
        collector.add(
            "duplicate_content",
            "medium",
            (
                f"Event {event['event_id']} has the same source, device, type, occurrence "
                f"time, location and payload as {event.get('duplicate_of') or 'an earlier event'}. "
                "It is stored as evidence but marked duplicate so it does not inflate analytics."
            ),
            "Confirm whether two feeds or integrations are publishing the same record; retain both until verified.",
            event_id=event["event_id"],
            device_id=event["device_id"],
            details={
                "duplicate_of": event.get("duplicate_of"),
                "content_hash_prefix": None,
                "occurred_at": event["occurred_at"],
            },
        )


def _rejection_issues(db: Database, collector: _Collector) -> None:
    rejected = db.fetchall(
        "SELECT id, source_event_id, errors, reason, received_at FROM rejected_events ORDER BY id"
    )
    required_fields = {"event_id", "source_type", "device_id", "event_type", "occurred_at"}
    for row in rejected:
        try:
            errors = json.loads(row["errors"])
        except (TypeError, ValueError):
            errors = [{"field": "record", "message": row["errors"]}]
        missing: set[str] = set()
        for error in errors:
            field = str(error.get("field", "")).split(".")[-1]
            message = str(error.get("message", "")).lower()
            if field in required_fields and (
                error.get("type") == "missing" or "required" in message
            ):
                missing.add(field)
        if row["reason"] == "duplicate_event_id":
            issue_type = "duplicate_event_id"
            severity = "medium"
            explanation = (
                f"A submitted record reused event_id {row['source_event_id']}. The first "
                "occurrence was kept; the later record and its raw input were preserved as rejected evidence."
            )
            action = "Check the upstream connector for ID generation collisions; reconcile the two records manually."
        elif missing:
            issue_type = "missing_mandatory_field"
            severity = "medium"
            explanation = (
                f"Record {row['source_event_id'] or '(no event_id)'} is missing mandatory "
                f"field(s): {', '.join(sorted(missing))}. It was rejected rather than guessed."
            )
            action = "Correct the source mapping or connector payload and resubmit the record."
        else:
            issue_type = "invalid_record"
            severity = "medium"
            explanation = (
                f"Record {row['source_event_id'] or '(no event_id)'} failed validation: "
                + "; ".join(f"{e.get('field')}: {e.get('message')}" for e in errors)
            )
            action = "Fix the malformed value at the source; the raw record is retained for review."
        collector.add(
            issue_type,
            severity,
            explanation,
            action,
            event_id=row["source_event_id"],
            key=row["id"],
            details={
                "rejected_record_id": row["id"],
                "reason": row["reason"],
                "errors": errors,
                "received_at": row["received_at"],
            },
        )


def _timing_issues(
    events: list[dict[str, Any]], settings: Settings, collector: _Collector
) -> None:
    delayed_threshold = settings.delayed_threshold_seconds
    for event in events:
        if event["validation_status"] != "valid":
            continue
        delta = event["received_epoch"] - event["occurred_epoch"]
        if delta > delayed_threshold:
            severity = "high" if delta > delayed_threshold * 4 else "medium"
            collector.add(
                "delayed_event",
                severity,
                (
                    f"Event {event['event_id']} from {event['device_id']} occurred at "
                    f"{event['occurred_at']} but was received {delta / 60:.1f} minutes later. "
                    "This is an observed transport delay; the cause is not yet known."
                ),
                "Check the device/connector connectivity and queue backlog; rely on event time, not arrival time.",
                event_id=event["event_id"],
                device_id=event["device_id"],
                details={
                    "delay_seconds": delta,
                    "occurred_at": event["occurred_at"],
                    "received_at": event["received_at"],
                },
            )
        if event["occurred_epoch"] - event["received_epoch"] > settings.clock_skew_tolerance_seconds:
            collector.add(
                "conflicting_timestamps",
                "medium",
                (
                    f"Event {event['event_id']} has an occurrence time later than its receipt "
                    "time, which is inconsistent without clock skew."
                ),
                "Verify device and gateway clocks, then treat the occurrence time as unconfirmed.",
                event_id=event["event_id"],
                device_id=event["device_id"],
                details={
                    "occurred_at": event["occurred_at"],
                    "received_at": event["received_at"],
                },
            )

    # Out-of-order arrivals are evaluated per device in arrival order, using
    # event time (not arrival order) as the source of truth.
    by_device: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if event["validation_status"] == "valid":
            by_device[event["device_id"]].append(event)
    for device_id, device_events in by_device.items():
        device_events.sort(key=lambda item: (item["received_epoch"], item["event_id"]))
        latest_occurred = None
        latest_event = None
        for event in device_events:
            if (
                latest_occurred is not None
                and event["occurred_epoch"] + settings.clock_skew_tolerance_seconds < latest_occurred
            ):
                gap = latest_occurred - event["occurred_epoch"]
                collector.add(
                    "out_of_order_arrival",
                    "medium",
                    (
                        f"Event {event['event_id']} arrived after event {latest_event['event_id']} "
                        f"from the same device but is {gap / 60:.1f} minutes older by event time. "
                        "Arrival order is not chronological."
                    ),
                    "Process events by occurrence time; investigate buffering or clock issues at the connector.",
                    event_id=event["event_id"],
                    device_id=device_id,
                    details={
                        "previous_event_id": latest_event["event_id"],
                        "event_occurred_at": event["occurred_at"],
                        "previous_occurred_at": latest_event["occurred_at"],
                    },
                )
            if latest_occurred is None or event["occurred_epoch"] > latest_occurred:
                latest_occurred = event["occurred_epoch"]
                latest_event = event


def _confidence_issues(
    events: list[dict[str, Any]], settings: Settings, collector: _Collector
) -> None:
    for event in events:
        if event["validation_status"] != "valid":
            continue
        if event["confidence"] < settings.low_confidence_threshold:
            collector.add(
                "low_confidence",
                "low" if event["confidence"] >= 0.3 else "medium",
                (
                    f"Event {event['event_id']} ({event['source_type']}/{event['event_type']}) "
                    f"has confidence {event['confidence']:.2f}, below the configured threshold "
                    f"{settings.low_confidence_threshold:.2f}. The detection is preserved but is not treated as confirmed."
                ),
                "Seek corroborating sensor evidence or human verification before acting on this detection.",
                event_id=event["event_id"],
                device_id=event["device_id"],
                details={"confidence": event["confidence"], "threshold": settings.low_confidence_threshold},
            )


def _conflict_issues(
    events: list[dict[str, Any]], settings: Settings, collector: _Collector
) -> None:
    window = settings.conflict_window_minutes * 60
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if event["validation_status"] != "valid":
            continue
        groups[(_flow_key(event, settings.geo_cell_degrees), event["event_type"])].append(event)

    comparisons = 0
    limit_hit = False
    for (location_key, event_type), group in groups.items():
        if len(group) < 2:
            continue
        group.sort(key=lambda item: (item["occurred_epoch"], item["event_id"]))
        for index, left in enumerate(group):
            for right in group[index + 1 :]:
                if right["occurred_epoch"] - left["occurred_epoch"] > window:
                    break
                if left["device_id"] == right["device_id"]:
                    continue
                comparisons += 1
                if comparisons > MAX_CONFLICT_COMPARISONS:
                    limit_hit = True
                    break
                left_payload = left["payload_obj"]
                right_payload = right["payload_obj"]
                left_result = _result_value(left_payload)
                right_result = _result_value(right_payload)
                conflict_reason = None
                if _values_contradict(left_result, right_result):
                    conflict_reason = (
                        f"reported results differ: {left['device_id']}={left_result!r} vs "
                        f"{right['device_id']}={right_result!r}"
                    )
                else:
                    left_speed = _numeric(left_payload, "speed_kmh", "speed", "velocity_kmh")
                    right_speed = _numeric(right_payload, "speed_kmh", "speed", "velocity_kmh")
                    if (
                        left_speed is not None
                        and right_speed is not None
                        and abs(left_speed - right_speed) > settings.conflict_speed_delta_kmh
                    ):
                        conflict_reason = (
                            f"speed readings differ by {abs(left_speed - right_speed):.1f} km/h "
                            f"({left_speed:.1f} vs {right_speed:.1f})"
                        )
                if conflict_reason:
                    pair_key = "|".join(sorted([left["event_id"], right["event_id"]]))
                    corroborated = min(left["confidence"], right["confidence"]) >= 0.5
                    collector.add(
                        "conflicting_sensors",
                        "high" if corroborated else "medium",
                        (
                            f"Two sensors reported the same {event_type} observation in "
                            f"{location_key.split(':', 1)[-1]} within {settings.conflict_window_minutes} minutes, "
                            f"but {conflict_reason}. This is a suspected conflict, not proof that either sensor is wrong."
                        ),
                        "Preserve both readings, check sensor calibration and clocks, and corroborate with CCTV/operator evidence.",
                        event_id=right["event_id"],
                        device_id=right["device_id"],
                        key=pair_key,
                        details={
                            "first_event_id": left["event_id"],
                            "second_event_id": right["event_id"],
                            "first_device": left["device_id"],
                            "second_device": right["device_id"],
                            "first_source": left["source_type"],
                            "second_source": right["source_type"],
                            "reason": conflict_reason,
                        },
                    )
            if limit_hit:
                break
        if limit_hit:
            break
    if limit_hit:
        collector.add(
            "analysis_limit_reached",
            "low",
            (
                "Pairwise sensor-conflict comparison reached the configured safety limit. "
                "Some conflicts may not be listed; no events were discarded."
            ),
            "Narrow the analysis by time/zone or raise the limit when reviewing a dense burst.",
            key="conflicts",
            details={"limit": MAX_CONFLICT_COMPARISONS},
        )


def _conflicting_location_issues(
    events: list[dict[str, Any]], settings: Settings, collector: _Collector
) -> None:
    """Same device, same observable, implausible geographic jump."""
    window = 120
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if event["validation_status"] != "valid":
            continue
        groups[(event["device_id"], event["event_type"])].append(event)
    for (device_id, event_type), group in groups.items():
        if len(group) < 2:
            continue
        group.sort(key=lambda item: (item["occurred_epoch"], item["event_id"]))
        for index, left in enumerate(group):
            for right in group[index + 1 :]:
                if right["occurred_epoch"] - left["occurred_epoch"] > window:
                    break
                lat1, lon1 = left["latitude"], left["longitude"]
                lat2, lon2 = right["latitude"], right["longitude"]
                jump = None
                if None not in (lat1, lon1, lat2, lon2):
                    distance = haversine_km(lat1, lon1, lat2, lon2)
                    if distance > 5.0:
                        jump = f"reported positions are {distance:.1f} km apart within two minutes"
                elif left["zone"] and right["zone"] and left["zone"].lower() != right["zone"].lower():
                    jump = f"reported zones differ ({left['zone']!r} vs {right['zone']!r})"
                if jump:
                    pair_key = "|".join(sorted([left["event_id"], right["event_id"]]))
                    collector.add(
                        "conflicting_location",
                        "high",
                        (
                            f"Device {device_id} reported {event_type} twice within two minutes, but {jump}. "
                            "This is a suspected location inconsistency."
                        ),
                        "Verify device identity, GPS fix and zone mapping; do not dispatch based on a single location.",
                        event_id=right["event_id"],
                        device_id=device_id,
                        key=pair_key,
                        details={
                            "first_event_id": left["event_id"],
                            "second_event_id": right["event_id"],
                            "first_zone": left["zone"],
                            "second_zone": right["zone"],
                        },
                    )
                    break


def _repetition_issues(
    db: Database, settings: Settings, collector: _Collector
) -> None:
    window_seconds = settings.repeated_event_window_minutes * 60
    rows = db.fetchall(
        """
        SELECT device_id, event_type,
               CAST(occurred_epoch / ? AS INTEGER) AS bucket,
               COUNT(*) AS count,
               MIN(occurred_at) AS first_seen,
               MAX(occurred_at) AS last_seen,
               MIN(zone) AS zone
        FROM events
        WHERE validation_status = 'valid'
        GROUP BY device_id, event_type, bucket
        HAVING COUNT(*) >= ?
        ORDER BY count DESC
        """,
        (window_seconds, settings.repeated_event_threshold),
    )
    for row in rows:
        severity = "high" if row["count"] >= settings.repeated_event_threshold * 2 else "medium"
        collector.add(
            "repeated_events",
            severity,
            (
                f"Device {row['device_id']} emitted {row['count']} '{row['event_type']}' events "
                f"within {settings.repeated_event_window_minutes} minutes "
                f"({row['first_seen']} to {row['last_seen']}). This is an observed repetition; it may be "
                "a stuck sensor, a retry loop or a genuine burst."
            ),
            "Inspect the device and connector for a retry loop or stuck sensor; confirm whether the burst is operationally real.",
            device_id=row["device_id"],
            key=f"{row['event_type']}|{row['bucket']}",
            details={
                "count": row["count"],
                "event_type": row["event_type"],
                "zone": row["zone"],
                "window_minutes": settings.repeated_event_window_minutes,
                "first_seen": row["first_seen"],
                "last_seen": row["last_seen"],
            },
        )


def _spike_issues(db: Database, settings: Settings, collector: _Collector) -> None:
    window_seconds = settings.spike_window_minutes * 60
    rows = db.fetchall(
        """
        SELECT COALESCE(NULLIF(TRIM(zone), ''), 'unlocated') AS zone,
               CAST(occurred_epoch / ? AS INTEGER) AS bucket,
               COUNT(*) AS count,
               MIN(occurred_at) AS first_seen,
               MAX(occurred_at) AS last_seen
        FROM events
        WHERE validation_status = 'valid'
        GROUP BY zone, bucket
        HAVING COUNT(*) >= ?
        ORDER BY count DESC
        """,
        (window_seconds, settings.spike_threshold),
    )
    for row in rows:
        severity = "high" if row["count"] >= settings.spike_threshold * 2 else "medium"
        collector.add(
            "event_spike",
            severity,
            (
                f"Observed {row['count']} events in zone '{row['zone']}' within "
                f"{settings.spike_window_minutes} minutes ({row['first_seen']} to {row['last_seen']}). "
                "This is a statistical spike relative to the configured threshold, not proof of an attack."
            ),
            "Review the zone's sources and capacity; confirm whether a real surge or a reporting artefact caused the spike.",
            key=f"{row['zone']}|{row['bucket']}",
            details={
                "zone": row["zone"],
                "count": row["count"],
                "window_minutes": settings.spike_window_minutes,
                "threshold": settings.spike_threshold,
                "first_seen": row["first_seen"],
                "last_seen": row["last_seen"],
            },
        )


def _health_issues(
    db: Database, settings: Settings, collector: _Collector
) -> None:
    rows = db.fetchall(
        """
        SELECT event_id, device_id, source_type, event_type, occurred_at,
               occurred_epoch, payload, zone
        FROM events
        WHERE validation_status = 'valid'
          AND (source_type IN ('device_health','device_health_monitor','health')
               OR event_type LIKE '%health%' OR event_type LIKE '%heartbeat%')
        ORDER BY occurred_epoch ASC
        """
    )
    seen_buckets: set[str] = set()
    for row in rows:
        payload = parse_json(row["payload"], {}) or {}
        status = _status_value(payload)
        online = payload.get("online")
        unhealthy = status in UNHEALTHY_STATUSES or online is False
        if not unhealthy:
            continue
        bucket = row["occurred_epoch"] // max(settings.repeated_event_window_minutes * 60, 60)
        dedup = f"{row['device_id']}|{status}|{online}|{bucket}"
        if dedup in seen_buckets:
            continue
        seen_buckets.add(dedup)
        severity = "high" if status in {"offline", "down", "failed", "failure", "critical"} or online is False else "medium"
        collector.add(
            "unhealthy_device",
            severity,
            (
                f"Device {row['device_id']} reported health status {status or '(no status)'}"
                + (f" with online={online}" if online is not None else "")
                + f" at {row['occurred_at']}. This is an explicit device report, not an inference."
            ),
            "Dispatch device maintenance and verify whether the affected sensor is still contributing evidence.",
            event_id=row["event_id"],
            device_id=row["device_id"],
            key=dedup,
            details={
                "status": status,
                "online": online,
                "source_type": row["source_type"],
                "event_type": row["event_type"],
                "zone": row["zone"],
            },
        )


def _setting(settings: Settings, name: str, env: str, default: float) -> float:
    """Optional threshold: a Settings attribute wins, then an env var, then the default."""
    value = getattr(settings, name, None)
    if value is None:
        value = os.getenv(env)
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------- silent devices
# Only sources that are expected to report on a cadence are monitored for
# silence; event-driven sensors (CCTV motion, ANPR, alarms) are legitimately
# quiet when nothing happens, so a gap there says nothing about device health.
OFFLINE_MONITORED_SOURCES = {
    "device_health",
    "device_health_monitor",
    "health",
    "heartbeat",
    "gps",
    "weather",
}
OFFLINE_GAP_MULTIPLIER = 10.0  # gap > multiplier x device's median interval
OFFLINE_MIN_GAP_SECONDS = 900.0  # never flag a gap shorter than 15 minutes
OFFLINE_MIN_INTERVALS = 4  # need a cadence baseline before judging silence


def _offline_monitored(event: dict[str, Any]) -> bool:
    return event["source_type"] in OFFLINE_MONITORED_SOURCES or "heartbeat" in event["event_type"]


def _offline_device_issues(
    events: list[dict[str, Any]], settings: Settings, collector: _Collector
) -> None:
    """Infer silent/offline devices from gaps in their own reporting cadence.

    A device's expected interval is its median gap between consecutive events.
    A gap is flagged when it exceeds max(min_gap, multiplier x median). Silence
    after a device's last event, measured against the latest event time in the
    dataset, is flagged with the same threshold.
    """
    multiplier = _setting(settings, "offline_gap_multiplier", "ZENITH_OFFLINE_GAP_MULTIPLIER", OFFLINE_GAP_MULTIPLIER)
    min_gap = _setting(settings, "offline_min_gap_seconds", "ZENITH_OFFLINE_MIN_GAP_SECONDS", OFFLINE_MIN_GAP_SECONDS)
    valid = [event for event in events if event["validation_status"] == "valid"]
    if not valid:
        return
    dataset_end = max(event["occurred_epoch"] for event in valid)
    by_device: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in valid:
        if _offline_monitored(event):
            by_device[event["device_id"]].append(event)

    for device_id, device_events in by_device.items():
        # Events are loaded in occurrence order; same-second repeats add no cadence.
        gaps = [
            right["occurred_epoch"] - left["occurred_epoch"]
            for left, right in zip(device_events, device_events[1:])
        ]
        positive = sorted(gap for gap in gaps if gap > 0)
        if len(positive) < OFFLINE_MIN_INTERVALS:
            continue
        median = positive[len(positive) // 2]
        threshold = max(min_gap, multiplier * median)

        def flag(before: dict[str, Any], after: dict[str, Any] | None, gap: float) -> None:
            severity = "high" if gap > threshold * 3 else "medium"
            evidence = [before["event_id"]] + ([after["event_id"]] if after else [])
            if after:
                window = f"between {before['occurred_at']} and {after['occurred_at']}"
                tail = "It resumed reporting afterwards."
            else:
                window = f"since {before['occurred_at']} (latest event in the dataset is {gap / 60:.1f} minutes later)"
                tail = "It has not reported since."
            collector.add(
                "offline_device",
                severity,
                (
                    f"Device {device_id} ({before['source_type']}) normally reports about every "
                    f"{median / 60:.1f} minutes but was silent for {gap / 60:.1f} minutes {window}. "
                    f"{tail} This is inferred from missing reports, not an explicit device status."
                ),
                "Check device power and connectivity; treat the zone's coverage from this device as unverified for the silent period.",
                event_id=(after or before)["event_id"],
                device_id=device_id,
                key=f"gap|{before['occurred_epoch']}",
                details={
                    "gap_seconds": gap,
                    "expected_interval_seconds": median,
                    "threshold_seconds": threshold,
                    "last_event_before_gap": before["event_id"],
                    "first_event_after_gap": after["event_id"] if after else None,
                    "evidence_event_ids": evidence,
                    "silent_from": before["occurred_at"],
                    "silent_until": after["occurred_at"] if after else None,
                    "source_type": before["source_type"],
                    "zone": before["zone"],
                    "inferred": True,
                },
            )

        for left, right, gap in zip(device_events, device_events[1:], gaps):
            if gap > threshold:
                flag(left, right, gap)
        trailing = dataset_end - device_events[-1]["occurred_epoch"]
        if trailing > threshold:
            flag(device_events[-1], None, trailing)


# ---------------------------------------------------- cross-type contradictions
INTRUSION_SOURCES = {
    "perimeter",
    "intrusion",
    "intrusion_detection",
    "intrusion_sensor",
    "alarm_panel",
    "motion_sensor",
    "pir",
    "fence_sensor",
}
CCTV_SOURCES = {"cctv", "camera", "video", "video_analytics", "vms"}
ACCESS_SOURCES = {"access_control", "access", "door_controller", "badge_reader"}
FACE_SOURCES = {"face_recognition", "facial_recognition", "face"}

INTRUSION_POSITIVE = {"alarm", "detected", "breach", "triggered", "intrusion", "active"}
INTRUSION_NEGATIVE = {"clear", "normal", "not_detected", "secure", "no_motion", "no_activity", "nothing_detected", "empty"}
CCTV_POSITIVE_TYPES = {"intrusion_detected", "line_crossing", "perimeter_intrusion", "fence_climb", "intrusion"}
ACCESS_GRANTED = {"granted", "allow", "allowed", "approved", "authorized"}
FACE_NEGATIVE = {"denied", "unknown", "no_match", "mismatch", "unrecognized", "unrecognised", "rejected", "unauthorized"}
SUBJECT_KEYS = ("subject", "person_id", "badge", "person")

# (tag_a, tag_b, rule label, window cap in seconds or None for the conflict window)
CROSS_TYPE_RULES = [
    ("intrusion+", "intrusion-", "intrusion detected vs area reported clear", None),
    ("access_granted", "face_negative", "access granted vs face recognition not matching", 120),
]


def _payload_token(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is not None and not isinstance(value, (dict, list)):
            return str(value).strip().lower().replace(" ", "_").replace("-", "_")
    return None


def _cross_type_tag(event: dict[str, Any]) -> str | None:
    """Classify an event's claim for the cross-type contradiction rules."""
    source, event_type = event["source_type"], event["event_type"]
    payload = event["payload_obj"]
    result = _result_value(payload)
    if source in INTRUSION_SOURCES:
        if result in INTRUSION_NEGATIVE:
            return "intrusion-"
        if result in INTRUSION_POSITIVE:
            return "intrusion+"
        if result is None and any(word in event_type for word in ("breach", "intrusion", "alarm")):
            return "intrusion+"
        return None
    if source in CCTV_SOURCES:
        if result in INTRUSION_NEGATIVE or _payload_token(payload, "object") in {"none", "nothing"}:
            return "intrusion-"
        if result in {"detected", "intrusion", "breach"} or event_type in CCTV_POSITIVE_TYPES:
            return "intrusion+"
        return None
    if source in ACCESS_SOURCES:
        return "access_granted" if result in ACCESS_GRANTED else None
    if source in FACE_SOURCES:
        match = _payload_token(payload, "match", "result", "decision")
        if match in FACE_NEGATIVE or "unknown" in event_type:
            return "face_negative"
    return None


def _same_subject(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """False only when both events name a subject and the subjects differ."""
    for key in SUBJECT_KEYS:
        a, b = left["payload_obj"].get(key), right["payload_obj"].get(key)
        if a is not None and b is not None:
            return str(a).strip().lower() == str(b).strip().lower()
    return True


def _cross_type_conflict_issues(
    events: list[dict[str, Any]], settings: Settings, collector: _Collector
) -> None:
    """Contradictory claims from different event types in the same zone/window.

    Same-event_type contradictions are handled by _conflict_issues; this pass
    only pairs events whose event types differ, so nothing is double-reported.
    Each zone is scanned once in time order with a per-tag sliding window.
    """
    base_window = settings.conflict_window_minutes * 60
    partner: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
    for tag_a, tag_b, label, cap in CROSS_TYPE_RULES:
        window = min(base_window, cap) if cap else base_window
        partner[tag_a].append((tag_b, label, window))
        partner[tag_b].append((tag_a, label, window))

    by_zone: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for event in events:
        if event["validation_status"] != "valid":
            continue
        tag = _cross_type_tag(event)
        if tag:
            by_zone[_flow_key(event, settings.geo_cell_degrees)].append((tag, event))

    comparisons = 0
    for location_key, tagged in by_zone.items():
        recent: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
        for tag, right in tagged:  # already in occurrence order
            for other_tag, label, window in partner[tag]:
                bucket = recent[other_tag]
                while bucket and right["occurred_epoch"] - bucket[0]["occurred_epoch"] > base_window:
                    bucket.popleft()
                for left in bucket:
                    if right["occurred_epoch"] - left["occurred_epoch"] > window:
                        continue
                    if left["device_id"] == right["device_id"] or left["event_type"] == right["event_type"]:
                        continue
                    comparisons += 1
                    if comparisons > MAX_CONFLICT_COMPARISONS:
                        return
                    if not _same_subject(left, right):
                        continue
                    pair_key = "|".join(sorted([left["event_id"], right["event_id"]]))
                    corroborated = min(left["confidence"], right["confidence"]) >= 0.5
                    zone = location_key.split(":", 1)[-1]
                    collector.add(
                        "conflicting_sensors",
                        "high" if corroborated else "medium",
                        (
                            f"{left['source_type']} {left['event_type']} ({left['device_id']}, "
                            f"{left['occurred_at']}) and {right['source_type']} {right['event_type']} "
                            f"({right['device_id']}, {right['occurred_at']}) in {zone} make contradictory "
                            f"claims: {label}. This is a suspected cross-sensor conflict, not proof that either is wrong."
                        ),
                        "Review both feeds side by side (e.g. CCTV footage for the window) and verify sensor calibration before acting on either claim.",
                        event_id=right["event_id"],
                        device_id=right["device_id"],
                        key=pair_key,
                        details={
                            "first_event_id": left["event_id"],
                            "second_event_id": right["event_id"],
                            "first_device": left["device_id"],
                            "second_device": right["device_id"],
                            "first_source": left["source_type"],
                            "second_source": right["source_type"],
                            "first_claim": _claim_label(left),
                            "second_claim": _claim_label(right),
                            "rule": label,
                            "cross_type": True,
                            "reason": label,
                        },
                    )
            recent[tag].append(right)


def _claim_label(event: dict[str, Any]) -> str:
    return f"{event['event_type']}={_result_value(event['payload_obj']) or _payload_token(event['payload_obj'], 'match', 'object') or 'n/a'}"


# --------------------------------------------------------- incomplete records
# Values EventIn substitutes when a field is absent or blank (see app/schemas.py).
DEFAULTED_FIELDS = {
    "severity": "medium",
    "confidence": 0.5,
    "received_at": "occurred_at",
}


def _incomplete_record_issues(db: Database, collector: _Collector) -> None:
    """Flag accepted events whose severity/confidence/received_at were defaulted.

    The raw submitted record is the evidence: a field counts as defaulted when
    it is missing, null or blank there. Evaluated in SQL so no JSON is parsed
    in Python for complete records.
    """
    conditions = " OR ".join(
        f"json_extract(raw_record, '$.{name}') IS NULL "
        f"OR TRIM(CAST(json_extract(raw_record, '$.{name}') AS TEXT)) = ''"
        for name in DEFAULTED_FIELDS
    )
    selects = ", ".join(
        f"(json_extract(raw_record, '$.{name}') IS NULL "
        f"OR TRIM(CAST(json_extract(raw_record, '$.{name}') AS TEXT)) = '') AS missing_{name}"
        for name in DEFAULTED_FIELDS
    )
    rows = db.fetchall(
        f"""
        SELECT event_id, device_id, source_type, event_type, severity, confidence,
               received_at, {selects}
        FROM events
        WHERE validation_status = 'valid' AND json_valid(raw_record) AND ({conditions})
        ORDER BY occurred_epoch, event_id
        """
    )
    for row in rows:
        defaulted = {name: DEFAULTED_FIELDS[name] for name in DEFAULTED_FIELDS if row[f"missing_{name}"]}
        applied = {
            "severity": row["severity"],
            "confidence": row["confidence"],
            "received_at": row["received_at"],
        }
        described = ", ".join(
            f"{name} (used {applied[name]!r}" + (", copied from occurred_at)" if name == "received_at" else ")")
            for name in defaulted
        )
        collector.add(
            "incomplete_record",
            "medium" if "severity" in defaulted or "confidence" in defaulted else "low",
            (
                f"Event {row['event_id']} ({row['source_type']}/{row['event_type']}) was accepted without "
                f"{', '.join(defaulted)}. Defaults were applied so it can be analysed: {described}. "
                "Severity and confidence for this event are therefore assumed, not reported."
            ),
            "Fix the source mapping so these fields are supplied; treat the defaulted values as placeholders when prioritising.",
            event_id=row["event_id"],
            device_id=row["device_id"],
            details={
                "defaulted_fields": sorted(defaulted),
                "applied_defaults": {name: applied[name] for name in defaulted},
            },
        )


def _persist(db: Database, collector: _Collector) -> int:
    rows = [issue.to_row() for issue in collector.issues.values()]
    with db.transaction() as connection:
        connection.execute("DELETE FROM quality_issues")
        if rows:
            connection.executemany(
                """
                INSERT INTO quality_issues (
                    issue_id, issue_type, severity, explanation, event_id, device_id,
                    detected_at, suggested_action, details, status
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                rows,
            )
    return len(rows)


def run_quality_analysis(
    db: Database,
    settings: Settings,
    *,
    include_uncorrelated: bool = False,
) -> int:
    """Recompute all quality issues from persisted evidence.

    Returns the total number of issues after the optional uncorroborated-event
    pass. The base pass clears prior issues first so stale findings do not
    persist after data is corrected.
    """
    analysis_time = now_iso()
    collector = _Collector(analysis_time)
    events = _load_events(db)

    _duplicate_content_issues(events, collector)
    _rejection_issues(db, collector)
    _timing_issues(events, settings, collector)
    _confidence_issues(events, settings, collector)
    _conflict_issues(events, settings, collector)
    _conflicting_location_issues(events, settings, collector)
    _repetition_issues(db, settings, collector)
    _spike_issues(db, settings, collector)
    _health_issues(db, settings, collector)
    _offline_device_issues(events, settings, collector)
    _cross_type_conflict_issues(events, settings, collector)
    _incomplete_record_issues(db, collector)

    total = _persist(db, collector)
    if include_uncorrelated:
        total += add_uncorrelated_issues(db, settings)
    return total


def add_uncorrelated_issues(db: Database, settings: Settings) -> int:
    """Flag high/critical events that have no corroborating event.

    Correlation runs before this pass, so it can identify single-event
    (possible-association) incidents. Existing issues are preserved.
    """
    rows = db.fetchall(
        """
        SELECT i.incident_id, i.event_ids, i.severity, i.zones, i.sources
        FROM incidents i
        WHERE i.is_possible_association = 1
          AND i.severity IN ('high','critical')
        """
    )
    analysis_time = now_iso()
    issue_rows: list[tuple] = []
    for row in rows:
        try:
            event_ids = json.loads(row["event_ids"])
        except (TypeError, ValueError):
            continue
        if not event_ids:
            continue
        event_id = event_ids[0]
        details = {
            "incident_id": row["incident_id"],
            "zones": json.loads(row["zones"]) if row["zones"] else [],
            "sources": json.loads(row["sources"]) if row["sources"] else [],
            "window_minutes": settings.correlation_window_minutes,
        }
        issue = QualityIssueDraft(
            issue_id=_issue_id("uncorroborated_event", event_id, "", row["incident_id"]),
            issue_type="uncorroborated_event",
            severity="low",
            explanation=(
                f"Event {event_id} is high/critical severity but no other event in the same "
                f"zone/category occurred within {settings.correlation_window_minutes} minutes. "
                "The relationship remains unresolved; this does not mean the event is false."
            ),
            event_id=event_id,
            device_id=None,
            detected_at=analysis_time,
            suggested_action=(
                "Seek corroborating CCTV, sensor or operator evidence; if none exists, "
                "handle as a single-source detection."
            ),
            details=details,
        )
        issue_rows.append(issue.to_row())
    if not issue_rows:
        return 0
    with db.transaction() as connection:
        connection.executemany(
            """
            INSERT OR IGNORE INTO quality_issues (
                issue_id, issue_type, severity, explanation, event_id, device_id,
                detected_at, suggested_action, details, status
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            issue_rows,
        )
    return len(issue_rows)
