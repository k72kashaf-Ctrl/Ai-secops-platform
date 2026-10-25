"""Command page — operations center in the 'Sensor Pulse' layout (design option 4).

Left: KPI strip, numbered view switcher (holo-map · sensor pulse · event flow · data trust)
and the selected view. Right: active-incident card with Overview / Evidence / Entities /
Timeline. Bottom: operator action bar. Every change still needs an explicit Confirm.
"""
from __future__ import annotations

import json

from ui import components, holo, pulse, theme, threatmap
from ui.common import *  # noqa: F401,F403

MODES = [
    ("map", ":material/view_in_ar: **Facility map**", "Facility", "map", "Live site security · zones streaming into the analysis core"),
    ("threat", ":material/public: **Threat map**", "Threat", "map", "Threat routes across zones · patterns, location jumps, entities"),
    ("cameras", ":material/videocam: **Camera wall**", "Camera", "wall", "Every camera's latest detection · live or recorded video when connected"),
    ("pulse", ":material/sensors: **Sensor pulse**", "Sensor", "pulse", "System health & telemetry · devices, activity, anomalies"),
]
INCIDENTS_PAGE = ":material/crisis_alert: Incidents"


@st.cache_data(ttl=5, show_spinner=False)
def load_situation(base_url: str) -> dict[str, Any]:
    return ZenithAPI(base_url).situation()


def _empty_state(api: ZenithAPI, api_url: str) -> None:
    theme.show(theme.agent_brief(
        "No events yet. Load the deterministic demo dataset to see correlation, data-quality checks and the agent at work.",
        [], "ZENITH BRIEF · WAITING FOR DATA"))
    if st.button("Load sample dataset", type="primary"):
        try:
            result = api.seed_sample(reset=True)
            st.success(f"Loaded {result['ingestion']['accepted']} accepted records, "
                       f"{result['incidents']} incidents and {result['quality_issues']} quality findings.")
            st.cache_data.clear()
            st.rerun()
        except Exception as exc:
            api_error(exc, api_url)


# ------------------------------------------------------------------ views
def _kpis(k: dict[str, Any]) -> None:
    sev = k["by_severity"]
    d1 = theme.pct_delta(k["incidents_last"], k["incidents_prev"])
    d2 = theme.pct_delta(k["crit_high_last"], k["crit_high_prev"])
    d3 = theme.pct_delta(k["events_last"], k["events_prev"])
    cards = [
        ("Open incidents", k["open_incidents"], f"{sev['critical']} critical · {sev['high']} high · {sev['medium']} medium",
         d1[0], d1[1] if d1[1] != "warning" else "neutral", k["incidents_series"], "#FF4D6D", "#FDA4AF", "incident"),
        ("Critical + high, new", k["crit_high_last"], f"last {k['window_minutes']} min vs previous ({k['crit_high_prev']})",
         d2[0], "critical" if d2[1] == "warning" else d2[1], k["crit_high_series"], "#22D3EE", "#67E8F9", "bell"),
        ("Events analysed", k["valid_events"], f"{k['received']} received · {k['rejected']} rejected · {k['duplicates']} duplicate",
         d3[0], "neutral", k["events_series"], "#A78BFA", "#DDD6FE", "events"),
        ("Quality flags", k["quality_issues"], f"{k['conflicts']} conflicts · {k['uncorroborated']} single-source · {k['spikes']} spike",
         f"{k['unhealthy_devices']} device", "warning" if k["unhealthy_devices"] else "ok",
         [k["conflicts"], k["uncorroborated"], k["spikes"], k["unhealthy_devices"], k["quality_issues"]], "#FBBF24", "#FDE68A", "trust"),
    ]
    for col, (i, card) in zip(st.columns(4), enumerate(cards)):
        with col:
            theme.show(theme.kpi_card(*card[:9], gid=f"zxk{i}"))


def _mode_strip() -> None:
    current = st.session_state.get("cmd_mode", "map")
    for i, (col, (key, label, *_)) in enumerate(zip(st.columns(len(MODES)), MODES)):
        on = key == current
        with col, st.container(key=f"zxmode{'on' if on else ''}_{key}"):
            if st.button(f"{i + 1} · {label}", key=f"cmd_mode_{key}", use_container_width=True) and not on:
                st.session_state["cmd_mode"] = key
                st.rerun()
    idx = next(i for i, m in enumerate(MODES) if m[0] == current)
    theme.show(pulse.mode_progress(idx, len(MODES)))


