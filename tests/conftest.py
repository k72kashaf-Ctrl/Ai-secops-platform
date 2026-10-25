"""Shared pytest fixtures. Every test uses an isolated temporary SQLite DB."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

os.environ["ZENITH_AUTO_SEED"] = "0"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import load_settings  # noqa: E402
from app.main import create_app  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXED_TIME = datetime(2026, 3, 12, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def settings(tmp_path):
    return load_settings().derive(
        db_path=tmp_path / "test_secops.db",
        sample_path=ROOT / "data" / "sample_events.json",
        # Tests ingest and then query immediately; deferred mode has its own tests.
        analysis_mode="sync",
    )


@pytest.fixture
def app(settings):
    return create_app(settings=settings)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db(app):
    return app.state.db


@pytest.fixture
def make_event():
    def _make(**overrides):
        record = {
            "event_id": "EVT-TEST-0001",
            "source_type": "cctv",
            "device_id": "CAM-TEST-01",
            "event_type": "motion_detected",
            "occurred_at": "2026-03-12T10:00:00Z",
            "received_at": "2026-03-12T10:00:05Z",
            "latitude": 51.5074,
            "longitude": -0.1278,
            "zone": "North Gate",
            "severity": "medium",
            "confidence": 0.9,
            "payload": {"object": "person"},
        }
        record.update(overrides)
        return record

    return _make


@pytest.fixture
def ingest(client):
    def _ingest(records):
        return client.post("/ingest", json=records)

    return _ingest
