"""Incidents page — master/detail investigation workspace."""
from __future__ import annotations

import re

from ui import components, theme
from ui.common import *  # noqa: F401,F403

PAGE_SIZE = 12
SCAN_PAGE = 200  # backend maximum page size
SORTS = ["Priority", "Newest first", "Oldest first", "Highest confidence", "Most events"]
FILTER_KEYS = ["inv_q", "inv_sev", "inv_status", "inv_from", "inv_to", "inv_watch", "inv_sort",
               "inv_cat", "inv_src", "inv_dev", "inv_zone"]
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
_PREFIX = re.compile(r"^([a-z]+(?:_[a-z]+)+):\s*")

_CSS = """<style>
[class*="st-key-inv_open_"] button{min-height:58px!important;border-radius:12px!important;font-size:1rem}
[class*="st-key-zxinvrow"]{background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.06);border-radius:14px;padding:10px 12px}
[class*="st-key-zxinvrow"] [data-testid="stCode"] pre{max-height:220px}
.zx-tl{position:relative;padding-left:26px}
.zx-tl::before{content:"";position:absolute;left:8px;top:6px;bottom:6px;width:2px;border-radius:2px;background:linear-gradient(180deg,#22D3EE,#818CF8 55%,#E879F9);box-shadow:0 0 12px rgba(129,140,248,.6)}
.zx-tl-item{position:relative;padding:0 0 16px}
.zx-tl-item:last-child{padding-bottom:0}
.zx-tl-dot{position:absolute;left:-24px;top:4px;width:14px;height:14px;border-radius:50%;border:2px solid #070A14}
</style>"""


# ------------------------------------------------------------------ helpers
def _detime(text: Any) -> str:
    """Replace raw ISO timestamps inside backend prose with '12 Mar 09:20'."""
    return _ISO.sub(lambda m: theme.fmt_dt(m.group(0)), str(text or ""))


def _split_kind(text: Any) -> tuple[str, str]:
    """'delayed_event: Event …' -> ('Delayed event', 'Event …')."""
    text = _detime(text)
    match = _PREFIX.match(text)
    if match:
        return theme.humanize(match.group(1)), text[match.end():]
    return "", text


def _reset_page() -> None:
    st.session_state["inv_page"] = 1


def _clear_filters() -> None:
    for k in FILTER_KEYS:
        st.session_state.pop(k, None)
    _reset_page()


@st.cache_data(ttl=10, show_spinner=False)
def _scan(base_url: str, params: tuple[tuple[str, Any], ...]) -> list[dict[str, Any]]:
    """Every incident matching the server-side filters, walked page by page."""
    api = ZenithAPI(base_url)
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        result = api.incidents(**dict(params), page=page, page_size=SCAN_PAGE)
        items.extend(result["items"])
        if page >= (result.get("pages") or 1):
            return items
        page += 1


def _sort(items: list[dict[str, Any]], how: str) -> list[dict[str, Any]]:
    if how == "Newest first":
        return sorted(items, key=lambda i: str(i.get("created_at") or ""), reverse=True)
    if how == "Oldest first":
        return sorted(items, key=lambda i: str(i.get("created_at") or ""))
    if how == "Highest confidence":
        return sorted(items, key=lambda i: float(i.get("confidence") or 0), reverse=True)
    if how == "Most events":
        return sorted(items, key=lambda i: int(i.get("event_count") or 0), reverse=True)
    return items  # backend order: severity, confidence, newest


