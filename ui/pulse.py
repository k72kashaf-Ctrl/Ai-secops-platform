"""'Sensor Pulse' command-screen builders (design option 4): health rings,
telemetry line charts with anomaly markers, evidence tiles and the incident card.

All HTML/SVG, rendered through theme.show(). Values always come from the API.
"""
from __future__ import annotations

import math
from datetime import timedelta
from typing import Any

from ui.theme import SEV, esc, fmt_time, humanize, parse_ts, sev_of

LINE_COLORS = {
    "Cameras & ANPR": "#38BDF8", "Access control": "#A78BFA", "Perimeter & radar": "#34D399",
    "Alarms & reports": "#FB7185", "Telemetry": "#94A3B8", "Other": "#F59E0B",
    "Contradictions": "#FBBF24", "Record integrity": "#FF4D6D", "Timing": "#818CF8",
    "Corroboration": "#2DD4BF", "Devices": "#F472B6",
}

# Small line icons per source type (24x24 viewBox paths).
SOURCE_ICONS = {
    "cctv": "M3 7h12l4-3v14l-4-3H3zM7 11h.01", "anpr": "M3 7h12l4-3v14l-4-3H3zM7 11h.01",
    "face_recognition": "M12 4a4 4 0 1 1 0 8 4 4 0 0 1 0-8zM4 21c1.5-4 4.5-6 8-6s6.5 2 8 6",
    "access_control": "M6 3h12v18H6zM14 12h.01", "perimeter": "M3 20V8M9 20V8M15 20V8M21 20V8M3 11h18M3 16h18",
    "radar": "M12 12l7-7M12 3a9 9 0 1 0 9 9M12 7a5 5 0 1 0 5 5", "emergency_alarm": "M12 3l9 16H3zM12 10v4M12 17h.01",
    "operator_report": "M12 4a4 4 0 1 1 0 8 4 4 0 0 1 0-8zM4 21c1.5-4 4.5-6 8-6s6.5 2 8 6",
    "gps": "M12 21s-7-6.5-7-12a7 7 0 0 1 14 0c0 5.5-7 12-7 12zM12 9h.01", "weather": "M7 18a5 5 0 1 1 1-9.9A6 6 0 0 1 19 10a4 4 0 0 1-1 8z",
    "network_log": "M4 5h16v6H4zM4 13h16v6H4zM8 8h.01M8 16h.01", "device_health": "M3 12h4l3-8 4 16 3-8h4",
    "red_light": "M9 3h6v18H9zM12 7h.01M12 12h.01M12 17h.01", "seatbelt_detection": "M5 4l14 16M12 4a3 3 0 1 1 0 6",
    "mobile_phone_detection": "M8 3h8v18H8zM12 18h.01",
}
DEFAULT_ICON = "M12 3l9 16H3z"


def _icon(path: str, size: int = 18, color: str = "currentColor", stroke: float = 1.8) -> str:
    return (f'<svg aria-hidden="true" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round"><path d="{path}"/></svg>')


def mini_spark(values: list[float], color: str, width: int = 120, height: int = 26) -> str:
    vals = [float(v or 0) for v in values] or [0.0, 0.0]
    if len(vals) == 1:
        vals *= 2
    top = max(vals) or 1.0
    pts = " ".join(f"{i * width / (len(vals) - 1):.1f},{height - 2 - v / top * (height - 6):.1f}" for i, v in enumerate(vals))
    return (f'<svg aria-hidden="true" width="100%" height="{height}" viewBox="0 0 {width} {height}" preserveAspectRatio="none">'
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.6" vector-effect="non-scaling-stroke" '
            f'style="filter:drop-shadow(0 0 3px {color})"/></svg>')


