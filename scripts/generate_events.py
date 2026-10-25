"""Synthetic security-event generator for volume and performance testing.

Examples:
    python scripts/generate_events.py --count 50000 --output data/large_events.jsonl
    python scripts/generate_events.py --count 50000 --ingest --batch-size 1000 --reset
    python scripts/generate_events.py --count 50000 --ingest --output data/large_events.jsonl

The generator is streaming (it never holds the full dataset in memory) and
reproducible with --seed. It injects controlled duplicates, malformed records,
out-of-order arrivals, conflicts, low-confidence detections, unhealthy devices,
related clusters and event bursts.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
import tracemalloc
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_settings  # noqa: E402
from app.database import Database  # noqa: E402
from app.services.analysis import run_full_analysis  # noqa: E402
from app.services.ingestion import ingest_records  # noqa: E402

BASE = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)

ZONES = {
    "North Gate": (51.5074, -0.1278),
    "Warehouse A": (51.5120, -0.1200),
    "South Lot": (51.5000, -0.1350),
    "Main Entrance": (51.5050, -0.1250),
    "Perimeter North": (51.5150, -0.1300),
    "Data Center": (51.5090, -0.1320),
    "West Road": (51.5200, -0.1400),
    "East Junction": (51.4950, -0.1100),
}

TEMPLATES = [
    ("cctv", "motion_detected"),
    ("cctv", "vehicle_motion"),
    ("anpr", "speed_violation"),
    ("radar", "speed_detection"),
    ("access_control", "access_decision"),
    ("face_recognition", "access_decision"),
    ("perimeter", "perimeter_breach"),
    ("device_health", "heartbeat"),
    ("operator_report", "patrol_report"),
    ("emergency_alarm", "panic_alarm"),
    ("gps", "fleet_position"),
    ("network_log", "failed_login"),
    ("weather", "temperature_high"),
    ("mobile_phone_detection", "imsi_detected"),
    ("seatbelt_detection", "no_seatbelt"),
    ("red_light", "red_light_violation"),
]

DEVICE_PREFIX = {
    "cctv": "CAM",
    "anpr": "ANPR",
    "radar": "RAD",
    "access_control": "DOOR",
    "face_recognition": "FACE",
    "perimeter": "PER",
    "device_health": "HEALTH",
    "operator_report": "OP",
    "emergency_alarm": "ALARM",
    "gps": "GPS",
    "network_log": "IDS",
    "weather": "WX",
    "mobile_phone_detection": "MP",
    "seatbelt_detection": "SB",
    "red_light": "RL",
}


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _payload(source: str, event_type: str, rng: random.Random, index: int) -> dict[str, Any]:
    if "access" in event_type:
        return {
            "result": rng.choice(["granted", "denied"]),
            "badge": f"SYN-{rng.randint(100, 999)}",
            "subject": f"subject-{rng.randint(1, 40)}",
        }
    if source in {"anpr", "radar", "seatbelt_detection", "red_light"} or "speed" in event_type:
        return {
            "speed_kmh": rng.randint(35, 110),
            "limit_kmh": 50,
            "plate": f"SYN-{rng.randint(1000, 9999)}",
        }
    if source == "perimeter":
        return {"sector": f"P-{rng.randint(1, 9)}", "result": rng.choice(["alarm", "clear"])}
    if source == "device_health":
        return {"status": "ok", "battery_pct": rng.randint(40, 100)}
    if source == "network_log":
        return {"src_ip": f"10.20.{rng.randint(1, 20)}.{rng.randint(1, 254)}", "attempts": rng.randint(1, 9)}
    if source == "weather":
        return {"temperature_c": round(rng.uniform(8, 42), 1), "result": "normal"}
    if source == "gps":
        return {"speed_kmh": rng.randint(0, 90), "heading": rng.randint(0, 359)}
    if source == "emergency_alarm":
        return {"alarm": rng.choice(["fire", "panic", "medical"])}
    return {"object": "person", "clip": f"clip-{index}.mp4"}


class EventGenerator:
    def __init__(self, count: int = 50_000, seed: int = 42):
        self.count = count
        self.seed = seed
        self.stats = {
            "generated": 0,
            "normal": 0,
            "duplicate_content": 0,
            "duplicate_id": 0,
            "malformed": 0,
            "missing_field": 0,
            "invalid_timestamp": 0,
            "low_confidence": 0,
            "conflict": 0,
            "unhealthy_device": 0,
            "out_of_order": 0,
            "burst_records": 0,
        }

    def __iter__(self) -> Iterator[Any]:
        rng = random.Random(self.seed)
        previous: dict[str, Any] | None = None
        zone_names = list(ZONES)
        for index in range(self.count):
            base_time = BASE + timedelta(seconds=index * 20)

            # Explicit malformed / invalid cases.
            if index % 11_000 == 0 and index > 0:
                self.stats["malformed"] += 1
                self.stats["generated"] += 1
                yield "MALFORMED RECORD: expected an event object"
                continue
            if index % 7_000 == 0 and index > 0:
                self.stats["invalid_timestamp"] += 1
                self.stats["generated"] += 1
                yield {
                    "event_id": f"EVT-GEN-{index:07d}",
                    "source_type": "radar",
                    "device_id": "RAD-GEN-ERR",
                    "event_type": "speed_detection",
                    "occurred_at": "not-a-timestamp",
                    "zone": "West Road",
                    "severity": "medium",
                    "confidence": 0.5,
                    "payload": {"speed_kmh": 70},
                }
                continue
            if index % 3_000 == 0 and index > 0:
                self.stats["missing_field"] += 1
                self.stats["generated"] += 1
                record = {
                    "event_id": f"EVT-GEN-{index:07d}",
                    "source_type": "cctv",
                    "event_type": "motion_detected",
                    "occurred_at": _iso(base_time),
                    "received_at": _iso(base_time + timedelta(seconds=2)),
                    "zone": "Warehouse A",
                    "severity": "low",
                    "confidence": 0.5,
                    "payload": {"object": "person"},
                }
                if index % 6_000 == 0:
                    record["device_id"] = "CAM-GEN-MISSING"
                yield record
                continue

            # Duplicate-ID anomaly (raw copy of the previous event, altered payload).
            if index % 2_500 == 0 and previous is not None:
                self.stats["duplicate_id"] += 1
                self.stats["generated"] += 1
                duplicate = json.loads(json.dumps(previous))
                duplicate["payload"]["injected_duplicate_id"] = True
                yield duplicate
                continue

            # Duplicate-content anomaly (new event_id, identical evidence).
            if index % 1_200 == 0 and previous is not None:
                self.stats["duplicate_content"] += 1
                self.stats["generated"] += 1
                duplicate = json.loads(json.dumps(previous))
                duplicate["event_id"] = f"EVT-GEN-{index:07d}-DUP"
                duplicate["received_at"] = _iso(base_time + timedelta(seconds=90))
                yield duplicate
                continue

            # A 16-event burst in one zone takes priority so a spike is guaranteed.
            if index % 4_000 < 16 and index > 0:
                zone = zone_names[(index // 4_000) % len(zone_names)]
                source, event_type = "anpr", "speed_violation"
                occurred = BASE + timedelta(
                    seconds=(index // 4_000) * 4_000 * 20 + (index % 4_000) * 5
                )
                related = False
                self.stats["burst_records"] += 1
            # Geographic/time cluster: every 400 events, six related records.
            elif index % 400 < 6:
                zone = zone_names[(index // 400) % len(zone_names)]
                source, event_type = rng.choice(
                    [("anpr", "speed_violation"), ("radar", "speed_detection"), ("cctv", "vehicle_motion")]
                )
                occurred = BASE + timedelta(seconds=(index // 400) * 400 * 20 + (index % 400) * 8)
                related = True
            else:
                zone = rng.choice(zone_names)
                source, event_type = rng.choice(TEMPLATES)
                occurred = base_time
                related = False

            # Unhealthy device anomaly has priority over the normal template.
            unhealthy = index % 1_000 == 0 and index > 0
            if unhealthy:
                source, event_type = "device_health", "heartbeat"
                severity = "high"
                confidence = 0.99
                payload = {"status": rng.choice(["offline", "degraded", "error"]), "battery_pct": 3}
                self.stats["unhealthy_device"] += 1
            else:
                severity = rng.choices(
                    ["critical", "high", "medium", "low"],
                    weights=[1, 10, 60, 29],
                    k=1,
                )[0]
                confidence = round(rng.uniform(0.55, 0.99), 2)
                payload = _payload(source, event_type, rng, index)

            low_confidence = index % 150 == 0 and not unhealthy
            if low_confidence:
                confidence = round(rng.uniform(0.1, 0.45), 2)
                self.stats["low_confidence"] += 1

            # Conflict anomaly: same observable, different device, contradictory result.
            conflict = index % 1_800 == 1 and previous is not None
            if conflict:
                previous_payload = previous.get("payload") or {}
                if "result" in previous_payload:
                    payload = json.loads(json.dumps(previous_payload))
                    payload["result"] = "granted" if payload["result"] != "granted" else "denied"
                else:
                    payload = {"result": "denied"}
                source, event_type = previous.get("source_type", source), previous.get(
                    "event_type", event_type
                )
                zone = previous.get("zone", zone)
                occurred = BASE + timedelta(seconds=index * 20)
                confidence = 0.9
                self.stats["conflict"] += 1

            device_seq = rng.randint(1, 4)
            device_id = f"{DEVICE_PREFIX.get(source, 'DEV')}-{zone.replace(' ', '')[:8]}-{device_seq:02d}"
            received = occurred + timedelta(seconds=rng.randint(0, 5))
            if index % 700 == 0 and index > 0:
                # Long transport delay; later events arrive first.
                received = occurred + timedelta(minutes=rng.randint(25, 90))
                self.stats["out_of_order"] += 1
            elif index % 1_900 == 0 and index > 0:
                # Clock skew: claimed occurrence is after receipt.
                received = occurred - timedelta(minutes=8)

            latitude, longitude = ZONES[zone]
            event_id = f"EVT-GEN-{index:07d}"
            record = {
                "event_id": event_id,
                "source_type": source,
                "device_id": device_id,
                "event_type": event_type,
                "occurred_at": _iso(occurred),
                "received_at": _iso(received),
                "latitude": latitude + rng.uniform(-0.0003, 0.0003),
                "longitude": longitude + rng.uniform(-0.0003, 0.0003),
                "zone": zone,
                "severity": severity,
                "confidence": confidence,
                "payload": payload,
            }
            self.stats["generated"] += 1
            if not related:
                self.stats["normal"] += 1
            previous = record
            yield record


def _run_ingestion(
    db: Database,
    settings: Any,
    generator: EventGenerator,
    batch_size: int,
    analyze: bool,
) -> dict[str, Any]:
    tracemalloc.start()
    started = time.perf_counter()
    batch: list[Any] = []
    accepted = rejected = duplicate_ids = duplicate_content = 0
    batches = 0
    first_errors: list[dict[str, Any]] = []
    for record in generator:
        batch.append(record)
        if len(batch) >= batch_size:
            summary = ingest_records(
                db,
                batch,
                settings,
                run_quality=False,
                run_correlation=False,
                source="generator",
            )
            accepted += summary.accepted
            rejected += summary.rejected
            duplicate_ids += summary.duplicate_ids
            duplicate_content += summary.duplicate_content
            if summary.errors and len(first_errors) < 10:
                first_errors.extend(summary.errors[: 10 - len(first_errors)])
            batches += 1
            batch = []
    if batch:
        summary = ingest_records(
            db, batch, settings, run_quality=False, run_correlation=False, source="generator"
        )
        accepted += summary.accepted
        rejected += summary.rejected
        duplicate_ids += summary.duplicate_ids
        duplicate_content += summary.duplicate_content
        if summary.errors and len(first_errors) < 10:
            first_errors.extend(summary.errors[: 10 - len(first_errors)])
        batches += 1
    ingest_seconds = time.perf_counter() - started

    analysis_seconds = 0.0
    incidents = quality = 0
    if analyze:
        analysis_start = time.perf_counter()
        result = run_full_analysis(db, settings)
        quality, incidents = result["quality_issues"], result["incidents"]
        analysis_seconds = time.perf_counter() - analysis_start

    total_seconds = time.perf_counter() - started
    _, peak_traced = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "records_generated": generator.stats["generated"],
        "accepted": accepted,
        "rejected": rejected,
        "duplicate_ids": duplicate_ids,
        "duplicate_content_accepted": duplicate_content,
        "batches": batches,
        "ingest_seconds": round(ingest_seconds, 3),
        "ingest_records_per_second": round(accepted / ingest_seconds, 1) if ingest_seconds else None,
        "analysis_seconds": round(analysis_seconds, 3),
        "incidents": incidents,
        "quality_issues": quality,
        "total_seconds": round(total_seconds, 3),
        "peak_traced_memory_mb": round(peak_traced / 1_048_576, 1),
        "generator_stats": generator.stats,
        "first_errors": first_errors,
        "database": str(db.path),
        "db_stats": db.stats(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate/ingest synthetic SecOps events.")
    parser.add_argument("--count", type=int, default=50_000, help="Number of records (default 50000)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, help="JSONL output file")
    parser.add_argument("--ingest", action="store_true", help="Ingest directly into SQLite")
    parser.add_argument("--db", type=Path, help="SQLite database path (defaults to app setting)")
    parser.add_argument("--batch-size", type=int, default=1_000)
    parser.add_argument("--reset", action="store_true", help="Delete existing data before ingestion")
    parser.add_argument("--no-analyze", action="store_true", help="Skip final correlation/quality analysis")
    parser.add_argument("--report", type=Path, help="Write a JSON performance report")
    args = parser.parse_args()

    if args.count < 1:
        parser.error("--count must be at least 1")
    if not args.output and not args.ingest:
        args.output = ROOT / "data" / "generated_events.jsonl"

    settings = load_settings()
    if args.db:
        settings = settings.derive(db_path=args.db)
    generator = EventGenerator(count=args.count, seed=args.seed)

    report: dict[str, Any] = {}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        with args.output.open("w", encoding="utf-8") as handle:
            for record in generator:
                handle.write(json.dumps(record, default=str) + "\n")
        write_seconds = time.perf_counter() - started
        report["write_seconds"] = round(write_seconds, 3)
        report["output"] = str(args.output)
        report["file_size_bytes"] = args.output.stat().st_size
        print(
            f"Wrote {generator.stats['generated']} records to {args.output} "
            f"in {write_seconds:.2f}s ({args.output.stat().st_size / 1_048_576:.1f} MiB)"
        )
        # Regenerate deterministically for ingestion rather than loading the file.
        if args.ingest:
            generator = EventGenerator(count=args.count, seed=args.seed)

    if args.ingest:
        db = Database(settings.db_path)
        db.init_schema()
        if args.reset:
            db.reset()
            print("Database reset.")
        print(f"Ingesting into {db.path} (batch_size={args.batch_size})...")
        result = _run_ingestion(db, settings, generator, args.batch_size, not args.no_analyze)
        report["ingestion"] = result
        print(json.dumps(result, indent=2, default=str))

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"Report written to {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
