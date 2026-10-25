"""Application configuration.

All thresholds are deterministic and can be overridden with environment
variables, which makes the behaviour testable and tunable without code edits.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "zenith_secops.db"
DEFAULT_SAMPLE_PATH = PROJECT_ROOT / "data" / "sample_events.json"
DEFAULT_MEDIA_DIR = PROJECT_ROOT / "data" / "media"
DEFAULT_CAMERAS_FILE = PROJECT_ROOT / "data" / "cameras.json"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _analysis_mode(value: str) -> str:
    mode = (value or "").strip().lower()
    return mode if mode in {"sync", "deferred"} else "deferred"


@dataclass(frozen=True)
class Settings:
    """Runtime configuration for ingestion, quality and correlation rules."""

    db_path: Path = DEFAULT_DB_PATH
    sample_path: Path = DEFAULT_SAMPLE_PATH
    # Camera wall: local clip directory served read-only at /media, and an
    # optional operator file mapping device_id -> {"url", "kind"} live streams.
    media_dir: Path = DEFAULT_MEDIA_DIR
    cameras_file: Path = DEFAULT_CAMERAS_FILE

    # Ingestion / data quality
    delayed_threshold_seconds: int = 300
    clock_skew_tolerance_seconds: int = 60
    low_confidence_threshold: float = 0.5
    repeated_event_threshold: int = 8
    repeated_event_window_minutes: int = 10
    spike_threshold: int = 15
    spike_window_minutes: int = 5
    conflict_window_minutes: int = 5
    conflict_speed_delta_kmh: float = 15.0

    # Correlation
    correlation_window_minutes: int = 15
    geo_cell_degrees: float = 0.01  # ~1.1 km at the equator
    possible_association_confidence_factor: float = 0.6

    # Briefing
    briefing_window_hours: int = 24
    briefing_top_incidents: int = 5

    # UI/API
    api_base_url: str = "http://localhost:8000"
    admin_api_key: str | None = None
    reasoning_provider: str = "deterministic"
    rate_limit_per_minute: int = 600
    # /ingest is metered in records (not requests) so one-record-per-POST
    # clients and 500-record batches are treated fairly; 0 disables the limit.
    ingest_records_per_minute: int = 300_000

    # Post-ingestion analysis: "deferred" inserts and recomputes in a debounced
    # background worker; "sync" recomputes inside every /ingest request.
    analysis_mode: str = "deferred"
    analysis_debounce_seconds: float = 2.0
    analysis_max_delay_seconds: float = 30.0
    cors_origins: tuple[str, ...] = (
        "http://localhost:8501",
        "http://127.0.0.1:8501",
        "http://localhost:3000",
    )

    extra: dict = field(default_factory=dict)

    def derive(self, **changes) -> "Settings":
        """Return a copy with selected values replaced (used by tests)."""
        return replace(self, **changes)


def load_settings() -> Settings:
    """Build settings from environment variables with safe defaults."""
    origins = os.getenv("ZENITH_CORS_ORIGINS", "")
    cors = tuple(o.strip() for o in origins.split(",") if o.strip()) if origins else (
        "http://localhost:8501",
        "http://127.0.0.1:8501",
        "http://localhost:3000",
    )
    db_env = os.getenv("ZENITH_DB_PATH")
    sample_env = os.getenv("ZENITH_SAMPLE_PATH")
    media_env = os.getenv("ZENITH_MEDIA_DIR")
    cameras_env = os.getenv("ZENITH_CAMERAS_FILE")
    return Settings(
        db_path=Path(db_env).expanduser() if db_env else DEFAULT_DB_PATH,
        sample_path=Path(sample_env).expanduser() if sample_env else DEFAULT_SAMPLE_PATH,
        media_dir=Path(media_env).expanduser() if media_env else DEFAULT_MEDIA_DIR,
        cameras_file=Path(cameras_env).expanduser() if cameras_env else DEFAULT_CAMERAS_FILE,
        delayed_threshold_seconds=_env_int("ZENITH_DELAYED_THRESHOLD_SECONDS", 300),
        clock_skew_tolerance_seconds=_env_int("ZENITH_CLOCK_SKEW_TOLERANCE_SECONDS", 60),
        low_confidence_threshold=_env_float("ZENITH_LOW_CONFIDENCE_THRESHOLD", 0.5),
        repeated_event_threshold=_env_int("ZENITH_REPEATED_EVENT_THRESHOLD", 8),
        repeated_event_window_minutes=_env_int("ZENITH_REPEATED_EVENT_WINDOW_MINUTES", 10),
        spike_threshold=_env_int("ZENITH_SPIKE_THRESHOLD", 15),
        spike_window_minutes=_env_int("ZENITH_SPIKE_WINDOW_MINUTES", 5),
        conflict_window_minutes=_env_int("ZENITH_CONFLICT_WINDOW_MINUTES", 5),
        conflict_speed_delta_kmh=_env_float("ZENITH_CONFLICT_SPEED_DELTA_KMH", 15.0),
        correlation_window_minutes=_env_int("ZENITH_CORRELATION_WINDOW_MINUTES", 15),
        geo_cell_degrees=_env_float("ZENITH_GEO_CELL_DEGREES", 0.01),
        briefing_window_hours=_env_int("ZENITH_BRIEFING_WINDOW_HOURS", 24),
        briefing_top_incidents=_env_int("ZENITH_BRIEFING_TOP_INCIDENTS", 5),
        api_base_url=os.getenv("ZENITH_API_URL", "http://localhost:8000"),
        admin_api_key=os.getenv("ZENITH_ADMIN_API_KEY") or None,
        reasoning_provider=os.getenv("ZENITH_REASONING_PROVIDER", "deterministic").strip().lower(),
        rate_limit_per_minute=_env_int("ZENITH_RATE_LIMIT_PER_MINUTE", 600),
        ingest_records_per_minute=_env_int("ZENITH_INGEST_RECORDS_PER_MINUTE", 300_000),
        analysis_mode=_analysis_mode(os.getenv("ZENITH_ANALYSIS_MODE", "deferred")),
        analysis_debounce_seconds=_env_float("ZENITH_ANALYSIS_DEBOUNCE_SECONDS", 2.0),
        analysis_max_delay_seconds=_env_float("ZENITH_ANALYSIS_MAX_DELAY_SECONDS", 30.0),
        cors_origins=cors,
    )