def _view_pulse(data: dict[str, Any]) -> None:
    act = data["activity"]
    buckets = act["buckets"]
    groups = [s for s in data["sensors"] if s["devices"]][:4]
    cols = st.columns([1] * len(groups) + [1.25])
    for i, (col, s) in enumerate(zip(cols, groups)):
        series = [b["counts"].get(s["group"], 0) for b in buckets]
        col.markdown(pulse.health_card(s["group"], s["devices"] - s["flagged"], s["devices"], series, f"zxh{i}"),
                     unsafe_allow_html=True)
    cols[-1].markdown(pulse.flagged_panel(data["sensors"]), unsafe_allow_html=True)
    st.html('<div style="height:14px"></div>')
    left, right = st.columns(2)
    labels = pulse.hour_labels(buckets)
    shown = [g for g in act["groups"] if any(b["counts"].get(g) for b in buckets)]
    left.markdown(pulse.line_chart(
        "Sensor activity", f"valid events per {act['bucket_minutes']} min · event time",
        {g: [b["counts"].get(g, 0) for b in buckets] for g in shown}, labels,
        anomalies=pulse.spike_indices(buckets, data["feed"]), gid="lca"), unsafe_allow_html=True)
    qt = data.get("quality_trend") or {"buckets": [], "families": [], "untimed": 0}
    right.markdown(pulse.line_chart(
        "Data-quality findings", f"by family · on the flagged event's time · {qt['untimed']} untimed",
        {f: [b["counts"].get(f, 0) for b in qt["buckets"]] for f in qt["families"]},
        pulse.hour_labels(qt["buckets"]), gid="lcq"), unsafe_allow_html=True)


def _view_map(data: dict[str, Any], k: dict[str, Any]) -> None:
    callouts = [
        {"zone": inc["zones"][0], "severity": "critical", "at": inc["created_at"], "conf": f"{inc['confidence']:.2f}",
         "title": inc["title"].split(" at ")[0], "sub": " · ".join(inc.get("sources") or []).replace("_", " ")}
        for inc in data["priority"] if inc["severity"] == "critical" and inc.get("zones")
    ]
    theme.show(holo.holo_map(data["zones"], callouts=callouts, height=640, analysed=k["valid_events"]))


def _view_threat(data: dict[str, Any]) -> None:
    routes = data.get("routes") or []
    theme.show(threatmap.threat_map(data["zones"], routes, data.get("campaign")))
    if routes:
        kinds = {"pattern": ("Possible pattern", "warning"), "location_conflict": ("Location jump", "info"), "entity": ("Entity movement", "critical")}
        rows = "".join(
            f'<tr><td>{theme.chip(kinds.get(r.get("kind"), (r.get("kind"), "neutral"))[0], kinds.get(r.get("kind"), ("", "neutral"))[1])}</td>'
            f'<td>{theme.esc(r.get("from_zone"))} → {theme.esc(r.get("to_zone"))}</td><td>{theme.esc(r.get("label"))}</td>'
            f'<td class="zx-mono" style="font-size:.7rem">{theme.esc(theme.fmt_time(r.get("at")))}</td>'
            f'<td>{"".join(theme.ev_link(x) for x in (r.get("refs") or [])[:2])}</td></tr>' for r in routes[:8])
        theme.show(f'<div class="zx-glass" style="margin-top:12px"><div class="zx-h" style="margin-bottom:6px">Routes on the map</div>'
                   f'<div class="zx-muted" style="font-size:.72rem;margin-bottom:6px">“Possible pattern” is an inference (same category, different zones, '
                   f'within 60 min) — not proof the incidents are related.</div><table class="zx-table"><thead><tr><th>Kind</th><th>Route</th><th>Why</th>'
                   f'<th>Time</th><th>Evidence</th></tr></thead><tbody>{rows}</tbody></table></div>')


@st.cache_data(ttl=5, show_spinner=False)
def load_cameras(base_url: str) -> dict[str, Any]:
    return ZenithAPI(base_url).cameras()