def _card(inc: dict[str, Any], active: bool) -> str:
    sev = theme.sev_of(inc.get("severity"))
    c = theme.SEV[sev]
    border = "rgba(125,211,252,.55)" if active else "rgba(255,255,255,.06)"
    bg = ("linear-gradient(100deg,rgba(34,211,238,.13),rgba(129,140,248,.10))" if active else "rgba(255,255,255,.02)")
    glow = "box-shadow:0 0 26px -10px rgba(34,211,238,.8);" if active else ""
    watch = (' <span class="zx-mono" style="font-size:.56rem;letter-spacing:.06em;color:#FDA4AF;border:1px dashed rgba(251,113,133,.45);'
             'border-radius:6px;padding:1px 5px">WATCHLIST · SINGLE SOURCE</span>') if inc.get("is_possible_association") else ""
    sources = " · ".join(theme.humanize(s) for s in (inc.get("sources") or []))
    conf = inc.get("confidence")
    return (
        f'<div style="display:flex;align-items:center;gap:10px;background:{bg};border:1px solid {border};border-radius:12px;padding:9px 12px;{glow}">'
        f'<span style="width:4px;align-self:stretch;border-radius:4px;background:linear-gradient({c["c"]},{c["light"]});box-shadow:0 0 10px {c["c"]}"></span>'
        f'<span style="flex:1;min-width:0;display:flex;flex-direction:column;gap:3px">'
        f'<span style="font-size:.84rem;font-weight:{600 if active else 500};overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{theme.esc(inc.get("title"))}</span>'
        f'<span class="zx-mono" style="font-size:.6rem;color:{c["c"]};overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'
        f'{theme.esc(sev.upper())} · {theme.esc(theme.humanize(inc.get("status")))} · {theme.esc(sources)}</span>'
        f'<span class="zx-mono" style="font-size:.58rem;color:#6B7790">{theme.esc(inc.get("incident_id"))} · {int(inc.get("event_count") or 0)} ev{watch}</span></span>'
        f'<span style="display:flex;flex-direction:column;align-items:flex-end;gap:2px">'
        f'<span style="font-family:var(--zx-head);font-size:.95rem;font-weight:600">{f"{float(conf):.2f}" if conf is not None else "—"}</span>'
        f'<span class="zx-mono" style="font-size:.6rem;color:#8B97B0">{theme.esc(theme.fmt_dt(inc.get("created_at")))}</span></span></div>'
    )


def _line(kind: str, text: str, ref: str = "") -> str:
    return (f'<div class="zx-line">{theme.kind_badge(kind)}<span style="font-size:.8rem;line-height:1.45;color:#DCE3F0">{text}</span>'
            f'{theme.ev_link(ref) if ref else "<span></span>"}</div>')


# ------------------------------------------------------------------- filters
def _filters(facets: dict[str, Any], watch_count: int | None) -> None:
    statuses = list(dict.fromkeys(INCIDENT_STATUSES + list(facets.get("statuses", []))))
    with st.container(key="zxg_inv_filters"):
        a, b, c, d = st.columns([1.6, 1, 1, 1.1], vertical_alignment="bottom")
        a.text_input("Search", key="inv_q", placeholder="Incident ID or title…", on_change=_reset_page)
        b.selectbox("Status", ["All"] + statuses, key="inv_status", format_func=status_label, on_change=_reset_page)
        c.selectbox("Sort", SORTS, key="inv_sort", on_change=_reset_page)
        with d.popover("More filters", use_container_width=True):
            st.selectbox("Category", ["All"] + facets.get("categories", []), key="inv_cat",
                         format_func=lambda v: v if v == "All" else theme.humanize(v), on_change=_reset_page)
            st.selectbox("Contains source", ["All"] + facets.get("source_types", []), key="inv_src",
                         format_func=lambda v: v if v == "All" else theme.humanize(v), on_change=_reset_page)
            st.selectbox("Contains device", ["All"] + facets.get("devices", []), key="inv_dev", on_change=_reset_page)
            st.selectbox("Contains zone", ["All"] + facets.get("zones", []), key="inv_zone", on_change=_reset_page)
            st.button("Clear all filters", key="inv_clear", on_click=_clear_filters, use_container_width=True)
        e, f, g, h = st.columns([1.25, 0.7, 0.7, 1.75], vertical_alignment="bottom")
        e.pills("Severity", SEVERITY_ORDER, selection_mode="multi", key="inv_sev",
                format_func=str.capitalize, on_change=_reset_page)
        f.date_input("Occurred from", value=None, key="inv_from", format="DD/MM/YYYY", on_change=_reset_page)
        g.date_input("Occurred to", value=None, key="inv_to", format="DD/MM/YYYY", on_change=_reset_page)
        count = "" if watch_count is None else f" · {watch_count}"
        h.toggle(f"Show watchlist (single-source possible incidents){count}", key="inv_watch", value=False,
                 on_change=_reset_page,
                 help="Possible associations backed by a single source. Hidden by default so corroborated incidents lead.")


