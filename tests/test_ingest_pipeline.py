"""Deferred post-ingestion analysis (G1) and fair /ingest rate limiting (G2)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def _events(make_event, count, prefix="EVT-PIPE", zone="North Gate"):
    return [
        make_event(
            event_id=f"{prefix}-{index:05d}",
            device_id=f"CAM-PIPE-{index % 3}",
            occurred_at=f"2026-03-12T10:{index // 60 % 60:02d}:{index % 60:02d}Z",
            received_at=f"2026-03-12T10:{index // 60 % 60:02d}:{index % 60:02d}Z",
            zone=zone,
        )
        for index in range(count)
    ]


def test_sync_mode_reports_no_pending_analysis(client):
    health = client.get("/health").json()
    assert health["analysis_pending"] is False
    assert health["analysis"]["mode"] == "sync"


def test_deferred_ingest_inserts_then_worker_analyses(settings, make_event):
    app = create_app(settings=settings.derive(analysis_mode="deferred", analysis_debounce_seconds=0.5))
    with TestClient(app) as client:
        response = client.post("/ingest", json=_events(make_event, 5))
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] == 5
        assert body["analysis"] == "deferred"
        # Events are queryable immediately; incidents follow after the debounce.
        assert client.get("/events").json()["total"] == 5
        assert client.get("/health").json()["analysis_pending"] is True

        assert app.state.analysis_worker.wait_idle(timeout=30)
        health = client.get("/health").json()
        assert health["analysis_pending"] is False
        assert health["analysis"]["runs"] >= 1
        assert health["analysis"]["last_error"] is None
        assert client.get("/incidents").json()["total"] >= 1


def test_deferred_worker_coalesces_bursts(settings, make_event):
    app = create_app(settings=settings.derive(analysis_mode="deferred", analysis_debounce_seconds=1.0))
    with TestClient(app) as client:
        for batch in range(10):
            client.post(
                "/ingest",
                json=_events(make_event, 3, prefix=f"EVT-BURST-{batch}", zone=f"Zone {batch}"),
            )
        assert app.state.analysis_worker.wait_idle(timeout=30)
        runs = client.get("/health").json()["analysis"]["runs"]
        assert 1 <= runs <= 3, f"10 quick batches should coalesce, got {runs} runs"
        assigned = app.state.db.scalar("SELECT COUNT(*) FROM incident_events", default=0)
        assert assigned == 30


def test_analyze_sync_query_overrides_deferred_mode(settings, make_event):
    app = create_app(settings=settings.derive(analysis_mode="deferred", analysis_debounce_seconds=60))
    with TestClient(app) as client:
        response = client.post("/ingest?analyze=sync", json=_events(make_event, 4))
        assert response.json()["analysis"] == "completed"
        assert client.get("/incidents").json()["total"] >= 1
        assert client.get("/health").json()["analysis_pending"] is False


def test_shutdown_flushes_pending_analysis(settings, make_event):
    app = create_app(settings=settings.derive(analysis_mode="deferred", analysis_debounce_seconds=60))
    with TestClient(app) as client:
        client.post("/ingest", json=_events(make_event, 4))
        assert client.get("/incidents").json()["total"] == 0
    # Lifespan shutdown stopped the worker and ran the pending recompute.
    assert app.state.db.scalar("SELECT COUNT(*) FROM incidents", default=0) >= 1
    assert app.state.analysis_worker.pending is False


def test_admin_recompute_still_works_in_deferred_mode(settings, make_event):
    app = create_app(settings=settings.derive(analysis_mode="deferred", analysis_debounce_seconds=60))
    with TestClient(app) as client:
        client.post("/ingest", json=_events(make_event, 4))
        result = client.post("/admin/recompute").json()
        assert result["status"] == "completed"
        assert result["incidents"] >= 1
        assert "duration_seconds" in result


def test_single_record_posts_are_not_throttled(settings, make_event):
    """An evaluator posting 2,000 events one by one must not hit 429."""
    app = create_app(
        settings=settings.derive(
            analysis_mode="deferred", analysis_debounce_seconds=60, rate_limit_per_minute=600
        )
    )
    with TestClient(app) as client:
        statuses = {
            client.post("/ingest", json=event).status_code
            for event in _events(make_event, 2000, prefix="EVT-ONE")
        }
        assert statuses == {200}
        assert client.get("/events?page_size=1").json()["total"] == 2000


def test_ingest_limit_counts_records_not_requests(settings, make_event):
    app = create_app(settings=settings.derive(ingest_records_per_minute=10))
    with TestClient(app) as client:
        assert client.post("/ingest", json=_events(make_event, 6, prefix="EVT-LIM-A")).status_code == 200
        limited = client.post("/ingest", json=_events(make_event, 6, prefix="EVT-LIM-B"))
        assert limited.status_code == 429
        assert "records/minute" in limited.json()["detail"]
        # A batch that fits the remaining budget is still accepted.
        assert client.post("/ingest", json=_events(make_event, 4, prefix="EVT-LIM-C")).status_code == 200


def test_other_writes_keep_request_rate_limit(settings):
    app = create_app(settings=settings.derive(rate_limit_per_minute=3))
    with TestClient(app) as client:
        codes = [client.post("/admin/recompute").status_code for _ in range(4)]
        assert codes[:3] == [200, 200, 200]
        assert codes[3] == 429
