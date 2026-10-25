"""Briefing page — an evidence-backed shift briefing document."""
from __future__ import annotations

import re

from ui import components, theme
from ui.common import *  # noqa: F401,F403

_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?")


def _clean(text: Any) -> str:
    """Replace raw ISO timestamps inside backend prose with '12 Mar 09:20'."""
    return _ISO.sub(lambda m: theme.fmt_dt(m.group()), "" if text is None else str(text))


ISSUE_LABEL = {
    "conflicting_sensors": "Sensor disagreement",
    "conflicting_location": "Location contradiction",
    "analysis_limit_reached": "Comparison limit reached",
    "missing_mandatory_field": "Missing mandatory field",
    "invalid_record": "Invalid record",
    "duplicate_event_id": "Duplicate event ID",
    "duplicate_content": "Duplicate content",
    "incomplete_record": "Defaulted fields",
    "delayed_event": "Late arrival",
    "out_of_order_arrival": "Out-of-order arrival",
    "conflicting_timestamps": "Clock skew",
    "uncorroborated_event": "Single-source alert",
    "low_confidence": "Low-confidence detection",
    "repeated_events": "Repeated burst",
    "event_spike": "Zone spike",
    "unhealthy_device": "Device reported unhealthy",
    "offline_device": "Silent device",
}


def _label(value: Any) -> str:
    return ISSUE_LABEL.get(str(value), theme.humanize(value))


def _chips(ids: list[Any] | None, limit: int = 8) -> str:
    ids = [i for i in (ids or []) if i]
    more = f'<span class="zx-muted" style="font-size:.68rem">+{len(ids) - limit} more</span>' if len(ids) > limit else ""
    return "".join(theme.ev_link(i) for i in ids[:limit]) + more


def _tile(label: str, value: Any, sub: str, color: str) -> str:
    return (
        f'<div class="zx-glass" style="flex:1;min-width:130px;margin:0;padding:12px 14px">'
        f'<span aria-hidden="true" style="position:absolute;right:-26px;top:-26px;width:90px;height:90px;border-radius:50%;background:{color}2E;filter:blur(24px)"></span>'
        f'<div style="font-size:.74rem;color:#B4BED3;position:relative">{theme.esc(label)}</div>'
        f'<div class="zx-kpi-v" style="font-size:1.8rem;margin:6px 0 2px;position:relative">{theme.esc(value)}</div>'
        f'<div class="zx-muted" style="font-size:.68rem;position:relative">{theme.esc(sub)}</div></div>'
    )


def _incident_row(inc: dict[str, Any]) -> str:
    sev = theme.sev_of(inc.get("severity"))
    c = theme.SEV[sev]
    conf = inc.get("confidence")
    sources = " · ".join(str(s).replace("_", " ") for s in inc.get("sources") or [])
    zones = ", ".join(inc.get("zones") or [])
    why = _clean(inc.get("explanation"))
    single = '<span style="margin-left:4px">' + theme.chip("single source", "warning") + "</span>" if inc.get("is_possible_association") else ""
    return (
        f'<div style="display:flex;gap:12px;padding:12px 0;border-bottom:1px solid rgba(255,255,255,.05)">'
        f'<span style="width:4px;border-radius:4px;background:linear-gradient({c["c"]},{c["light"]});box-shadow:0 0 10px {c["c"]}"></span>'
        f'<div style="flex:1;min-width:0">'
        f'<div class="zx-row" style="flex-wrap:wrap;gap:8px">{theme.sev_pill(sev, solid=sev == "critical")}'
        f'<span style="font-weight:600;font-size:.92rem">{theme.esc(inc.get("title"))}</span>{single}'
        f'<span class="zx-row" style="margin-left:auto;gap:8px">{theme.status_pill(inc.get("status"))}'
        f'<span style="font-family:var(--zx-head);font-weight:600">{"—" if conf is None else f"{float(conf):.2f}"}</span></span></div>'
        f'<div class="zx-mono" style="font-size:.66rem;color:#8B97B0;margin-top:4px">{theme.esc(inc.get("incident_id"))} · '
        f'{theme.esc(theme.fmt_dt(inc.get("created_at")))} · {theme.esc(zones)} · {theme.esc(sources)}</div>'
        f'<div class="zx-row" style="flex-wrap:wrap;gap:6px;margin-top:8px">'
        f'<span class="zx-muted" style="font-size:.7rem">Evidence</span>{_chips(inc.get("event_ids"))}</div>'
        f'<details style="margin-top:6px"><summary style="cursor:pointer;font-size:.74rem;color:#A5B4FC">Why linked</summary>'
        f'<div style="font-size:.78rem;color:#C7D0E0;line-height:1.5;margin-top:4px">{theme.esc(why)}</div></details>'
        f'</div></div>'
    )