def _server_params(severities: list[str]) -> dict[str, Any]:
    s = st.session_state
    pick = lambda k: None if s.get(k) in (None, "", "All") else s.get(k)  # noqa: E731
    start, end = s.get("inv_from"), s.get("inv_to")
    if start and end and start > end:
        start, end = end, start
    return {
        "search": (s.get("inv_q") or "").strip() or None,
        "severity": severities[0] if len(severities) == 1 else None,
        "status": pick("inv_status"),
        "category": pick("inv_cat"),
        "source": pick("inv_src"),
        "device": pick("inv_dev"),
        "zone": pick("inv_zone"),
        "start": datetime_to_param(start) if start else None,
        "end": datetime_to_param(end, end_of_day=True) if end else None,
    }


# ------------------------------------------------------------------- detail tabs
def _tab_evidence(incident: dict[str, Any]) -> None:
    timeline = incident.get("timeline") or []
    if not timeline:
        theme.show(theme.empty("No contributing events recorded for this incident."))
        return
    theme.show(theme.section("Contributing evidence", f"{len(timeline)} original events · IDs preserved"))
    for i, ev in enumerate(timeline):
        with st.container(key=f"zxinvrow_{i}"):
            conf = float(ev.get("confidence") or 0)
            theme.show(
                f'<div class="zx-row" style="flex-wrap:wrap;gap:8px">'
                f'{theme.chip(theme.humanize(ev.get("source_type")), "info")}{theme.ev_link(ev.get("event_id"))}'
                f'{theme.sev_pill(ev.get("severity"))}'
                f'<span style="font-size:.82rem;font-weight:500">{theme.esc(theme.humanize(ev.get("event_type")))}</span>'
                f'<span class="zx-mono" style="margin-left:auto;font-size:.7rem;color:#B4BED3">{theme.esc(theme.fmt_dt(ev.get("occurred_at")))}'
                f'<span style="color:#6B7790">:{theme.esc(theme.fmt_time(ev.get("occurred_at"), True)[-2:])}</span></span></div>'
                f'<div class="zx-muted" style="font-size:.74rem;margin:6px 0 4px">Device <span class="zx-mono" style="color:#C7D0E0">'
                f'{theme.esc(ev.get("device_id") or "—")}</span> · zone {theme.esc(ev.get("zone") or "not provided")} · '
                f'confidence <b style="color:#DCE3F0">{conf:.2f}</b> · received {theme.esc(theme.fmt_time(ev.get("received_at"), True))}</div>'
            )
            st.code(safe_json(ev.get("payload") or {}), language="json")


