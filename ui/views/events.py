"""Events page — premium explorer over the original evidence."""
from __future__ import annotations

import re
from datetime import date, timedelta
from itertools import product

from ui import components, theme
from ui.common import *  # noqa: F401,F403

INCIDENTS_PAGE = ":material/crisis_alert: Incidents"
WINDOWS = {"All time": None, "Last hour": 1, "Last 6 h": 6, "Last 24 h": 24, "Custom": "custom"}
MAX_COMBOS = 24  # multi-value source x severity filters fan out into this many server queries at most
ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?")


# ------------------------------------------------------------------ loaders
@st.cache_data(ttl=30, show_spinner=False)
def _facets(base_url: str) -> dict[str, Any]:
    return ZenithAPI(base_url).event_facets()


@st.cache_data(ttl=10, show_spinner=False)
def _latest(base_url: str) -> str | None:
    items = ZenithAPI(base_url).events(page_size=1).get("items") or []
    return items[0].get("occurred_at") if items else None


@st.cache_data(ttl=5, show_spinner=False)
def _query(base_url: str, params: tuple) -> dict[str, Any]:
    return ZenithAPI(base_url).events(**dict(params))


@st.cache_data(ttl=10, show_spinner=False)
def _detail(base_url: str, event_id: str) -> dict[str, Any]:
    return ZenithAPI(base_url).event(event_id)


@st.cache_data(ttl=10, show_spinner=False)
def _incident(base_url: str, incident_id: str) -> dict[str, Any]:
    return ZenithAPI(base_url).incident(incident_id)


def _search(base_url: str, base: dict[str, Any], sources: list[str], sevs: list[str], page: int, size: int) -> dict[str, Any]:
    """Server-side pagination; multi-value filters merge disjoint server queries.

    The top ``page * size`` rows of a union of disjoint sets are always inside
    the top ``page * size`` rows of each set, so the merge is exact.
    """
    combos = list(product(sources or [None], sevs or [None]))
    if len(combos) == 1:
        s, v = combos[0]
        params = {**base, "source_type": s, "severity": v, "page": page, "page_size": size}
        return _query(base_url, tuple(sorted((k, x) for k, x in params.items() if x not in (None, ""))))
    need = page * size
    merged: list[dict[str, Any]] = []
    total = 0
    for s, v in combos:
        got, sub_page = 0, 1
        while True:
            chunk = min(500, need)
            params = {**base, "source_type": s, "severity": v, "page": sub_page, "page_size": chunk}
            res = _query(base_url, tuple(sorted((k, x) for k, x in params.items() if x not in (None, ""))))
            if sub_page == 1:
                total += res.get("total", 0)
            items = res.get("items") or []
            merged.extend(items)
            got += len(items)
            if got >= need or len(items) < chunk:
                break
            sub_page += 1
    merged.sort(key=lambda e: (-(theme.parse_ts(e.get("occurred_at")).timestamp() if theme.parse_ts(e.get("occurred_at")) else 0),
                               e.get("event_id") or ""))
    pages = (total + size - 1) // size if total else 0
    return {"items": merged[(page - 1) * size: page * size], "total": total, "page": page, "page_size": size, "pages": pages}


# ------------------------------------------------------------------ helpers
def _dts(value: Any) -> str:
    dt = theme.parse_ts(value)
    return dt.strftime("%d %b %H:%M:%S") if dt else "—"


def _lag(ev: dict[str, Any]) -> float | None:
    a, b = theme.parse_ts(ev.get("occurred_at")), theme.parse_ts(ev.get("received_at"))
    return (b - a).total_seconds() if a and b else None


