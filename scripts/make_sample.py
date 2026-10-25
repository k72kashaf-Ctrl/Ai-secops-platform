"""Generate the deterministic demo dataset at data/sample_events.json.

The dataset is synthetic (no real people) and intentionally includes both
normal records and controlled anomalies used by the dashboard and tests.

Usage: python scripts/make_sample.py
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "data" / "sample_events.json"
BASE = datetime(2026, 3, 12, 7, 0, tzinfo=timezone.utc)

ZONES = {
    "North Gate": (51.5074, -0.1278),
    "Warehouse A": (51.5120, -0.1200),
    "South Lot": (51.5000, -0.1350),
    "Main Entrance": (51.5050, -0.1250),
    "Perimeter North": (51.5150, -0.1300),
    "Data Center": (51.5090, -0.1320),
    "West Road": (51.5200, -0.1400),
    "East Junction": (51.4950, -0.1100),
    "Remote Yard": (51.6000, -0.3000),
}


def dt(minutes: int, seconds: int = 0) -> datetime:
    return BASE + timedelta(minutes=minutes, seconds=seconds)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def event(
    event_id: str,
    source_type: str,
    device_id: str,
    event_type: str,
    occurred: datetime,
    zone: str | None,
    *,
    severity: str = "medium",
    confidence: float = 0.85,
    payload: dict | None = None,
    received: datetime | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
) -> dict:
    if zone and zone in ZONES and latitude is None and longitude is None:
        latitude, longitude = ZONES[zone]
    return {
        "event_id": event_id,
        "source_type": source_type,
        "device_id": device_id,
        "event_type": event_type,
        "occurred_at": iso(occurred),
        "received_at": iso(received or occurred + timedelta(seconds=3)),
        "latitude": latitude,
        "longitude": longitude,
        "zone": zone,
        "severity": severity,
        "confidence": confidence,
        "payload": payload or {},
    }


records: list = []

# ---------------------------------------------------------------- normal feed
records += [
    event("EVT-N001", "access_control", "DOOR-ME-01", "access_granted", dt(30), "Main Entrance",
          confidence=0.96, payload={"result": "granted", "badge": "SYN-104", "subject": "staff-07"}),
    event("EVT-N002", "cctv", "CAM-ME-01", "person_detected", dt(35), "Main Entrance",
          confidence=0.90, payload={"object": "person", "clip": "clip-0001.mp4"}),
    event("EVT-N003", "network_log", "FW-DC-01", "failed_login", dt(40), "Data Center",
          confidence=0.80, payload={"user": "svc_backup", "attempts": 3, "src_ip": "10.20.4.17"}),
    event("EVT-N004", "weather", "WX-DC-01", "temperature_high", dt(45), "Data Center",
          confidence=0.98, payload={"temperature_c": 31.2, "result": "normal"}),
    event("EVT-N005", "gps", "GPS-VAN-03", "fleet_position", dt(50), "South Lot",
          confidence=0.90, payload={"speed_kmh": 8, "heading": 180}),
    event("EVT-N006", "anpr", "ANPR-NG-01", "plate_recognized", dt(55), "North Gate",
          confidence=0.91, payload={"plate": "SYN-2201", "direction": "inbound"}),
    event("EVT-N007", "seatbelt_detection", "SB-SL-01", "no_seatbelt", dt(60), "South Lot",
          severity="medium", confidence=0.85, payload={"plate": "SYN-7710"}),
    event("EVT-N008", "radar", "RAD-WR-01", "speed_detection", dt(65), "West Road",
          confidence=0.88, payload={"speed_kmh": 90, "limit_kmh": 50}),
    event("EVT-N009", "operator_report", "OP-01", "patrol_report", dt(70), "Warehouse A",
          confidence=0.90, payload={"note": "Routine patrol complete", "operator": "OP-01"}),
    event("EVT-N010", "device_health", "HEALTH-WH-A", "heartbeat", dt(75), "Warehouse A",
          confidence=0.99, payload={"status": "ok", "battery_pct": 96}),
    event("EVT-N011", "face_recognition", "FACE-ME-01", "known_face", dt(80), "Main Entrance",
          confidence=0.92, payload={"subject": "staff-07", "match": "allowlist"}),
    event("EVT-N012", "perimeter", "PER-PN-01", "perimeter_clear", dt(85), "Perimeter North",
          confidence=0.90, payload={"result": "clear", "sector": "P-1"}),
    event("EVT-N013", "emergency_alarm", "ALARM-WH-A", "fire_alarm", dt(88), "Warehouse A",
          severity="critical", confidence=0.95, payload={"alarm": "fire", "panel": "WH-A"}),
    event("EVT-N014", "mobile_phone_detection", "MP-ME-01", "imsi_detected", dt(92), "Main Entrance",
          confidence=0.55, payload={"technology": "wifi", "count": 2}),
    event("EVT-N015", "red_light", "RL-EJ-01", "red_light_violation", dt(94), "East Junction",
          severity="high", confidence=0.90, payload={"plate": "SYN-9910", "phase": "red"}),
    event("EVT-N016", "radar", "RAD-WR-02", "speed_detection", dt(96), "West Road",
          confidence=0.87, payload={"speed_kmh": 61, "limit_kmh": 50}),
    event("EVT-N017", "radar", "RAD-WR-03", "speed_detection", dt(68), "West Road",
          confidence=0.84, payload={"speed_kmh": 56, "limit_kmh": 50}),
    event("EVT-N018", "cctv", "CAM-NG-02", "vehicle_motion", dt(100), "North Gate",
          confidence=0.80, payload={"object": "vehicle"}),
    event("EVT-N019", "gps", "GPS-VAN-04", "fleet_position", dt(102), "Warehouse A",
          confidence=0.90, payload={"speed_kmh": 4}),
    event("EVT-N020", "access_control", "DOOR-WH-A", "door_held_open", dt(104), "Warehouse A",
          confidence=0.70, payload={"result": "open", "duration_s": 42}),
    event("EVT-N021", "weather", "WX-PN-01", "wind_gust", dt(106), "Perimeter North",
          confidence=0.95, payload={"wind_kmh": 48}),
    event("EVT-N022", "device_health", "HEALTH-DC-01", "battery_low", dt(108), "Data Center",
          severity="medium", confidence=0.98, payload={"status": "degraded", "battery_pct": 12}),
    event("EVT-N023", "cctv", "CAM-DC-01", "tamper_detected", dt(112), "Data Center",
          severity="high", confidence=0.88, payload={"object": "tamper", "clip": "clip-0023.mp4"}),
    event("EVT-N024", "operator_report", "OP-02", "observation", dt(114), "Main Entrance",
          confidence=0.90, payload={"note": "Queue forming at gate", "operator": "OP-02"}),
    event("EVT-N025", "network_log", "IDS-DC-01", "malware_detected", dt(118), "Data Center",
          severity="high", confidence=0.86, payload={"host": "dc-app-07", "signature": "SYN-TEST/Malware"}),
    event("EVT-N026", "emergency_alarm", "PANIC-ME-01", "panic_alarm", dt(130), "Main Entrance",
          severity="high", confidence=0.90, payload={"alarm": "panic", "result": "alarm"}),
    event("EVT-N027", "access_control", "DOOR-NG-01", "forced_door", dt(132), "North Gate",
          severity="high", confidence=0.80, payload={"result": "alarm", "door": "NG-1"}),
    event("EVT-N028", "cctv", "CAM-SL-01", "crowd_detected", dt(134), "South Lot",
          confidence=0.75, payload={"object": "crowd", "count": 18}),
    event("EVT-N029", "weather", "WX-NG-01", "rain_heavy", dt(136), "North Gate",
          confidence=0.90, payload={"rain_mm": 14}),
    event("EVT-N030", "gps", "GPS-VAN-05", "geofence_exit", dt(138), "South Lot",
          confidence=0.88, payload={"geofence": "south-lot", "speed_kmh": 22}),
    event("EVT-N031", "network_log", "IDS-DC-02", "port_scan", dt(150), "Data Center",
          confidence=0.75, payload={"src_ip": "10.20.9.44", "ports": 120}),
    event("EVT-N032", "anpr", "ANPR-WR-01", "plate_recognized", dt(155), "West Road",
          confidence=0.90, payload={"plate": "SYN-3320", "direction": "outbound"}),
    event("EVT-N033", "face_recognition", "FACE-ME-02", "unknown_face", dt(165), "Main Entrance",
          confidence=0.62, payload={"subject": "unknown", "match": "no_match"}),
    event("EVT-N034", "mobile_phone_detection", "MP-WH-A", "imsi_detected", dt(170), "Warehouse A",
          confidence=0.50, payload={"technology": "cellular", "count": 1}),
    event("EVT-N035", "perimeter", "PER-DC-01", "fence_vibration", dt(172), "Data Center",
          confidence=0.60, payload={"sector": "DC-3", "amplitude": 0.31}),
    event("EVT-N036", "emergency_alarm", "MED-WH-A", "medical_alarm", dt(180), "Warehouse A",
          severity="high", confidence=0.95, payload={"alarm": "medical"}),
    event("EVT-N037", "operator_report", "OP-03", "shift_handover", dt(185), "Data Center",
          confidence=0.90, payload={"note": "Handover completed", "operator": "OP-03"}),
    event("EVT-N038", "seatbelt_detection", "SB-WR-02", "no_seatbelt", dt(192), "West Road",
          confidence=0.80, payload={"plate": "SYN-5110"}),
    event("EVT-N039", "radar", "RAD-EJ-01", "speed_detection", dt(198), "East Junction",
          confidence=0.90, payload={"speed_kmh": 74, "limit_kmh": 50}),
    event("EVT-N040", "device_health", "HEALTH-SL-01", "heartbeat", dt(202), "South Lot",
          confidence=0.99, payload={"status": "ok", "battery_pct": 88}),
    event("EVT-N041", "cctv", "CAM-WH-A", "motion_detected", dt(204), "Warehouse A",
          confidence=0.70, payload={"object": "motion", "clip": "clip-0041.mp4"}),
]

# ------------------------------------------- related traffic group, North Gate
records += [
    event("EVT-T1001", "anpr", "ANPR-NG-02", "speed_violation", dt(120, 10), "North Gate",
          severity="high", confidence=0.93,
          payload={"plate": "SYN-4821", "speed_kmh": 82, "limit_kmh": 50}),
    event("EVT-T1002", "radar", "RAD-NG-04", "speed_detection", dt(120, 12), "North Gate",
          severity="high", confidence=0.90,
          payload={"plate": "SYN-4821", "speed_kmh": 84, "limit_kmh": 50}),
    event("EVT-T1003", "red_light", "RL-NG-01", "red_light_violation", dt(120, 15), "North Gate",
          severity="high", confidence=0.88,
          received=dt(121, 45), payload={"plate": "SYN-4821", "phase": "red", "result": "violation"}),
    event("EVT-T1004", "cctv", "CAM-NG-03", "vehicle_motion", dt(120, 20), "North Gate",
          severity="high", confidence=0.85, payload={"object": "vehicle", "plate_hint": "SYN-4821"}),
]

# ---------------------------------------- related intrusion group, Perimeter N
records += [
    event("EVT-I2001", "perimeter", "PER-PN-02", "perimeter_breach", dt(140), "Perimeter North",
          severity="critical", confidence=0.92,
          payload={"sector": "P-4", "result": "alarm", "sensor": "fence-04"}),
    event("EVT-I2002", "cctv", "CAM-PN-01", "motion_detected", dt(140, 30), "Perimeter North",
          severity="high", confidence=0.86, payload={"object": "person", "clip": "clip-0201.mp4"}),
    event("EVT-I2003", "operator_report", "OP-04", "intrusion_confirmed", dt(141), "Perimeter North",
          severity="high", confidence=0.90,
          payload={"note": "Patrol observed movement near fence P-4", "operator": "OP-04"}),
]

# ----------------------------------------------- related access group + conflict
records += [
    event("EVT-A3001", "access_control", "DOOR-ME-02", "access_decision", dt(160), "Main Entrance",
          severity="high", confidence=0.90,
          payload={"result": "denied", "badge": "SYN-331", "subject": "visitor-12"}),
    event("EVT-A3002", "face_recognition", "FACE-ME-03", "access_decision", dt(160, 20), "Main Entrance",
          severity="medium", confidence=0.28,
          payload={"result": "granted", "subject": "visitor-12", "match": "tentative"}),
    event("EVT-A3003", "access_control", "DOOR-ME-03", "access_decision", dt(161), "Main Entrance",
          severity="medium", confidence=0.85,
          payload={"result": "granted", "badge": "SYN-331", "subject": "visitor-12"}),
]

# ------------------------------------------------------- duplicate content case
access_source = next(
    record for record in records
    if isinstance(record, dict) and record.get("event_id") == "EVT-A3001"
)
duplicate = json.loads(json.dumps(access_source))
duplicate["event_id"] = "EVT-D6002"
duplicate["received_at"] = iso(dt(161, 10))
records.append(duplicate)

# ----------------------------------------------------------- duplicate ID case
duplicate_id = json.loads(json.dumps(access_source))
duplicate_id["payload"] = {"result": "granted", "badge": "SYN-331", "subject": "visitor-12"}
duplicate_id["received_at"] = iso(dt(162))
records.append(duplicate_id)

# ------------------------------------------------------------- rejected records
records += [
    {"event_id": "EVT-BAD7001", "source_type": "cctv", "event_type": "motion_detected",
     "occurred_at": iso(dt(175)), "zone": "Warehouse A", "severity": "low",
     "payload": {"object": "motion"}},  # missing device_id
    {"event_id": "EVT-BAD7002", "source_type": "radar", "device_id": "RAD-BAD-01",
     "event_type": "speed_detection", "zone": "West Road", "severity": "medium",
     "payload": {"speed_kmh": 999}},  # missing occurred_at
    {"event_id": "EVT-BAD7003", "source_type": "anpr", "device_id": "ANPR-BAD-01",
     "event_type": "plate_recognized", "occurred_at": "12/03/2026 09:15",
     "zone": "North Gate", "severity": "medium", "payload": {"plate": "BAD-TIME"}},
    {"source_type": "operator_report", "device_id": "OP-BAD", "event_type": "observation",
     "occurred_at": iso(dt(176)), "zone": "Main Entrance", "severity": "low",
     "payload": {"note": "no event id"}},  # missing event_id
    "this is not an event object",  # malformed record
]

# ------------------------------------------------- delayed and out-of-order feed
records += [
    event("EVT-W7001", "weather", "WX-NG-02", "temperature_warning", dt(10), "North Gate",
          severity="medium", confidence=0.92, received=dt(190),
          payload={"temperature_c": 41.0, "threshold_c": 38.0}),
    event("EVT-OOO8001", "cctv", "CAM-WH-A", "person_detected", dt(120), "Warehouse A",
          confidence=0.80, received=dt(110), payload={"object": "person", "clip": "clip-0801.mp4"}),
    event("EVT-OOO8002", "cctv", "CAM-WH-A", "person_detected", dt(100), "Warehouse A",
          confidence=0.85, received=dt(150), payload={"object": "person", "clip": "clip-0802.mp4"}),
    event("EVT-GPS9001", "gps", "GPS-TRUCK-01", "fleet_position", dt(200), "South Lot",
          confidence=0.80, received=dt(140), payload={"speed_kmh": 55}),
]

# ------------------------------------------ conflicting location, same device
records += [
    event("EVT-LOC9501", "cctv", "CAM-DC-01", "motion_detected", dt(170), "Data Center",
          confidence=0.80, payload={"object": "motion", "clip": "clip-9501.mp4"}),
    event("EVT-LOC9502", "cctv", "CAM-DC-01", "motion_detected", dt(171), "Remote Yard",
          confidence=0.80, payload={"object": "motion", "clip": "clip-9502.mp4"},
          latitude=51.6000, longitude=-0.3000),
]

# ------------------------------------- repeated device burst / event-spike case
for index in range(16):
    occurred = dt(116, index * 10)  # 08:56:00 - 08:58:30
    records.append(
        event(
            f"EVT-SPIKE{5001 + index}",
            "anpr",
            "ANPR-SL-01",
            "speed_violation",
            occurred,
            "South Lot",
            severity="medium",
            confidence=0.80,
            payload={"plate": f"SYN-{7000 + index}", "speed_kmh": 68 + index, "limit_kmh": 40},
        )
    )

# Shuffle deterministically so arrival order is not chronological.
records = records[::2] + records[1::2]

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
with OUTPUT.open("w", encoding="utf-8") as handle:
    json.dump(records, handle, indent=2)
    handle.write("\n")

print(f"Wrote {len(records)} sample records to {OUTPUT}")