def _tab_timeline(incident: dict[str, Any]) -> None:
    timeline = sorted(incident.get("timeline") or [], key=lambda e: str(e.get("occurred_at") or ""))
    if not timeline:
        theme.show(theme.empty("No events to place on the timeline."))
        return
    items = []
    for ev in timeline:
        c = theme.SEV[theme.sev_of(ev.get("severity"))]
        occ, rec = theme.parse_ts(ev.get("occurred_at")), theme.parse_ts(ev.get("received_at"))
        lag = ""
        if occ and rec and (rec - occ).total_seconds() >= 60:
            lag = (f' {theme.chip(f"arrived {(rec - occ).total_seconds() / 60:.0f} min later", "warning", dot=False)}')
        items.append(
            f'<div class="zx-tl-item"><span class="zx-tl-dot" style="background:{c["c"]};box-shadow:0 0 0 3px {c["halo"]},0 0 14px {c["c"]}"></span>'
            f'<div class="zx-row" style="gap:8px;flex-wrap:wrap"><span class="zx-mono" style="font-size:.74rem;color:#E0F2FE;font-weight:600">'
            f'{theme.esc(theme.fmt_time(ev.get("occurred_at"), True))}</span>'
            f'<span style="font-size:.86rem;font-weight:600">{theme.esc(theme.humanize(ev.get("event_type")))}</span>'
            f'{theme.sev_pill(ev.get("severity"))}{lag}</div>'
            f'<div class="zx-muted" style="font-size:.74rem;margin-top:2px">{theme.esc(theme.humanize(ev.get("source_type")))} · '
            f'<span class="zx-mono">{theme.esc(ev.get("device_id"))}</span> · {theme.esc(ev.get("zone") or "zone not provided")} · '
            f'conf {float(ev.get("confidence") or 0):.2f} · {theme.ev_link(ev.get("event_id"))}</div></div>'
        )
    first, last = timeline[0].get("occurred_at"), timeline[-1].get("occurred_at")
    theme.show(theme.section("Ordered by event time", f"{theme.fmt_dt(first)} → {theme.fmt_dt(last)} · arrival order ignored")
               + f'<div class="zx-tl">{"".join(items)}</div>')


def _tab_quality(incident: dict[str, Any]) -> None:
    issues = incident.get("quality_issues") or []
    if not issues:
        theme.show(theme.empty("No data-quality findings touch this incident's evidence."))
        return
    theme.show(theme.section("Data-quality findings", f"{len(issues)} touching this incident"))
    cards = []
    for q in issues:
        sev = theme.sev_of(q.get("severity"))
        c = theme.SEV[sev]
        action = (f'<div style="display:flex;gap:8px;font-size:.78rem;color:#C7D0E0;margin-top:8px"><span style="color:#A5B4FC">→</span>'
                  f'{theme.esc(q.get("suggested_action"))}</div>') if q.get("suggested_action") else ""
        cards.append(
            f'<div style="border-radius:14px;padding:12px 14px;margin-bottom:8px;background:linear-gradient(90deg,{c["halo"]},rgba(255,255,255,.015) 60%);'
            f'border:1px solid {c["c"]}40;border-left:3px solid {c["c"]}">'
            f'<div class="zx-row" style="flex-wrap:wrap;gap:8px"><span style="font-family:var(--zx-head);font-weight:600;font-size:.92rem">'
            f'{theme.esc(theme.humanize(q.get("issue_type")))}</span>{theme.sev_pill(sev)}'
            f'<span style="margin-left:auto" class="zx-row">{theme.ev_link(q.get("event_id")) if q.get("event_id") else ""}'
            f'<span class="zx-mono" style="font-size:.66rem;color:#8B97B0">{theme.esc(q.get("device_id") or "")}</span></span></div>'
            f'<div style="font-size:.8rem;line-height:1.5;color:#DCE3F0;margin-top:6px">{theme.esc(_detime(q.get("explanation")))}</div>{action}'
            f'<div class="zx-mono" style="font-size:.6rem;color:#5F6B85;margin-top:6px">{theme.esc(q.get("issue_id"))} · detected {theme.esc(theme.fmt_dt(q.get("detected_at")))}</div></div>'
        )
    theme.show("".join(cards))


