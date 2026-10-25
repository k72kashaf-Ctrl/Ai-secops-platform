"""System page — health rings, ingestion runs and the admin audit log."""
from __future__ import annotations

from ui import theme
from ui.common import *  # noqa: F401,F403

_RUN_TONE = {"completed": "ok", "complete": "ok", "success": "ok", "partial": "warning", "failed": "critical", "running": "info"}


def _bytes(n: Any) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _duration(start: Any, end: Any) -> str:
    a, b = theme.parse_ts(start), theme.parse_ts(end)
    if not (a and b):
        return "—"
    secs = (b - a).total_seconds()
    return f"{secs * 1000:.0f} ms" if secs < 1 else f"{secs:.1f} s"


def _tile(ring_html: str, title: str, value: str, sub: str) -> str:
    return (
        f'<div style="display:flex;align-items:center;gap:14px;padding:6px 4px">{ring_html}'
        f'<div style="min-width:0"><div class="zx-eyebrow" style="color:#8B97B0">{theme.esc(title)}</div>'
        f'<div style="font-family:var(--zx-head);font-size:1.15rem;font-weight:600;margin:2px 0">{theme.esc(value)}</div>'
        f'<div class="zx-muted" style="font-size:.7rem;line-height:1.35">{sub}</div></div></div>'
    )


def _health_rings(health: dict[str, Any]) -> str:
    db = health.get("database") or {}
    analysis = health.get("analysis") or {}
    ok_api = health.get("status") == "ok"
    db_ok = bool(db.get("database_connected"))
    integrity = str(db.get("database_integrity") or "unknown")
    pending = bool(health.get("analysis_pending"))
    good, warn, bad = ("#34D399", "#22D3EE"), ("#FBBF24", "#FB7185"), ("#FF4D6D", "#FB7185")

    api_ring = theme.ring(1.0 if ok_api else 0.25, 70, 7, *(good if ok_api else bad), label="OK" if ok_api else "!", gid="zxs-api")
    db_ring = theme.ring(1.0 if db_ok and integrity == "ok" else (0.6 if db_ok else 0.15), 70, 7,
                         *(good if db_ok and integrity == "ok" else (warn if db_ok else bad)),
                         label="DB" if db_ok else "!", gid="zxs-db")
    an_ring = theme.ring(0.55 if pending else 1.0, 70, 7, *(warn if pending else ("#818CF8", "#E879F9")),
                         label="…" if pending else "✓", gid="zxs-an")
    ver_ring = theme.ring(1.0, 70, 7, "#22D3EE", "#818CF8", label=f"v{health.get('version', '?')}", gid="zxs-ver")

    last = analysis.get("last_completed_at")
    last_txt = f'last run {theme.esc(theme.fmt_time(last, True))}' if last else "no background run yet"
    dur = analysis.get("last_duration_seconds")
    if isinstance(dur, (int, float)):
        last_txt += f' · {dur:.2f}s'
    err = analysis.get("last_error")
    an_sub = f'{theme.esc(theme.humanize(analysis.get("mode") or "inline"))} mode · {analysis.get("runs", 0)} run(s) · {last_txt}'
    if err:
        an_sub += f' · <span style="color:#FF8FA6">error: {theme.esc(err)}</span>'

    tiles = [
        _tile(api_ring, "API", "Online" if ok_api else theme.humanize(health.get("status") or "degraded"),
              f'checked {theme.esc(theme.fmt_time(health.get("timestamp"), True))} UTC'),
        _tile(db_ring, "Database", "Connected" if db_ok else "Down",
              f'integrity {theme.esc(integrity)} · {theme.esc(_bytes(db.get("database_size_bytes")))}'),
        _tile(an_ring, "Analysis worker", "Pending" if pending else "Current", an_sub),
        _tile(ver_ring, "Version", f'{health.get("version", "unknown")}', theme.esc(health.get("app") or "Zenith")),
    ]
    return f'<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:10px">{"".join(tiles)}</div>'


def _stat_strip(stats: dict[str, Any]) -> str:
    items = [
        ("Valid events", "valid_events", "#22D3EE"), ("Incidents", "incidents", "#818CF8"),
        ("Open", "open_incidents", theme.SEV["critical"]["c"]), ("Quality flags", "quality_issues", theme.SEV["high"]["c"]),
        ("Rejected", "rejected_events", "#FB7185"), ("Duplicate content", "duplicate_content_events", "#A78BFA"),
        ("Unhealthy devices", "unhealthy_devices", theme.SEV["high"]["c"]),
    ]
    cells = "".join(
        f'<div style="padding:10px 12px;border-radius:14px;background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.06)">'
        f'<div class="zx-row" style="gap:6px"><span class="zx-dot" style="background:{c};box-shadow:0 0 8px {c}"></span>'
        f'<span class="zx-muted" style="font-size:.7rem">{theme.esc(label)}</span></div>'
        f'<div style="font-family:var(--zx-head);font-size:1.45rem;font-weight:600;margin-top:4px">{theme.esc(stats.get(key, 0))}</div></div>'
        for label, key, c in items)
    return f'<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px;margin-top:12px">{cells}</div>'


def _details_text(value: Any) -> str:
    if value in (None, "", "{}"):
        return "—"
    data = value
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except ValueError:
            return value[:120]
    if isinstance(data, dict):
        if not data:
            return "—"
        return " · ".join(f"{theme.humanize(k).lower()} {v}" for k, v in list(data.items())[:4])[:160]
    return str(data)[:160]