def _signal_row(issue: dict[str, Any]) -> str:
    refs = [issue.get("issue_id"), issue.get("event_id")]
    dev = issue.get("device_id")
    dev_html = (f'<span class="zx-ev" style="background:rgba(167,139,250,.10);color:#C4B5FD">{theme.esc(dev)}</span>' if dev else "")
    return (
        f'<div style="padding:10px 0;border-bottom:1px solid rgba(255,255,255,.05)">'
        f'<div class="zx-row" style="gap:8px;flex-wrap:wrap">{theme.sev_pill(issue.get("severity"))}'
        f'<span style="font-weight:600;font-size:.85rem">{theme.esc(_label(issue.get("issue_type")))}</span></div>'
        f'<div style="font-size:.8rem;color:#DCE3F0;line-height:1.45;margin-top:6px">{theme.esc(_clean(issue.get("explanation")))}</div>'
        f'<div class="zx-row" style="flex-wrap:wrap;gap:6px;margin-top:6px">{_chips(refs)}{dev_html}</div>'
        f'<div style="font-size:.76rem;color:#A5B4FC;margin-top:6px">→ {theme.esc(issue.get("suggested_action"))}</div></div>'
    )


def _load(api: ZenithAPI, api_url: str, force: bool) -> dict[str, Any]:
    if force or "briefing_cache" not in st.session_state:
        try:
            with st.spinner("Aggregating incidents, devices and quality findings…"):
                st.session_state["briefing_cache"] = api.briefing()
        except Exception as exc:
            api_error(exc, api_url)
            st.stop()
    return st.session_state["briefing_cache"]


