"""Deterministic, evidence-backed operational briefing generation.

No external AI service is required. Every statement is assembled from database
rows and carries incident/event/quality-issue references where available.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from app.config import Settings
from app.database import Database
from app.utils import ACTIVE_STATUS_SQL, now_iso, parse_json, severity_rank

SEVERITY_SQL = (
    "CASE severity WHEN 'critical' THEN 4 WHEN 'high' THEN 3 "
    "WHEN 'medium' THEN 2 ELSE 1 END"
)


def _parse_list(value: Any) -> list:
    parsed = parse_json(value, [])
    return parsed if isinstance(parsed, list) else []


def _incident_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "incident_id": row["incident_id"],
        "title": row["title"],
        "category": row["category"],
        "severity": row["severity"],
        "confidence": row["confidence"],
        "status": row["status"],
        "created_at": row["created_at"],
        "event_count": row["event_count"],
        "event_ids": _parse_list(row["event_ids"]),
        "zones": _parse_list(row["zones"]),
        "sources": _parse_list(row["sources"]),
        "explanation": row["explanation"],
        "missing_info": _parse_list(row["missing_info"]),
        "recommended_actions": _parse_list(row["recommended_actions"]),
        "is_possible_association": bool(row["is_possible_association"]),
    }


def _issue_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "issue_id": row["issue_id"],
        "issue_type": row["issue_type"],
        "severity": row["severity"],
        "explanation": row["explanation"],
        "event_id": row["event_id"],
        "device_id": row["device_id"],
        "suggested_action": row["suggested_action"],
        "details": parse_json(row["details"], {}) or {},
    }


def generate_briefing(db: Database, settings: Settings) -> dict[str, Any]:
    reference = db.fetchone(
        "SELECT MAX(received_epoch) AS latest_received, MAX(occurred_epoch) AS latest_occurred, "
        "MIN(occurred_epoch) AS earliest_occurred, COUNT(*) AS event_count FROM events"
    ) or {}
    stats = db.stats()
    generated_at = now_iso()
    latest_received = reference.get("latest_received")
    if latest_received:
        reference_time = datetime.fromtimestamp(latest_received, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    else:
        reference_time = generated_at
    reference_epoch = latest_received or int(datetime.now(timezone.utc).timestamp())
    window_start = reference_epoch - settings.briefing_window_hours * 3600

    top_rows = db.fetchall(
        f"""
        SELECT * FROM incidents
        ORDER BY {SEVERITY_SQL} DESC, confidence DESC, created_epoch DESC
        LIMIT ?
        """,
        (settings.briefing_top_incidents,),
    )
    unresolved_rows = db.fetchall(
        f"""
        SELECT * FROM incidents WHERE status IN ({ACTIVE_STATUS_SQL})
        ORDER BY {SEVERITY_SQL} DESC, confidence DESC, created_epoch DESC
        LIMIT 10
        """
    )
    new_rows = db.fetchall(
        f"""
        SELECT * FROM incidents WHERE created_epoch >= ?
        ORDER BY {SEVERITY_SQL} DESC, created_epoch DESC LIMIT 10
        """,
        (window_start,),
    )
    unresolved_count = db.scalar(
        f"SELECT COUNT(*) FROM incidents WHERE status IN ({ACTIVE_STATUS_SQL})", default=0
    )
    resolved_count = db.scalar(
        "SELECT COUNT(*) FROM incidents WHERE status IN ('resolved','closed')", default=0
    )
    false_positive_count = db.scalar(
        "SELECT COUNT(*) FROM incidents WHERE status = 'false_positive'", default=0
    )
    possible_count = db.scalar(
        "SELECT COUNT(*) FROM incidents WHERE is_possible_association = 1", default=0
    )
    avg_open_confidence = db.scalar(
        f"SELECT AVG(confidence) FROM incidents WHERE status IN ({ACTIVE_STATUS_SQL})",
        default=None,
    )

    repeated = [
        _issue_row(row)
        for row in db.fetchall(
            "SELECT * FROM quality_issues WHERE issue_type='repeated_events' "
            f"ORDER BY {SEVERITY_SQL} DESC LIMIT 5"
        )
    ]
    spikes = [
        _issue_row(row)
        for row in db.fetchall(
            "SELECT * FROM quality_issues WHERE issue_type='event_spike' "
            f"ORDER BY {SEVERITY_SQL} DESC LIMIT 5"
        )
    ]
    health = [
        _issue_row(row)
        for row in db.fetchall(
            "SELECT * FROM quality_issues WHERE issue_type IN "
            "('unhealthy_device','offline_device') "
            f"ORDER BY {SEVERITY_SQL} DESC LIMIT 5"
        )
    ]
    quality_by_type = [
        dict(row)
        for row in db.fetchall(
            "SELECT issue_type, COUNT(*) AS count, "
        "CASE MAX(CASE severity WHEN 'critical' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 WHEN 'low' THEN 1 ELSE 0 END) WHEN 4 THEN 'critical' WHEN 3 THEN 'high' WHEN 2 THEN 'medium' WHEN 1 THEN 'low' ELSE 'unknown' END AS max_severity "
            "FROM quality_issues GROUP BY issue_type ORDER BY count DESC"
        )
    ]
    low_confidence_count = db.scalar(
        "SELECT COUNT(*) FROM quality_issues WHERE issue_type='low_confidence'", default=0
    )
    uncorroborated_count = db.scalar(
        "SELECT COUNT(*) FROM quality_issues WHERE issue_type='uncorroborated_event'", default=0
    )
    contradiction_count = db.scalar(
        "SELECT COUNT(*) FROM quality_issues WHERE issue_type IN "
        "('conflicting_sensors','conflicting_location','conflicting_timestamps')",
        default=0,
    )

    top_incidents = [_incident_row(row) for row in top_rows]
    unresolved = [_incident_row(row) for row in unresolved_rows]
    newly_detected = [_incident_row(row) for row in new_rows]

    recommended_management_actions: list[dict[str, Any]] = []
    seen_actions: set[str] = set()

    def add_action(action: str, reason: str, **refs: Any) -> None:
        if action in seen_actions:
            return
        seen_actions.add(action)
        recommended_management_actions.append(
            {"action": action, "reason": reason, **{key: value for key, value in refs.items() if value}}
        )

    for incident in unresolved:
        if severity_rank(incident["severity"]) >= 3:
            for action in incident["recommended_actions"][:2]:
                add_action(
                    action,
                    f"Unresolved {incident['severity']} incident {incident['incident_id']}",
                    incident_id=incident["incident_id"],
                )
    for issue in health[:3]:
        add_action(
            issue["suggested_action"],
            f"Device-health finding {issue['issue_id']}"
            + (f" on {issue['device_id']}" if issue["device_id"] else ""),
            issue_id=issue["issue_id"],
        )
    for issue in spikes[:2]:
        add_action(
            issue["suggested_action"],
            f"Event spike {issue['issue_id']}",
            issue_id=issue["issue_id"],
        )
    for issue in repeated[:2]:
        add_action(
            issue["suggested_action"],
            f"Repeated events {issue['issue_id']}",
            issue_id=issue["issue_id"],
        )
    if possible_count:
        add_action(
            "Seek corroborating evidence for single-source/possible-association incidents before escalation.",
            f"{possible_count} incident(s) are represented as possible associations.",
        )
    if not recommended_management_actions:
        recommended_management_actions.append(
            {
                "action": "Maintain routine monitoring; no unresolved high-priority findings require management action.",
                "reason": "No open high/critical incidents or device-health findings are present in the stored data.",
            }
        )
    recommended_management_actions = recommended_management_actions[:10]

    if not reference.get("event_count"):
        summary_line = (
            "No events have been ingested yet. The API and database are available, but there is "
            "no operational evidence to brief. Load the sample dataset to begin the demonstration."
        )
    else:
        summary_line = (
            f"{stats['valid_events']} valid events are stored across "
            f"{stats['incidents']} correlated incident(s). {unresolved_count} incident(s) remain "
            f"open or under investigation, including {stats['high_severity_incidents']} at high or "
            f"critical severity. {stats['quality_issues']} data-quality finding(s) and "
            f"{stats['unhealthy_devices']} unhealthy device(s) are recorded."
        )

    contradiction_statement = (
        "Confidence values are deterministic aggregations of source confidence with a penalty for "
        "documented contradictions and single-source associations. They are not probabilities that "
        "an event is malicious."
    )
    if avg_open_confidence is not None:
        contradiction_statement += (
            f" The mean confidence of open/investigating incidents is {avg_open_confidence:.2f}."
        )
    if low_confidence_count or uncorroborated_count or contradiction_count:
        contradiction_statement += (
            f" Uncertainty is explicit: {low_confidence_count} low-confidence detection(s), "
            f"{uncorroborated_count} uncorroborated high-severity event(s), and "
            f"{contradiction_count} conflicting/clock finding(s) are recorded."
        )
    else:
        contradiction_statement += " No low-confidence, uncorroborated or conflicting findings are currently recorded."

    sections = {
        "summary": {
            "text": summary_line,
            "stats": stats,
            "unresolved_incidents": unresolved_count,
            "resolved_incidents": resolved_count,
            "false_positive_incidents": false_positive_count,
            "possible_associations": possible_count,
            "window_hours": settings.briefing_window_hours,
            "reference_time": reference_time,
        },
        "highest_priority_incidents": top_incidents,
        "unresolved_incidents": unresolved,
        "newly_detected_incidents": newly_detected,
        "repeated_events": repeated,
        "event_spikes": spikes,
        "device_health": health,
        "quality_by_type": quality_by_type,
        "recommended_management_actions": recommended_management_actions,
        "confidence_statement": contradiction_statement,
    }

    evidence = {
        "incident_ids": sorted({item["incident_id"] for item in top_incidents + unresolved + newly_detected}),
        "event_ids": sorted({eid for item in top_incidents for eid in item["event_ids"]}),
        "quality_issue_ids": sorted(
            {item["issue_id"] for item in repeated + spikes + health}
        ),
    }

    markdown = _render_markdown(
        sections,
        generated_at=generated_at,
        reference_time=reference_time,
        stats=stats,
        settings=settings,
    )
    return {
        "generated_at": generated_at,
        "reference_time": reference_time,
        "window_hours": settings.briefing_window_hours,
        "stats": stats,
        "sections": sections,
        "evidence": evidence,
        "markdown": markdown,
    }


def _incident_line(incident: dict[str, Any]) -> str:
    evidence = ", ".join(f"`{eid}`" for eid in incident["event_ids"][:6])
    if len(incident["event_ids"]) > 6:
        evidence += f" (+{len(incident['event_ids']) - 6} more)"
    return (
        f"**{incident['title']}** (`{incident['incident_id']}`) — "
        f"severity **{incident['severity']}**, confidence {incident['confidence']:.2f}, "
        f"status `{incident['status']}`, {incident['event_count']} event(s).  \n"
        f"  Evidence: {evidence or 'none'}.  \n"
        f"  Why linked: {incident['explanation'][:300]}"
    )


def _render_markdown(
    sections: dict[str, Any],
    *,
    generated_at: str,
    reference_time: str,
    stats: dict[str, Any],
    settings: Settings,
) -> str:
    summary = sections["summary"]
    lines: list[str] = [
        "# Zenith SecOps — Operational Briefing",
        "",
        f"**Generated:** {generated_at}  ",
        f"**Data reference time (latest received event):** {reference_time}  ",
        f"**New-incident window:** {settings.briefing_window_hours} hours ending at the data reference time  ",
        "**Mode:** deterministic template over stored evidence (offline, no external AI).",
        "",
        "## Executive summary",
        summary["text"],
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Valid events | {stats['valid_events']} |",
        f"| Incidents | {stats['incidents']} |",
        f"| Open / investigating | {summary['unresolved_incidents']} |",
        f"| High / critical incidents | {stats['high_severity_incidents']} |",
        f"| Data-quality findings | {stats['quality_issues']} |",
        f"| Unhealthy devices | {stats['unhealthy_devices']} |",
        f"| Possible associations | {summary['possible_associations']} |",
        "",
        "## Highest-priority incidents",
    ]
    if sections["highest_priority_incidents"]:
        for incident in sections["highest_priority_incidents"]:
            lines.append(f"- {_incident_line(incident)}")
    else:
        lines.append("- No incidents are stored. No priority claim is made.")

    lines.extend(["", "## Unresolved incidents"])
    if sections["unresolved_incidents"]:
        for incident in sections["unresolved_incidents"]:
            lines.append(
                f"- `{incident['incident_id']}` **{incident['title']}** — "
                f"{incident['severity']} / {incident['status']} / confidence {incident['confidence']:.2f}"
                f" — events: {', '.join(f'`{e}`' for e in incident['event_ids'][:5]) or 'none'}"
            )
    else:
        lines.append("- No open or investigating incidents.")

    lines.extend(["", "## Newly detected incidents"])
    if sections["newly_detected_incidents"]:
        for incident in sections["newly_detected_incidents"][:5]:
            lines.append(
                f"- `{incident['incident_id']}` {incident['title']} "
                f"(detected from evidence received {incident['created_at']})"
            )
    else:
        lines.append(
            f"- No incidents were formed from evidence received in the last "
            f"{settings.briefing_window_hours} hours of the data window."
        )

    lines.extend(["", "## Repeated events by device / location"])
    if sections["repeated_events"]:
        for issue in sections["repeated_events"]:
            details = issue["details"]
            lines.append(
                f"- `{issue['issue_id']}` {issue['severity']}: {issue['explanation']} "
                f"Affected device: `{issue['device_id'] or 'n/a'}`; zone: "
                f"{details.get('zone') or 'n/a'}; event type: {details.get('event_type') or 'n/a'}."
            )
    else:
        lines.append("- No repeated-event findings above the configured threshold.")

    lines.extend(["", "## Significant event spikes"])
    if sections["event_spikes"]:
        for issue in sections["event_spikes"]:
            lines.append(f"- `{issue['issue_id']}` {issue['explanation']}")
    else:
        lines.append("- No event spikes above the configured threshold.")

    lines.extend(["", "## Device-health concerns"])
    if sections["device_health"]:
        for issue in sections["device_health"]:
            lines.append(
                f"- `{issue['issue_id']}` {issue['explanation']} "
                f"Recommended: {issue['suggested_action']}"
            )
    else:
        lines.append("- No explicit unhealthy-device reports. This is not proof that every device is online.")

    lines.extend(["", "## Data-quality problems"])
    if sections["quality_by_type"]:
        for row in sections["quality_by_type"]:
            lines.append(
                f"- `{row['issue_type']}`: {row['count']} finding(s), maximum severity {row['max_severity']}."
            )
    else:
        lines.append("- No data-quality findings are recorded.")

    lines.extend(["", "## Recommended management actions"])
    for item in sections["recommended_management_actions"]:
        refs = []
        if item.get("incident_id"):
            refs.append(f"incident `{item['incident_id']}`")
        if item.get("issue_id"):
            refs.append(f"issue `{item['issue_id']}`")
        suffix = f" ({', '.join(refs)})" if refs else ""
        lines.append(f"- {item['action']}{suffix} — {item['reason']}")

    incident_ids = sorted(
        {
            incident["incident_id"]
            for incident in (
                sections["highest_priority_incidents"]
                + sections["unresolved_incidents"]
                + sections["newly_detected_incidents"]
            )
        }
    )
    issue_ids = sorted(
        {
            issue["issue_id"]
            for issue in (
                sections["repeated_events"]
                + sections["event_spikes"]
                + sections["device_health"]
            )
        }
    )
    lines.extend(
        [
            "",
            "## Confidence and uncertainty",
            sections["confidence_statement"],
            "",
            "## Evidence index",
            f"- Incidents: {', '.join(f'`{i}`' for i in incident_ids) or 'none'}",
            f"- Quality issues: {', '.join(f'`{i}`' for i in issue_ids) or 'none'}",
            f"- Valid events: {stats['valid_events']}; duplicates (preserved but excluded from analytics): "
            f"{stats['duplicate_content_events']}; rejected raw records: {stats['rejected_events']}.",
            "- All counts above were computed from the local SQLite database at generation time. "
            "No incidents, statistics or sources were invented.",
        ]
    )
    return "\n".join(lines)
