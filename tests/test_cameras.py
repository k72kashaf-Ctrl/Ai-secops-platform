"""GET /cameras camera wall and the read-only /media clip directory."""
from __future__ import annotations

import json
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.cameras import CAMERA_SOURCES, load_stream_config

CAMERA_KEYS = {
    "device_id", "source_type", "zone", "events", "last_event", "alert", "clip", "media_url", "stream",
}


@contextmanager
def _seeded(settings):
    with TestClient(create_app(settings=settings)) as client:
        assert client.post("/admin/seed-sample", params={"reset": "true"}).status_code == 200
        yield client


@pytest.fixture
def media_settings(settings, tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    return settings.derive(media_dir=media, cameras_file=tmp_path / "cameras.json")


def test_cameras_empty_database(media_settings):
    with TestClient(create_app(settings=media_settings)) as client:
        body = client.get("/cameras").json()
    assert body["cameras"] == []
    assert body["configured_streams"] == 0
    assert body["latest_occurred_at"] is None
    assert isinstance(body["media_dir"], str) and body["media_dir"]


def test_cameras_sample(media_settings):
    with _seeded(media_settings) as client:
        body = client.get("/cameras").json()
    cameras = body["cameras"]
    assert 15 <= len(cameras) <= 30
    assert all(set(cam) == CAMERA_KEYS for cam in cameras)
    assert all(cam["source_type"] in CAMERA_SOURCES for cam in cameras)
    assert len({cam["device_id"] for cam in cameras}) == len(cameras)
    assert body["latest_occurred_at"].startswith("2026-03-12T10:24")
    assert body["configured_streams"] == 0

    alerts = [cam["alert"] for cam in cameras]
    assert alerts == sorted(alerts, reverse=True)  # alert first
    for flag in (True, False):
        stamps = [c["last_event"]["occurred_at"] for c in cameras if c["alert"] is flag]
        assert stamps == sorted(stamps, reverse=True)

    pn = next(cam for cam in cameras if cam["device_id"] == "CAM-PN-01")
    assert pn["alert"] is True
    assert pn["clip"] == "clip-0201.mp4"
    assert pn["media_url"] is None
    assert pn["stream"] is None
    assert pn["zone"] == "Perimeter North"
    assert pn["last_event"]["event_id"] == "EVT-I2002"
    assert all(cam["stream"] is None for cam in cameras)


def test_cameras_media_file_is_served(media_settings):
    (media_settings.media_dir / "clip-0201.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    with _seeded(media_settings) as client:
        cameras = client.get("/cameras").json()["cameras"]
        pn = next(cam for cam in cameras if cam["device_id"] == "CAM-PN-01")
        assert pn["media_url"] == "/media/clip-0201.mp4"
        response = client.get(pn["media_url"])
        assert response.status_code == 200
        assert response.content == b"\x00\x00\x00\x18ftypmp42"
        # Only the media directory is exposed.
        assert client.get("/media/missing.mp4").status_code == 404
        assert client.get("/media/..%2Fcameras.json").status_code == 404
        assert client.get("/media/../test_secops.db").status_code == 404


def test_cameras_stream_config(media_settings):
    media_settings.cameras_file.write_text(
        json.dumps(
            {
                "CAM-PN-01": {"url": "http://10.0.0.5/stream.mjpg", "kind": "MJPEG"},
                "CAM-WH-A": {"url": "https://nvr.local/a.m3u8", "kind": "rtsp"},  # bad kind
                "CAM-DC-01": {"url": "javascript:alert(1)", "kind": "mp4"},  # bad url
                "CAM-NG-03": "not-an-object",
                "CAM-ME-01": {"url": "/media/demo-gate.mp4", "kind": "mp4", "label": "  Demo footage  "},
            }
        ),
        encoding="utf-8",
    )
    with _seeded(media_settings) as client:
        body = client.get("/cameras").json()
    assert body["configured_streams"] == 2
    by_id = {cam["device_id"]: cam for cam in body["cameras"]}
    assert by_id["CAM-ME-01"]["stream"] == {"url": "/media/demo-gate.mp4", "kind": "mp4", "label": "Demo footage"}
    assert by_id["CAM-PN-01"]["stream"] == {"url": "http://10.0.0.5/stream.mjpg", "kind": "mjpeg"}
    assert by_id["CAM-WH-A"]["stream"] is None
    assert by_id["CAM-DC-01"]["stream"] is None


def test_stream_config_missing_or_invalid(tmp_path):
    assert load_stream_config(tmp_path / "nope.json") == {}
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert load_stream_config(broken) == {}
    listing = tmp_path / "list.json"
    listing.write_text("[]", encoding="utf-8")
    assert load_stream_config(listing) == {}
