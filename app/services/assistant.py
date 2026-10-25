"""Deterministic text operations assistant with explicit confirmation safety.

Design rules:
- Read-only questions never create a pending action. A state change is only
  proposed when the query is phrased as a command ("resolve INC-…",
  "mark INC-… as false positive", "assign INC-… to Sam").
- Every state change is returned as ``pending_action`` and only applied via
  ``/assistant/confirm``; a request can be decided (confirmed or cancelled)
  exactly once.
- Answers are grounded in stored rows and split into facts, inferences,
  missing information, conflicts and recommended actions.
- ``session_id`` gives follow-up context: "summarise this incident" resolves
  to the incidents referenced by the previous answer in the same session.
"""
from __future__ import annotations

import json
import re
from typing import Any
from uuid import uuid4

from app.config import Settings
from app.database import Database
from app.services.briefing import generate_briefing
from app.services.incident_status import apply_incident_update
from app.services.reasoning import generate_incident_reasoning
from app.utils import ACTIVE_STATUSES, normalize_zone, now_iso, parse_json


_INCIDENT_RE = re.compile(r"\bINC-[A-Za-z0-9-]+\b", re.IGNORECASE)
_POLITE_PREFIX = re.compile(r"^(?:(?:please|kindly|can you|could you|would you|pls)\s+)+")
_REFERENCE_RE = re.compile(r"\b(this|that|these|those|it|its|them|same)\b")
_FOCUS_WORDS = re.compile(r"\b(summari[sz]e|summary|explain|why|details?|describe|tell me about)\b")

# Ordered so multi-word phrases win over their substrings.
_STATUS_PHRASES: tuple[tuple[str, str], ...] = (
    ("false positive", "false_positive"),
    ("false-positive", "false_positive"),
    ("false_positive", "false_positive"),
    ("not a threat", "false_positive"),
    ("under investigation", "investigating"),
    ("in progress", "investigating"),
    ("investigating", "investigating"),
    ("acknowledged", "acknowledged"),
    ("reopened", "open"),
    ("resolved", "resolved"),
    ("closed", "closed"),
    ("open", "open"),
)
_VERB_STATUS: dict[str, str] = {
    "acknowledge": "acknowledged",
    "ack": "acknowledged",
    "investigate": "investigating",
    "resolve": "resolved",
    "close": "closed",
    "reopen": "open",
    "dismiss": "false_positive",
}
_SET_VERBS = re.compile(r"^(?:mark|set|change|update|move|flag|label)\b(?P<rest>.*)$")
_ASSIGN_RE = re.compile(r"^assign\b.*?\bto\s+(?P<who>[a-z0-9][a-z0-9 ._@'-]{0,80}?)\s*[.!]?$")
_LEADING_VERB = re.compile(r"^(?P<verb>[a-z]+)\b")


# ---------------------------------------------------------------- parsing
def _command_text(query: str) -> str:
    return _POLITE_PREFIX.sub("", query.strip().lower()).strip()


def _status_from_phrase(text: str) -> str | None:
    for phrase, status in _STATUS_PHRASES:
        if re.search(rf"\b{re.escape(phrase)}\b", text):
            return status
    return None


def parse_command(query: str) -> dict[str, Any] | None:
    """Return the requested state change, or None for a read-only question."""
    text = _command_text(query)
    assign = _ASSIGN_RE.match(text)
    if assign:
        # Keep the assignee's original casing.
        original = re.search(r"\bto\s+(.+?)\s*[.!]?$", query.strip(), re.IGNORECASE)
        who = (original.group(1) if original else assign.group("who")).strip()
        return {"action": "assign_incident", "assigned_to": who[:100]}
    set_match = _SET_VERBS.match(text)
    if set_match:
        target = re.search(r"\b(?:as|to)\s+(?P<target>.+)$", set_match.group("rest"))
        status = _status_from_phrase(target.group("target")) if target else None
        return {"action": "update_incident_status", "status": status} if status else None
    verb = _LEADING_VERB.match(text)
    if verb and verb.group("verb") in _VERB_STATUS:
        return {"action": "update_incident_status", "status": _VERB_STATUS[verb.group("verb")]}
    if text.startswith("start investigating"):
        return {"action": "update_incident_status", "status": "investigating"}
    return None


def _known_zones(db: Database) -> list[str]:
    rows = db.fetchall("SELECT DISTINCT zone FROM events WHERE zone IS NOT NULL AND zone <> ''")
    return [row["zone"] for row in rows]


