"""Site map page — the offline site picture with per-zone drill-down."""
from __future__ import annotations

from ui import components, holo, theme
from ui.common import *  # noqa: F401,F403

INCIDENTS_PAGE = ":material/crisis_alert: Incidents"
EVENTS_PAGE = ":material/view_list: Events"
INACTIVE = {"false_positive", "resolved", "closed"}


# ------------------------------------------------------------------ loaders
@st.cache_data(ttl=5, show_spinner=False)
def _situation(base_url: str) -> dict[str, Any]:
    return ZenithAPI(base_url).situation()


@st.cache_data(ttl=10, show_spinner=False)
def _zone_incidents(base_url: str, zone: str) -> list[dict[str, Any]]:
    items = ZenithAPI(base_url).incidents(zone=zone, page_size=200).get("items", [])
    active = [i for i in items if (i.get("status") or "open") not in INACTIVE]
    return sorted(active, key=lambda i: (theme.SEV_RANK.get(theme.sev_of(i.get("severity")), 9),
                                         -(float(i.get("confidence") or 0)), i.get("incident_id") or ""))


@st.cache_data(ttl=10, show_spinner=False)
def _zone_events(base_url: str, zone: str) -> dict[str, Any]:
    return ZenithAPI(base_url).events(zone=zone, validation_status="valid", page_size=500)


# ------------------------------------------------------------------ helpers
def _zone_order(z: dict[str, Any]) -> tuple:
    sev = z.get("worst_severity")
    return (theme.SEV_RANK.get(sev, 9) if sev else 9, -(z.get("events") or 0), -(z.get("incidents") or 0), z.get("zone") or "")


def _stat(label: str, value: Any, sub: str = "", color: str = "#EEF2FA") -> str:
    return (f'<div style="border-radius:14px;padding:10px 12px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06)">'
            f'<div class="zx-mono" style="font-size:.6rem;letter-spacing:.1em;color:#8B97B0;text-transform:uppercase">{theme.esc(label)}</div>'
            f'<div style="font-family:var(--zx-head);font-size:1.5rem;font-weight:600;line-height:1.2;color:{color}">{theme.esc(value)}</div>'
            f'<div class="zx-muted" style="font-size:.68rem">{theme.esc(sub)}</div></div>')


def _mini_bar(value: float, top: float, c1: str = "#22D3EE", c2: str = "#818CF8") -> str:
    pct = 0 if not top else max(2.0, min(100.0, value / top * 100))
    return (f'<div style="display:flex;align-items:center;gap:8px"><span class="zx-mono" style="min-width:28px;text-align:right">{int(value)}</span>'
            f'<span style="flex:1;min-width:60px;height:6px;border-radius:6px;background:rgba(255,255,255,.06);overflow:hidden">'
            f'<span style="display:block;height:100%;width:{pct:.1f}%;border-radius:6px;background:linear-gradient(90deg,{c1},{c2});'
            f'box-shadow:0 0 8px {c1}88"></span></span></div>')


def _event_row(ev: dict[str, Any]) -> str:
    return (
        f'<div style="display:grid;grid-template-columns:44px 1fr auto;gap:10px;align-items:center;padding:7px 0;border-bottom:1px solid rgba(255,255,255,.04)">'
        f'<span class="zx-mono" style="font-size:.7rem;color:#8B97B0">{theme.esc(theme.fmt_time(ev.get("occurred_at")))}</span>'
        f'<span style="min-width:0;display:flex;flex-direction:column;gap:1px">'
        f'<span style="font-size:.8rem;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{theme.esc(theme.humanize(ev.get("event_type")))}</span>'
        f'<span class="zx-mono" style="font-size:.62rem;color:#8B97B0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'
        f'{theme.esc(theme.humanize(ev.get("source_type")))} · {theme.esc(ev.get("device_id"))}</span></span>'
        f'{theme.sev_pill(ev.get("severity"))}</div>'
    )


