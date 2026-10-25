"""Read-only "operations center" situation snapshot.

Everything is computed with SQL aggregates so the endpoint stays cheap on
large datasets (50k events / 15k incidents). All time windows use EVENT time
(``occurred_at``); "now" is the latest valid event occurrence time in the
database (data time), never the wall clock.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from app.database import Database
from app.utils import ACTIVE_STATUS_SQL, parse_json, to_iso

WINDOW_MINUTES = 60
BUCKET_MINUTES = 15
MAX_BUCKETS = 96
INCIDENT_SERIES_LEN = 8
EVENT_SERIES_LEN = 16
PRIORITY_LIMIT = 6
FEED_LIMIT = 10

GROUPS: tuple[str, ...] = (
    "Cameras & ANPR",
    "Access control",
    "Perimeter & radar",
    "Alarms & reports",
    "Telemetry",
    "Other",
)
SOURCE_GROUPS: dict[str, str] = {
    "cctv": "Cameras & ANPR",
    "anpr": "Cameras & ANPR",
    "face_recognition": "Cameras & ANPR",
    "red_light": "Cameras & ANPR",
    "seatbelt_detection": "Cameras & ANPR",
    "mobile_phone_detection": "Cameras & ANPR",
    "access_control": "Access control",
    "perimeter": "Perimeter & radar",
    "radar": "Perimeter & radar",
    "emergency_alarm": "Alarms & reports",
    "operator_report": "Alarms & reports",
    "gps": "Telemetry",
    "weather": "Telemetry",
    "network_log": "Telemetry",
    "device_health": "Telemetry",
}

# SQL CASE expression mapping events.source_type to its display group.
_GROUP_CASE = (
    "CASE e.source_type "
    + " ".join(f"WHEN '{source}' THEN '{group}'" for source, group in SOURCE_GROUPS.items())
    + " ELSE 'Other' END"
)
# Quality issue types grouped into the families the Data trust page uses.
QUALITY_FAMILIES: dict[str, tuple[str, ...]] = {
    "Contradictions": ("conflicting_sensors", "conflicting_location", "comparison_limit_reached"),
    "Record integrity": ("missing_mandatory_field", "invalid_record", "duplicate_event_id",
                         "duplicate_content", "incomplete_record"),
    "Timing": ("delayed_event", "out_of_order_arrival", "conflicting_timestamps"),
    "Corroboration": ("uncorroborated_event", "low_confidence", "repeated_events", "event_spike"),
    "Devices": ("unhealthy_device", "offline_device"),
}
_FAMILY_CASE = (
    "CASE q.issue_type "
    + " ".join(f"WHEN '{t}' THEN '{fam}'" for fam, types in QUALITY_FAMILIES.items() for t in types)
    + " ELSE 'Other' END"
)
_SEVERITY_RANK = (
    "CASE {col} WHEN 'critical' THEN 4 WHEN 'high' THEN 3 "
    "WHEN 'medium' THEN 2 WHEN 'low' THEN 1 ELSE 0 END"
)
_EFFECTIVE_STATUS = "COALESCE(s.status, i.status)"
_INCIDENTS_FROM = "incidents i LEFT JOIN incident_status s ON s.incident_id = i.incident_id"
_ACTIVE = f"{_EFFECTIVE_STATUS} IN ({ACTIVE_STATUS_SQL})"

FEED_ISSUE_KINDS: dict[str, str] = {
    "conflicting_sensors": "conflict",
    "conflicting_location": "conflict",
    "conflicting_timestamps": "conflict",
    "event_spike": "spike",
    "unhealthy_device": "device",
    "offline_device": "offline",
    "delayed_event": "delay",
}
# Quality issue types that flag a specific sensor/device.
SENSOR_FLAG_TYPES: tuple[str, ...] = (
    "unhealthy_device",
    "offline_device",
    "conflicting_sensors",
    "conflicting_location",
    "conflicting_timestamps",
    "delayed_event",
    "low_confidence",
    "repeated_events",
    "out_of_order_arrival",
)

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _in_list(values: tuple[str, ...] | list[str]) -> str:
    return ",".join(f"'{value}'" for value in values)


def _first_sentence(text: str | None, limit: int = 140) -> str:
    text = (text or "").strip()
    sentence = _SENTENCE_END.split(text, maxsplit=1)[0] if text else ""
    if len(sentence) > limit:
        sentence = sentence[: limit - 1].rstrip() + "…"
    return sentence


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


def _window_index_sql(column: str, width_seconds: int) -> str:
    """Window index: 0 = [now-W, now] (anything at/after now), 1 = [now-2W, now-W), ..."""
    return (
        f"CASE WHEN {column} >= :now THEN 0 "
        f"ELSE (:now - {column} - 1) / {width_seconds} END"
    )


def _series(rows: list[dict[str, Any]], length: int, key: str = "n") -> list[int]:
    """Convert (idx, n) rows (0 = newest) into an oldest->newest list."""
    newest_first = [0] * length
    for row in rows:
        idx = row["idx"]
        if idx is not None and 0 <= idx < length:
            newest_first[idx] = int(row[key] or 0)
    return list(reversed(newest_first))


def _serialize_priority(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "incident_id": row["incident_id"],
        "title": row["title"],
        "category": row["category"],
        "severity": row["severity"],
        "confidence": row["confidence"],
        "status": row["effective_status"],
        "assigned_to": row.get("assigned_to"),
        "created_at": row["created_at"],
        "event_count": row["event_count"],
        "sources": parse_json(row["sources"], []) or [],
        "zones": parse_json(row["zones"], []) or [],
        "devices": parse_json(row["devices"], []) or [],
    }

# ------------------------------------------------------------------ routes
PATTERN_WINDOW_MINUTES = 60
ROUTE_CAP = 30
ENTITY_KEYS: tuple[str, ...] = ("plate", "badge", "subject")
_ENTITY_PLACEHOLDERS: tuple[str, ...] = ("unknown", "none", "null", "n/a", "-")
_RANK_TO_SEV = {4: "critical", 3: "high", 2: "medium", 1: "low"}
_SEV_TO_RANK = {sev: rank for rank, sev in _RANK_TO_SEV.items()}


def _max_severity(*values: str | None) -> str | None:
    best = max((_SEV_TO_RANK.get(v or "", 0) for v in values), default=0)
    return _RANK_TO_SEV.get(best, values[0] if values else None)


def _pattern_routes(conn: Any) -> list[dict[str, Any]]:
    """Consecutive same-category open incidents in different zones within 60 min.

    A deterministic INFERENCE ("possible pattern"), never a confirmed link. Each
    category is ordered by creation time and only consecutive pairs are compared,
    so n incidents yield at most n-1 links rather than n^2.
    """
    sql = f"""
        WITH base AS (
            SELECT i.incident_id, i.category, i.severity, i.created_at, i.created_epoch,
                   i.event_count, json_extract(i.zones, '$[0]') AS zone
            FROM {_INCIDENTS_FROM}
            WHERE {_ACTIVE} AND i.is_possible_association = 0 AND json_valid(i.zones)
              AND json_extract(i.zones, '$[0]') IS NOT NULL
        ), seq AS (
            SELECT *,
                   LAG(incident_id) OVER w AS prev_id,
                   LAG(zone) OVER w AS prev_zone,
                   LAG(severity) OVER w AS prev_severity,
                   LAG(created_epoch) OVER w AS prev_epoch,
                   LAG(event_count) OVER w AS prev_events
            FROM base
            WINDOW w AS (PARTITION BY category ORDER BY created_epoch, incident_id)
        )
        SELECT * FROM seq
        WHERE prev_id IS NOT NULL AND zone <> prev_zone
          AND created_epoch - prev_epoch <= {PATTERN_WINDOW_MINUTES * 60}
        ORDER BY created_epoch DESC, incident_id LIMIT {ROUTE_CAP}
    """
    routes = []
    for row in conn.execute(sql).fetchall():
        minutes = round((row["created_epoch"] - row["prev_epoch"]) / 60)
        routes.append(
            {
                "kind": "pattern",
                "from_zone": row["prev_zone"],
                "to_zone": row["zone"],
                "severity": _max_severity(row["prev_severity"], row["severity"]),
                "label": f"Possible {row['category']} pattern · {minutes} min apart",
                "refs": [row["prev_id"], row["incident_id"]],
                "at": row["created_at"],
                # internal helpers for the campaign cluster; stripped from the response
                "_category": row["category"],
                "_events": {row["prev_id"]: int(row["prev_events"] or 0),
                            row["incident_id"]: int(row["event_count"] or 0)},
            }
        )
    return routes


def _location_conflict_routes(conn: Any) -> list[dict[str, Any]]:
    sql = f"""
        SELECT q.issue_id, q.severity, q.device_id, q.detected_at,
               json_extract(q.details, '$.first_zone') AS first_zone,
               json_extract(q.details, '$.second_zone') AS second_zone,
               e.occurred_at AS occurred_at
        FROM quality_issues q LEFT JOIN events e ON e.event_id = q.event_id
        WHERE q.issue_type = 'conflicting_location' AND json_valid(q.details)
          AND json_extract(q.details, '$.first_zone') IS NOT NULL
          AND json_extract(q.details, '$.second_zone') IS NOT NULL
          AND json_extract(q.details, '$.first_zone') <> json_extract(q.details, '$.second_zone')
        ORDER BY COALESCE(e.occurred_at, q.detected_at) DESC, q.issue_id LIMIT {ROUTE_CAP}
    """
    return [
        {
            "kind": "location_conflict",
            "from_zone": row["first_zone"],
            "to_zone": row["second_zone"],
            "severity": row["severity"],
            "label": f"Location jump · {row['device_id'] or 'unknown device'}",
            "refs": [row["issue_id"]],
            "at": row["occurred_at"] or row["detected_at"],
        }
        for row in conn.execute(sql).fetchall()
    ]


def _entity_routes(conn: Any) -> list[dict[str, Any]]:
    """Same payload entity (plate/badge/subject) seen in consecutive different zones."""
    union = " UNION ALL ".join(
        f"SELECT '{key}' AS ekey, CAST(json_extract(payload, '$.{key}') AS TEXT) AS evalue, "
        "event_id, zone, severity, occurred_at, occurred_epoch FROM events "
        "WHERE validation_status = 'valid' AND zone IS NOT NULL AND zone <> '' "
        f"AND json_valid(payload) AND json_extract(payload, '$.{key}') IS NOT NULL"
        for key in ENTITY_KEYS
    )
    sql = f"""
        WITH obs AS (
            SELECT * FROM ({union})
            WHERE TRIM(evalue) <> ''
              AND LOWER(TRIM(evalue)) NOT IN ({_in_list(_ENTITY_PLACEHOLDERS)})
        ), multi AS (
            SELECT ekey, evalue, COUNT(DISTINCT zone) AS nzones FROM obs
            GROUP BY ekey, evalue HAVING COUNT(DISTINCT zone) >= 2
        ), seq AS (
            SELECT o.*, m.nzones,
                   LAG(o.event_id) OVER w AS prev_id,
                   LAG(o.zone) OVER w AS prev_zone,
                   LAG(o.severity) OVER w AS prev_severity
            FROM obs o JOIN multi m ON m.ekey = o.ekey AND m.evalue = o.evalue
            WINDOW w AS (PARTITION BY o.ekey, o.evalue ORDER BY o.occurred_epoch, o.event_id)
        )
        SELECT * FROM seq WHERE prev_id IS NOT NULL AND zone <> prev_zone
        ORDER BY occurred_epoch DESC, event_id LIMIT {ROUTE_CAP}
    """
    return [
        {
            "kind": "entity",
            "from_zone": row["prev_zone"],
            "to_zone": row["zone"],
            "severity": _max_severity(row["prev_severity"], row["severity"]),
            "label": f"Same {row['ekey']} {row['evalue']} seen in {row['nzones']} zones",
            "refs": [row["prev_id"], row["event_id"]],
            "at": row["occurred_at"],
        }
        for row in conn.execute(sql).fetchall()
    ]


def _campaign(pattern_routes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Largest connected cluster of pattern links (by zones, then events)."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for route in pattern_routes:
        a, b = route["refs"]
        parent[find(a)] = find(b)
    clusters: dict[str, list[dict[str, Any]]] = {}
    for route in pattern_routes:
        clusters.setdefault(find(route["refs"][0]), []).append(route)
    best: dict[str, Any] | None = None
    best_key: tuple | None = None
    for routes in clusters.values():
        routes.sort(key=lambda r: r["at"] or "")
        incidents: list[str] = []
        zones: list[str] = []
        events: dict[str, int] = {}
        for route in routes:
            for ref in route["refs"]:
                if ref not in incidents:
                    incidents.append(ref)
            for zone in (route["from_zone"], route["to_zone"]):
                if zone not in zones:
                    zones.append(zone)
            events.update(route["_events"])
        total = sum(events.values())
        key = (len(zones), total, routes[-1]["at"] or "")
        if best_key is None or key > best_key:
            best_key = key
            category = routes[0]["_category"]
            best = {
                "label": f"Possible {category} pattern",
                "category": category,
                "zones": zones,
                "incidents": incidents,
                "events": total,
                "severity": _max_severity(*(r["severity"] for r in routes)),
            }
    return best


def build_routes(conn: Any) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Cross-zone links (pattern inference, location conflicts, entity hops) + campaign."""
    patterns = _pattern_routes(conn)
    campaign = _campaign(patterns)
    routes = patterns + _location_conflict_routes(conn) + _entity_routes(conn)
    routes.sort(key=lambda r: (r["at"] or "", r["kind"]), reverse=True)
    clean = [{k: v for k, v in route.items() if not k.startswith("_")} for route in routes]
    return clean, campaign


