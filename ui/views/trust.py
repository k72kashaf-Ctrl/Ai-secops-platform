"""Data trust page — what the agent refused to take at face value."""
from __future__ import annotations

import re

from ui import theme
from ui.common import *  # noqa: F401,F403

PAGE_SIZE = 8

_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?")


def _clean(text: Any) -> str:
    """Replace raw ISO timestamps inside backend prose with '12 Mar 09:20'."""
    return _ISO.sub(lambda m: theme.fmt_dt(m.group()), "" if text is None else str(text))


# Every deterministic check in app/services/data_quality.py, grouped for reviewers.
FAMILIES: dict[str, dict[str, Any]] = {
    "Contradictions": {
        "types": ["conflicting_sensors", "conflicting_location", "analysis_limit_reached"],
        "c1": "#FBBF24", "c2": "#FB7185",
        "did": "Kept both claims side by side and lowered incident confidence instead of picking a winner.",
    },
    "Record integrity": {
        "types": ["missing_mandatory_field", "invalid_record", "duplicate_event_id", "duplicate_content", "incomplete_record"],
        "c1": "#22D3EE", "c2": "#818CF8",
        "did": "Rejected bad records with raw input preserved, kept the first duplicate, and marked defaulted fields as assumed.",
    },
    "Timing": {
        "types": ["delayed_event", "out_of_order_arrival", "conflicting_timestamps"],
        "c1": "#818CF8", "c2": "#E879F9",
        "did": "Ordered evidence by event time, never arrival time, and flagged clocks it could not trust.",
    },
    "Corroboration": {
        "types": ["uncorroborated_event", "low_confidence", "repeated_events", "event_spike"],
        "c1": "#A78BFA", "c2": "#22D3EE",
        "did": "Held single-source and low-confidence signals on a watchlist; bursts are reported as observations, not attacks.",
    },
    "Devices": {
        "types": ["unhealthy_device", "offline_device"],
        "c1": "#34D399", "c2": "#22D3EE",
        "did": "Treated coverage from degraded or silent devices as unverified for the affected window.",
    },
}