def _lag_text(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    if abs(seconds) < 90:
        return f"{seconds:.0f} s"
    if abs(seconds) < 5400:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


def _prose(text: Any) -> str:
    """Escape backend prose and replace raw ISO timestamps with readable ones."""
    return ISO_RE.sub(lambda m: theme.fmt_dt(m.group(0)), theme.esc(text))


def _issue_kind(issue_type: str) -> str:
    t = (issue_type or "").lower()
    if "conflict" in t or "contradict" in t:
        return "CONFLICT"
    if "missing" in t or "gap" in t or "silent" in t:
        return "MISSING"
    if "uncorroborated" in t or "spike" in t or "anomal" in t:
        return "INFERENCE"
    return "FACT"


def _stat(label: str, value: Any, sub: str, c1: str, c2: str) -> str:
    return (f'<div class="zx-glass" style="padding:12px 16px;margin-bottom:0">'
            f'<span aria-hidden="true" style="position:absolute;right:-30px;top:-30px;width:100px;height:100px;border-radius:50%;background:{c1}2E;filter:blur(24px)"></span>'
            f'<div class="zx-mono" style="position:relative;font-size:.6rem;letter-spacing:.12em;color:#8B97B0;text-transform:uppercase">{theme.esc(label)}</div>'
            f'<div style="position:relative;font-family:var(--zx-head);font-size:1.8rem;font-weight:600;line-height:1.15;'
            f'background:linear-gradient(100deg,{c2},{c1});-webkit-background-clip:text;background-clip:text;color:transparent">{theme.esc(value)}</div>'
            f'<div class="zx-muted" style="position:relative;font-size:.7rem">{theme.esc(sub)}</div></div>')


def _conf_bar(v: Any, _row: dict[str, Any]) -> str:
    conf = float(v or 0)
    return (f'<span style="display:inline-flex;align-items:center;gap:6px"><span class="zx-mono" style="font-size:.72rem">{conf:.2f}</span>'
            f'<span style="width:44px;height:5px;border-radius:5px;background:rgba(255,255,255,.07);overflow:hidden">'
            f'<span style="display:block;height:100%;width:{conf * 100:.0f}%;background:linear-gradient(90deg,#22D3EE,#818CF8)"></span></span></span>')


def _field(label: str, value: Any, mono: bool = False) -> str:
    cls = ' class="zx-mono"' if mono else ""
    size = ".74rem" if mono else ".82rem"
    return (f'<div style="padding:8px 10px;border-radius:10px;background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.05);min-width:0">'
            f'<div class="zx-mono" style="font-size:.58rem;letter-spacing:.1em;color:#6B7790;text-transform:uppercase">{theme.esc(label)}</div>'
            f'<div{cls} style="font-size:{size};color:#E6EBF5;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="{theme.esc(value)}">'
            f'{theme.esc(value)}</div></div>')


def _grid(cells: list[str], cols: int = 4) -> str:
    return f'<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax({760 // cols}px,1fr));gap:8px">{"".join(cells)}</div>'


def _reset_page() -> None:
    st.session_state["events_page"] = 1


# ------------------------------------------------------------------- render
def render(api: ZenithAPI, api_url: str) -> None:
    try:
        facets = _facets(api_url)
        latest = _latest(api_url)
    except Exception as exc:
        theme.page_header("Events", "Explorer", "Original evidence, normalised values and quality context.",
                          status=st.session_state.get("zx_status"))
        api_error(exc, api_url)
        st.stop()

    theme.page_header(
        "Events", "Explorer",
        f"{len(facets.get('source_types', []))} sources · {len(facets.get('devices', []))} devices · "
        f"{len(facets.get('zones', []))} zones · original evidence with normalised values",
        latest=latest, status=st.session_state.get("zx_status"),
    )

    # presets handed over by other pages (e.g. Site map → "Explore zone events")
    zones = ["All zones"] + list(facets.get("zones") or [])
    preset = st.session_state.pop("ev_zone_preset", None)
    if preset:
        st.session_state["ev_zone"] = preset if preset in zones else "All zones"
        _reset_page()

    # ------------------------------------------------------------ filter bar
    with st.container(key="zxg_ev_filters"):
        theme.show(theme.section("Filters", "applied server-side · occurrence (event) time"))
        c1, c2, c3, c4 = st.columns([1.5, 1.4, 1, 1])
        search = c1.text_input("Search", key="ev_search", placeholder="Event ID or device ID…", on_change=_reset_page)
        sources = c2.multiselect("Source", facets.get("source_types") or [], key="ev_sources", format_func=theme.humanize,
                                 placeholder="All sources", on_change=_reset_page)
        zone = c3.selectbox("Zone", zones, key="ev_zone", on_change=_reset_page)
        statuses = ["All statuses"] + list(facets.get("validation_statuses") or [])
        vstatus = c4.selectbox("Validation", statuses, key="ev_vstatus", on_change=_reset_page,
                               format_func=lambda s: s if s == "All statuses" else theme.humanize(s))
        d1, d2, d3 = st.columns([1.3, 1.6, 0.6])
        sevs = d1.pills("Severity", facets.get("severities") or SEVERITY_ORDER, key="ev_sevs", selection_mode="multi",
                        format_func=lambda s: s.capitalize(), on_change=_reset_page) or []
        window = d2.segmented_control("Occurrence window", list(WINDOWS), default="All time", key="ev_window",
                                      on_change=_reset_page) or "All time"
        page_size = d3.selectbox("Rows", [25, 50, 100, 250], index=1, key="ev_page_size", on_change=_reset_page)

        start = end = None
        anchor = theme.parse_ts(latest)
        if WINDOWS[window] == "custom":
            e1, e2, _ = st.columns([1, 1, 2])
            default_day = anchor.date() if anchor else date.today()
            start_d = e1.date_input("Occurred from", value=default_day, key="ev_from", on_change=_reset_page)
            end_d = e2.date_input("Occurred to", value=default_day, key="ev_to", on_change=_reset_page)
            start, end = datetime_to_param(start_d), datetime_to_param(end_d, end_of_day=True)
        elif WINDOWS[window] and anchor:
            start = (anchor - timedelta(hours=WINDOWS[window])).isoformat()
            end = anchor.isoformat()
        if start and WINDOWS[window] != "custom" and anchor:
            theme.show(f'<div class="zx-muted" style="font-size:.7rem">Window anchored on the latest event: '
                       f'<span class="zx-mono">{theme.fmt_dt(start)} → {theme.fmt_dt(end)}</span></div>')

    combos = max(1, len(sources)) * max(1, len(sevs))
    if combos > MAX_COMBOS:
        st.warning(f"That is {combos} source × severity combinations; narrow it to {MAX_COMBOS} or fewer.")
        return

    base = {
        "search": search.strip() or None,
        "zone": None if zone == "All zones" else zone,
        "validation_status": None if vstatus == "All statuses" else vstatus,
        "start": start, "end": end,
    }
    page_number = st.session_state.get("events_page", 1)
    try:
        result = _search(api_url, base, sources, sevs, page_number, page_size)
    except Exception as exc:
        api_error(exc, api_url)
        st.stop()
    if result["pages"] and page_number > result["pages"]:
        st.session_state["events_page"] = result["pages"]
        st.rerun()

    items = result["items"]
    # ------------------------------------------------------------- KPI strip
    page_sources = {e.get("source_type") for e in items}
    page_devices = {e.get("device_id") for e in items}
    hot = sum(1 for e in items if e.get("severity") in ("critical", "high"))
    k = st.columns(4)
    k[0].html(_stat("Matching events", result["total"], f"page {result['page']} of {max(result['pages'], 1)}",
                        "#22D3EE", "#A5F3FC"))
    k[1].html(_stat("Sources", len(page_sources), f"on this page · {len(facets.get('source_types', []))} known",
                        "#A78BFA", "#DDD6FE"))
    k[2].html(_stat("Devices", len(page_devices), f"on this page · {len(facets.get('devices', []))} known",
                        "#34D399", "#A7F3D0"))
    k[3].html(_stat("Critical + high", hot, "on this page", "#FF4D6D", "#FBBF24"))

    # ---------------------------------------------------------- results table
    with st.container(key="zxg_ev_results"):
        theme.show(theme.section("Results", f"{result['total']} event(s) · newest first by event time"))
        if not items:
            theme.show('<div class="zx-muted" style="text-align:center;padding:24px 0">No events match the current filters.</div>')
        else:
            rows = [dict(e, lag=_lag(e)) for e in items]
            theme.show(theme.html_table(
                rows,
                [("occurred_at", "Occurred"), ("severity", "Severity"), ("event_type", "Event"), ("source_type", "Source"),
                 ("device_id", "Device"), ("zone", "Zone"), ("confidence", "Confidence"), ("lag", "Arrival lag"),
                 ("validation_status", "Status")],
                {
                    "occurred_at": lambda v, r: f'<span class="zx-mono" style="font-size:.74rem;color:#C7D0E0">{theme.esc(_dts(v))}</span>',
                    "severity": lambda v, r: theme.sev_pill(v),
                    "event_type": lambda v, r: (f'<div style="display:flex;flex-direction:column;gap:3px"><span style="font-weight:500">'
                                                f'{theme.esc(theme.humanize(v))}</span>{theme.ev_link(r.get("event_id"))}</div>'),
                    "source_type": lambda v, r: theme.esc(theme.humanize(v)),
                    "device_id": lambda v, r: f'<span class="zx-mono" style="font-size:.74rem">{theme.esc(v)}</span>',
                    "zone": lambda v, r: theme.esc(v) if v else '<span class="zx-muted">not provided</span>',
                    "confidence": _conf_bar,
                    "lag": lambda v, r: (f'<span class="zx-mono" style="font-size:.72rem;color:{"#FBBF24" if (v or 0) > 300 else "#8B97B0"}">'
                                         f'{theme.esc(_lag_text(v))}</span>'),
                    "validation_status": lambda v, r: theme.chip(theme.humanize(v), "ok" if v == "valid" else "warning", dot=False),
                },
            ))
        pagination("events_page", result["page"], result["pages"], result["total"])

    if not items:
        return

    # ----------------------------------------------------------- detail panel
    ids = [e["event_id"] for e in items]
    labels = {e["event_id"]: f"{theme.fmt_time(e.get('occurred_at'))} · {theme.humanize(e.get('event_type'))} · "
                             f"{e.get('device_id')} · {e['event_id']}" for e in items}
    wanted = st.session_state.pop("ev_event_preset", None)
    if wanted and wanted not in ids:
        ids.insert(0, wanted)
        labels[wanted] = wanted
    if wanted:
        st.session_state["ev_selected"] = wanted
    if st.session_state.get("ev_selected") not in ids:
        st.session_state["ev_selected"] = ids[0]

    with st.container(key="zxg_ev_detail"):
        h1, h2 = st.columns([1, 1.2])
        h1.html(theme.section("Event detail", "normalised · raw · quality · incidents"))
        selected = h2.selectbox("Inspect event", ids, key="ev_selected", format_func=lambda i: labels.get(i, i),
                                label_visibility="collapsed")
        try:
            d = _detail(api_url, selected)
        except Exception as exc:
            api_error(exc, api_url)
            return
        _render_detail(api_url, d)


def _render_detail(api_url: str, d: dict[str, Any]) -> None:
    sev = theme.sev_of(d.get("severity"))
    c = theme.SEV[sev]
    vstatus = d.get("validation_status") or "unknown"
    theme.show(
        f'<div style="border-radius:16px;padding:14px 16px;margin-bottom:12px;background:linear-gradient(120deg,{c["halo"]},rgba(255,255,255,.01) 70%);'
        f'border:1px solid {c["c"]}44">'
        f'<div class="zx-row" style="justify-content:space-between;flex-wrap:wrap">'
        f'<span class="zx-row" style="gap:8px">{theme.sev_pill(sev, solid=True)}'
        f'{theme.chip(theme.humanize(vstatus), "ok" if vstatus == "valid" else "warning")}</span>'
        f'<span class="zx-mono" style="font-size:.72rem;color:#B4BED3">{theme.esc(d.get("event_id"))}</span></div>'
        f'<div style="font-family:var(--zx-head);font-size:1.35rem;font-weight:600;margin:8px 0 2px">{theme.esc(theme.humanize(d.get("event_type")))}</div>'
        f'<div style="font-size:.84rem;color:#C7D0E0">{theme.esc(theme.humanize(d.get("source_type")))} · '
        f'<span class="zx-mono">{theme.esc(d.get("device_id"))}</span> · {theme.esc(d.get("zone") or "zone not provided")} · '
        f'occurred <span class="zx-mono">{theme.esc(_dts(d.get("occurred_at")))}</span></div></div>'
    )
    if d.get("duplicate_of"):
        theme.show(f'<div style="margin-bottom:12px">{theme.chip("Duplicate content of " + str(d["duplicate_of"]) + " — preserved as evidence", "warning")}</div>')

    lag = _lag(d)
    lat, lon = d.get("latitude"), d.get("longitude")
    fields = [
        _field("Source", theme.humanize(d.get("source_type"))),
        _field("Device", d.get("device_id"), mono=True),
        _field("Event type", theme.humanize(d.get("event_type"))),
        _field("Zone", d.get("zone") or "not provided"),
        _field("Severity", sev.capitalize()),
        _field("Confidence", f"{float(d.get('confidence') or 0):.2f}", mono=True),
        _field("Occurred", _dts(d.get("occurred_at")), mono=True),
        _field("Received", _dts(d.get("received_at")), mono=True),
        _field("Arrival lag", _lag_text(lag), mono=True),
        _field("Ingested", _dts(d.get("ingested_at")), mono=True),
        _field("Coordinates", f"{lat:.5f}, {lon:.5f}" if lat is not None and lon is not None else "not provided", mono=True),
        _field("Batch", d.get("batch_id") or "—", mono=True),
        _field("Content hash", (d.get("content_hash") or "—")[:16] + ("…" if d.get("content_hash") else ""), mono=True),
    ]
    payload = d.get("payload") or {}
    payload_fields = [_field(theme.humanize(k), v if not isinstance(v, (dict, list)) else safe_json(v, 200), mono=isinstance(v, (int, float)))
                      for k, v in list(payload.items())[:12]]
    theme.show('<div class="zx-eyebrow" style="margin-bottom:8px">NORMALISED FIELDS</div>' + _grid(fields, 5))
    if payload_fields:
        theme.show('<div class="zx-eyebrow" style="margin:14px 0 8px">PAYLOAD</div>' + _grid(payload_fields, 5))

    raw = d.get("raw_record")
    changes, ts_same = [], 0
    if isinstance(raw, dict):
        for key, rv in raw.items():
            if key in d and key != "payload" and str(rv) != str(d.get(key)):
                ra, na = theme.parse_ts(rv) if isinstance(rv, str) else None, theme.parse_ts(d.get(key))
                if ra and na:
                    if ra == na:
                        ts_same += 1  # same instant, only the ISO format was canonicalised
                        continue
                    rv, nv = _dts(rv), _dts(d.get(key))
                    changes.append((key, rv, nv))
                    continue
                changes.append((key, rv, d.get(key)))
    left, right = st.columns([1.15, 1], gap="medium")
    with left:
        theme.show('<div class="zx-eyebrow" style="margin:14px 0 6px">EVIDENCE</div>')
        t1, t2 = st.tabs(["Original raw record", "Normalised payload"])
        with t1:
            st.code(safe_json(raw if raw is not None else {}), language="json")
        with t2:
            st.code(safe_json(payload), language="json")
        if ts_same:
            theme.show(f'<div class="zx-muted" style="font-size:.72rem;margin:4px 0 2px">{ts_same} timestamp(s) canonicalised to UTC '
                       'microsecond format — same instant, no value change.</div>')
        if changes:
            theme.show(
                '<div class="zx-muted" style="font-size:.72rem;margin:4px 0 6px">Normalisation changed '
                f'{len(changes)} field(s); the original stays above, unmodified.</div>'
                + "".join(f'<div class="zx-line" style="grid-template-columns:110px 1fr">'
                          f'<span class="zx-mono" style="font-size:.66rem;color:#8B97B0">{theme.esc(theme.humanize(k))}</span>'
                          f'<span class="zx-mono" style="font-size:.7rem;color:#C7D0E0;overflow:hidden;text-overflow:ellipsis">'
                          f'{theme.esc(rv)} <span style="color:#A5B4FC">→</span> {theme.esc(nv)}</span></div>' for k, rv, nv in changes[:8]))

    with right:
        issues = d.get("quality_issues") or []
        theme.show('<div class="zx-eyebrow" style="margin:14px 0 6px">DATA QUALITY</div>')
        if not issues:
            theme.show(f'<div style="margin-bottom:8px">{theme.chip("No quality findings target this event", "ok")}</div>')
        for q in issues:
            action = (f'<div style="display:flex;gap:8px;font-size:.76rem;color:#A5B4FC;margin-top:4px"><span>→</span>'
                      f'<span style="color:#C7D0E0">{_prose(q.get("suggested_action"))}</span></div>') if q.get("suggested_action") else ""
            theme.show(
                f'<div style="border-radius:12px;padding:10px 12px;margin-bottom:8px;background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.06)">'
                f'<div class="zx-row" style="gap:8px;flex-wrap:wrap">{theme.kind_badge(_issue_kind(q.get("issue_type")))}'
                f'<span style="font-size:.84rem;font-weight:600">{theme.esc(theme.humanize(q.get("issue_type")))}</span>'
                f'<span style="margin-left:auto">{theme.sev_pill(q.get("severity"))}</span></div>'
                f'<div style="font-size:.78rem;color:#DCE3F0;line-height:1.45;margin-top:6px">{_prose(q.get("explanation"))}</div>{action}'
                f'<div class="zx-mono" style="font-size:.6rem;color:#6B7790;margin-top:6px">{theme.esc(q.get("issue_id"))} · detected {theme.esc(theme.fmt_dt(q.get("detected_at")))}</div></div>'
            )

        theme.show('<div class="zx-eyebrow" style="margin:14px 0 6px">INCIDENT MEMBERSHIP</div>')
        inc_ids = d.get("incident_ids") or []
        if not inc_ids:
            theme.show(theme.chip("Not part of a correlated incident", "neutral"))
        for iid in inc_ids:
            try:
                inc = _incident(api_url, iid)
            except Exception:
                inc = None
            if not isinstance(inc, dict):
                inc = {"incident_id": iid, "title": iid}
            theme.show(theme.incident_card(inc))
            if st.button(f"Open {iid} in Incidents →", key=f"ev_inc_{d.get('event_id')}_{iid}", type="tertiary"):
                components.goto(INCIDENTS_PAGE, inv_incident=iid)