def build_situation(db: Database) -> dict[str, Any]:
    with db.read() as conn:
        def all_rows(sql: str, params: Any = ()) -> list[dict[str, Any]]:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

        def one(sql: str, params: Any = ()) -> dict[str, Any]:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else {}

        # ------------------------------------------------------------ basics
        event_counts = one(
            "SELECT "
            "SUM(validation_status = 'valid') AS valid, "
            "SUM(validation_status = 'duplicate') AS duplicates, "
            "COUNT(*) AS stored, "
            "MAX(CASE WHEN validation_status = 'valid' THEN occurred_epoch END) AS max_epoch, "
            "MIN(CASE WHEN validation_status = 'valid' THEN occurred_epoch END) AS min_epoch "
            "FROM events"
        )
        valid_events = int(event_counts.get("valid") or 0)
        duplicates = int(event_counts.get("duplicates") or 0)
        rejected = int(conn.execute("SELECT COUNT(*) FROM rejected_events").fetchone()[0])
        received = valid_events + duplicates + rejected
        now_epoch = event_counts.get("max_epoch")
        min_epoch = event_counts.get("min_epoch")
        latest_occurred_at = None
        if now_epoch is not None:
            latest_occurred_at = conn.execute(
                "SELECT MAX(occurred_at) FROM events "
                "WHERE validation_status = 'valid' AND occurred_epoch = ?",
                (now_epoch,),
            ).fetchone()[0]

        # --------------------------------------------------------- incidents
        sev_rows = all_rows(
            f"SELECT i.severity AS severity, COUNT(*) AS n FROM {_INCIDENTS_FROM} "
            f"WHERE {_ACTIVE} GROUP BY i.severity"
        )
        by_severity = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for row in sev_rows:
            if row["severity"] in by_severity:
                by_severity[row["severity"]] = int(row["n"])
        open_incidents = sum(int(row["n"]) for row in sev_rows)
        watchlist_count = int(
            conn.execute(
                f"SELECT COUNT(*) FROM {_INCIDENTS_FROM} "
                f"WHERE {_ACTIVE} AND i.is_possible_association = 1"
            ).fetchone()[0]
        )
        critical_corroborated = int(
            conn.execute(
                f"SELECT COUNT(*) FROM {_INCIDENTS_FROM} WHERE {_ACTIVE} "
                "AND i.severity = 'critical' AND i.is_possible_association = 0 "
                "AND json_array_length(i.sources) >= 2"
            ).fetchone()[0]
        )

        # ----------------------------------------------------- quality issues
        quality = one(
            "SELECT COUNT(*) AS total, "
            "SUM(issue_type LIKE 'conflicting_%') AS conflicts, "
            "SUM(issue_type = 'uncorroborated_event') AS uncorroborated, "
            "SUM(issue_type = 'event_spike') AS spikes "
            "FROM quality_issues"
        )
        unhealthy_devices = int(
            conn.execute(
                "SELECT COUNT(DISTINCT device_id) FROM quality_issues "
                "WHERE issue_type IN ('unhealthy_device','offline_device') "
                "AND device_id IS NOT NULL"
            ).fetchone()[0]
        )
        conflicts = int(quality.get("conflicts") or 0)

        # ------------------------------------------------ trend windows/series
        hour = WINDOW_MINUTES * 60
        quarter = BUCKET_MINUTES * 60
        incidents_series = [0] * INCIDENT_SERIES_LEN
        crit_high_series = [0] * INCIDENT_SERIES_LEN
        events_series = [0] * EVENT_SERIES_LEN
        events_last = events_prev = 0
        if now_epoch is not None:
            params = {"now": now_epoch, "lo": now_epoch - INCIDENT_SERIES_LEN * hour}
            inc_rows = all_rows(
                f"SELECT {_window_index_sql('created_epoch', hour)} AS idx, COUNT(*) AS n, "
                "SUM(severity IN ('critical','high')) AS ch "
                "FROM incidents WHERE created_epoch >= :lo GROUP BY idx",
                params,
            )
            incidents_series = _series(inc_rows, INCIDENT_SERIES_LEN)
            crit_high_series = _series(inc_rows, INCIDENT_SERIES_LEN, key="ch")
            ev_params = {"now": now_epoch, "lo": now_epoch - EVENT_SERIES_LEN * quarter}
            ev_rows = all_rows(
                f"SELECT {_window_index_sql('occurred_epoch', quarter)} AS idx, COUNT(*) AS n "
                "FROM events WHERE validation_status = 'valid' "
                "AND occurred_epoch >= :lo AND occurred_epoch <= :now GROUP BY idx",
                ev_params,
            )
            events_series = _series(ev_rows, EVENT_SERIES_LEN)
            hourly_events = all_rows(
                f"SELECT {_window_index_sql('occurred_epoch', hour)} AS idx, COUNT(*) AS n "
                "FROM events WHERE validation_status = 'valid' "
                "AND occurred_epoch >= :lo AND occurred_epoch <= :now GROUP BY idx",
                {"now": now_epoch, "lo": now_epoch - 2 * hour},
            )
            ev_two = _series(hourly_events, 2)
            events_prev, events_last = ev_two[0], ev_two[1]

        kpis = {
            "valid_events": valid_events,
            "received": received,
            "rejected": rejected,
            "duplicates": duplicates,
            "open_incidents": open_incidents,
            "by_severity": by_severity,
            "quality_issues": int(quality.get("total") or 0),
            "conflicts": conflicts,
            "uncorroborated": int(quality.get("uncorroborated") or 0),
            "spikes": int(quality.get("spikes") or 0),
            "unhealthy_devices": unhealthy_devices,
            "window_minutes": WINDOW_MINUTES,
            "incidents_last": incidents_series[-1],
            "incidents_prev": incidents_series[-2],
            "crit_high_last": crit_high_series[-1],
            "crit_high_prev": crit_high_series[-2],
            "events_last": events_last,
            "events_prev": events_prev,
            "incidents_series": incidents_series,
            "crit_high_series": crit_high_series,
            "events_series": events_series,
        }

        # -------------------------------------------------------------- zones
        zone_map: dict[str, dict[str, Any]] = {}
        for row in all_rows(
            "SELECT zone, COUNT(*) AS events, AVG(latitude) AS lat, AVG(longitude) AS lon "
            "FROM events WHERE validation_status = 'valid' AND zone IS NOT NULL AND zone <> '' "
            "GROUP BY zone"
        ):
            zone_map[row["zone"]] = {
                "zone": row["zone"],
                "latitude": row["lat"],
                "longitude": row["lon"],
                "events": int(row["events"]),
                "incidents": 0,
                "worst_severity": None,
                "critical": 0,
                "high": 0,
            }
        rank_to_sev = {4: "critical", 3: "high", 2: "medium", 1: "low"}
        for row in all_rows(
            f"SELECT j.value AS zone, COUNT(*) AS n, "
            f"MAX({_SEVERITY_RANK.format(col='i.severity')}) AS worst, "
            "SUM(i.severity = 'critical') AS critical, SUM(i.severity = 'high') AS high "
            f"FROM {_INCIDENTS_FROM}, json_each(i.zones) j "
            f"WHERE {_ACTIVE} AND json_valid(i.zones) GROUP BY j.value"
        ):
            name = row["zone"]
            if not isinstance(name, str) or not name:
                continue
            entry = zone_map.setdefault(
                name,
                {"zone": name, "latitude": None, "longitude": None, "events": 0,
                 "incidents": 0, "worst_severity": None, "critical": 0, "high": 0},
            )
            entry["incidents"] = int(row["n"])
            entry["worst_severity"] = rank_to_sev.get(int(row["worst"] or 0))
            entry["critical"] = int(row["critical"] or 0)
            entry["high"] = int(row["high"] or 0)
        zones = sorted(zone_map.values(), key=lambda z: (-z["events"], -z["incidents"], z["zone"]))

        # ----------------------------------------------------------- activity
        buckets: list[dict[str, Any]] = []
        if now_epoch is not None:
            last_idx = now_epoch // quarter
            first_idx = max(min_epoch // quarter, last_idx - MAX_BUCKETS + 1)
            counts_by_idx: dict[int, dict[str, int]] = {
                idx: {group: 0 for group in GROUPS} for idx in range(first_idx, last_idx + 1)
            }
            for row in all_rows(
                f"SELECT e.occurred_epoch / {quarter} AS idx, {_GROUP_CASE} AS grp, COUNT(*) AS n "
                "FROM events e WHERE e.validation_status = 'valid' "
                "AND e.occurred_epoch >= ? AND e.occurred_epoch <= ? GROUP BY idx, grp",
                (first_idx * quarter, now_epoch),
            ):
                if row["idx"] in counts_by_idx:
                    counts_by_idx[row["idx"]][row["grp"]] = int(row["n"])
            buckets = [
                {
                    "start": to_iso(datetime.fromtimestamp(idx * quarter, tz=timezone.utc)),
                    "counts": counts_by_idx[idx],
                }
                for idx in range(first_idx, last_idx + 1)
            ]
        activity = {"bucket_minutes": BUCKET_MINUTES, "groups": list(GROUPS), "buckets": buckets}

        # ----------------------------------------------------- quality trend
        # Findings placed on the same event-time buckets via the flagged event's
        # occurrence time; findings with no event (e.g. zone spikes) are counted as untimed.
        q_buckets: list[dict[str, Any]] = []
        untimed = int(one("SELECT COUNT(*) AS n FROM quality_issues q LEFT JOIN events e ON e.event_id = q.event_id "
                          "WHERE e.occurred_epoch IS NULL")["n"] or 0)
        if buckets:
            q_counts: dict[int, dict[str, int]] = {
                idx: {family: 0 for family in QUALITY_FAMILIES} for idx in range(first_idx, last_idx + 1)
            }
            for row in all_rows(
                f"SELECT e.occurred_epoch / {quarter} AS idx, {_FAMILY_CASE} AS fam, COUNT(*) AS n "
                "FROM quality_issues q JOIN events e ON e.event_id = q.event_id "
                "WHERE e.occurred_epoch >= ? AND e.occurred_epoch <= ? GROUP BY idx, fam",
                (first_idx * quarter, now_epoch),
            ):
                if row["idx"] in q_counts:
                    q_counts[row["idx"]][row["fam"]] = q_counts[row["idx"]].get(row["fam"], 0) + int(row["n"])
            q_buckets = [{"start": b["start"], "counts": q_counts[idx]}
                         for b, idx in zip(buckets, range(first_idx, last_idx + 1))]
        quality_trend = {"bucket_minutes": BUCKET_MINUTES, "families": list(QUALITY_FAMILIES),
                         "buckets": q_buckets, "untimed": untimed}

        # ------------------------------------------------------------ sensors
        # Distinct (device, source) pairs are few even on large datasets, so the
        # device->group mapping is resolved once and reused for both counts.
        device_groups: dict[str, set[str]] = {}
        for row in all_rows(
            "SELECT DISTINCT device_id, source_type FROM events WHERE validation_status = 'valid'"
        ):
            group = SOURCE_GROUPS.get(row["source_type"], "Other")
            device_groups.setdefault(group, set()).add(row["device_id"])
        device_counts = {group: len(devices) for group, devices in device_groups.items()}
        flagged: dict[str, dict[str, Any]] = {}
        for row in all_rows(
            "SELECT device_id, issue_type, explanation, issue_id FROM quality_issues "
            f"WHERE issue_type IN ({_in_list(SENSOR_FLAG_TYPES)}) AND device_id IS NOT NULL "
            "ORDER BY CASE issue_type WHEN 'offline_device' THEN 0 "
            "WHEN 'unhealthy_device' THEN 1 ELSE 2 END, issue_id"
        ):
            for group in GROUPS:
                if row["device_id"] not in device_groups.get(group, ()):
                    continue
                entry = flagged.setdefault(group, {"devices": [], "notes": []})
                if row["device_id"] not in entry["devices"]:
                    entry["devices"].append(row["device_id"])
                note = _first_sentence(row["explanation"])
                if note and note not in entry["notes"] and len(entry["notes"]) < 3:
                    entry["notes"].append(note)
        sensors = []
        for group in GROUPS:
            devices = device_counts.get(group, 0)
            if group == "Other" and devices == 0:
                continue
            entry = flagged.get(group, {"devices": [], "notes": []})
            sensors.append(
                {
                    "group": group,
                    "devices": devices,
                    "flagged": len(entry["devices"]),
                    "flagged_devices": sorted(entry["devices"]),
                    "notes": entry["notes"],
                }
            )

        # ----------------------------------------------------------- priority
        priority_rows = all_rows(
            f"SELECT i.*, {_EFFECTIVE_STATUS} AS effective_status, s.assigned_to AS assigned_to "
            f"FROM {_INCIDENTS_FROM} WHERE {_ACTIVE} AND i.is_possible_association = 0 "
            f"ORDER BY {_SEVERITY_RANK.format(col='i.severity')} DESC, i.confidence DESC, "
            f"i.created_epoch DESC, i.incident_id LIMIT {PRIORITY_LIMIT}"
        )
        priority = [_serialize_priority(row) for row in priority_rows]

        # --------------------------------------------------------------- feed
        feed: list[dict[str, Any]] = []
        for row in priority_rows:
            sources = parse_json(row["sources"], []) or []
            event_ids = parse_json(row["event_ids"], []) or []
            feed.append(
                {
                    "at": row["created_at"],
                    "kind": "linked",
                    "severity": row["severity"],
                    "text": (
                        f"Linked {' + '.join(sources) or 'events'} into "
                        f"{row['incident_id'][:10]} (conf {float(row['confidence']):.2f})"
                    ),
                    "ref": row["incident_id"],
                    "event_id": event_ids[0] if event_ids else None,
                }
            )
        issue_rows = all_rows(
            "SELECT q.issue_id, q.issue_type, q.severity, q.explanation, q.event_id, "
            "COALESCE(e.occurred_at, "
            "CASE WHEN json_valid(q.details) THEN COALESCE("
            "json_extract(q.details, '$.occurred_at'), "
            "json_extract(q.details, '$.last_seen'), "
            "json_extract(q.details, '$.last_event_at')) END, "
            "q.detected_at) AS at "
            "FROM quality_issues q LEFT JOIN events e ON e.event_id = q.event_id "
            f"WHERE q.issue_type IN ({_in_list(tuple(FEED_ISSUE_KINDS))}) "
            f"ORDER BY at DESC, q.issue_id LIMIT {FEED_LIMIT}"
        )
        for row in issue_rows:
            feed.append(
                {
                    "at": row["at"],
                    "kind": FEED_ISSUE_KINDS[row["issue_type"]],
                    "severity": row["severity"],
                    "text": _first_sentence(row["explanation"]),
                    "ref": row["issue_id"],
                    "event_id": row["event_id"],
                }
            )
        feed.sort(key=lambda item: (item["at"] or "", item["ref"]), reverse=True)
        feed = feed[:FEED_LIMIT]

        # ------------------------------------------------------------- routes
        routes, campaign = build_routes(conn)

    # --------------------------------------------------------------- brief
    if open_incidents == 0:
        headline = f"No open incidents. {_plural(valid_events, 'event')} analysed."
    else:
        parts = []
        critical = by_severity["critical"]
        if critical_corroborated:
            verb = "is" if critical_corroborated == 1 else "are"
            parts.append(
                f"{_plural(critical_corroborated, 'critical incident')} {verb} "
                "corroborated by independent sources."
            )
            if critical > critical_corroborated:
                parts.append(f"{critical - critical_corroborated} more critical awaiting corroboration.")
        elif critical:
            verb = "is" if critical == 1 else "are"
            parts.append(f"{_plural(critical, 'critical incident')} {verb} open.")
        else:
            verb = "is" if open_incidents == 1 else "are"
            parts.append(f"{_plural(open_incidents, 'open incident')} {verb} active; none critical.")
        if conflicts:
            verb = "needs" if conflicts == 1 else "need"
            parts.append(f"{_plural(conflicts, 'sensor conflict')} {verb} a human check.")
        if watchlist_count:
            verb = "is" if watchlist_count == 1 else "are"
            parts.append(
                f"{_plural(watchlist_count, 'possible association')} {verb} on the watchlist."
            )
        headline = " ".join(parts)
    chips = []
    if by_severity["critical"]:
        chips.append({"label": f"{by_severity['critical']} critical", "tone": "critical"})
    if conflicts:
        chips.append({"label": _plural(conflicts, "conflict"), "tone": "warning"})
    if watchlist_count:
        chips.append({"label": f"{watchlist_count} on watchlist", "tone": "info"})
    if unhealthy_devices:
        chips.append({"label": f"{unhealthy_devices} device flagged", "tone": "warning"})

    return {
        "latest_occurred_at": latest_occurred_at,
        "kpis": kpis,
        "zones": zones,
        "activity": activity,
        "quality_trend": quality_trend,
        "sensors": sensors,
        "priority": priority,
        "watchlist_count": watchlist_count,
        "feed": feed,
        "brief": {"headline": headline, "chips": chips},
        "routes": routes,
        "campaign": campaign,
    }
