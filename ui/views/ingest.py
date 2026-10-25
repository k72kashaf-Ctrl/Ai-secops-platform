"""Ingest page — upload, manual submission, result funnel and guarded demo controls."""
from __future__ import annotations

from ui import theme
from ui.common import *  # noqa: F401,F403

SOURCE_TYPES = [
    "cctv", "anpr", "face_recognition", "mobile_phone_detection",
    "seatbelt_detection", "red_light", "radar", "access_control",
    "perimeter", "device_health", "operator_report", "emergency_alarm",
    "gps", "network_log", "weather",
]
_RESULT_KEY = "ing_last_result"


# ------------------------------------------------------------------ helpers
def _run_ingest(api: ZenithAPI, api_url: str, records: Any, origin: str) -> None:
    try:
        status_code, payload = api.ingest(records)
    except Exception as exc:
        api_error(exc, api_url)
        return
    st.session_state[_RESULT_KEY] = {"status": status_code, "payload": payload, "origin": origin}
    st.cache_data.clear()


def _funnel(status_code: int, payload: dict[str, Any], origin: str) -> str:
    total = int(payload.get("total") or 0)
    accepted = int(payload.get("accepted") or 0)
    rejected = int(payload.get("rejected") or 0)
    dup_ids = int(payload.get("duplicate_ids") or 0)
    dup_content = int(payload.get("duplicate_content") or 0)
    if status_code == 422 or (rejected and not accepted):
        verdict, tone = "Rejected", "critical"
    elif rejected or dup_ids:
        verdict, tone = "Partial", "warning"
    else:
        verdict, tone = "Complete", "ok"
    note = ("Correlation & quality analysis queued — refreshes shortly." if payload.get("analysis_pending")
            else "Correlation & quality analysis ran automatically.")
    stages = [
        ("Received", total, "#22D3EE", "#818CF8", "records in the request"),
        ("Accepted", accepted, theme.SEV["low"]["c"], "#22D3EE", "validated & stored"),
        ("Rejected", rejected, theme.SEV["critical"]["c"], "#FB7185", "failed validation · raw kept"),
        ("Duplicate IDs", dup_ids, theme.SEV["high"]["c"], "#F59E0B", "event_id already stored"),
        ("Duplicate content", dup_content, theme.SEV["medium"]["c"], "#A78BFA", "same payload, new id"),
    ]
    top = max(total, 1)
    rows = []
    for label, value, c1, c2, sub in stages:
        width = max(2.0, value / top * 100) if value else 0
        rows.append(
            f'<div style="display:grid;grid-template-columns:130px 1fr 56px;gap:12px;align-items:center;margin-bottom:8px">'
            f'<div><div style="font-size:.82rem;font-weight:500">{theme.esc(label)}</div>'
            f'<div class="zx-muted" style="font-size:.64rem">{theme.esc(sub)}</div></div>'
            f'<div style="height:12px;border-radius:999px;background:rgba(255,255,255,.05);overflow:hidden">'
            f'<div style="width:{width:.1f}%;height:100%;border-radius:999px;background:linear-gradient(90deg,{c1},{c2});box-shadow:0 0 12px {c1}88"></div></div>'
            f'<div style="font-family:var(--zx-head);font-size:1.15rem;font-weight:600;text-align:right">{value}</div></div>'
        )
    return (
        theme.section("Ingest result", f"{origin} · HTTP {status_code}",
                      theme.chip(verdict, tone))
        + "".join(rows)
        + f'<div class="zx-muted" style="font-size:.7rem;margin-top:4px">{theme.esc(note)} · batch '
          f'<span class="zx-mono">{theme.esc(payload.get("batch_id"))}</span></div>'
    )