def _tab_correlation(incident: dict[str, Any], reasoning: dict[str, Any]) -> None:
    possible = bool(incident.get("is_possible_association"))
    assoc = (theme.chip("Possible association · watchlist", "warning") if possible
             else theme.chip("Correlated · multi-signal", "ok"))
    theme.show(theme.glass(
        f'<div class="zx-row" style="margin-bottom:8px;flex-wrap:wrap"><span class="zx-eyebrow zx-grad">WHY THESE EVENTS WERE LINKED</span>'
        f'<span style="margin-left:auto">{assoc}</span></div>'
        f'<div style="font-size:.86rem;line-height:1.55;color:#DCE3F0">{theme.esc(_detime(incident.get("correlation_explanation") or "No rationale recorded."))}</div>'
        + (f'<div class="zx-muted" style="font-size:.74rem;margin-top:8px">Single-source evidence: treat as unresolved until a second, '
           f'independent source corroborates it.</div>' if possible else ""),
        ai=True))

    lines = []
    for item in incident.get("contradictions") or []:
        label, text = _split_kind(item)
        lines.append(_line("CONFLICT", (f"<b>{theme.esc(label)}</b> — " if label else "") + theme.esc(text)))
    for item in incident.get("missing_info") or []:
        label, text = _split_kind(item)
        lines.append(_line("MISSING", (f"<b>{theme.esc(label)}</b> — " if label else "") + theme.esc(text)))
    theme.show(theme.section("Contradictions & gaps", f"{len(incident.get('contradictions') or [])} conflict(s) · "
                                                      f"{len(incident.get('missing_info') or [])} gap(s)")
               + ("".join(lines) or '<div class="zx-muted" style="padding:6px 0 12px">No contradictions or declared evidence gaps.</div>'))

    facts = reasoning.get("facts") or []
    infs = reasoning.get("inferences") or []
    if facts or infs:
        theme.show('<div style="height:10px"></div>' + theme.section("Structured reasoning", "facts vs inferences")
                   + "".join(_line("FACT", theme.esc(_detime(f))) for f in facts[:8])
                   + "".join(_line("INFERENCE", theme.esc(_detime(i))) for i in infs[:4]))
    actions = incident.get("recommended_actions") or []
    if actions:
        theme.show('<div style="height:10px"></div>' + theme.section("Recommended next actions", "proposals · you decide")
                   + "".join(_line("ACTION", theme.esc(a)) for a in actions))

    def ent(vals: list[Any], none: str, human: bool = False) -> str:
        chips = " ".join(theme.chip(theme.humanize(v) if human else v, "neutral", dot=False) for v in vals)
        return chips or f'<span class="zx-muted">{none}</span>'

    theme.show(
        '<div style="height:10px"></div>' + theme.section("Involved entities")
        + f'<div style="display:grid;grid-template-columns:90px 1fr;gap:8px;align-items:center;font-size:.78rem;color:#8B97B0">'
          f'<span>Sources</span><span class="zx-row" style="flex-wrap:wrap;gap:6px">{ent(incident.get("sources") or [], "none", True)}</span>'
          f'<span>Devices</span><span class="zx-row" style="flex-wrap:wrap;gap:6px">{ent(incident.get("devices") or [], "none")}</span>'
          f'<span>Locations</span><span class="zx-row" style="flex-wrap:wrap;gap:6px">{ent(incident.get("locations") or [], "not provided")}</span></div>'
    )
    grounded = "grounded" if reasoning.get("grounded") else "not grounded"
    fallback = f" · fell back to deterministic reasoning: {reasoning['fallback_reason']}" if reasoning.get("fallback_reason") else ""
    theme.show(f'<div class="zx-muted" style="font-size:.68rem;margin-top:12px">Provider {theme.esc(reasoning.get("provider") or "deterministic")} · '
               f'{grounded} in {len(reasoning.get("evidence_event_ids") or [])} event(s){theme.esc(fallback)}</div>')


