"""Correlation checks against the scenarios the synthetic generator plants."""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from app.database import Database
from app.services.correlation import run_correlation
from app.services.data_quality import run_quality_analysis
from app.services.ingestion import ingest_records
from scripts.generate_events import BASE, ZONES, EventGenerator

COUNT = 8_000


def _iso(value):
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _planted_groups(records):
    """Return {label: [event_id, ...]} for related clusters and bursts the generator planted.

    Related cluster: indices 400k..400k+5 share a zone and are 8 s apart.
    Burst: indices 4000k..4000k+15 are ANPR detections 5 s apart in one zone.
    Records overridden by other anomalies (unhealthy heartbeat, wrong time/zone)
    are skipped.
    """
    zone_names = list(ZONES)
    by_id = {record["event_id"]: record for record in records if isinstance(record, dict)}
    groups: dict[str, list[str]] = defaultdict(list)
    for index in range(1, COUNT):
        event_id = f"EVT-GEN-{index:07d}"
        record = by_id.get(event_id)
        # Every 1000th record is replaced by an unhealthy-device heartbeat.
        if record is None or index % 1_000 == 0:
            continue
        if index % 4_000 < 16:
            label = f"burst-{index // 4_000}"
            zone = zone_names[(index // 4_000) % len(zone_names)]
            occurred = BASE + timedelta(seconds=(index // 4_000) * 4_000 * 20 + (index % 4_000) * 5)
        elif index % 400 < 6:
            label = f"related-{index // 400}"
            zone = zone_names[(index // 400) % len(zone_names)]
            occurred = BASE + timedelta(seconds=(index // 400) * 400 * 20 + (index % 400) * 8)
        else:
            continue
        if record.get("zone") == zone and record.get("occurred_at") == _iso(occurred):
            groups[label].append(event_id)
    return {label: ids for label, ids in groups.items() if len(ids) >= 2}


def test_planted_related_groups_and_bursts_form_single_incidents(settings):
    db = Database(settings.db_path)
    db.init_schema()
    records = list(EventGenerator(count=COUNT, seed=42))
    ingest_records(db, records, settings, run_quality=False, run_correlation=False, source="test")
    run_quality_analysis(db, settings)
    incident_count = run_correlation(db, settings)

    membership = {
        row["event_id"]: row["incident_id"]
        for row in db.fetchall("SELECT event_id, incident_id FROM incident_events")
    }
    groups = _planted_groups(records)
    assert len(groups) >= 15
    for label, event_ids in groups.items():
        present = [event_id for event_id in event_ids if event_id in membership]
        assert len({membership[event_id] for event_id in present}) == 1, label

    valid_events = db.scalar("SELECT COUNT(*) FROM events WHERE validation_status = 'valid'")
    assert len(membership) == valid_events
    # Correlation must compress the stream substantially without collapsing it.
    assert incident_count < valid_events * 0.4
    largest = db.scalar("SELECT MAX(event_count) FROM incidents")
    assert largest <= 50