def _rejection_rows(errors: list[Any]) -> tuple[list[dict[str, Any]], list[str]]:
    rows, notes = [], []
    for item in errors:
        if not isinstance(item, dict):
            notes.append(str(item))
            continue
        if "note" in item and "reason" not in item:
            extra = item.get("errors")
            notes.append(item["note"] + (f": {'; '.join(map(str, extra))}" if extra else ""))
            continue
        details = item.get("errors") or []
        detail_text = "; ".join(
            f"{d.get('field', 'record')}: {d.get('message', '')}" if isinstance(d, dict) else str(d) for d in details
        )
        record = item.get("event_id") or (f"record #{item['index']}" if item.get("index") is not None else "—")
        rows.append({"record": record, "reason": item.get("reason"), "detail": detail_text or "—"})
    return rows, notes


def _show_result() -> None:
    last = st.session_state.get(_RESULT_KEY)
    if not last:
        return
    payload = last["payload"]
    with st.container(key="zxg_ing_result"):
        if not isinstance(payload, dict):
            st.error(f"Unexpected response ({last['status']}): {payload}")
            return
        theme.show(_funnel(last["status"], payload, last.get("origin", "ingest")))
        rows, notes = _rejection_rows(payload.get("errors") or [])
        if rows:
            reason_tone = {"validation_error": "critical", "malformed_record": "critical", "duplicate_event_id": "warning"}
            with st.expander(f"Rejection details · {len(rows)} record(s)", expanded=last["status"] == 422):
                theme.show(theme.html_table(rows[:200], [("record", "Record"), ("reason", "Reason"), ("detail", "Field · message")], {
                    "record": lambda v, r: f'<span class="zx-mono" style="font-size:.72rem">{theme.esc(v)}</span>',
                    "reason": lambda v, r: theme.chip(theme.humanize(v), reason_tone.get(v, "neutral")),
                    "detail": lambda v, r: f'<span style="color:#C7D0E0;font-size:.78rem">{theme.esc(v)}</span>',
                }))
        for note in notes:
            theme.show(f'<div class="zx-muted" style="font-size:.72rem">{theme.esc(note)}</div>')
        if st.button("Dismiss result", key="ing_dismiss"):
            st.session_state.pop(_RESULT_KEY, None)
            st.rerun()


def _preview(records: list[Any]) -> None:
    sample = [r for r in records if isinstance(r, dict)][:8]
    if not sample:
        return
    theme.show(theme.html_table(sample, [
        ("event_id", "Event"), ("source_type", "Source"), ("device_id", "Device"),
        ("event_type", "Type"), ("occurred_at", "Occurred"), ("severity", "Severity"),
    ], {
        "event_id": lambda v, r: f'<span class="zx-mono" style="font-size:.72rem">{theme.esc(v or "— missing")}</span>',
        "source_type": lambda v, r: theme.esc(theme.humanize(v) or "—"),
        "device_id": lambda v, r: f'<span class="zx-mono" style="font-size:.7rem;color:#B4BED3">{theme.esc(v or "—")}</span>',
        "event_type": lambda v, r: theme.esc(theme.humanize(v) or "—"),
        "occurred_at": lambda v, r: f'<span class="zx-mono" style="font-size:.72rem">{theme.esc(theme.fmt_dt(v) if theme.parse_ts(v) else (v or "—"))}</span>',
        "severity": lambda v, r: theme.sev_pill(v) if v else '<span class="zx-muted">—</span>',
    }))
    if len(records) > len(sample):
        theme.show(f'<div class="zx-muted" style="font-size:.7rem">Showing {len(sample)} of {len(records)} records.</div>')


FORMAT_HELP = (
    '<div class="zx-eyebrow" style="margin-bottom:8px">ACCEPTED FORMATS</div>'
    '<div style="display:flex;flex-direction:column;gap:8px;font-size:.8rem;color:#C7D0E0;line-height:1.45">'
    '<div><b>JSON</b> — one event object, an array of events, or <span class="zx-mono" style="font-size:.72rem">{"events": [...]}</span></div>'
    '<div><b>CSV</b> — one row per event; a <span class="zx-mono" style="font-size:.72rem">payload</span> column may hold a JSON object</div>'
    '<div class="zx-muted" style="font-size:.72rem">Required: <span class="zx-mono">event_id · source_type · device_id · event_type · occurred_at</span>. '
    'Optional: received_at, zone, latitude, longitude, severity, confidence, payload.</div>'
    '<div class="zx-muted" style="font-size:.72rem">Invalid rows are rejected with reasons; their raw content is preserved. '
    'Duplicate ids are rejected, duplicate content is flagged. Analysis runs after every batch.</div></div>'
)