def _tab_assign(api: ZenithAPI, api_url: str, incident: dict[str, Any]) -> None:
    iid = incident["incident_id"]
    current_status = incident.get("status") or "open"
    current_owner = incident.get("assigned_to") or ""
    statuses = list(dict.fromkeys(INCIDENT_STATUSES + [current_status]))
    theme.show(theme.section("Ownership & status", "persisted in SQLite · survives recomputation",
                             theme.status_pill(current_status))
               + f'<div class="zx-muted" style="font-size:.78rem;margin-bottom:10px">Currently assigned to '
                 f'<b style="color:#DCE3F0">{theme.esc(current_owner or "nobody")}</b> · last updated {theme.esc(theme.fmt_dt(incident.get("updated_at")))}</div>')
    pending_key = f"inv_assign_pending_{iid}"
    pending = st.session_state.get(pending_key)
    if pending:
        new_status, new_owner = pending
        changes = []
        if new_status != current_status:
            changes.append(f"status {theme.esc(theme.humanize(current_status))} → <b>{theme.esc(theme.humanize(new_status))}</b>")
        if new_owner and new_owner != current_owner:
            changes.append(f"owner {theme.esc(current_owner or 'nobody')} → <b>{theme.esc(new_owner)}</b>")
        theme.show(
            f'<div style="padding:10px 12px;border-radius:12px;background:rgba(251,191,36,.10);border:1px solid rgba(251,191,36,.45);font-size:.82rem;margin-bottom:8px">'
            f'Apply to <b>{theme.esc(iid)}</b>: {"; ".join(changes)}? An audit entry is written.</div>')
        a, b = st.columns(2)
        if a.button("Confirm change", key="inv_assign_confirm", type="primary", use_container_width=True):
            try:
                api.update_incident_status(iid, new_status, new_owner or None)
                st.session_state.pop(pending_key, None)
                st.session_state["inv_assign_done"] = (iid, "; ".join(changes))
                st.cache_data.clear()
                st.rerun()
            except Exception as exc:
                api_error(exc, api_url)
        if b.button("Cancel", key="inv_assign_cancel", use_container_width=True):
            st.session_state.pop(pending_key, None)
            st.rerun()
        return
    done = st.session_state.get("inv_assign_done")
    if done and done[0] == iid:
        theme.show(f'<div style="padding:8px 12px;border-radius:12px;background:rgba(45,212,191,.10);border:1px solid rgba(45,212,191,.4);'
                   f'color:#5EEAD4;font-size:.8rem;margin-bottom:8px">✓ Saved · {re.sub("<[^>]+>", "", done[1])}</div>')
    a, b = st.columns(2)
    owner = a.text_input("Assign to", value=current_owner, key=f"inv_owner_{iid}", placeholder="Analyst name or team")
    status = b.selectbox("Status", statuses, index=statuses.index(current_status), key=f"inv_newstatus_{iid}",
                         format_func=theme.humanize)
    owner = owner.strip()
    changed = status != current_status or (owner and owner != current_owner)
    if st.button("Review change", key="inv_assign_review", type="primary", disabled=not changed, use_container_width=True):
        st.session_state[pending_key] = (status, owner)
        st.rerun()
    theme.show('<div class="zx-muted" style="font-size:.7rem;margin-top:6px">Nothing changes until you confirm. '
               'Clearing an existing assignee is not supported by the API; reassign instead.</div>')