def _view_cameras(api: ZenithAPI) -> None:
    try:
        data = load_cameras(api.base_url)
    except Exception as exc:
        st.warning(f"Camera list unavailable: {exc}")
        return
    cams = data.get("cameras") or []
    alerts = sum(1 for c in cams if c.get("alert"))
    playable = sum(1 for c in cams if c.get("media_url") or (c.get("stream") or {}).get("url"))
    head_l, head_r = st.columns([1.4, 1])
    head_l.html(f'<div class="zx-row" style="gap:8px;flex-wrap:wrap">{theme.chip(f"{len(cams)} cameras", "info")}'
                f'{theme.chip(f"{alerts} alerting", "critical" if alerts else "ok")}{theme.chip(f"{playable} with video", "ok" if playable else "neutral")}</div>')
    view = head_r.segmented_control("Cameras", ["All", "Alerts", "Video"], default="All", key="cmd_cam_filter",
                                    label_visibility="collapsed")
    shown = [c for c in cams if view in (None, "All") or (view == "Alerts" and c.get("alert"))
             or (view == "Video" and (c.get("media_url") or (c.get("stream") or {}).get("url")))]
    # Alerting cameras first, then cameras that have video, then the rest (API order within each).
    shown.sort(key=lambda c: (not c.get("alert"), not (c.get("media_url") or (c.get("stream") or {}).get("url"))))
    if not shown:
        theme.show(theme.empty("No cameras match this filter."))
    cols = st.columns(3)
    for i, cam in enumerate(shown[:12]):
        cols[i % 3].markdown(threatmap.camera_tile(cam, api.base_url), unsafe_allow_html=True)
    if len(shown) > 12:
        theme.show(f'<div class="zx-muted" style="font-size:.72rem">+{len(shown) - 12} more cameras — filter by Alerts to focus.</div>')
    if not playable:
        theme.show(
            f'<div class="zx-glass" style="margin-top:4px;border-color:rgba(251,191,36,.35)"><div style="font-weight:600;font-size:.86rem">'
            f'No video source is connected</div><div class="zx-muted" style="font-size:.76rem;line-height:1.5;margin-top:4px">'
            f'Each tile shows the camera’s real latest detection. Zenith ingests event records, not video, so it never fakes footage. To show video: '
            f'put the clips referenced by events (e.g. <code>clip-0201.mp4</code>) in <code>{theme.esc(data.get("media_dir") or "data/media")}</code>, '
            f'or map cameras to stream URLs in <code>data/cameras.json</code> (format in <code>data/cameras.example.json</code>).</div></div>')