# ------------------------------------------------------------------- render
def render(api: ZenithAPI, api_url: str) -> None:
    try:
        data = _situation(api_url)
    except Exception as exc:
        theme.page_header("Site", "map", "Zones projected from event coordinates.", status=st.session_state.get("zx_status"))
        api_error(exc, api_url)
        st.stop()

    zones = sorted(data.get("zones") or [], key=_zone_order)
    mapped = [z for z in zones if z.get("latitude") is not None and z.get("longitude") is not None]
    unmapped = [z for z in zones if z not in mapped]
    open_total = sum(z.get("incidents") or 0 for z in zones)
    theme.page_header(
        "Site", "map",
        f"{len(zones)} zones · {len(mapped)} with coordinates · {open_total} open incident links · offline projection",
        latest=data.get("latest_occurred_at"), status=st.session_state.get("zx_status"),
    )
    if not zones:
        theme.show(theme.empty("No zones yet — ingest events with a zone (and latitude/longitude) to draw the site picture."))
        if st.button("Go to Ingest", key="map_to_ingest"):
            components.goto(":material/upload: Ingest")
        return

    names = [z["zone"] for z in zones]
    by_name = {z["zone"]: z for z in zones}
    current = st.session_state.get("map_selected")
    if current not in by_name:
        current = names[0]

    # ------------------------------------------------------------- map card
    with st.container(key="zxg_sitemap"):
        head_l, head_r = st.columns([1, 0.36])
        head_l.html(theme.section("Live site picture", "average event coordinates per zone · event time"))
        layer = head_r.segmented_control("Layer", ["All zones", "High + critical"], default="All zones",
                                         key="map_layer", label_visibility="collapsed")

        def _label(name: str) -> str:
            z = by_name[name]
            sev = z.get("worst_severity")
            tag = f" · {sev}" if sev else ""
            return f"{name} · {z.get('incidents', 0)}{tag}"

        picked = st.pills("Zone", names, default=current, key="map_zone_pills", format_func=_label,
                          selection_mode="single", label_visibility="collapsed")
        if picked and picked != current:
            current = picked
        st.session_state["map_selected"] = current

        callouts = []
        for inc in data.get("priority") or []:
            if inc.get("severity") == "critical" and inc.get("zones"):
                callouts.append({"zone": inc["zones"][0], "severity": "critical", "at": inc.get("created_at"),
                                 "conf": f"{float(inc.get('confidence') or 0):.2f}",
                                 "title": str(inc.get("title") or "").split(" at ")[0],
                                 "sub": " · ".join(inc.get("sources") or [])})
        theme.show(holo.holo_map(zones, selected=current, hot_only=layer == "High + critical", callouts=callouts, height=640))
        if unmapped:
            theme.show(
                '<div class="zx-row" style="flex-wrap:wrap;gap:6px;margin-top:10px"><span class="zx-muted" style="font-size:.72rem">No coordinates:</span>'
                + "".join(theme.chip(f"{z['zone']} · {z.get('events', 0)} events", "neutral", dot=False) for z in unmapped)
                + "</div>")

    zone = by_name[current]
    sev = theme.sev_of(zone.get("worst_severity"))
    sc = theme.SEV[sev]
    try:
        incidents = _zone_incidents(api_url, current)
        ev_result = _zone_events(api_url, current)
    except Exception as exc:
        api_error(exc, api_url)
        return
    events = ev_result.get("items") or []
    devices = sorted({e.get("device_id") for e in events if e.get("device_id")})
    sources = sorted({e.get("source_type") for e in events if e.get("source_type")})

    a, b, c = st.columns([0.9, 1.1, 1.1], gap="medium")
    # ------------------------------------------------------- (a) zone KPIs
    with a, st.container(key="zxg_zone_kpi"):
        total_events = sum(z.get("events") or 0 for z in zones) or 1
        share = (zone.get("events") or 0) / total_events
        worst = theme.sev_pill(sev, solid=True) if zone.get("worst_severity") else theme.chip("No open incidents", "ok")
        ring = theme.ring(share, size=104, stroke=10, c1=sc["c"], c2=sc["light"], label=f"{round(share * 100)}%",
                          sub="OF EVENTS", gid="zxmap-ring")
        coords = (f"{zone['latitude']:.4f}, {zone['longitude']:.4f}" if zone.get("latitude") is not None
                  and zone.get("longitude") is not None else "no coordinates")
        theme.show(
            f'<div style="margin:-16px -18px 12px;padding:16px 18px 12px;background:linear-gradient(180deg,{sc["halo"]},transparent)">'
            f'<div class="zx-eyebrow">SELECTED ZONE</div>'
            f'<div style="font-family:var(--zx-head);font-size:1.35rem;font-weight:600;margin:4px 0 8px">{theme.esc(current)}</div>'
            f'<div class="zx-row" style="gap:8px;flex-wrap:wrap"><span class="zx-muted" style="font-size:.72rem">Worst open</span>{worst}</div></div>'
            f'<div style="display:grid;grid-template-columns:112px 1fr;gap:12px;align-items:center;margin-bottom:12px">{ring}'
            f'<div style="display:flex;flex-direction:column;gap:4px;font-size:.76rem;color:#C7D0E0">'
            f'<span><b style="color:{theme.SEV["critical"]["c"]}">{zone.get("critical", 0)}</b> critical · '
            f'<b style="color:{theme.SEV["high"]["c"]}">{zone.get("high", 0)}</b> high open</span>'
            f'<span class="zx-mono" style="font-size:.66rem;color:#8B97B0">{theme.esc(coords)}</span>'
            f'<span class="zx-muted" style="font-size:.68rem">{theme.esc(", ".join(theme.humanize(s) for s in sources[:4]) or "no sources")}'
            f'{" +" + str(len(sources) - 4) if len(sources) > 4 else ""}</span></div></div>'
            f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">'
            + _stat("Events", zone.get("events", 0), "valid, event time")
            + _stat("Open incidents", zone.get("incidents", 0), "active statuses", sc["c"] if zone.get("incidents") else "#EEF2FA")
            + _stat("Sensors seen", len(devices), f"{len(sources)} source type(s)")
            + _stat("Latest", theme.fmt_time(events[0].get("occurred_at")) if events else "—",
                    theme.fmt_dt(events[0].get("occurred_at")) if events else "no events")
            + "</div>"
        )
        if st.button("Explore zone events →", key="map_to_events", use_container_width=True):
            components.goto(EVENTS_PAGE, ev_zone_preset=current)

    # --------------------------------------------------- (b) open incidents
    with b, st.container(key="zxg_zone_incidents"):
        theme.show(theme.section("Open incidents", f"{len(incidents)} in {current}"))
        if not incidents:
            theme.show('<div class="zx-muted" style="padding:10px 0">No open incidents touch this zone.</div>')
        for inc in incidents[:6]:
            theme.show(theme.incident_card(inc))
            if st.button("Investigate →", key=f"map_inv_{inc['incident_id']}", type="tertiary"):
                components.goto(INCIDENTS_PAGE, inv_incident=inc["incident_id"])
        if len(incidents) > 6:
            theme.show(f'<div class="zx-muted" style="font-size:.72rem">+{len(incidents) - 6} more — open Incidents and filter by zone.</div>')

    # ---------------------------------------------------- (c) latest events
    with c, st.container(key="zxg_zone_events"):
        theme.show(theme.section("Latest events", f"{ev_result.get('total', len(events))} valid · newest first"))
        if not events:
            theme.show('<div class="zx-muted" style="padding:10px 0">No valid events recorded in this zone.</div>')
        else:
            theme.show("".join(_event_row(e) for e in events[:9]))

    # ---------------------------------------------------- zone league table
    with st.container(key="zxg_zone_league"):
        theme.show(theme.section("Zone league", "ordered by worst open severity, then event volume"))
        top_events = max((z.get("events") or 0) for z in zones) or 1
        top_inc = max((z.get("incidents") or 0) for z in zones) or 1
        rows = [dict(z, rank=i + 1) for i, z in enumerate(zones)]

        def _zone_cell(v: Any, row: dict[str, Any]) -> str:
            sel = row["zone"] == current
            return (f'<span style="font-weight:{600 if sel else 500};color:{"#7DD3FC" if sel else "#EEF2FA"}">'
                    f'{"◉ " if sel else ""}{theme.esc(v)}</span>')

        def _inc_cell(v: Any, row: dict[str, Any]) -> str:
            s = theme.sev_of(row.get("worst_severity"))
            c1 = theme.SEV[s]["c"] if row.get("worst_severity") else "#475569"
            return _mini_bar(v or 0, top_inc, c1, theme.SEV[s]["light"])

        theme.show(theme.html_table(
            rows,
            [("rank", "#"), ("zone", "Zone"), ("worst_severity", "Worst open"), ("incidents", "Open incidents"),
             ("crit_high", "Critical · high"), ("events", "Events"), ("coords", "Map")],
            {
                "rank": lambda v, r: f'<span class="zx-mono" style="color:#8B97B0">{v:02d}</span>',
                "zone": _zone_cell,
                "worst_severity": lambda v, r: theme.sev_pill(v) if v else '<span class="zx-muted">none</span>',
                "incidents": _inc_cell,
                "crit_high": lambda v, r: (f'<span class="zx-mono"><span style="color:{theme.SEV["critical"]["c"]}">{r.get("critical", 0)}</span>'
                                           f' · <span style="color:{theme.SEV["high"]["c"]}">{r.get("high", 0)}</span></span>'),
                "events": lambda v, r: _mini_bar(v or 0, top_events),
                "coords": lambda v, r: (theme.chip("mapped", "ok", dot=False) if r.get("latitude") is not None
                                        and r.get("longitude") is not None else theme.chip("no coordinates", "neutral", dot=False)),
            },
        ))