def _zones_in_query(db: Database, query: str) -> list[str]:
    """Zones named in the query, tolerant of case, spaces, '-' and '_'."""
    needle = normalize_zone(query)
    matches = []
    for zone in _known_zones(db):
        key = normalize_zone(zone)
        if len(key) >= 3 and key in needle:
            matches.append(zone)
    if matches:
        return matches
    # "near <zone>" / "in <zone>" for zones the full-query scan missed.
    near = re.search(r"\b(?:near|in|at|around)\s+([a-z0-9 _-]+?)(?:\s+incidents?|\s+zone|[?.!]|\s*$)", query.lower())
    if near:
        phrase = normalize_zone(near.group(1))
        if len(phrase) >= 3:
            return [zone for zone in _known_zones(db) if phrase in normalize_zone(zone)]
    return []


def _status_filter(lowered: str) -> list[str] | None:
    if re.search(r"\b(unresolved|open|active|outstanding|pending)\b", lowered):
        return list(ACTIVE_STATUSES)
    status = _status_from_phrase(lowered)
    return [status] if status else None


def _explicit_incident_ids(db: Database, query: str) -> list[str]:
    explicit = list(dict.fromkeys(value.upper() for value in _INCIDENT_RE.findall(query)))
    if not explicit:
        return []
    rows = db.fetchall(
        "SELECT incident_id FROM incidents WHERE upper(incident_id) IN "
        + "(" + ",".join("?" for _ in explicit) + ")",
        explicit,
    )
    found = {row["incident_id"].upper(): row["incident_id"] for row in rows}
    return [found[value] for value in explicit if value in found]


def _search_incidents(db: Database, query: str) -> list[str]:
    lowered = query.lower()
    where: list[str] = []
    params: list[Any] = []
    statuses = _status_filter(lowered)
    if statuses:
        where.append(f"i.status IN ({','.join('?' for _ in statuses)})")
        params.extend(statuses)
    if "critical" in lowered:
        where.append("i.severity = 'critical'")
    elif "high" in lowered:
        where.append("i.severity IN ('high','critical')")
    zones = _zones_in_query(db, query)
    if zones:
        where.append(
            "EXISTS (SELECT 1 FROM incident_events ie JOIN events e ON e.event_id = ie.event_id "
            f"WHERE ie.incident_id = i.incident_id AND e.zone IN ({','.join('?' for _ in zones)}))"
        )
        params.extend(zones)

    clause = f"WHERE {' AND '.join(where)}" if where else ""
    rows = db.fetchall(
        f"SELECT i.incident_id FROM incidents i {clause} "
        "ORDER BY CASE i.severity WHEN 'critical' THEN 4 WHEN 'high' THEN 3 "
        "WHEN 'medium' THEN 2 ELSE 1 END DESC, i.confidence DESC LIMIT 10",
        params,
    )
    return [row["incident_id"] for row in rows]


def _session_context(db: Database, session_id: str | None) -> list[str]:
    if not session_id:
        return []
    rows = db.fetchall(
        "SELECT response FROM assistant_history WHERE session_id = ? "
        "ORDER BY created_at DESC, rowid DESC LIMIT 20",
        (session_id,),
    )
    for row in rows:
        ids = parse_json(row["response"], {}).get("incident_ids") or []
        if ids:
            existing = db.fetchall(
                "SELECT incident_id FROM incidents WHERE incident_id IN "
                + "(" + ",".join("?" for _ in ids) + ")",
                ids,
            )
            present = {item["incident_id"] for item in existing}
            return [incident_id for incident_id in ids if incident_id in present]
    return []


# ---------------------------------------------------------------- answers
def _incident_rows(db: Database, incident_ids: list[str]) -> list[dict[str, Any]]:
    if not incident_ids:
        return []
    placeholders = ",".join("?" for _ in incident_ids)
    rows = db.fetchall(
        f"SELECT * FROM incidents WHERE incident_id IN ({placeholders})", incident_ids
    )
    order = {incident_id: index for index, incident_id in enumerate(incident_ids)}
    return sorted(rows, key=lambda row: order.get(row["incident_id"], 0))


def _evidence_for_incidents(db: Database, incident_ids: list[str]) -> list[str]:
    if not incident_ids:
        return []
    placeholders = ",".join("?" for _ in incident_ids)
    rows = db.fetchall(
        f"SELECT DISTINCT event_id FROM incident_events WHERE incident_id IN ({placeholders}) "
        "ORDER BY event_id",
        incident_ids,
    )
    return [row["event_id"] for row in rows]


def _unique(values: list[Any]) -> list[Any]:
    return list(dict.fromkeys(value for value in values if value))


def _confidence(rows: list[dict[str, Any]]) -> float:
    """Mean stored confidence of the incidents the answer is based on."""
    if not rows:
        return 0.0
    return round(sum(float(row["confidence"]) for row in rows) / len(rows), 3)


def _summary_line(row: dict[str, Any]) -> str:
    return (
        f"{row['incident_id']}: {row['title']} — {row['severity']} / "
        f"{row['status']} / confidence {float(row['confidence']):.2f}"
    )