# ------------------------------------------------------------ incident card
def _incident_panel(api: ZenithAPI, api_url: str, data: dict[str, Any]) -> str | None:
    priority = data["priority"]
    with st.container(key="zxg_active"):
        theme.show(theme.section(
            "ACTIVE INCIDENTS", "",
            f'<span class="zx-mono" style="font-size:.7rem;font-weight:700;color:#fff;background:#FF4D6D;border-radius:999px;padding:2px 8px;'
            f'box-shadow:0 0 10px #FF4D6D">{len(priority)}</span>'))
        if not priority:
            theme.show('<div class="zx-muted">No corroborated open incidents.</div>')
            return None
        ids = [p["incident_id"] for p in priority]
        current = st.session_state.get("cmd_incident")
        if current not in ids:
            current = ids[0]
        names = {p["incident_id"]: f'{p["severity"][:4].upper()} · {p["title"].split(" at ")[0][:22]}' for p in priority}
        picked = st.selectbox("Incident", ids, index=ids.index(current), format_func=names.get,
                              key="cmd_incident_pick", label_visibility="collapsed")
        st.session_state["cmd_incident"] = picked
        try:
            inc, reasoning = components.load_incident(api_url, picked)
        except Exception as exc:
            st.warning(f"Could not load {picked}: {exc}")
            return picked
        timeline = inc.get("timeline") or []
        latest = data.get("latest_occurred_at")
        theme.show(pulse.incident_head(inc, latest))
        devices = inc.get("devices") or []
        ov, evd, ent, tl = st.tabs(["Overview", f"Evidence {len(timeline)}",
                                    f"Entities {len(devices) + len(inc.get('locations') or [])}", "Timeline"])
        first = timeline[0]["occurred_at"] if timeline else inc.get("created_at")
        with ov:
            lead = next((e for e in timeline if e.get("source_type") in ("cctv", "anpr", "face_recognition")),
                        timeline[0] if timeline else None)
            top_l, top_r = st.columns([1, 1.5])
            if lead:
                top_l.markdown(pulse.evidence_tile(lead, active=True, big=True), unsafe_allow_html=True)
            top_r.markdown(pulse.conf_block(float(inc.get("confidence") or 0), inc.get("status") or "open", first, latest),
                           unsafe_allow_html=True)
            inference = (reasoning.get("inferences") or [""])[0]
            inference = inference if len(inference) <= 190 else inference[:189].rsplit(" ", 1)[0] + "…"
            theme.show(f'<div style="font-size:.8rem;color:#C7D0E0;line-height:1.5;margin:10px 0">{theme.esc(theme.detime(inference))}</div>')
            theme.show(f'<div style="font-weight:600;font-size:.86rem;margin-bottom:6px">Verified evidence ({len(timeline)})</div>'
                       f'<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(112px,1fr));gap:8px">'
                       + "".join(pulse.evidence_tile(e, active=e is lead) for e in timeline[:4]) + "</div>")
            facts = [f"{theme.humanize(e.get('event_type'))} · {e.get('device_id')}" for e in timeline[:4]]
            conflicts = [theme.detime(u) for u in dict.fromkeys(reasoning.get("uncertainty") or [])][:2]
            findings = "".join(
                f'<div style="display:flex;gap:8px;align-items:flex-start;font-size:.76rem;color:#DCE3F0;margin-bottom:6px">'
                f'{theme.kind_badge(kind)}<span>{theme.esc(text)}</span></div>'
                for kind, text in [("FACT", f) for f in facts] + [("CONFLICT", c[:120]) for c in conflicts])
            ents = "".join(
                f'<div style="display:flex;gap:8px;align-items:center;font-size:.76rem;margin-bottom:7px">'
                f'{pulse._icon(pulse.SOURCE_ICONS.get(src, pulse.DEFAULT_ICON), 16, "#7DD3FC")}'
                f'<span><span class="zx-mono" style="color:#E6EBF5">{theme.esc(dev)}</span><br>'
                f'<span style="font-size:.66rem;color:#8B97B0">{theme.esc(theme.humanize(src))}</span></span></div>'
                for dev, src in dict.fromkeys((e.get("device_id"), e.get("source_type")) for e in timeline))
            theme.show(f'<div style="display:grid;grid-template-columns:1.35fr 1fr;gap:14px;margin-top:12px">'
                       f'<div><div style="font-weight:600;font-size:.86rem;margin-bottom:8px">Key findings</div>{findings}</div>'
                       f'<div><div style="font-weight:600;font-size:.86rem;margin-bottom:8px">Related entities</div>{ents}</div></div>')
        with evd:
            theme.show('<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:8px">'
                       + "".join(pulse.evidence_tile(e, big=True) for e in timeline) + "</div>")
            with st.expander("Raw payloads"):
                st.code(json.dumps([{"event_id": e.get("event_id"), "payload": e.get("payload")} for e in timeline], indent=2),
                        language="json")
        with ent:
            chips = ([("ZONE", z) for z in inc.get("locations") or []] + [("DEVICE", d) for d in devices]
                     + [("SOURCE", theme.humanize(s)) for s in inc.get("sources") or []])
            theme.show('<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">' + "".join(
                f'<div style="border-radius:12px;padding:10px 12px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.07)">'
                f'<div class="zx-mono" style="font-size:.6rem;letter-spacing:.1em;color:#7DD3FC">{kind}</div>'
                f'<div style="font-family:var(--zx-head);font-weight:600">{theme.esc(v)}</div></div>' for kind, v in chips) + "</div>")
        with tl:
            theme.show(theme.evidence_chain(timeline) + "".join(
                f'<div style="display:grid;grid-template-columns:70px 14px 1fr;gap:10px;min-height:48px">'
                f'<span class="zx-mono" style="font-size:.74rem;color:#DCE3F0">{theme.esc(theme.fmt_time(e.get("occurred_at"), True))}</span>'
                f'<span style="display:flex;flex-direction:column;align-items:center"><span style="width:11px;height:11px;border-radius:50%;'
                f'background:{theme.SEV[theme.sev_of(e.get("severity"))]["c"]};box-shadow:0 0 10px {theme.SEV[theme.sev_of(e.get("severity"))]["c"]}"></span>'
                f'<span style="flex:1;width:2px;background:rgba(255,255,255,.08)"></span></span>'
                f'<span style="font-size:.78rem">{theme.esc(theme.humanize(e.get("event_type")))}<br><span class="zx-mono" style="font-size:.64rem;color:#8B97B0">'
                f'{theme.esc(e.get("event_id"))} · {theme.esc(e.get("device_id"))}</span></span></div>' for e in timeline))
    return picked


