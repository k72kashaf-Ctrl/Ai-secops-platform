"""Camera wall: camera-like devices, their latest event, local clips and streams.

Read-only. "Recent" is measured in EVENT time against the latest valid event
occurrence in the database, never the wall clock.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.config import PROJECT_ROOT, Settings
from app.database import Database

log = logging.getLogger(__name__)

CAMERA_SOURCES: tuple[str, ...] = (
    "cctv",
    "anpr",
    "face_recognition",
    "red_light",
    "seatbelt_detection",
    "mobile_phone_detection",
)
STREAM_KINDS: frozenset[str] = frozenset({"mp4", "hls", "mjpeg", "image"})
ALERT_SEVERITIES: frozenset[str] = frozenset({"high", "critical"})
ALERT_KEYWORDS: tuple[str, ...] = ("motion", "person", "intrusion", "breach")
ALERT_RECENT_SECONDS = 30 * 60
_STREAM_URL_PREFIXES = ("http://", "https://", "/")

_SOURCES_SQL = ",".join(f"'{source}'" for source in CAMERA_SOURCES)
_CAMERAS_SQL = f"""
    WITH cam AS (
        SELECT event_id, device_id, source_type, zone, event_type, occurred_at,
               occurred_epoch, severity, confidence, payload
        FROM events
        WHERE validation_status = 'valid' AND source_type IN ({_SOURCES_SQL})
    ), ranked AS (
        SELECT cam.*,
               ROW_NUMBER() OVER (PARTITION BY device_id
                                  ORDER BY occurred_epoch DESC, event_id DESC) AS rn,
               COUNT(*) OVER (PARTITION BY device_id) AS n
        FROM cam
    ), clips AS (
        SELECT device_id, CAST(json_extract(payload, '$.clip') AS TEXT) AS clip,
               ROW_NUMBER() OVER (PARTITION BY device_id
                                  ORDER BY occurred_epoch DESC, event_id DESC) AS rn
        FROM cam
        WHERE json_valid(payload) AND json_extract(payload, '$.clip') IS NOT NULL
    )
    SELECT r.*, c.clip AS clip
    FROM ranked r LEFT JOIN clips c ON c.device_id = r.device_id AND c.rn = 1
    WHERE r.rn = 1
"""


def display_path(path: Path) -> str:
    """Path relative to the project root when possible (what the operator sees)."""
    try:
        return Path(path).resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def load_stream_config(path: Path) -> dict[str, dict[str, str]]:
    """Read the optional operator stream mapping; bad entries are ignored."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        log.warning("Ignoring unreadable camera stream file %s: %s", path, exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    streams: dict[str, dict[str, str]] = {}
    for device_id, entry in raw.items():
        if not isinstance(device_id, str) or not device_id or not isinstance(entry, dict):
            continue
        url, kind = entry.get("url"), entry.get("kind")
        if not isinstance(url, str) or not url.strip().startswith(_STREAM_URL_PREFIXES):
            continue
        if not isinstance(kind, str) or kind.strip().lower() not in STREAM_KINDS:
            continue
        stream = {"url": url.strip(), "kind": kind.strip().lower()}
        label = entry.get("label")
        if isinstance(label, str) and label.strip():
            # Shown on the tile, e.g. "Demo footage", so presentation clips are never mistaken for evidence.
            stream["label"] = label.strip()[:40]
        streams[device_id] = stream
    return streams


def _media_url(media_dir: Path, clip: str | None) -> str | None:
    """'/media/<clip>' only for a plain file name that exists in the media dir."""
    if not clip or clip != Path(clip).name or clip in {".", ".."} or "\\" in clip:
        return None
    try:
        if (Path(media_dir) / clip).is_file():
            return f"/media/{quote(clip)}"
    except OSError:
        return None
    return None


def _is_alert(row: dict[str, Any], latest_epoch: int | None) -> bool:
    if (row["severity"] or "").lower() in ALERT_SEVERITIES:
        return True
    event_type = (row["event_type"] or "").lower()
    if not any(keyword in event_type for keyword in ALERT_KEYWORDS):
        return False
    return latest_epoch is not None and latest_epoch - int(row["occurred_epoch"]) <= ALERT_RECENT_SECONDS


def build_cameras(db: Database, settings: Settings) -> dict[str, Any]:
    with db.read() as conn:
        latest = conn.execute(
            "SELECT occurred_epoch, occurred_at FROM events WHERE validation_status = 'valid' "
            "ORDER BY occurred_epoch DESC, occurred_at DESC LIMIT 1"
        ).fetchone()
        rows = [dict(row) for row in conn.execute(_CAMERAS_SQL).fetchall()]
    latest_epoch = int(latest["occurred_epoch"]) if latest else None
    streams = load_stream_config(settings.cameras_file)
    cameras = []
    for row in rows:
        cameras.append(
            {
                "device_id": row["device_id"],
                "source_type": row["source_type"],
                "zone": row["zone"],
                "events": int(row["n"]),
                "last_event": {
                    "event_id": row["event_id"],
                    "event_type": row["event_type"],
                    "occurred_at": row["occurred_at"],
                    "severity": row["severity"],
                    "confidence": row["confidence"],
                },
                "alert": _is_alert(row, latest_epoch),
                "clip": row["clip"],
                "media_url": _media_url(settings.media_dir, row["clip"]),
                "stream": streams.get(row["device_id"]),
            }
        )
    cameras.sort(key=lambda c: (c["device_id"]))
    cameras.sort(key=lambda c: c["last_event"]["occurred_at"] or "", reverse=True)
    cameras.sort(key=lambda c: not c["alert"])
    return {
        "cameras": cameras,
        "configured_streams": len(streams),
        "media_dir": display_path(settings.media_dir),
        "latest_occurred_at": latest["occurred_at"] if latest else None,
    }