def health_card(group: str, ok: int, total: int, series: list[float], gid: str) -> str:
    """Big health ring (devices with no flag) + device count + activity sparkline, like the design's sensor tiles."""
    frac = ok / total if total else 0.0
    c1, c2 = ("#34D399", "#22D3EE") if frac >= 0.95 else (("#FBBF24", "#F59E0B") if frac >= 0.7 else ("#FF4D6D", "#FB7185"))
    size, stroke = 96, 9
    r = (size - stroke) / 2 - 1
    circ = 2 * math.pi * r
    return (
        f'<div class="zx-glass zx-hud" style="padding:14px 12px 10px;display:flex;flex-direction:column;align-items:center;gap:6px;margin:0">'
        f'<span style="font-size:.82rem;font-weight:600;color:#DCE3F0">{esc(group)}</span>'
        f'<div style="position:relative;width:{size}px;height:{size}px">'
        f'<svg aria-hidden="true" width="{size}" height="{size}" viewBox="0 0 {size} {size}" style="filter:drop-shadow(0 0 10px {c1})">'
        f'<defs><linearGradient id="{gid}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{c1}"/><stop offset="1" stop-color="{c2}"/></linearGradient></defs>'
        f'<circle cx="{size / 2}" cy="{size / 2}" r="{r:.1f}" fill="none" stroke="rgba(255,255,255,.07)" stroke-width="{stroke}"/>'
        f'<circle cx="{size / 2}" cy="{size / 2}" r="{r:.1f}" fill="none" stroke="url(#{gid})" stroke-width="{stroke}" stroke-linecap="round" '
        f'stroke-dasharray="{circ * frac:.1f} {circ:.1f}" transform="rotate(-90 {size / 2} {size / 2})"/></svg>'
        f'<span style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-family:var(--zx-head);'
        f'font-size:22px;font-weight:700;color:#fff;text-shadow:0 0 12px {c1}">{round(frac * 100)}%</span></div>'
        f'<span class="zx-mono" style="font-size:.72rem;color:{c1}">{ok} / {total} clear</span>'
        f'<div style="width:100%">{mini_spark(series, c1)}</div></div>'
    )


