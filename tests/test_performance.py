"""Large dataset generation and end-to-end performance checks."""
from __future__ import annotations

import json
import statistics
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.database import Database
from app.main import create_app
from scripts.generate_events import EventGenerator, _run_ingestion


def test_generator_is_reproducible_and_streaming():
    first_generator = EventGenerator(count=600, seed=7)
    first = list(first_generator)
    second_generator = EventGenerator(count=600, seed=7)
    second = list(second_generator)
    assert first == second
    assert first_generator.stats == second_generator.stats
    assert len(first) == 600
    assert first_generator.stats["malformed"] >= 0
    assert first_generator.stats["duplicate_content"] >= 0
    assert isinstance(first[0], dict)
    assert all("event_id" in item for item in first if isinstance(item, dict))


@pytest.mark.performance
def test_50000_event_pipeline(tmp_path):
    settings = load_settings().derive(
        db_path=tmp_path / "perf_50000.db",
        delayed_threshold_seconds=300,
        correlation_window_minutes=15,
    )
    database = Database(settings.db_path)
    database.init_schema()
    generator = EventGenerator(count=50_000, seed=42)

    started = time.perf_counter()
    result = _run_ingestion(
        database,
        settings,
        generator,
        batch_size=1_000,
        analyze=True,
    )
    elapsed = time.perf_counter() - started
    result["wall_seconds"] = round(elapsed, 3)
    print("\nPERFORMANCE_RESULT=" + json.dumps(result, default=str))

    assert result["records_generated"] == 50_000
    assert result["accepted"] + result["rejected"] == 50_000
    assert result["accepted"] >= 45_000
    assert result["rejected"] >= 1
    assert result["duplicate_content_accepted"] >= 1
    assert result["duplicate_ids"] >= 1
    assert result["batches"] == 50

    stats = database.stats()
    assert stats["valid_events"] > 40_000
    assert stats["valid_events"] == result["accepted"] - result["duplicate_content_accepted"]
    assert stats["rejected_events"] == result["rejected"]
    assert stats["incidents"] == result["incidents"] > 0
    assert stats["quality_issues"] == result["quality_issues"] > 0

    assigned = database.scalar("SELECT COUNT(*) FROM incident_events", default=0)
    assert assigned == stats["valid_events"], (
        "Every valid event must be assigned to exactly one incident "
        f"(events={stats['valid_events']}, assigned={assigned})"
    )
    assert result["ingest_records_per_second"] > 100
    assert result["peak_traced_memory_mb"] < 800
    assert result["total_seconds"] < 300, "50k pipeline exceeded the 5-minute budget"
    assert elapsed < 300


@pytest.mark.performance
def test_50000_events_via_http_api(tmp_path):
    """50k events POSTed to /ingest in batches of 500 (deferred analysis).

    A reader thread polls GET endpoints throughout to prove the API stays
    responsive while ingestion and background recomputes run.
    """
    settings = load_settings().derive(
        db_path=tmp_path / "perf_http_50000.db",
        analysis_mode="deferred",
        analysis_debounce_seconds=2.0,
        analysis_max_delay_seconds=30.0,
    )
    app = create_app(settings=settings)
    generator = EventGenerator(count=50_000, seed=42)
    latencies: list[tuple[str, float, int]] = []
    stop_reader = threading.Event()

    with TestClient(app) as client:

        def reader() -> None:
            paths = ("/health", "/incidents?page_size=5", "/events?page_size=5")
            index = 0
            while not stop_reader.is_set():
                path = paths[index % len(paths)]
                started = time.perf_counter()
                status = client.get(path).status_code
                latencies.append((path, time.perf_counter() - started, status))
                index += 1
                stop_reader.wait(0.25)

        reader_thread = threading.Thread(target=reader, daemon=True)
        started = time.perf_counter()
        reader_thread.start()

        accepted = rejected = requests = 0
        statuses: set[int] = set()
        batch: list = []

        def post(records: list) -> None:
            nonlocal accepted, rejected, requests
            response = client.post("/ingest", json=records)
            statuses.add(response.status_code)
            body = response.json()
            accepted += body.get("accepted", 0)
            rejected += body.get("rejected", 0)
            requests += 1

        for record in generator:
            batch.append(record)
            if len(batch) == 500:
                post(batch)
                batch = []
        if batch:
            post(batch)
        ingest_seconds = time.perf_counter() - started

        deadline = time.monotonic() + 150
        while client.get("/health").json()["analysis_pending"] and time.monotonic() < deadline:
            time.sleep(0.5)
        total_seconds = time.perf_counter() - started
        stop_reader.set()
        reader_thread.join(10)
        health = client.get("/health").json()

    stats = app.state.db.stats()
    assigned = app.state.db.scalar("SELECT COUNT(*) FROM incident_events", default=0)
    read_times = sorted(seconds for _, seconds, _ in latencies)
    result = {
        "path": "http",
        "records": generator.stats["generated"],
        "requests": requests,
        "accepted": accepted,
        "rejected": rejected,
        "http_statuses": sorted(statuses),
        "ingest_seconds": round(ingest_seconds, 3),
        "ingest_records_per_second": round(50_000 / ingest_seconds, 1),
        "total_seconds_until_analysed": round(total_seconds, 3),
        "analysis_runs": health["analysis"]["runs"],
        "last_analysis_seconds": health["analysis"]["last_duration_seconds"],
        "reads_during_ingest": len(read_times),
        "read_p50_ms": round(statistics.median(read_times) * 1000, 1) if read_times else None,
        "read_p95_ms": round(read_times[int(len(read_times) * 0.95) - 1] * 1000, 1) if read_times else None,
        "read_max_ms": round(read_times[-1] * 1000, 1) if read_times else None,
        "incidents": stats["incidents"],
        "quality_issues": stats["quality_issues"],
        "valid_events": stats["valid_events"],
    }
    print("\nPERFORMANCE_RESULT_HTTP=" + json.dumps(result, default=str))

    assert statuses <= {200}, f"unexpected HTTP statuses {statuses}"
    assert accepted + rejected == 50_000
    assert health["analysis_pending"] is False
    assert health["analysis"]["last_error"] is None
    assert stats["incidents"] > 0 and stats["quality_issues"] > 0
    assert assigned == stats["valid_events"], "every valid event must belong to one incident"
    assert total_seconds < 180, "50k via HTTP (ingest + analysis) exceeded 3 minutes"
    assert len(read_times) >= 10
    assert all(status == 200 for _, _, status in latencies)
    assert result["read_p95_ms"] < 2_000, "GET endpoints were not responsive during ingest"