def _structured_from_rows(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    facts = [_summary_line(row) for row in rows]
    inferences = [f"{row['incident_id']}: {row['explanation']}" for row in rows[:5]]
    missing: list[str] = []
    conflicts: list[str] = []
    actions: list[str] = []
    for row in rows:
        missing.extend(parse_json(row.get("missing_info"), []) or [])
        conflicts.extend(
            f"{row['incident_id']}: {item}" for item in parse_json(row.get("contradictions"), []) or []
        )
        actions.extend(parse_json(row.get("recommended_actions"), []) or [])
    return {
        "facts": _unique(facts),
        "inferences": _unique(inferences),
        "missing_information": _unique(missing)[:10],
        "conflicts": _unique(conflicts)[:10],
        "recommended_actions": _unique(actions)[:8],
    }


def _structured_from_reasoning(
    db: Database, incident_ids: list[str], provider_name: str
) -> tuple[dict[str, list[str]], list[str], str, list[str]]:
    """Reuse the incident reasoning provider for focused (1–3 incident) answers."""
    fields: dict[str, list[str]] = {
        "facts": [],
        "inferences": [],
        "missing_information": [],
        "conflicts": [],
        "recommended_actions": [],
    }
    answers: list[str] = []
    evidence: list[str] = []
    providers: list[str] = []
    for incident_id in incident_ids[:3]:
        reasoning = generate_incident_reasoning(db, incident_id, provider_name)
        if not reasoning:
            continue
        answers.append(f"{incident_id}: {reasoning['answer']}")
        providers.append(reasoning.get("provider", "deterministic"))
        evidence.extend(reasoning.get("evidence_event_ids", []))
        fields["facts"].extend(reasoning.get("facts", []))
        fields["inferences"].extend(reasoning.get("inferences", []))
        fields["missing_information"].extend(reasoning.get("missing_information", []))
        fields["conflicts"].extend(reasoning.get("uncertainty", []))
        fields["recommended_actions"].extend(reasoning.get("recommended_actions", []))
    provider = providers[0] if len(set(providers)) == 1 else ", ".join(_unique(providers)) or "deterministic"
    return {key: _unique(value) for key, value in fields.items()}, answers, provider, _unique(evidence)


def _describe_action(pending: dict[str, Any], row: dict[str, Any]) -> str:
    if pending["action"] == "assign_incident":
        return f"Assign {row['incident_id']} ({row['title']}) to {pending['assigned_to']}."
    return (
        f"Change {row['incident_id']} ({row['title']}) status from "
        f"{row['status']} to {pending['status']}."
    )


def answer_query(
    db: Database,
    query: str,
    settings: Settings,
    session_id: str | None = None,
) -> dict[str, Any]:
    normalized = query.strip()
    lowered = normalized.lower()
    request_id = f"REQ-{uuid4().hex[:12].upper()}"
    provider_name = getattr(settings, "reasoning_provider", "deterministic")

    command = parse_command(normalized)
    explicit_ids = _explicit_incident_ids(db, normalized)
    context_ids: list[str] = []
    if not explicit_ids and (command or _REFERENCE_RE.search(lowered)):
        context_ids = _session_context(db, session_id)
    targeted_ids = explicit_ids or context_ids
    used_context = bool(context_ids) and not explicit_ids

    pending: dict[str, Any] | None = None
    provider = "deterministic"
    structured: dict[str, list[str]]

    if command:
        intent = "command"
        incident_ids = targeted_ids[:1]
        rows = _incident_rows(db, incident_ids)
        if rows:
            pending = {**command, "incident_id": rows[0]["incident_id"], "current_status": rows[0]["status"]}
            pending["description"] = _describe_action(pending, rows[0])
            answer = f"{pending['description']} Confirm to apply this change."
        else:
            answer = (
                "This looks like a request to change an incident, but no incident was identified. "
                "Name the incident ID (for example 'resolve INC-…') or ask about an incident first."
            )
        structured = _structured_from_rows(rows)
        evidence = _evidence_for_incidents(db, incident_ids)
    elif "briefing" in lowered or "shift" in lowered:
        intent = "briefing"
        briefing = generate_briefing(db, settings)
        sections = briefing["sections"]
        answer = sections["summary"]["text"]
        evidence = briefing["evidence"].get("event_ids", [])
        incident_ids = briefing["evidence"].get("incident_ids", [])[:10]
        rows = _incident_rows(db, incident_ids)
        structured = _structured_from_rows(rows)
        structured["facts"] = _unique([answer] + structured["facts"][:5])
        structured["recommended_actions"] = _unique(
            [
                item["action"] if isinstance(item, dict) else str(item)
                for item in sections.get("recommended_management_actions") or []
            ]
            + structured["recommended_actions"]
        )[:8]
        statement = sections.get("confidence_statement")
        if isinstance(statement, str) and statement:
            structured["conflicts"] = _unique([statement] + structured["conflicts"])
    else:
        incident_ids = targeted_ids or _search_incidents(db, normalized)
        rows = _incident_rows(db, incident_ids)
        focused = bool(targeted_ids) or (len(rows) == 1 and bool(_FOCUS_WORDS.search(lowered)))
        if "evidence" in lowered or "support" in lowered:
            intent = "evidence"
            evidence = _evidence_for_incidents(db, incident_ids)
            answer = (
                f"Evidence for {', '.join(incident_ids) or 'the requested incident'}: "
                + (", ".join(evidence) or "no stored events")
            )
            structured = _structured_from_rows(rows)
        elif focused and rows:
            intent = "incident_summary"
            structured, answers, provider, evidence = _structured_from_reasoning(
                db, incident_ids, provider_name
            )
            answer = "\n".join(answers)
        else:
            intent = "incident_list"
            answer = "\n".join(_summary_line(row) for row in rows) or "No incidents matched the request."
            evidence = _evidence_for_incidents(db, incident_ids)
            structured = _structured_from_rows(rows)

    confidence = _confidence(rows)
    result = {
        "request_id": request_id,
        "session_id": session_id,
        "intent": intent,
        "answer": answer,
        **structured,
        "incident_ids": [row["incident_id"] for row in rows],
        "evidence_event_ids": evidence,
        "confidence": confidence,
        "confidence_basis": (
            f"mean stored confidence of {len(rows)} incident(s)" if rows else "no matching incidents"
        ),
        "used_session_context": used_context,
        "requires_confirmation": pending is not None,
        "pending_action": pending,
        "provider": provider,
        "grounded": True,
    }
    db.execute(
        "INSERT INTO assistant_history(history_id, query, response, pending_action, created_at, session_id) "
        "VALUES (?,?,?,?,?,?)",
        (
            request_id,
            normalized,
            json.dumps(result, sort_keys=True),
            json.dumps(pending, sort_keys=True) if pending else None,
            now_iso(),
            session_id,
        ),
    )
    return result


def confirm_request(db: Database, request_id: str, confirmed: bool) -> dict[str, Any]:
    row = db.fetchone(
        "SELECT history_id, pending_action, decision, confirmed_at FROM assistant_history "
        "WHERE history_id = ?",
        (request_id,),
    )
    if not row:
        return {}
    pending = parse_json(row.get("pending_action"), None)
    if not pending:
        return {"request_id": request_id, "status": "no_action"}
    if row.get("decision") or row.get("confirmed_at"):
        return {
            "request_id": request_id,
            "status": "already_decided",
            "decision": row.get("decision") or "confirmed",
        }
    if pending.get("action") not in {"update_incident_status", "assign_incident"}:
        return {"request_id": request_id, "status": "rejected"}

    decision = "confirmed" if confirmed else "cancelled"
    timestamp = now_iso()
    # Claim the decision atomically so a replayed confirm cannot apply twice.
    with db.transaction() as connection:
        claimed = connection.execute(
            "UPDATE assistant_history SET decision = ?, confirmed_at = ? "
            "WHERE history_id = ? AND decision IS NULL AND confirmed_at IS NULL",
            (decision, timestamp if confirmed else None, request_id),
        ).rowcount
    if not claimed:
        return {"request_id": request_id, "status": "already_decided", "decision": "unknown"}
    if not confirmed:
        return {"request_id": request_id, "status": "cancelled", "pending_action": pending}

    if pending["action"] == "assign_incident":
        applied = apply_incident_update(db, pending["incident_id"], assigned_to=pending["assigned_to"])
    else:
        applied = apply_incident_update(db, pending["incident_id"], status=pending["status"])
    if not applied:
        db.execute(
            "UPDATE assistant_history SET decision = 'not_found' WHERE history_id = ?",
            (request_id,),
        )
        return {"request_id": request_id, "status": "not_found"}
    return {
        "request_id": request_id,
        "status": "confirmed",
        "action": pending,
        "result": applied,
        "confirmed_at": timestamp,
    }


def history(db: Database, limit: int = 50, session_id: str | None = None) -> list[dict[str, Any]]:
    where, params = "", []
    if session_id:
        where, params = "WHERE session_id = ?", [session_id]
    rows = db.fetchall(
        "SELECT history_id, query, response, pending_action, confirmed_at, decision, session_id, created_at "
        f"FROM assistant_history {where} ORDER BY created_at DESC LIMIT ?",
        (*params, limit),
    )
    for row in rows:
        row["response"] = parse_json(row["response"], {})
        row["pending_action"] = parse_json(row["pending_action"], None)
    return rows