# -------------------------------------------------------------------- render
def render(api: ZenithAPI, api_url: str) -> None:
    theme.show(_CSS)
    header_slot = st.empty()
    try:
        facets = api.incident_facets()
    except Exception as exc:
        with header_slot.container():
            theme.page_header("Incident", "investigation", "Correlation rationale, evidence and ownership.",
                              status=st.session_state.get("zx_status"))
        api_error(exc, api_url)
        st.stop()

    # Preselection handed over by other pages (Command → "Open full investigation").
    handed = st.session_state.pop("inv_incident", None)
    if handed:
        st.session_state["inv_selected"] = handed

    filters_slot = st.container()
    severities = list(st.session_state.get("inv_sev") or [])
    params = {k: v for k, v in _server_params(severities).items() if v is not None}
    show_watch = bool(st.session_state.get("inv_watch", False))
    sort = st.session_state.get("inv_sort", "Priority")
    page = int(st.session_state.get("inv_page", 1))
    try:
        matched = _scan(api_url, tuple(sorted(params.items())))
    except Exception as exc:
        with header_slot.container():
            theme.page_header("Incident", "investigation", "Correlation rationale, evidence and ownership.",
                              status=st.session_state.get("zx_status"))
        api_error(exc, api_url)
        st.stop()
    if len(severities) > 1:
        matched = [i for i in matched if i.get("severity") in severities]
    watch_count = sum(1 for i in matched if i.get("is_possible_association"))

    with header_slot.container():
        theme.page_header(
            "Incident", "investigation",
            f"{len(matched)} matching · {len(matched) - watch_count} corroborated · {watch_count} single-source on watchlist",
            status=st.session_state.get("zx_status"))
    with filters_slot:
        _filters(facets, watch_count)

    # Fast path: the backend can answer directly, so page on the server.
    if show_watch and len(severities) <= 1 and sort == "Priority":
        try:
            result = api.incidents(**params, page=page, page_size=PAGE_SIZE)
        except Exception as exc:
            api_error(exc, api_url)
            st.stop()
        items, total, pages = result["items"], result["total"], result["pages"]
    else:
        pool = _sort([i for i in matched if show_watch or not i.get("is_possible_association")], sort)
        total = len(pool)
        pages = (total + PAGE_SIZE - 1) // PAGE_SIZE if total else 0
        items = pool[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    if pages and page > pages:
        st.session_state["inv_page"] = pages
        st.rerun()

    ids = [i["incident_id"] for i in items]
    selected = st.session_state.get("inv_selected")
    if not selected and ids:
        selected = ids[0]
        st.session_state["inv_selected"] = selected

    left, right = st.columns([0.9, 1.25], gap="medium")
    with left, st.container(key="zxg_inv_list"):
        hidden = "" if show_watch or not watch_count else f"{watch_count} watchlist hidden"
        theme.show(theme.section("Incidents", f"{total} shown · {sort.lower()}",
                                 f'<span class="zx-muted" style="font-size:.7rem">{hidden}</span>'))
        if not items:
            msg = ("No incidents match these filters."
                   + (f" {watch_count} single-source incident(s) are on the watchlist — toggle it on to review them."
                      if watch_count and not show_watch else ""))
            theme.show(theme.empty(msg))
        for inc in items:
            iid = inc["incident_id"]
            active = iid == selected
            card_col, btn_col = st.columns([1, 0.16], gap="small", vertical_alignment="center")
            with card_col:
                theme.show(_card(inc, active))
            if btn_col.button("●" if active else "›", key=f"inv_open_{iid}", disabled=active,
                              type="primary" if active else "secondary", use_container_width=True,
                              help="Currently open" if active else f"Open {iid}"):
                st.session_state["inv_selected"] = iid
                st.rerun()
        if pages > 1:
            pagination("inv_page", page, pages, total)

    with right:
        if not selected:
            theme.show(theme.empty("Select an incident to investigate."))
            return
        if ids and selected not in ids:
            theme.show(f'<div class="zx-muted" style="font-size:.74rem;margin:0 0 8px">Showing <span class="zx-mono">{theme.esc(selected)}</span>, '
                       f'which is outside the current filters or page.</div>')
        with st.container(key="zxg_inv_panel"):
            components.incident_panel(api, api_url, selected, key="inv")
        try:
            incident, reasoning = components.load_incident(api_url, selected)
        except Exception:
            return  # incident_panel already explained the failure
        with st.container(key="zxg_inv_tabs"):
            n_q = len(incident.get("quality_issues") or [])
            n_c = len(incident.get("contradictions") or []) + len(incident.get("missing_info") or [])
            tabs = st.tabs([f"Evidence · {len(incident.get('timeline') or [])}", "Timeline", f"Data quality · {n_q}",
                            f"Correlation · {n_c}" if n_c else "Correlation", "Assign"])
            with tabs[0]:
                _tab_evidence(incident)
            with tabs[1]:
                _tab_timeline(incident)
            with tabs[2]:
                _tab_quality(incident)
            with tabs[3]:
                _tab_correlation(incident, reasoning)
            with tabs[4]:
                _tab_assign(api, api_url, incident)