def line_chart(title: str, sub: str, series: dict[str, list[float]], labels: list[str],
               anomalies: list[tuple[int, str]] | None = None, height: int = 190, gid: str = "lc") -> str:
    """Multi-line telemetry chart with glow, area fill, gridlines and anomaly markers (index, label)."""
    W, H, L, R, T, B = 600.0, float(height), 34.0, 10.0, 14.0, 26.0
    n = max((len(v) for v in series.values()), default=0)
    if n < 2:
        return (f'<div class="zx-glass" style="margin:0"><div class="zx-h">{esc(title)}</div>'
                f'<div class="zx-muted" style="padding:24px 0;text-align:center">Not enough data yet.</div></div>')
    top = max((max(v) for v in series.values() if v), default=1) or 1
    nice = next(x for x in (1, 2, 4, 5, 10, 15, 20, 25, 50, 100, 200, 500, 1000, 5000, 10**6) if x >= top)
    sx = lambda i: L + i * (W - L - R) / (n - 1)  # noqa: E731
    sy = lambda v: T + (1 - v / nice) * (H - T - B)  # noqa: E731
    grid = "".join(
        f'<line x1="{L}" y1="{sy(nice * f):.1f}" x2="{W - R}" y2="{sy(nice * f):.1f}" stroke="rgba(255,255,255,{.10 if f == 0 else .05})"/>'
        f'<text x="{L - 6}" y="{sy(nice * f) + 3:.1f}" text-anchor="end" font-size="9" fill="#5F6B85" font-family="JetBrains Mono">{nice * f:g}</text>'
        for f in (0, .5, 1))
    xl = "".join(
        f'<text x="{sx(i):.1f}" y="{H - 8}" text-anchor="middle" font-size="9" fill="#5F6B85" font-family="JetBrains Mono">{esc(labels[i])}</text>'
        for i in sorted({0, n // 4, n // 2, 3 * n // 4, n - 1}) if i < len(labels))
    defs, paths = [], []
    for k, (name, vals) in enumerate(series.items()):
        if not any(vals):
            continue
        color = LINE_COLORS.get(name, "#7DD3FC")
        pts = [(sx(i), sy(v)) for i, v in enumerate(vals)]
        d = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts)
        defs.append(f'<linearGradient id="{gid}{k}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{color}" stop-opacity=".28"/>'
                    f'<stop offset="1" stop-color="{color}" stop-opacity="0"/></linearGradient>')
        paths.append(f'<path d="{d} L{pts[-1][0]:.1f} {sy(0):.1f} L{pts[0][0]:.1f} {sy(0):.1f} Z" fill="url(#{gid}{k})"/>'
                     f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.8" stroke-linejoin="round" '
                     f'style="filter:drop-shadow(0 0 4px {color})"/>')
    marks = ""
    for idx, text in anomalies or []:
        if not 0 <= idx < n:
            continue
        peak = max((vals[idx] for vals in series.values() if idx < len(vals)), default=0)
        x, y = sx(idx), sy(peak)
        flip = x > W * 0.7
        marks += (f'<line x1="{x:.1f}" y1="{T}" x2="{x:.1f}" y2="{sy(0):.1f}" stroke="#FF4D6D" stroke-dasharray="3 4" stroke-opacity=".7"/>'
                  f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="#FF4D6D" style="filter:drop-shadow(0 0 6px #FF4D6D)"/>'
                  f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="none" stroke="#FF4D6D"><animate attributeName="r" values="5;14" dur="1.6s" repeatCount="indefinite"/>'
                  f'<animate attributeName="opacity" values="1;0" dur="1.6s" repeatCount="indefinite"/></circle>'
                  f'<g transform="translate({x + (-138 if flip else 10):.1f},{max(T, y - 30):.1f})"><rect width="128" height="30" rx="6" fill="rgba(60,10,24,.92)" stroke="#FF4D6D"/>'
                  f'<text x="8" y="12" font-size="9.5" fill="#FFB3C1" font-family="Inter" font-weight="600">Anomaly detected</text>'
                  f'<text x="8" y="24" font-size="9" fill="#F5C2CF" font-family="JetBrains Mono">{esc(text)}</text></g>')
    legend = "".join(
        f'<span style="display:inline-flex;align-items:center;gap:5px;font-size:.68rem;color:#C7D0E0">'
        f'<span style="width:8px;height:8px;border-radius:50%;background:{LINE_COLORS.get(name, "#7DD3FC")};'
        f'box-shadow:0 0 6px {LINE_COLORS.get(name, "#7DD3FC")}"></span>{esc(name)}</span>'
        for name, vals in series.items() if any(vals))
    return (
        f'<div class="zx-glass zx-hud" style="margin:0;padding:14px 16px 8px">'
        f'<div class="zx-row" style="justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:6px">'
        f'<div><div class="zx-h" style="font-size:.95rem">{esc(title)}</div><div class="zx-muted" style="font-size:.7rem">{esc(sub)}</div></div>'
        f'<div style="display:flex;gap:10px;flex-wrap:wrap">{legend}</div></div>'
        f'<svg viewBox="0 0 {W:.0f} {H:.0f}" width="100%" height="{H:.0f}" preserveAspectRatio="none" style="display:block;margin-top:6px">'
        f'<defs>{"".join(defs)}</defs>{grid}{"".join(paths)}{marks}{xl}</svg></div>'
    )


def flagged_panel(sensors: list[dict[str, Any]]) -> str:
    """'Flagged devices' panel (stands in for the design's dispatch units): which devices the agent flagged."""
    rows = []
    for s in sensors:
        for dev in (s.get("flagged_devices") or [])[:3]:
            rows.append((s["group"], dev))
    total = sum(s.get("flagged", 0) for s in sensors)
    body = "".join(
        f'<div style="display:grid;grid-template-columns:22px 1fr auto;gap:8px;align-items:center;padding:6px 0;border-bottom:1px solid rgba(255,255,255,.05)">'
        f'{_icon(DEFAULT_ICON if "Telemetry" not in g else SOURCE_ICONS["device_health"], 16, LINE_COLORS.get(g, "#7DD3FC"))}'
        f'<span class="zx-mono" style="font-size:.72rem;color:#E6EBF5;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{esc(d)}</span>'
        f'<span style="font-size:.66rem;color:{LINE_COLORS.get(g, "#7DD3FC")}">{esc(g.split(" ")[0])}</span></div>' for g, d in rows[:5])
    more = f'<div class="zx-muted" style="font-size:.66rem;margin-top:6px">+{total - min(total, 5)} more on the Data trust page</div>' if total > 5 else ""
    return (f'<div class="zx-glass zx-hud" style="margin:0;padding:14px 14px 10px;height:100%">'
            f'<div class="zx-row" style="justify-content:space-between;flex-wrap:wrap;gap:4px"><span style="font-size:.82rem;font-weight:600;white-space:nowrap">Flagged devices</span>'
            f'<span class="zx-mono" style="font-size:.7rem;color:#FBBF24">{total} flagged</span></div>'
            f'<div style="margin-top:6px">{body or "<div class=zx-muted>No device flagged.</div>"}</div>{more}</div>')


def evidence_tile(ev: dict[str, Any], active: bool = False, big: bool = False) -> str:
    """Stand-in for a camera thumbnail: the sensor that produced the evidence, styled like a feed tile."""
    sev = SEV[sev_of(ev.get("severity"))]
    icon = SOURCE_ICONS.get(str(ev.get("source_type") or ""), DEFAULT_ICON)
    h = 104 if big else 74
    border = "#22D3EE" if active else "rgba(255,255,255,.10)"
    payload = ev.get("payload") or {}
    clip = payload.get("clip") if isinstance(payload, dict) else None
    return (
        f'<div style="border-radius:10px;overflow:hidden;border:1.5px solid {border};background:#060B14;'
        f'box-shadow:{"0 0 18px -4px rgba(34,211,238,.8)" if active else "none"}">'
        f'<div style="position:relative;height:{h}px;display:flex;align-items:center;justify-content:center;'
        f'background:radial-gradient(circle at 50% 40%,{sev["halo"]},transparent 70%),repeating-linear-gradient(0deg,rgba(255,255,255,.03) 0 1px,transparent 1px 3px),#08101C">'
        f'{_icon(icon, 30 if big else 24, sev["light"], 1.6)}'
        f'<span class="zx-mono" style="position:absolute;left:6px;top:5px;font-size:9px;color:#9FB3C8">{esc(ev.get("device_id"))}</span>'
        f'<span class="zx-mono" style="position:absolute;right:6px;top:5px;font-size:9px;color:{sev["c"]}">● {esc(fmt_time(ev.get("occurred_at"), True))}</span>'
        + (f'<span class="zx-mono" style="position:absolute;left:6px;bottom:5px;font-size:8.5px;color:#FCD34D">▶ {esc(clip)} · not ingested</span>' if clip else "")
        + f'</div><div style="padding:5px 7px"><div style="font-size:.7rem;font-weight:600;color:#E6EBF5;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">'
        f'{esc(humanize(ev.get("event_type")))}</div><div class="zx-mono" style="font-size:.6rem;color:#8B97B0">{esc(ev.get("event_id"))} · '
        f'conf {float(ev.get("confidence") or 0):.2f}</div></div></div>'
    )


def minutes_between(a: Any, b: Any) -> int | None:
    ta, tb = parse_ts(a), parse_ts(b)
    if not ta or not tb:
        return None
    return max(0, round((tb - ta).total_seconds() / 60))


def incident_head(inc: dict[str, Any], latest: Any) -> str:
    """Top of the design's 'Active incidents' card: severity chip, id, age, title, summary."""
    sev = sev_of(inc.get("severity"))
    c = SEV[sev]
    first = (inc.get("timeline") or [{}])[0].get("occurred_at") or inc.get("created_at")
    age = minutes_between(first, latest)
    return (
        f'<div style="border-radius:14px;padding:12px 14px;background:linear-gradient(160deg,{c["halo"]},rgba(10,12,24,.6) 60%);'
        f'border:1px solid {c["c"]}66;box-shadow:inset 0 0 30px {c["halo"]}">'
        f'<div class="zx-row" style="justify-content:space-between"><span class="zx-row" style="gap:8px">'
        f'<span class="zx-mono" style="font-size:.62rem;font-weight:700;letter-spacing:.08em;color:#fff;background:{c["c"]};border-radius:6px;padding:2px 8px;'
        f'box-shadow:0 0 12px {c["glow"]}">● {esc(sev.upper())}</span><span class="zx-mono" title="{esc(inc.get("incident_id"))}" style="font-size:.7rem;color:#C7D0E0;white-space:nowrap">{esc(str(inc.get("incident_id") or "")[:10])}</span></span>'
        f'<span class="zx-mono" style="font-size:.68rem;color:#9FB3C8;white-space:nowrap">{f"{age} min old · data time" if age is not None else ""}</span></div>'
        f'<div style="font-family:var(--zx-head);font-size:1.25rem;font-weight:600;margin:8px 0 2px;color:#fff">{esc(inc.get("title"))}</div>'
        f'<div style="font-size:.78rem;color:#C7D0E0">{esc(" · ".join(humanize(s) for s in inc.get("sources") or []))} · '
        f'{esc(", ".join(inc.get("locations") or []))}</div></div>'
    )


def conf_block(conf: float, status: str, first: Any, latest: Any) -> str:
    level = "High" if conf >= 0.8 else ("Medium" if conf >= 0.5 else "Low")
    color = "#34D399" if conf >= 0.8 else ("#FBBF24" if conf >= 0.5 else "#FF4D6D")
    size, stroke = 78, 7
    r = (size - stroke) / 2 - 1
    circ = 2 * math.pi * r
    elapsed = minutes_between(first, latest)
    return (
        f'<div style="display:grid;grid-template-columns:1fr;gap:8px">'
        f'<div style="border-radius:12px;padding:10px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.07);display:flex;gap:10px;align-items:center">'
        f'<div style="position:relative;width:{size}px;height:{size}px;flex:0 0 {size}px"><svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" '
        f'style="filter:drop-shadow(0 0 8px {color})"><circle cx="{size / 2}" cy="{size / 2}" r="{r:.1f}" fill="none" stroke="rgba(255,255,255,.07)" stroke-width="{stroke}"/>'
        f'<circle cx="{size / 2}" cy="{size / 2}" r="{r:.1f}" fill="none" stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" '
        f'stroke-dasharray="{circ * conf:.1f} {circ:.1f}" transform="rotate(-90 {size / 2} {size / 2})"/></svg>'
        f'<span style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-family:var(--zx-head);font-size:18px;font-weight:700">'
        f'{round(conf * 100)}%</span></div><div><div class="zx-muted" style="font-size:.7rem">Confidence</div>'
        f'<div style="font-family:var(--zx-head);font-size:1.05rem;font-weight:600;color:{color}">{level}</div></div></div>'
        f'<div style="border-radius:12px;padding:10px 12px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.07);display:grid;grid-template-columns:auto 1fr;gap:4px 12px;align-items:center">'
        f'<div class="zx-muted" style="font-size:.7rem">Status</div><div><span class="zx-chip" style="color:#7DD3FC;background:rgba(56,189,248,.12);'
        f'border:1px solid rgba(56,189,248,.4);height:22px">{esc(humanize(status))}</span></div>'
        f'<div class="zx-muted" style="font-size:.7rem">Elapsed</div><div style="font-family:var(--zx-head);font-size:1rem;font-weight:600">'
        f'{elapsed if elapsed is not None else "—"} min <span class="zx-mono" style="font-size:.62rem;color:#8B97B0;font-weight:400">since {esc(fmt_time(first, True))} UTC</span></div></div></div>'
    )


def mode_progress(index: int, total: int) -> str:
    pct = (index + 1) / total * 100
    return (f'<div style="display:flex;align-items:center;gap:10px;margin:-4px 0 12px">'
            f'<div style="flex:1;height:3px;border-radius:3px;background:rgba(255,255,255,.06);overflow:hidden">'
            f'<div style="width:{pct:.0f}%;height:3px;background:linear-gradient(90deg,#22D3EE,#818CF8);box-shadow:0 0 10px #22D3EE"></div></div>'
            f'<span class="zx-mono" style="font-size:.66rem;color:#7DD3FC">VIEW {index + 1} / {total}</span></div>')


def hour_labels(buckets: list[dict[str, Any]]) -> list[str]:
    return [fmt_time(b.get("start")) for b in buckets]


def spike_indices(buckets: list[dict[str, Any]], feed: list[dict[str, Any]]) -> list[tuple[int, str]]:
    """Map the agent's flagged spikes (feed kind='spike') onto chart bucket indexes."""
    starts = [parse_ts(b.get("start")) for b in buckets]
    out = []
    for item in feed:
        if item.get("kind") != "spike":
            continue
        t = parse_ts(item.get("at"))
        if not t:
            continue
        idx = max((i for i, s in enumerate(starts) if s and s <= t), default=None)
        # Only mark spikes that fall inside the charted window (never a wall-clock fallback).
        if idx is not None and t < starts[idx] + timedelta(minutes=15):
            out.append((idx, f"{fmt_time(item.get('at'))} · flagged spike"))
    return out