def _audit(api: ZenithAPI) -> None:
    try:
        items = api._request("GET", "/admin/audit", params={"limit": 50})[1].get("items", [])
    except ApiError as exc:
        if exc.status_code in (401, 403):
            theme.show(theme.empty("The audit log needs the admin key — set ZENITH_ADMIN_API_KEY for the dashboard."))
        else:
            st.warning(f"Audit log unavailable: {exc}")
        return
    except Exception as exc:
        st.warning(f"Audit log unavailable: {exc}")
        return
    if not items:
        theme.show(theme.empty("No audited actions yet. Resets, sample loads, recomputes and confirmed changes appear here."))
        return

    def action_chip(v: Any, r: dict[str, Any]) -> str:
        v = str(v or "")
        tone = "critical" if "reset" in v else ("warning" if "seed" in v else ("ok" if "confirm" in v or "update" in v else "info"))
        return theme.chip(v, tone)

    theme.show(theme.html_table(items, [
        ("created_at", "When"), ("action", "Action"), ("target", "Target"), ("actor", "Actor"), ("details", "Details"),
    ], {
        "created_at": lambda v, r: f'<span class="zx-mono" style="font-size:.72rem">{theme.esc(theme.fmt_dt(v))}</span>',
        "action": action_chip,
        "target": lambda v, r: theme.ev_link(v) if v else '<span class="zx-muted">—</span>',
        "actor": lambda v, r: f'<span class="zx-mono" style="font-size:.7rem;color:#B4BED3">{theme.esc(v)}</span>',
        "details": lambda v, r: f'<span class="zx-muted" style="font-size:.74rem">{theme.esc(_details_text(v))}</span>',
    }))


def render(api: ZenithAPI, api_url: str) -> None:
    theme.page_header("System", "Health", "API availability, database integrity, analysis worker, ingestion runs and audit trail.",
                      status=st.session_state.get("zx_status"))
    try:
        health_data = api.health()
    except Exception as exc:
        api_error(exc, api_url)
        st.stop()

    db_info = health_data.get("database", {})
    with st.container(key="zxg_sys_health"):
        head_l, head_r = st.columns([1, 0.2])
        head_l.html(theme.section("Platform health", "live from /health"))
        if head_r.button("Refresh", key="sys_refresh", type="primary", use_container_width=True):
            cached_health.clear()
            st.rerun()
        theme.show(_health_rings(health_data) + _stat_strip(health_data.get("statistics") or {}))
        pending = health_data.get("analysis_pending")
        note = ("Analysis pending — new events are stored; correlation and quality results are being recomputed in the background."
                if pending else "Analysis up to date — incidents and quality findings reflect all stored events.")
        theme.show(
            f'<div class="zx-row" style="margin-top:12px;flex-wrap:wrap;gap:8px">{theme.chip(note, "warning" if pending else "ok")}</div>'
            f'<div class="zx-muted" style="font-size:.7rem;margin-top:10px">Database <span class="zx-mono">{theme.esc(db_info.get("database_path"))}</span></div>'
        )
        l1, l2, _ = st.columns([0.3, 0.3, 0.4])
        l1.link_button("API docs (/docs)", f"{api_url}/docs", use_container_width=True)
        l2.link_button("Mobile view (/mobile)", f"{api_url}/mobile", use_container_width=True)

    runs_col, audit_col = st.columns([1, 1], gap="medium")
    with runs_col, st.container(key="zxg_sys_runs"):
        try:
            runs = api.overview().get("recent_ingestions") or []
        except Exception as exc:
            api_error(exc, api_url)
            runs = None
        if runs is not None:
            theme.show(theme.section("Ingestion runs", f"{len(runs)} recent batch(es)"))
            if not runs:
                theme.show(theme.empty("No ingestion runs yet."))
            else:
                for r in runs:
                    r["_dur"] = _duration(r.get("initiated_at"), r.get("completed_at"))
                theme.show(theme.html_table(runs, [
                    ("batch_id", "Batch"), ("initiated_at", "Started"), ("_dur", "Took"), ("total", "Total"),
                    ("accepted", "Accepted"), ("rejected", "Rejected"), ("duplicates", "Dup."), ("status", "Status"),
                ], {
                    "batch_id": lambda v, r: f'<span class="zx-mono" style="font-size:.7rem;white-space:nowrap">{theme.esc(str(v or "")[:22])}</span>',
                    "initiated_at": lambda v, r: f'<span class="zx-mono" style="font-size:.72rem;white-space:nowrap">{theme.esc(theme.fmt_dt(v))}</span>',
                    "_dur": lambda v, r: f'<span class="zx-mono zx-muted" style="font-size:.7rem">{theme.esc(v)}</span>',
                    "accepted": lambda v, r: f'<span style="color:#6EE7B7">{theme.esc(v)}</span>',
                    "rejected": lambda v, r: f'<span style="color:{"#FF8FA6" if v else "#8B97B0"}">{theme.esc(v)}</span>',
                    "status": lambda v, r: theme.chip(theme.humanize(v), _RUN_TONE.get(str(v), "neutral")),
                }))
    with audit_col, st.container(key="zxg_sys_audit"):
        theme.show(theme.section("Audit log", "admin actions · newest first"))
        _audit(api)
