"""Sample dataset loading and a clean demo reset."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import Settings
from app.database import Database
from app.services.analysis import run_full_analysis
from app.services.ingestion import ingest_records


def load_sample_records(path: str | Path | None = None, settings: Settings | None = None) -> list[Any]:
    target = Path(path) if path else (settings.sample_path if settings else None)
    if target is None:
        raise FileNotFoundError("sample path is not configured")
    if not target.exists():
        raise FileNotFoundError(f"sample dataset not found: {target}")
    with target.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError("sample dataset must be a JSON array")
    return payload


def seed_sample(
    db: Database,
    settings: Settings,
    *,
    reset: bool = True,
) -> dict[str, Any]:
    """Reset (optional) and load the deterministic demonstration dataset."""
    if reset:
        db.reset()
    records = load_sample_records(settings=settings)
    summary = ingest_records(
        db,
        records,
        settings,
        run_quality=False,
        run_correlation=False,
        source="sample",
        batch_id="SAMPLE-DATASET",
    )
    result = run_full_analysis(db, settings)
    return {
        "reset": reset,
        "ingestion": summary.to_dict(),
        "quality_issues": result["quality_issues"],
        "incidents": result["incidents"],
        "stats": db.stats(),
    }