# ------------------------------------------------------------- action bar
def _action_bar(api: ZenithAPI, api_url: str, incident_id: str | None) -> None:
    pending = st.session_state.get("cmd_pending")
    if incident_id and pending:
        try:
            current = components.load_incident(api_url, incident_id)[0].get("status") or "open"
        except Exception:
            current = "open"
        target, assignee = pending
        what = (f"{theme.esc(theme.humanize(current))} → <b>{theme.esc(theme.humanize(target))}</b>"
                + (f" · assign to <b>{theme.esc(assignee)}</b>" if assignee else ""))
        theme.show(f'<div style="padding:10px 14px;border-radius:12px;background:rgba(251,191,36,.10);border:1px solid rgba(251,191,36,.45);'
                   f'font-size:.84rem;margin-bottom:8px">Confirm change for <b>{theme.esc(incident_id)}</b>: {what}? '
                   f'Nothing changes until you confirm.</div>')
        a, b, _ = st.columns([1, 1, 4])
        if a.button("Confirm", key="cmd_confirm", type="primary", use_container_width=True):
            try:
                api.update_incident_status(incident_id, target, assignee or None)
                st.session_state["cmd_done"] = f"{incident_id} → {theme.humanize(target)}" + (f" · {assignee}" if assignee else "")
                st.session_state.pop("cmd_pending", None)
                st.cache_data.clear()
                st.rerun()
            except Exception as exc:
                st.error(f"Update failed: {exc}")
        if b.button("Cancel", key="cmd_cancel", use_container_width=True):
            st.session_state.pop("cmd_pending", None)
            st.rerun()
    done = st.session_state.pop("cmd_done", None)
    if done:
        st.toast(f"✓ {done} · audit entry written")

    with st.container(key="zxbar"):
        cols = st.columns([1.05, 1.6, 0.95, 1.15, 1.05, 1.1, 0.95, 0.85], vertical_alignment="center")
        cols[0].html('<span class="zx-mono" style="font-size:.7rem;letter-spacing:.12em;color:#9FB3C8">OPERATOR<br>ACTIONS</span>')
        disabled = incident_id is None
        with cols[1], st.container(key="zxbar_ack"):
            if st.button(":material/warning: Acknowledge incident", key="cmd_ack", use_container_width=True, disabled=disabled):
                st.session_state["cmd_pending"] = ("acknowledged", "")
                st.rerun()
        with cols[2].popover(":material/person_add: Assign", use_container_width=True, disabled=disabled):
            who = st.text_input("Assign to", key="cmd_assignee", placeholder="e.g. Patrol 2")
            if st.button("Propose assignment", key="cmd_assign_go", disabled=not who.strip()):
                st.session_state["cmd_pending"] = ("investigating", who.strip())
                st.rerun()
        with cols[3].popover(":material/swap_horiz: Change status", use_container_width=True, disabled=disabled):
            target = st.selectbox("New status", components.STATUSES, format_func=theme.humanize, key="cmd_status_target")
            if st.button("Propose change", key="cmd_status_go"):
                st.session_state["cmd_pending"] = (target, "")
                st.rerun()
        if cols[4].button(":material/auto_awesome: Ask Zenith", key="cmd_ask_inc", use_container_width=True, disabled=disabled):
            components.goto(":material/auto_awesome: Copilot", zx_copilot_prefill=f"Explain incident {incident_id}")
        if cols[5].button(":material/description: Create report", key="cmd_report", use_container_width=True):
            components.goto(":material/description: Briefing")
        if incident_id:
            try:
                inc, reasoning = components.load_incident(api_url, incident_id)
                export = json.dumps({"incident": inc, "reasoning": reasoning}, indent=2, default=str)
            except Exception:
                export = "{}"
            cols[6].download_button(":material/download: Export", export, file_name=f"{incident_id}.json",
                                    mime="application/json", key="cmd_export", use_container_width=True)
        if cols[7].button(":material/more_horiz: More", key="cmd_more", use_container_width=True):
            components.goto(INCIDENTS_PAGE, inv_incident=incident_id)


# ------------------------------------------------------------------ render
def render(api: ZenithAPI, api_url: str) -> None:
    try:
        data = load_situation(api_url)
    except Exception as exc:
        theme.page_header("Operations", "Center", "Live posture from events, incidents and quality findings.",
                          status=st.session_state.get("zx_status"))
        api_error(exc, api_url)
        st.stop()

    k = data["kpis"]
    mode = next((m for m in MODES if m[0] == st.session_state.get("cmd_mode", "map")), MODES[0])
    theme.page_header(mode[2].upper(), mode[3].upper(), mode[4], latest=data.get("latest_occurred_at"),
                      status=st.session_state.get("zx_status"))
    if k["valid_events"] == 0:
        _empty_state(api, api_url)
        return
    theme.show(holo.ticker([{"at": data.get("latest_occurred_at"), "severity": "medium",
                             "text": "BRIEF — " + data["brief"]["headline"]}] + data["feed"]))

    main, side = st.columns([2.05, 1], gap="medium")
    with main:
        _kpis(k)
        _mode_strip()
        view = st.session_state.get("cmd_mode", "map")
        if view == "map":
            _view_map(data, k)
        elif view == "threat":
            _view_threat(data)
        elif view == "cameras":
            _view_cameras(api)
        else:
            _view_pulse(data)
    with side:
        incident_id = _incident_panel(api, api_url, data)
    _action_bar(api, api_url, incident_id)