TYPE_LABEL = {
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


def _label(issue_type: Any) -> str:
    return TYPE_LABEL.get(str(issue_type), theme.humanize(issue_type))


def _family_of(issue_type: str) -> str:
    for name, fam in FAMILIES.items():
        if issue_type in fam["types"]:
            return name
    return "Other"


@st.cache_data(ttl=10, show_spinner=False)
def _load(base_url: str) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    api = ZenithAPI(base_url)
    overview = api.overview()
    summary = api.quality_summary()
    issues = api.quality_issues(page_size=500)["items"]
    return overview, summary, issues


def _reset_page() -> None:
    st.session_state["quality_page"] = 1


# ---------------------------------------------------------------- sections
def _funnel(stats: dict[str, Any]) -> str:
    valid = stats.get("valid_events", 0)
    dup = stats.get("duplicate_content_events", 0)
    rej = stats.get("rejected_events", 0)
    received = valid + dup + rej
    tiles = [
        ("Received", received, "records submitted", "#22D3EE"),
        ("Rejected", rej, "raw input preserved", "#FF4D6D"),
        ("Duplicate content", dup, "kept first, linked copy", "#FBBF24"),
        ("Valid", valid, "analysed as evidence", "#34D399"),
        ("Incidents", stats.get("incidents", 0), f"{stats.get('high_severity_incidents', 0)} high / critical", "#E879F9"),
    ]
    arrow = ('<span aria-hidden="true" class="zx-hide-sm" style="align-self:center;color:#5F6B85;font-size:1.2rem;'
             'text-shadow:0 0 10px rgba(125,211,252,.5)">→</span>')
    parts = []
    for i, (label, value, sub, color) in enumerate(tiles):
        share = f"{value / received * 100:.0f}% of received" if received and i in (1, 2, 3) else sub
        parts.append(
            f'<div class="zx-glass" style="flex:1;min-width:140px;margin:0;padding:14px 16px;box-shadow:0 0 0 1px {color}22 inset,0 18px 40px -22px {color}">'
            f'<span aria-hidden="true" style="position:absolute;right:-30px;top:-30px;width:100px;height:100px;border-radius:50%;background:{color}30;filter:blur(26px)"></span>'
            f'<div class="zx-eyebrow" style="color:{color};position:relative">{theme.esc(label)}</div>'
            f'<div class="zx-kpi-v" style="font-size:2rem;margin:8px 0 4px;position:relative;text-shadow:0 0 18px {color}66">{theme.esc(value)}</div>'
            f'<div class="zx-muted" style="font-size:.72rem;position:relative">{theme.esc(sub)}'
            f'{"" if share == sub else " · " + theme.esc(share)}</div></div>'
        )
        if i < len(tiles) - 1:
            parts.append(arrow)
    return f'<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px">{"".join(parts)}</div>'


def _family_card(name: str, fam: dict[str, Any], counts: dict[str, int], max_sev: dict[str, str]) -> str:
    total = sum(counts.get(t, 0) for t in fam["types"])
    top = max([counts.get(t, 0) for t in fam["types"]] + [1])
    rows = []
    for t in fam["types"]:
        n = counts.get(t, 0)
        width = n / top * 100 if n else 0
        sev = f'<span style="margin-left:6px">{theme.sev_pill(max_sev[t])}</span>' if n and t in max_sev else ""
        rows.append(
            f'<div style="margin-bottom:8px"><div class="zx-row" style="justify-content:space-between;font-size:.78rem;margin-bottom:4px">'
            f'<span style="color:{"#DCE3F0" if n else "#6B7790"}">{theme.esc(_label(t))}{sev}</span>'
            f'<span class="zx-mono" style="color:{"#EEF2FA" if n else "#5F6B85"}">{n}</span></div>'
            f'<div style="height:6px;border-radius:6px;background:rgba(255,255,255,.05);overflow:hidden">'
            f'<div style="width:{width:.0f}%;height:100%;border-radius:6px;background:linear-gradient(90deg,{fam["c1"]},{fam["c2"]});'
            f'box-shadow:0 0 10px {fam["c1"]}88"></div></div></div>'
        )
    return theme.glass(
        f'<div class="zx-row" style="justify-content:space-between;align-items:baseline;margin-bottom:10px">'
        f'<span class="zx-h">{theme.esc(name)}</span>'
        f'<span style="font-family:var(--zx-head);font-size:1.6rem;font-weight:600;background:linear-gradient(100deg,{fam["c1"]},{fam["c2"]});'
        f'-webkit-background-clip:text;background-clip:text;color:transparent">{total}</span></div>'
        f'{"".join(rows)}'
        f'<div style="margin-top:10px;padding-top:10px;border-top:1px solid rgba(255,255,255,.06);font-size:.74rem;color:#C7D0E0;line-height:1.45">'
        f'<span class="zx-eyebrow zx-grad" style="margin-right:6px">AGENT DID</span>{theme.esc(fam["did"])}</div>',
        style="min-height:340px;margin:0",
    )


def _coverage_card(fams: dict[str, dict[str, Any]], counts: dict[str, int]) -> str:
    checks = [t for f in fams.values() for t in f["types"]]
    fired = [t for t in checks if counts.get(t)]
    clear = [t for t in checks if not counts.get(t)]
    frac = len(fired) / len(checks) if checks else 0
    clear_html = "".join(theme.chip(_label(t), "ok") for t in clear) or '<span class="zx-muted">Every check produced findings.</span>'
    return theme.glass(
        f'<div class="zx-row" style="gap:16px">{theme.ring(frac, 84, 8, "#22D3EE", "#E879F9", f"{len(fired)}/{len(checks)}", gid="zxdq-cov", sub="FIRED")}'
        f'<div><div class="zx-h">Check coverage</div><div class="zx-muted" style="font-size:.76rem;margin-top:4px;line-height:1.45">'
        f'{len(checks)} deterministic checks ran on every analysis. {len(fired)} produced findings; '
        f'{len(clear)} found nothing to flag in this dataset.</div></div></div>'
        f'<div class="zx-eyebrow" style="margin:14px 0 8px">CLEAN CHECKS</div>'
        f'<div class="zx-row" style="flex-wrap:wrap;gap:6px">{clear_html}</div>'
        f'<div style="margin-top:14px;font-size:.74rem;color:#C7D0E0;line-height:1.45">'
        f'<span class="zx-eyebrow zx-grad" style="margin-right:6px">NO AI GUESSING</span>'
        f'Same input, same findings: checks are rules over stored events, re-run on every ingest.</div>',
        style="min-height:340px;margin:0", ai=True,
    )


def _refs(issue: dict[str, Any]) -> list[str]:
    details = issue.get("details") or {}
    refs = [issue.get("event_id")]
    for key in ("first_event_id", "second_event_id", "duplicate_of", "previous_event_id"):
        refs.append(details.get(key))
    return [r for r in dict.fromkeys(refs) if r]


def _issue_card(issue: dict[str, Any]) -> str:
    sev = theme.sev_of(issue.get("severity"))
    c = theme.SEV[sev]
    refs = "".join(theme.ev_link(r) for r in _refs(issue))
    device = issue.get("device_id")
    details = issue.get("details") or {}
    if details.get("zone"):
        refs += theme.chip(details["zone"], "neutral", dot=False)
    affected = ""
    if refs or device:
        dev = (f'<span class="zx-ev" style="background:rgba(167,139,250,.10);color:#C4B5FD">{theme.esc(device)}</span>'
               if device else "")
        affected = (f'<div class="zx-row" style="flex-wrap:wrap;gap:6px;margin-top:10px">'
                    f'<span class="zx-muted" style="font-size:.7rem">Affected</span>{refs}{dev}</div>')
    proposal = ""
    if issue.get("suggested_action"):
        proposal = (
            '<div style="margin-top:10px;border-radius:12px;padding:9px 12px;background:linear-gradient(135deg,rgba(129,140,248,.10),rgba(232,121,249,.05));'
            'border:1px solid rgba(165,180,252,.2);font-size:.8rem;color:#C7D0E0;line-height:1.45">'
            '<span style="font-weight:600;color:#EEF2FA">Zenith proposes</span> <span style="color:#A5B4FC">· you decide</span><br>'
            f'<span style="color:#A5B4FC">→</span> {theme.esc(issue["suggested_action"])}</div>')
    return (
        f'<div style="display:flex;gap:12px;background:rgba(255,255,255,.02);border:1px solid rgba(255,255,255,.06);border-radius:14px;padding:12px 14px;margin-top:4px">'
        f'<span style="width:4px;border-radius:4px;background:linear-gradient({c["c"]},{c["light"]});box-shadow:0 0 10px {c["c"]}"></span>'
        f'<div style="flex:1;min-width:0">'
        f'<div class="zx-row" style="flex-wrap:wrap;gap:8px">{theme.sev_pill(sev)}'
        f'<span style="font-family:var(--zx-head);font-weight:600;font-size:.98rem">{theme.esc(_label(issue.get("issue_type")))}</span>'
        f'<span class="zx-muted" style="font-size:.7rem">{theme.esc(_family_of(str(issue.get("issue_type"))))}</span>'
        f'<span class="zx-mono" style="margin-left:auto;font-size:.66rem;color:#8B97B0">{theme.esc(issue.get("issue_id"))} · '
        f'{theme.esc(theme.fmt_dt(issue.get("detected_at")))}</span></div>'
        f'<div style="margin-top:8px;font-size:.84rem;color:#DCE3F0;line-height:1.5">{theme.esc(_clean(issue.get("explanation")))}</div>'
        f'{affected}{proposal}</div></div>'
    )


def _explorer(api: ZenithAPI, api_url: str, issues_all: list[dict[str, Any]], present_types: list[str]) -> None:
    families = ["All families"] + [f for f in FAMILIES if any(_family_of(t) == f for t in present_types)]
    if any(_family_of(t) == "Other" for t in present_types):
        families.append("Other")
    devices = sorted({i["device_id"] for i in issues_all if i.get("device_id")})

    with st.container(key="zxg_explorer"):
        theme.show(theme.section("Issue explorer", "every finding explains why it was flagged and what to do next"))
        c1, c2, c3, c4, c5 = st.columns([1.1, 1.3, 0.9, 1.1, 1.4])
        family = c1.selectbox("Family", families, key="dq_family", on_change=_reset_page)
        type_opts = [t for t in present_types if family == "All families" or _family_of(t) == family]
        if st.session_state.get("dq_type") not in (["All"] + type_opts):
            st.session_state["dq_type"] = "All"
        issue_type = c2.selectbox("Check", ["All"] + type_opts, key="dq_type", on_change=_reset_page,
                                  format_func=lambda t: "All checks" if t == "All" else _label(t))
        severity = c3.selectbox("Severity", ["All"] + SEVERITY_ORDER, key="dq_sev", on_change=_reset_page,
                                format_func=lambda s: "All" if s == "All" else s.capitalize())
        device = c4.selectbox("Device", ["All"] + devices, key="dq_device", on_change=_reset_page)
        search = c5.text_input("Search", key="dq_search", on_change=_reset_page,
                               placeholder="Issue ID or explanation text")

        page_number = st.session_state.get("quality_page", 1)
        client_side = family != "All families" and issue_type == "All"
        filters = dict(
            issue_type=None if issue_type == "All" else issue_type,
            severity=None if severity == "All" else severity,
            device_id=None if device == "All" else device,
            search=search.strip() or None,
        )
        try:
            if client_side:
                items = [i for i in api.quality_issues(**filters, page_size=500)["items"]
                         if _family_of(str(i.get("issue_type"))) == family]
                total = len(items)
                pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
                page_number = max(1, min(page_number, pages or 1))
                result = {"items": items[(page_number - 1) * PAGE_SIZE: page_number * PAGE_SIZE],
                          "total": total, "page": page_number, "pages": pages}
            else:
                result = api.quality_issues(**filters, page=page_number, page_size=PAGE_SIZE)
        except Exception as exc:
            api_error(exc, api_url)
            return
        if result["pages"] and page_number > result["pages"]:
            st.session_state["quality_page"] = result["pages"]
            st.rerun()

        theme.show(f'<div class="zx-muted" style="font-size:.74rem;margin:2px 0 6px">'
                   f'{result["total"]} finding(s) match the current filters</div>')
        if not result["items"]:
            theme.show(theme.empty("No findings match these filters."))
            return
        for issue in result["items"]:
            theme.show(_issue_card(issue))
            with st.expander(f"Supporting details · {issue['issue_id']}"):
                st.code(safe_json(issue.get("details", {})), language="json")
        if result["pages"] > 1:
            pagination("quality_page", result["page"], result["pages"], result["total"])


def _rejected_preview(issues_all: list[dict[str, Any]]) -> None:
    rows = [i for i in issues_all if "rejected_record_id" in (i.get("details") or {})]
    if not rows:
        return
    rows.sort(key=lambda i: i["details"].get("rejected_record_id") or 0)
    with st.container(key="zxg_rejected"):
        theme.show(theme.section(
            "Rejected records", f"{len(rows)} record(s) quarantined · raw input preserved, never guessed",
            theme.chip("not used as evidence", "critical")))
        table_rows = []
        for i in rows:
            errors = i["details"].get("errors") or []
            table_rows.append({
                "record": i["details"].get("rejected_record_id"),
                "event": i.get("event_id") or "(no event_id)",
                "type": _label(i.get("issue_type")),
                "reason": "; ".join(f"{e.get('field')}: {e.get('message')}" for e in errors) or _clean(i.get("explanation")),
                "received": i["details"].get("received_at"),
            })
        theme.show(theme.html_table(
            table_rows,
            [("record", "Raw #"), ("event", "Submitted ID"), ("type", "Check"), ("reason", "Why rejected"), ("received", "Received")],
            {
                "record": lambda v, r: f'<span class="zx-mono" style="color:#8B97B0">#{theme.esc(v)}</span>',
                "event": lambda v, r: theme.ev_link(v),
                "reason": lambda v, r: f'<span style="color:#DCE3F0">{theme.esc(v)}</span>',
                "received": lambda v, r: f'<span class="zx-mono" style="font-size:.72rem">{theme.esc(theme.fmt_dt(v))}</span>',
            },
        ))


# ------------------------------------------------------------------- page
def render(api: ZenithAPI, api_url: str) -> None:
    theme.page_header("Data", "trust", "What the agent refused to take at face value — every check, every finding, every handling decision.",
                      status=st.session_state.get("zx_status"))
    try:
        overview, summary, issues_all = _load(api_url)
    except Exception as exc:
        api_error(exc, api_url)
        st.stop()

    stats = overview.get("stats", {})
    counts = {row["issue_type"]: row["count"] for row in summary["by_type"]}
    sev_rank = {s: i for i, s in enumerate(reversed(SEVERITY_ORDER))}
    max_sev: dict[str, str] = {}
    for i in issues_all:
        t, s = i["issue_type"], theme.sev_of(i.get("severity"))
        if sev_rank.get(s, -1) > sev_rank.get(max_sev.get(t, ""), -1):
            max_sev[t] = s
    by_sev = {row["severity"]: row["count"] for row in summary["by_severity"]}

    chips = [{"label": f"{by_sev.get(s, 0)} {s}", "tone": t} for s, t in
             (("high", "warning"), ("medium", "info"), ("low", "ok")) if by_sev.get(s)]
    if by_sev.get("critical"):
        chips.insert(0, {"label": f"{by_sev['critical']} critical", "tone": "critical"})
    head_l, head_r = st.columns([1, 0.22], vertical_alignment="center")
    with head_l:
        theme.show(theme.agent_brief(
            f'{summary["total"]} findings across {sum(len(f["types"]) for f in FAMILIES.values())} deterministic checks. '
            f'{stats.get("rejected_events", 0)} records rejected with raw input kept; nothing was silently dropped or guessed.',
            chips, "ZENITH TRUST LEDGER · DETERMINISTIC CHECKS"))
    with head_r:
        if st.button("Re-run quality analysis", type="primary", use_container_width=True, key="dq_rerun"):
            try:
                result = api.analyze()
                st.success(f"Recomputed {result['quality_issues']} findings across "
                           f"{result['stats']['valid_events']} valid events.")
                st.cache_data.clear()
            except Exception as exc:
                api_error(exc, api_url)

    theme.show(theme.section("Ingest funnel", "how raw records became evidence"))
    theme.show(_funnel(stats))

    theme.show(theme.section("Issue families", "every check is listed, including those with zero findings"))
    fams = dict(FAMILIES)
    other = [t for t in counts if _family_of(t) == "Other"]
    if other:
        fams["Other"] = {"types": other, "c1": "#94A3B8", "c2": "#818CF8",
                         "did": "Recorded the finding with its explanation and suggested action for review."}
    cards = [_family_card(name, fam, counts, max_sev) for name, fam in fams.items()]
    cards.append(_coverage_card(fams, counts))
    for start in range(0, len(cards), 3):
        cols = st.columns(3, gap="small")
        for col, card in zip(cols, cards[start:start + 3]):
            with col:
                theme.show(card)
        theme.show('<div style="height:12px"></div>')

    present_types = [row["issue_type"] for row in summary["by_type"]]
    _explorer(api, api_url, issues_all, present_types)
    _rejected_preview(issues_all)