def render(api: ZenithAPI, api_url: str) -> None:
    theme.page_header("Shift", "briefing", "Evidence-backed handover generated deterministically from stored records.",
                      status=st.session_state.get("zx_status"))
    force = st.session_state.pop("brief_force", False)
    briefing = _load(api, api_url, force)
    sections = briefing["sections"]
    summary = sections["summary"]
    stats = briefing["stats"]

    # ------------------------------------------------------------ AI header
    with st.container(key="zxai_brief_head"):
        left, right = st.columns([1, 0.26], vertical_alignment="center")
        left.markdown(
            f'<div class="zx-row" style="gap:16px;flex-wrap:wrap">{theme.orb(56)}'
            f'<div style="flex:1;min-width:240px"><div class="zx-eyebrow">ZENITH · SHIFT BRIEFING</div>'
            f'<div style="font-family:var(--zx-head);font-size:1.45rem;font-weight:600;margin:2px 0 4px">Shift briefing</div>'
            f'<div class="zx-row" style="flex-wrap:wrap;gap:8px">'
            f'{theme.chip("Generated " + theme.fmt_dt(briefing["generated_at"]), "info")}'
            f'{theme.chip("Data to " + theme.fmt_dt(briefing["reference_time"]), "neutral")}'
            f'{theme.chip(str(briefing["window_hours"]) + "h new-incident window", "neutral")}'
            f'{theme.chip("provider deterministic", "ok")}</div></div></div>'
            f'<div style="font-family:var(--zx-head);font-size:1.02rem;line-height:1.5;margin-top:14px;color:#E6EBF5">'
            f'{theme.esc(_clean(summary.get("text")))}</div>',
            unsafe_allow_html=True,
        )
        with right:
            if st.button("Regenerate", type="primary", use_container_width=True, key="brief_regen"):
                st.session_state["brief_force"] = True
                st.rerun()
            st.download_button(
                "Download markdown",
                data=briefing["markdown"],
                file_name=f"zenith-briefing-{briefing['generated_at'][:10]}.md",
                mime="text/markdown",
                use_container_width=True,
                key="brief_dl",
            )

    # ----------------------------------------------------------- KPI strip
    fp = summary.get("false_positive_incidents")
    tiles = [
        ("Valid events", stats.get("valid_events", 0), f"{stats.get('rejected_events', 0)} rejected · {stats.get('duplicate_content_events', 0)} duplicate", "#22D3EE"),
        ("Incidents", stats.get("incidents", 0), f"{summary.get('possible_associations', 0)} possible associations", "#818CF8"),
        ("Unresolved", summary.get("unresolved_incidents", 0), f"{summary.get('resolved_incidents', 0)} resolved · {fp or 0} false positive", "#E879F9"),
        ("High / critical", stats.get("high_severity_incidents", 0), "open or investigating", "#FF4D6D"),
        ("Quality findings", stats.get("quality_issues", 0), f"{len(sections.get('quality_by_type') or [])} check types fired", "#FBBF24"),
        ("Unhealthy devices", stats.get("unhealthy_devices", 0), "explicit device reports", "#34D399"),
    ]
    theme.show('<div style="display:flex;gap:10px;flex-wrap:wrap;margin:14px 0">' + "".join(_tile(*t) for t in tiles) + "</div>")

    main, side = st.columns([1, 0.62], gap="medium")
    with main:
        prio = sections.get("highest_priority_incidents") or []
        with st.container(key="zxg_brief_attention"):
            theme.show(theme.section("Needs attention", f"{len(prio)} highest-priority incident(s)",
                                     theme.chip("by severity, then confidence", "neutral", dot=False)))
            if not prio:
                theme.show(theme.empty("No unresolved incidents."))
            for inc in prio:
                theme.show(_incident_row(inc))
            if prio:
                ids = [p["incident_id"] for p in prio]
                a, b = st.columns([1, 0.42], vertical_alignment="bottom")
                pick = a.selectbox("Open incident", ids, key="brief_pick", label_visibility="collapsed",
                                   format_func=lambda i: next((f"{p['title'][:48]} · {i}" for p in prio if p["incident_id"] == i), i))
                if b.button("Open investigation →", key="brief_open", use_container_width=True):
                    components.goto(":material/crisis_alert: Incidents", inv_incident=pick)

        actions = sections.get("recommended_management_actions") or []
        with st.container(key="zxg_brief_actions"):
            theme.show(theme.section("Recommended actions", "proposals only · nothing changes without an operator"))
            if not actions:
                theme.show('<div class="zx-muted">No actions proposed.</div>')
            theme.show("".join(
                f'<div style="display:grid;grid-template-columns:22px 1fr auto;gap:8px;align-items:start;padding:8px 0;border-bottom:1px solid rgba(255,255,255,.05)">'
                f'<span class="zx-mono" style="font-size:.7rem;color:#A5B4FC">{n:02d}</span>'
                f'<span><span style="font-size:.84rem;color:#EEF2FA">{theme.esc(a.get("action"))}</span><br>'
                f'<span class="zx-muted" style="font-size:.72rem">{theme.esc(a.get("reason"))}</span></span>'
                f'{theme.ev_link(a.get("incident_id")) if a.get("incident_id") else "<span></span>"}</div>'
                for n, a in enumerate(actions, 1)))

        new = sections.get("newly_detected_incidents") or []
        unresolved = sections.get("unresolved_incidents") or []
        with st.container(key="zxg_brief_queues"):
            theme.show(theme.section("Incident queues", f"new in last {briefing['window_hours']}h · unresolved"))
            t1, t2 = st.tabs([f"Newly detected ({len(new)})", f"Unresolved ({len(unresolved)})"])
            for tab, rows in ((t1, new), (t2, unresolved)):
                with tab:
                    if rows:
                        theme.show("".join(theme.incident_card(r) for r in rows))
                    else:
                        theme.show('<div class="zx-muted">None.</div>')

    with side:
        signals = [*(sections.get("repeated_events") or []), *(sections.get("event_spikes") or []),
                   *(sections.get("device_health") or [])]
        with st.container(key="zxg_brief_signals"):
            theme.show(theme.section("Signals & devices", "repeats · spikes · device health"))
            if signals:
                theme.show("".join(_signal_row(s) for s in signals))
            else:
                theme.show('<div class="zx-muted">No repeats, spikes or device-health findings.</div>')

        qbt = sections.get("quality_by_type") or []
        with st.container(key="zxg_brief_caveats"):
            theme.show(theme.section("Data-quality caveats", f"{stats.get('quality_issues', 0)} finding(s) qualify these conclusions"))
            top = max([r["count"] for r in qbt] + [1])
            theme.show("".join(
                f'<div style="margin-bottom:8px"><div class="zx-row" style="justify-content:space-between;font-size:.78rem;margin-bottom:4px">'
                f'<span>{theme.esc(_label(r["issue_type"]))}</span>'
                f'<span class="zx-mono">{r["count"]}</span></div>'
                f'<div style="height:5px;border-radius:5px;background:rgba(255,255,255,.05)"><div style="width:{r["count"] / top * 100:.0f}%;height:100%;'
                f'border-radius:5px;background:linear-gradient(90deg,#22D3EE,#818CF8,#E879F9)"></div></div></div>'
                for r in qbt) or '<div class="zx-muted">No quality findings recorded.</div>')
            theme.show(
                '<div style="margin-top:12px;border-radius:12px;padding:10px 12px;background:linear-gradient(135deg,rgba(251,191,36,.10),rgba(251,191,36,.02));'
                'border:1px solid rgba(251,191,36,.28);font-size:.78rem;color:#E5D3A6;line-height:1.5">'
                f'<span class="zx-eyebrow" style="color:#FCD34D;margin-right:6px">CONFIDENCE</span>{theme.esc(_clean(sections.get("confidence_statement")))}</div>')
            if st.button("Open data trust →", key="brief_trust", use_container_width=True):
                components.goto(":material/verified_user: Trust")

    # ------------------------------------------------------- evidence index
    evidence = briefing.get("evidence") or {}
    with st.expander(f"Structured evidence index · {sum(len(v) for v in evidence.values() if isinstance(v, list))} references"):
        for key, ids in evidence.items():
            if isinstance(ids, list):
                theme.show(f'<div style="margin-bottom:10px"><div class="zx-eyebrow" style="margin-bottom:6px">{theme.esc(theme.humanize(key))} · {len(ids)}</div>'
                           f'<div class="zx-row" style="flex-wrap:wrap;gap:6px">{_chips(ids, limit=200)}</div></div>')
        st.json(evidence)
    with st.expander("Markdown preview"):
        st.markdown(briefing["markdown"])
