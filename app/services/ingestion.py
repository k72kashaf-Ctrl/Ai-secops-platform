"""Event ingestion, validation, deduplication and persistence."""
from __future__ import annotations

import json
import uuid
from datetime import timezone
from typing import Any, Iterable

from pydantic import ValidationError

from app.config import Settings
from app.database import Database
from app.models import IngestSummary
from app.schemas import EventIn
from app.utils import (
    canonical_json,
    clean_text,
    event_content_hash,
    now_iso,
    to_iso,
)


def _batches(items: list[Any], size: int = 500) -> Iterable[list[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _validation_errors(exc: ValidationError) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ()))
        errors.append(
            {
                "field": location or "record",
                "message": error.get("msg", "invalid value"),
                "type": error.get("type", "value_error"),
            }
        )
    return errors


def _raw_json(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    try:
        return json.dumps(raw, default=str, sort_keys=True)
    except (TypeError, ValueError):
        return json.dumps({"unserialisable_record": str(raw)})


def ingest_records(
    db: Database,
    records: Iterable[Any],
    settings: Settings,
    *,
    batch_id: str | None = None,
    run_quality: bool = True,
    run_correlation: bool = True,
    source: str = "api",
) -> IngestSummary:
    """Validate and persist a batch of events.

    Invalid records are preserved verbatim in ``rejected_events`` with their
    validation errors. Duplicate event IDs are rejected (the first record is
    kept); duplicate *content* under a new event ID is stored and marked as a
    duplicate so the evidence is never silently discarded.
    """
    batch_id = batch_id or f"BATCH-{uuid.uuid4().hex[:12].upper()}"
    record_list = list(records)
    summary = IngestSummary(batch_id=batch_id, total=len(record_list))
    initiated_at = now_iso()

    validated: list[tuple[EventIn, dict[str, Any], str]] = []
    rejections: list[tuple] = []
    summary_errors: list[dict[str, Any]] = []

    for raw_record in record_list:
        if not isinstance(raw_record, dict):
            rejections.append(
                (
                    batch_id,
                    None,
                    _raw_json(raw_record),
                    json.dumps([{"field": "record", "message": "record must be a JSON object"}]),
                    "malformed_record",
                    now_iso(),
                )
            )
            summary_errors.append(
                {"index": summary.total, "event_id": None, "reason": "malformed_record",
                 "errors": [{"field": "record", "message": "record must be a JSON object"}]}
            )
            continue
        try:
            event = EventIn.model_validate(raw_record)
        except ValidationError as exc:
            errors = _validation_errors(exc)
            source_event_id = clean_text(raw_record.get("event_id"), 200)
            rejections.append(
                (
                    batch_id,
                    source_event_id,
                    _raw_json(raw_record),
                    json.dumps(errors, default=str),
                    "validation_error",
                    now_iso(),
                )
            )
            summary_errors.append(
                {
                    "event_id": source_event_id,
                    "reason": "validation_error",
                    "errors": errors,
                }
            )
            continue
        validated.append((event, raw_record, _raw_json(raw_record)))

    # ------------------------------------------------------ duplicate content
    hashes: list[str] = []
    prepared: list[tuple[EventIn, dict[str, Any], str, str]] = []
    for event, raw_record, raw_text in validated:
        occurred_at = to_iso(event.occurred_at)
        digest = event_content_hash(
            event.source_type,
            event.device_id,
            event.event_type,
            occurred_at,
            event.latitude,
            event.longitude,
            event.zone,
            event.payload,
        )
        hashes.append(digest)
        prepared.append((event, raw_record, raw_text, digest))

    # One IMMEDIATE transaction: duplicate checks and inserts are atomic, so
    # concurrent /ingest calls cannot both accept the same event_id.
    with db.transaction() as connection:
        # ---------------------------------------------------------- duplicate IDs
        candidate_ids = [event.event_id for event, _, _ in validated]
        existing_ids: set[str] = set()
        for batch in _batches(candidate_ids):
            placeholders = ",".join("?" for _ in batch)
            rows = connection.execute(
                f"SELECT event_id FROM events WHERE event_id IN ({placeholders})", batch
            ).fetchall()
            existing_ids.update(row["event_id"] for row in rows)

        existing_hash_map: dict[str, str] = {}
        unique_hashes = list(dict.fromkeys(hashes))
        for batch in _batches(unique_hashes):
            placeholders = ",".join("?" for _ in batch)
            rows = connection.execute(
                f"SELECT content_hash, MIN(event_id) AS event_id FROM events "
                f"WHERE content_hash IN ({placeholders}) GROUP BY content_hash",
                batch,
            ).fetchall()
            for row in rows:
                existing_hash_map[row["content_hash"]] = row["event_id"]

        seen_ids = set(existing_ids)
        seen_hashes = dict(existing_hash_map)
        accepted_rows: list[tuple] = []
        ingested_at = now_iso()

        for event, raw_record, raw_text, digest in prepared:
            if event.event_id in seen_ids:
                errors = [
                    {
                        "field": "event_id",
                        "message": f"duplicate event_id {event.event_id!r}; the first occurrence is preserved",
                        "type": "duplicate_event_id",
                    }
                ]
                rejections.append(
                    (
                        batch_id,
                        event.event_id,
                        raw_text,
                        json.dumps(errors),
                        "duplicate_event_id",
                        now_iso(),
                    )
                )
                summary.duplicate_ids += 1
                summary_errors.append(
                    {"event_id": event.event_id, "reason": "duplicate_event_id", "errors": errors}
                )
                continue

            seen_ids.add(event.event_id)
            duplicate_of = seen_hashes.get(digest)
            validation_status = "duplicate" if duplicate_of else "valid"
            if duplicate_of:
                summary.duplicate_content += 1
            else:
                seen_hashes[digest] = event.event_id

            received_at = event.received_at or event.occurred_at
            occurred_text = to_iso(event.occurred_at)
            received_text = to_iso(received_at)
            accepted_rows.append(
                (
                    event.event_id,
                    event.source_type,
                    event.device_id,
                    event.event_type,
                    occurred_text,
                    int(event.occurred_at.timestamp()),
                    received_text,
                    int(received_at.astimezone(timezone.utc).timestamp()),
                    event.latitude,
                    event.longitude,
                    event.zone,
                    event.severity,
                    event.confidence,
                    canonical_json(event.payload),
                    raw_text,
                    digest,
                    validation_status,
                    duplicate_of,
                    ingested_at,
                    batch_id,
                )
            )

        accepted_count = len(accepted_rows)
        rejected_count = len(rejections)

        if accepted_rows:
            connection.executemany(
                """
                INSERT INTO events (
                    event_id, source_type, device_id, event_type, occurred_at,
                    occurred_epoch, received_at, received_epoch, latitude,
                    longitude, zone, severity, confidence, payload, raw_record,
                    content_hash, validation_status, duplicate_of, ingested_at,
                    batch_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                accepted_rows,
            )
        if rejections:
            connection.executemany(
                """
                INSERT INTO rejected_events (
                    batch_id, source_event_id, raw_record, errors, reason, received_at
                ) VALUES (?,?,?,?,?,?)
                """,
                rejections,
            )
        summary.accepted = accepted_count
        summary.rejected = rejected_count
        if rejected_count and accepted_count:
            summary.status = "partial"
        elif rejected_count and not accepted_count:
            summary.status = "rejected"
        elif not record_list:
            summary.status = "empty"
        else:
            summary.status = "completed"
        connection.execute(
            """
            INSERT INTO ingestion_runs (
                completed_at, accepted, rejected, duplicates, run_quality,
                run_correlation, status, error, batch_id, initiated_at, total
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                now_iso(),
                accepted_count,
                rejected_count,
                summary.duplicate_content,
                int(run_quality),
                int(run_correlation),
                summary.status,
                json.dumps(summary_errors[:200]) if summary_errors else None,
                batch_id,
                initiated_at,
                len(record_list),
            ),
        )

    summary.errors = summary_errors[:200]
    if summary_errors:
        summary.errors.append(
            {"note": f"{len(summary_errors)} rejection(s); first 200 shown. Raw records are preserved."}
        )

    # ------------------------------------------------------- post-processing
    if accepted_count or rejected_count:
        analysis_errors: list[str] = []
        if run_correlation or run_quality:
            from app.services.analysis import run_full_analysis
            from app.services.correlation import run_correlation as correlate
            from app.services.data_quality import (
                add_uncorrelated_issues,
                run_quality_analysis,
            )

            try:
                if run_quality and run_correlation:
                    run_full_analysis(db, settings)
                elif run_quality:
                    run_quality_analysis(db, settings, include_uncorrelated=False)
                    add_uncorrelated_issues(db, settings)
                else:
                    correlate(db, settings)
            except Exception as exc:  # keep accepted evidence even if analysis fails
                analysis_errors.append(f"{type(exc).__name__}: {exc}")
        if analysis_errors:
            summary.status = "ingested_with_analysis_error"
            summary.errors.append({"note": "Post-ingestion analysis error", "errors": analysis_errors})
            with db.transaction() as connection:
                connection.execute(
                    "UPDATE ingestion_runs SET status=?, error=? WHERE batch_id=?",
                    (
                        summary.status,
                        json.dumps(analysis_errors),
                        batch_id,
                    ),
                )

    return summary