# --------------------------------------------------------------------- page
def render(api: ZenithAPI, api_url: str) -> None:
    theme.page_header("Event", "Ingestion",
                      "Upload JSON/CSV, submit a validated event, or load the deterministic demo dataset.",
                      status=st.session_state.get("zx_status"))
    _show_result()
    upload_tab, manual_tab, demo_tab = st.tabs(["Upload file", "Manual event", "Demo controls"])

    # ---- upload
    with upload_tab:
        left, right = st.columns([1.35, 1], gap="medium")
        with right, st.container(key="zxai_ing_help"):
            theme.show(FORMAT_HELP)
        with left, st.container(key="zxg_ing_drop"):
            theme.show(theme.section("Drop a batch", "JSON or CSV · validated server-side"))
            uploaded = st.file_uploader("Choose a JSON or CSV file", type=["json", "csv"], label_visibility="collapsed")
            if uploaded is not None:
                records, error = parse_upload(uploaded)
                if error:
                    st.error(error)
                elif records is None:
                    st.error("No records found in the file.")
                else:
                    theme.show(f'<div class="zx-row" style="margin:6px 0 8px">{theme.chip(f"{len(records)} record(s) parsed", "info")}'
                               f'<span class="zx-mono zx-muted" style="font-size:.72rem">{theme.esc(uploaded.name)}</span></div>')
                    _preview(records)
                    if records:
                        with st.expander("Raw parsed records"):
                            st.dataframe(pd.json_normalize(records, max_level=1).head(25), width="stretch", hide_index=True)
                    if st.button(f"Ingest {len(records)} record(s)", type="primary", disabled=not records, key="ing_upload_go"):
                        _run_ingest(api, api_url, records, f"upload · {uploaded.name}")
                        st.rerun()

    # ---- manual
    with manual_tab, st.container(key="zxg_ing_manual"):
        theme.show(theme.section("Manual event", "fields marked ** are required by the API schema"))
        with st.form("manual_event_form", border=False):
            col1, col2, col3 = st.columns(3)
            event_id = col1.text_input("Event ID **", value=f"EVT-WEB-{uuid.uuid4().hex[:8].upper()}")
            source_type = col1.selectbox("Source type **", SOURCE_TYPES, format_func=theme.humanize)
            device_id = col1.text_input("Device ID **", value="DEMO-DEVICE-01")
            event_type = col2.text_input("Event type **", value="motion_detected")
            occurred_at = col2.datetime_input("Occurred at (UTC) **", value=datetime.now().replace(microsecond=0))
            received_at = col2.datetime_input("Received at (UTC)", value=datetime.now().replace(microsecond=0))
            severity = col3.selectbox("Severity", SEVERITY_ORDER, index=2, format_func=lambda s: s.upper())
            confidence = col3.slider("Confidence", 0.0, 1.0, 0.8, 0.01)
            zone = col3.text_input("Zone", value="Main Entrance")
            g1, g2 = col3.columns(2)
            latitude = g1.text_input("Latitude", placeholder="51.5050")
            longitude = g2.text_input("Longitude", placeholder="-0.1250")
            payload_text = st.text_area("Payload (JSON object)", value='{"object": "person", "clip": "demo.mp4"}', height=90)
            submitted = st.form_submit_button("Submit event", type="primary")

        if submitted:
            payload_value: Any = {}
            payload_error = None
            try:
                payload_value = json.loads(payload_text) if payload_text.strip() else {}
                if not isinstance(payload_value, dict):
                    payload_error = "Payload must be a JSON object."
            except ValueError as exc:
                payload_error = f"Payload is not valid JSON: {exc}"
            lat_value = lon_value = None
            try:
                lat_value = float(latitude) if latitude.strip() else None
                lon_value = float(longitude) if longitude.strip() else None
            except ValueError:
                payload_error = "Latitude/longitude must be numeric when supplied."
            if payload_error:
                st.error(payload_error)
            else:
                record = {
                    "event_id": event_id,
                    "source_type": source_type,
                    "device_id": device_id,
                    "event_type": event_type,
                    "occurred_at": occurred_at.replace(tzinfo=timezone.utc).isoformat(),
                    "received_at": received_at.replace(tzinfo=timezone.utc).isoformat(),
                    "latitude": lat_value,
                    "longitude": lon_value,
                    "zone": zone or None,
                    "severity": severity,
                    "confidence": confidence,
                    "payload": payload_value,
                }
                _run_ingest(api, api_url, record, f"manual · {event_id}")
                st.rerun()

    # ---- demo controls / danger zone
    with demo_tab:
        theme.show(theme.glass(
            '<div class="zx-eyebrow" style="margin-bottom:6px">DEMO DATASET</div>'
            '<div style="font-size:.84rem;color:#C7D0E0;line-height:1.5">Deterministic and synthetic: related incidents, unrelated events, '
            'duplicate IDs/content, malformed records, delayed and out-of-order arrivals, conflicting sensors, low confidence, '
            'device-health failures and a spike.</div>'))
        safe, danger = st.columns([1, 1.6], gap="medium")
        with safe, st.container(key="zxg_ing_recompute"):
            theme.show(theme.section("Recompute", "safe · re-runs analysis")
                       + '<div class="zx-muted" style="font-size:.74rem;margin-bottom:10px">Rebuilds correlation and quality findings from stored events. No data is deleted.</div>')
            if st.button("Recompute correlation & quality", key="ing_recompute", use_container_width=True):
                try:
                    result = api.recompute()
                    st.success(f"Recomputed {result['incidents']} incidents and {result['quality_issues']} quality findings.")
                    st.cache_data.clear()
                except Exception as exc:
                    api_error(exc, api_url)
        with danger, st.container(key="zxg_ing_danger"):
            theme.show(
                '<div style="margin:-16px -18px 12px;padding:12px 18px;border-radius:20px 20px 0 0;'
                'background:linear-gradient(180deg,rgba(255,77,109,.18),rgba(255,77,109,.02));border-bottom:1px solid rgba(255,77,109,.25)">'
                f'<div class="zx-row">{theme.chip("DANGER ZONE", "critical")}'
                '<span class="zx-muted" style="font-size:.72rem">destructive · audited · cannot be undone</span></div></div>'
            )
            d1, d2 = st.columns(2, gap="medium")
            with d1:
                theme.show('<div style="font-size:.84rem;font-weight:600">Reload sample dataset</div>'
                           '<div class="zx-muted" style="font-size:.72rem;margin-bottom:6px">Deletes all current data, then loads the demo set.</div>')
                ok_reload = st.checkbox("I understand current data is replaced", key="ing_confirm_reload")
                if st.button("Reset & load sample", type="primary" if ok_reload else "secondary", disabled=not ok_reload, key="ing_reload", use_container_width=True):
                    try:
                        result = api.seed_sample(reset=True)
                        st.success(
                            f"Reset and loaded: {result['ingestion']['accepted']} accepted, "
                            f"{result['ingestion']['rejected']} rejected, "
                            f"{result['incidents']} incidents, {result['quality_issues']} quality findings."
                        )
                        st.cache_data.clear()
                    except Exception as exc:
                        api_error(exc, api_url)
            with d2:
                theme.show('<div style="font-size:.84rem;font-weight:600;color:#FF8FA6">Reset database</div>'
                           '<div class="zx-muted" style="font-size:.72rem;margin-bottom:6px">Deletes every event, incident and quality finding.</div>')
                confirm = st.checkbox("Confirm deletion of all data", key="ing_confirm_reset")
                if st.button("Reset database", disabled=not confirm, key="ing_reset", use_container_width=True):
                    try:
                        api.reset()
                        st.success("Database reset. All events, incidents and quality issues were deleted.")
                        st.cache_data.clear()
                    except Exception as exc:
                        api_error(exc, api_url)
