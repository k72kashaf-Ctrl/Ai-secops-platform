"""Interactive building blocks shared by several pages (widgets + theme HTML)."""
from __future__ import annotations

from typing import Any

import streamlit as st

from ui import theme
from ui.api_client import ApiConnectionError, ApiError, ZenithAPI

STATUSES = ["open", "acknowledged", "investigating", "false_positive", "resolved", "closed"]


@st.cache_data(ttl=10, show_spinner=False)
def load_incident(base_url: str, incident_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    api = ZenithAPI(base_url)
    return api.incident(incident_id), api.incident_reasoning(incident_id)


def goto(page_label: str, **state: Any) -> None:
    for k, v in state.items():
        st.session_state[k] = v
    st.session_state["zx_goto"] = page_label
    st.rerun()


def incident_panel(api: ZenithAPI, base_url: str, incident_id: str, key: str, compact: bool = False) -> None:
    """Severity-tinted incident card: confidence, why, evidence chain, findings, proposals, actions."""
    try:
        incident, reasoning = load_incident(base_url, incident_id)
    except (ApiConnectionError, ApiError) as exc:
        st.warning(f"Could not load {incident_id}: {exc}")
        return
    sev = theme.sev_of(incident.get("severity"))
    c = theme.SEV[sev]
    conf = float(incident.get("confidence") or 0)
    status = incident.get("status") or "open"
    timeline = incident.get("timeline") or []

    head = (
        f'<div style="margin:-16px -18px 12px;padding:16px 18px 12px;background:linear-gradient(180deg,{c["halo"]},transparent)">'
        f'<div class="zx-row" style="justify-content:space-between">{theme.sev_pill(sev, solid=True)}'
        f'<span class="zx-row" style="gap:8px">{theme.status_pill(status)}'
        f'<span class="zx-mono" style="font-size:.7rem;color:#B4BED3">{theme.esc(incident_id)}</span></span></div>'
        f'<div style="font-family:var(--zx-head);font-size:1.3rem;font-weight:600;margin:8px 0 4px;letter-spacing:-.01em">{theme.esc(incident.get("title"))}</div>'
        f'<div style="color:#C7D0E0;line-height:1.5;font-size:.88rem">{theme.esc(reasoning.get("answer") or "")}</div></div>'
    )
    why = []
    sources = incident.get("sources") or []
    if len(sources) > 1:
        why.append(f"{len(sources)} independent sources agree: {', '.join(sources)}")
    elif sources:
        why.append(f"Single source ({sources[0]}) — needs corroboration")
    why.append(f"{incident.get('event_count', len(timeline))} events, ordered by event time")
    uncertainty = [theme.detime(u) for u in dict.fromkeys(reasoning.get("uncertainty") or [])]
    missing = [theme.detime(m) for m in reasoning.get("missing_information") or []]
    why.append("No conflicts or missing fields flagged" if not (uncertainty or missing)
               else f"{len(uncertainty)} conflict(s), {len(missing)} gap(s) lower the score")
    why_html = "".join(
        f'<div style="display:flex;gap:8px;font-size:.8rem;color:#DCE3F0;line-height:1.35;margin-bottom:5px">'
        f'<span style="color:{"#2DD4BF" if i < 2 or not (uncertainty or missing) else "#FBBF24"}">{"✓" if i < 2 or not (uncertainty or missing) else "!"}</span>'
        f'{theme.esc(w)}</div>' for i, w in enumerate(why))
    ring = theme.ring(conf, size=96, stroke=9, c1="#67E8F9", c2="#A78BFA", label=f"{conf:.2f}", sub="CONFIDENCE", gid=f"zxc-{key}")
    score = (f'<div style="display:grid;grid-template-columns:104px 1fr;gap:14px;align-items:center">{ring}'
             f'<div><div class="zx-eyebrow zx-grad" style="margin-bottom:8px">WHY THIS SCORE</div>{why_html}</div></div>')

    lines = []
    for ev in timeline[: (4 if compact else 12)]:
        payload = ev.get("payload") or {}
        detail = ", ".join(f"{k}={v}" for k, v in list(payload.items())[:3] if k not in ("clip",))
        text = f"{theme.humanize(ev.get('event_type'))} · {ev.get('device_id')} · conf {float(ev.get('confidence') or 0):.2f}"
        if detail:
            text += f" · {detail}"
        lines.append(("FACT", text, ev.get("event_id")))
    for inf in (reasoning.get("inferences") or [])[:2]:
        lines.append(("INFERENCE", inf if len(inf) < 220 else inf[:217] + "…", f"{len(timeline)} events"))
    for u in uncertainty[:3]:
        lines.append(("CONFLICT", u, ""))
    for m in missing[:3]:
        lines.append(("MISSING", m, ""))
    findings = "".join(
        f'<div class="zx-line">{theme.kind_badge(k)}<span style="font-size:.8rem;line-height:1.4;color:#DCE3F0">{theme.esc(t)}</span>'
        f'{theme.ev_link(r) if r else "<span></span>"}</div>' for k, t, r in lines)
    recs = reasoning.get("recommended_actions") or incident.get("recommended_actions") or []
    proposals = ""
    if recs:
        proposals = (
            '<div style="margin-top:12px;border-radius:14px;padding:12px 14px;background:linear-gradient(135deg,rgba(129,140,248,.10),rgba(232,121,249,.05));'
            'border:1px solid rgba(165,180,252,.2)"><div style="font-size:.8rem;font-weight:600;margin-bottom:6px">Zenith proposes '
            '<span style="font-weight:400;color:#A5B4FC">· you decide</span></div>'
            + "".join(f'<div style="display:flex;gap:8px;font-size:.8rem;color:#C7D0E0;line-height:1.4;margin-bottom:3px">'
                      f'<span style="color:#A5B4FC">→</span>{theme.esc(r)}</div>' for r in recs[:4])
            + "</div>")
    provider = theme.esc(reasoning.get("provider") or "deterministic")
    theme.show(head + score + theme.evidence_chain(timeline) + findings + proposals
               + f'<div class="zx-muted" style="font-size:.66rem;margin-top:10px">Grounded in {len(reasoning.get("evidence_event_ids") or [])} event(s) · provider {provider}</div>')
    status_actions(api, incident_id, status, key)


def status_actions(api: ZenithAPI, incident_id: str, status: str, key: str) -> None:
    """Propose → confirm → undo status change. Nothing changes without a click on Confirm."""
    pending_key, undo_key = f"{key}_pending", f"{key}_undo"
    pending = st.session_state.get(pending_key)
    if pending:
        theme.show(
            f'<div style="margin-top:10px;padding:10px 12px;border-radius:12px;background:rgba(251,191,36,.10);border:1px solid rgba(251,191,36,.45);font-size:.82rem">'
            f'Change <b>{theme.esc(incident_id)}</b>: {theme.esc(theme.humanize(status))} → <b>{theme.esc(theme.humanize(pending))}</b>?</div>'
        )
        a, b = st.columns(2)
        if a.button("Confirm", key=f"{key}_confirm", type="primary", use_container_width=True):
            try:
                api.update_incident_status(incident_id, pending)
                st.session_state[undo_key] = (incident_id, status, pending)
                st.session_state.pop(pending_key, None)
                st.cache_data.clear()
                st.rerun()
            except (ApiConnectionError, ApiError) as exc:
                st.error(f"Update failed: {exc}")
        if b.button("Cancel", key=f"{key}_cancel", use_container_width=True):
            st.session_state.pop(pending_key, None)
            st.rerun()
        return
    undo = st.session_state.get(undo_key)
    if undo and undo[0] == incident_id:
        u1, u2 = st.columns([3, 1])
        u1.html(
            f'<div style="padding:8px 12px;border-radius:12px;background:rgba(45,212,191,.10);border:1px solid rgba(45,212,191,.4);color:#5EEAD4;font-size:.8rem">'
            f'✓ {theme.esc(theme.humanize(undo[2]))} · audit entry written</div>')
        if u2.button("Undo", key=f"{key}_undo_btn", use_container_width=True):
            try:
                api.update_incident_status(incident_id, undo[1])
                st.session_state.pop(undo_key, None)
                st.cache_data.clear()
                st.rerun()
            except (ApiConnectionError, ApiError) as exc:
                st.error(f"Undo failed: {exc}")
    cols = st.columns([1.2, 1.2, 1])
    if status == "open" and cols[0].button("Acknowledge", key=f"{key}_ack", type="primary", use_container_width=True):
        st.session_state[pending_key] = "acknowledged"
        st.rerun()
    if status != "open" and cols[0].button("Investigate", key=f"{key}_inv", type="primary", use_container_width=True,
                                          disabled=status == "investigating"):
        st.session_state[pending_key] = "investigating"
        st.rerun()
    choice = cols[1].selectbox("Set status", [s for s in STATUSES if s != status], key=f"{key}_sel",
                               format_func=theme.humanize, index=None, placeholder="Change status…",
                               label_visibility="collapsed")
    if choice:
        st.session_state[pending_key] = choice
        st.session_state.pop(f"{key}_sel", None)
        st.rerun()
    if cols[2].button("✦ Ask", key=f"{key}_ask", use_container_width=True):
        goto(":material/auto_awesome: Copilot", zx_copilot_prefill=f"Explain incident {incident_id}")
