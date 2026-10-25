"""Threat map (design option 2 look) and camera wall (design option 3 look).

Threat map: one SVG — dotted dark map, glowing zone nodes with level cards, animated
threat routes (from /situation "routes"), data-flow lines to the SOC node, campaign card
and legend. Routes are real: possible same-category patterns (labelled as inference),
location jumps from quality findings, and entities seen in several zones.

Camera wall: tiles for real camera devices from /cameras. A tile plays footage only when a
real source exists (a clip file in data/media or a stream in data/cameras.json); otherwise
it says "No live feed" — never synthetic video.
"""
from __future__ import annotations

import math
from typing import Any

from ui.theme import SEV, esc, fmt_time, humanize, sev_of

W, H = 1000.0, 560.0
LEVEL = {"critical": ("Critical", "#FF4D6D"), "high": ("High", "#FF4D6D"), "medium": ("Medium", "#FBBF24"),
         "low": ("Low", "#34D399"), None: ("Monitored", "#34D399")}
ROUTE_STYLE = {
    "pattern": ("#FF4D6D", "7 7", 2.2),
    "location_conflict": ("#E879F9", "2 6", 2.0),
    "entity": ("#F97316", "10 5", 2.4),
}


def _layout(zones: list[dict[str, Any]], pad_x: float = 120, pad_y: float = 80):
    pts = [z for z in zones if z.get("latitude") is not None and z.get("longitude") is not None]
    if not pts:
        return {}, {}
    lats = sorted(z["latitude"] for z in pts)
    lons = sorted(z["longitude"] for z in pts)
    mlat, mlon = lats[len(lats) // 2], lons[len(lons) // 2]
    kx = math.cos(math.radians(mlat))

    def dist(z: dict[str, Any]) -> float:
        return math.hypot((z["longitude"] - mlon) * kx, z["latitude"] - mlat)

    med = sorted(dist(z) for z in pts)[len(pts) // 2] or 1e-6
    on = [z for z in pts if dist(z) <= med * 5] or pts
    x0, x1 = min(z["longitude"] for z in on), max(z["longitude"] for z in on)
    y0, y1 = min(z["latitude"] for z in on), max(z["latitude"] for z in on)
    spanx, spany = max((x1 - x0) * kx, 1e-4), max(y1 - y0, 1e-4)
    sx, sy = (W - 2 * pad_x) / spanx, (H - 2 * pad_y - 40) / spany
    pos = {z["zone"]: (pad_x + (z["longitude"] - x0) * kx * sx, pad_y + (y1 - z["latitude"]) * sy) for z in on}
    # Far-away zones go to a fixed "off-map" slot (top-right) instead of a misleading edge position.
    off = {}
    for i, z in enumerate(z for z in pts if z["zone"] not in pos):
        off[z["zone"]] = (W - 175, 78 + i * 64, dist(z) * 111.3)
    return pos, off


def _node(x: float, y: float, z: dict[str, Any], off_km: float | None = None) -> str:
    sev = z.get("worst_severity")
    level, color = LEVEL.get(sev, LEVEL[None])
    hot = sev in ("critical", "high")
    icon = ('<path d="M0 -7 L7 6 L-7 6 Z" fill="none" stroke="#fff" stroke-width="1.8" stroke-linejoin="round"/>'
            '<line x1="0" y1="-2" x2="0" y2="2" stroke="#fff" stroke-width="1.8"/>') if hot else \
           ('<path d="M0 -7 L6 -4 V1 C6 5 3 7 0 8 C-3 7 -6 5 -6 1 V-4 Z" fill="none" stroke="#fff" stroke-width="1.6"/>')
    pulse = (f'<circle r="14" fill="none" stroke="{color}" stroke-width="2"><animate attributeName="r" values="14;34" dur="2s" repeatCount="indefinite"/>'
             f'<animate attributeName="opacity" values=".9;0" dur="2s" repeatCount="indefinite"/></circle>') if hot else ""
    name = esc(z.get("zone"))
    sub = f"{level} · {z.get('incidents', 0)} open" + (f" · off-map {off_km:.0f} km" if off_km else "")
    label_w = max(118, 7.4 * max(len(str(z.get('zone') or '')), len(sub) * 0.82))
    lx = 22 if x < W - label_w - 40 else -label_w - 22
    return (
        f'<g transform="translate({x:.1f},{y:.1f})">'
        f'<circle r="46" fill="url(#tm-glow-{sev or "none"})"/>{pulse}'
        f'<circle r="15" fill="{color}" fill-opacity=".9" style="filter:drop-shadow(0 0 8px {color})"/>'
        f'<circle r="19" fill="none" stroke="{color}" stroke-opacity=".6"/>{icon}'
        f'<g transform="translate({lx:.1f},-24)"><rect width="{label_w:.0f}" height="40" rx="7" fill="rgba(6,10,20,.88)" stroke="{color}" stroke-width="1.3"/>'
        f'<text x="10" y="17" font-family="Space Grotesk" font-size="13" font-weight="600" fill="#F1F5FB">{name}</text>'
        f'<text x="10" y="32" font-family="Inter" font-size="10.5" fill="{color}">{esc(sub)}</text></g></g>'
    )


def threat_map(zones: list[dict[str, Any]], routes: list[dict[str, Any]], campaign: dict[str, Any] | None,
               soc_label: str = "ZENITH SOC") -> str:
    pos, off = _layout(zones)
    if not pos:
        return ('<div class="zh-stage" style="height:220px;display:flex;align-items:center;justify-content:center;color:#8B97B0">'
                'No zone coordinates yet — ingest events with latitude/longitude to draw the threat map.</div>')
    soc = (W / 2, H - 70)
    where = {**pos, **{k: (v[0], v[1]) for k, v in off.items()}}
    by_zone = {z["zone"]: z for z in zones}

    def arc(a: tuple[float, float], b: tuple[float, float], bend: float = .22) -> str:
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        dx, dy = b[0] - a[0], b[1] - a[1]
        return f"M{a[0]:.1f} {a[1]:.1f} Q{mx - dy * bend:.1f} {my + dx * bend:.1f} {b[0]:.1f} {b[1]:.1f}"

    flows = "".join(
        f'<path class="zh-flow" d="{arc(p, soc, .12)}" fill="none" stroke="#FBBF24" stroke-opacity=".55" stroke-width="1.2" stroke-dasharray="3 7"/>'
        for p in pos.values())
    route_svg, labels = [], []
    seen = set()
    for i, r in enumerate(routes or []):
        a, b = where.get(r.get("from_zone")), where.get(r.get("to_zone"))
        if not a or not b or (r.get("from_zone"), r.get("to_zone"), r.get("kind")) in seen:
            continue
        seen.add((r.get("from_zone"), r.get("to_zone"), r.get("kind")))
        color, dash, width = ROUTE_STYLE.get(r.get("kind"), ROUTE_STYLE["pattern"])
        d = arc(a, b, .25 if i % 2 else -.25)
        route_svg.append(
            f'<path d="{d}" fill="none" stroke="{color}" stroke-opacity=".25" stroke-width="{width + 5}"/>'
            f'<path class="zh-flow" d="{d}" fill="none" stroke="{color}" stroke-width="{width}" stroke-dasharray="{dash}" '
            f'style="filter:drop-shadow(0 0 4px {color})"/>'
            f'<circle r="4" fill="#fff" style="filter:drop-shadow(0 0 6px {color})"><animateMotion dur="{2.4 + i % 3 * .6:.1f}s" repeatCount="indefinite" path="{d}"/></circle>')
        if r.get("kind") == "location_conflict":
            mx, my = a[0] + (b[0] - a[0]) * .62, a[1] + (b[1] - a[1]) * .62
            labels.append(f'<g transform="translate({mx:.0f},{my - 14:.0f})"><rect x="-62" y="-11" width="124" height="20" rx="10" fill="rgba(40,8,44,.9)" stroke="#E879F9"/>'
                          f'<text text-anchor="middle" y="3" font-family="Inter" font-size="10" fill="#F5D0FE">{esc(r.get("label"))[:26]}</text></g>')
    camp = ""
    if campaign and campaign.get("zones"):
        pts = [where[z] for z in campaign["zones"] if z in where]
        if pts:
            ccx = sum(p[0] for p in pts) / len(pts)
            ccy = sum(p[1] for p in pts) / len(pts)
            bx, by = W - 252, H - 74  # fixed slot, leader line to the pattern's centre
            camp = (f'<path d="M{bx:.0f} {by + 25:.0f} Q{(bx + ccx) / 2:.0f} {by + 25:.0f} {ccx:.0f} {ccy:.0f}" fill="none" stroke="#FF4D6D" '
                    f'stroke-opacity=".5" stroke-dasharray="2 5"/><circle cx="{ccx:.0f}" cy="{ccy:.0f}" r="4" fill="#FF4D6D"/>'
                    f'<g transform="translate({bx:.0f},{by:.0f})"><rect width="230" height="50" rx="8" fill="rgba(70,10,26,.92)" stroke="#FF4D6D" stroke-width="1.5" '
                    f'style="filter:drop-shadow(0 0 12px rgba(255,77,109,.7))"/>'
                    f'<path d="M18 16 L28 34 L8 34 Z" fill="none" stroke="#FF8FA6" stroke-width="2" stroke-linejoin="round"/>'
                    f'<text x="40" y="22" font-family="Space Grotesk" font-size="13.5" font-weight="700" fill="#FFB3C1">{esc(campaign.get("label"))}</text>'
                    f'<text x="40" y="39" font-family="Inter" font-size="11" fill="#F5C2CF">{len(campaign["zones"])} zones · {campaign.get("events", 0)} related events · inference</text></g>')
    nodes = "".join(_node(x, y, by_zone[zn]) for zn, (x, y) in pos.items())
    nodes += "".join(_node(x, y, by_zone[zn], km) for zn, (x, y, km) in off.items())
    soc_node = (f'<g transform="translate({soc[0]:.0f},{soc[1]:.0f})"><circle r="30" fill="url(#tm-glow-soc)"/>'
                f'<rect x="-13" y="-13" width="26" height="26" rx="6" transform="rotate(45)" fill="#0E1730" stroke="#67E8F9" stroke-width="1.6" '
                f'style="filter:drop-shadow(0 0 8px #22D3EE)"/><text text-anchor="middle" y="4" font-family="JetBrains Mono" font-size="9" fill="#A5F3FC">SOC</text>'
                f'<text text-anchor="middle" y="34" font-family="JetBrains Mono" font-size="9.5" letter-spacing="1.5" fill="#67E8F9">{esc(soc_label)}</text></g>')
    legend_items = [("#FF4D6D", "dot", "Active threat (critical/high)"), ("#FBBF24", "dot", "Suspicious activity (medium)"),
                    ("#34D399", "dot", "Monitored zone"), ("#FBBF24", "flow", "Data flow → SOC"),
                    ("#FF4D6D", "route", "Threat route (possible pattern)"), ("#E879F9", "jump", "Location jump")]
    leg = "".join(
        (f'<circle cx="14" cy="{18 + i * 17}" r="4.5" fill="{c}"/>' if kind == "dot" else
         f'<line x1="6" y1="{18 + i * 17}" x2="22" y2="{18 + i * 17}" stroke="{c}" stroke-width="2" stroke-dasharray="{ {"flow": "3 4", "route": "6 4", "jump": "2 4"}[kind] }"/>')
        + f'<text x="30" y="{22 + i * 17}" font-family="Inter" font-size="10.5" fill="#C7D0E0">{t}</text>'
        for i, (c, kind, t) in enumerate(legend_items))
    legend = (f'<g transform="translate(16,{H - 24 - len(legend_items) * 17 - 14:.0f})"><rect width="214" height="{len(legend_items) * 17 + 18}" rx="10" '
              f'fill="rgba(4,8,18,.82)" stroke="rgba(125,211,252,.25)"/>{leg}</g>')
    glows = "".join(
        f'<radialGradient id="tm-glow-{k}"><stop offset="0" stop-color="{c}" stop-opacity=".45"/><stop offset="1" stop-color="{c}" stop-opacity="0"/></radialGradient>'
        for k, c in (("critical", "#FF4D6D"), ("high", "#FF4D6D"), ("medium", "#FBBF24"), ("low", "#34D399"), ("none", "#34D399"), ("soc", "#22D3EE")))
    grid = "".join(f'<line x1="{x}" y1="0" x2="{x}" y2="{H}" stroke="rgba(103,232,249,.06)"/>' for x in range(0, int(W) + 1, 100)) + \
        "".join(f'<line x1="0" y1="{y}" x2="{W}" y2="{y}" stroke="rgba(103,232,249,.06)"/>' for y in range(0, int(H) + 1, 80))
    return (
        f'<div class="zh-stage" style="padding:0;perspective:none">'
        f'<svg viewBox="0 0 {W:.0f} {H:.0f}" width="100%" style="display:block">'
        f'<defs>{glows}<pattern id="tm-dots" width="9" height="9" patternUnits="userSpaceOnUse"><circle cx="1.5" cy="1.5" r="1.1" fill="rgba(103,232,249,.16)"/></pattern>'
        f'<radialGradient id="tm-vig" cx="50%" cy="45%" r="70%"><stop offset="0" stop-color="#0D2440" stop-opacity=".9"/><stop offset="1" stop-color="#03060C" stop-opacity="1"/></radialGradient></defs>'
        f'<rect width="{W:.0f}" height="{H:.0f}" fill="url(#tm-vig)"/><rect width="{W:.0f}" height="{H:.0f}" fill="url(#tm-dots)"/>{grid}'
        f'{flows}{"".join(route_svg)}{soc_node}{nodes}{"".join(labels)}{camp}{legend}'
        f'<text x="{W - 16}" y="22" text-anchor="end" font-family="JetBrains Mono" font-size="10" letter-spacing="1.5" fill="#67E8F9">'
        f'THREAT MAP · SITE SCHEMATIC · NORTH ↑</text>'
        f'<text x="16" y="22" font-family="JetBrains Mono" font-size="10" letter-spacing="1.5" fill="#67E8F9">{len(pos) + len(off)} ZONES · {len(seen)} ROUTES</text>'
        f'</svg></div>'
    )


# ------------------------------------------------------------------ camera wall
def camera_tile(cam: dict[str, Any], api_base: str) -> str:
    last = cam.get("last_event") or {}
    alert = bool(cam.get("alert"))
    sev = SEV[sev_of(last.get("severity"))]
    border = "#FF4D6D" if alert else "rgba(255,255,255,.10)"
    shadow = "0 0 26px -6px rgba(255,77,109,.9)" if alert else "none"
    media = ""
    stream = cam.get("stream") or {}
    if cam.get("media_url"):
        media = (f'<video src="{esc(api_base + cam["media_url"])}" autoplay muted loop playsinline '
                 f'style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover"></video>')
        live = ("#34D399", "Recorded clip")
    elif stream.get("url") and stream.get("kind") in ("mp4", "hls"):
        src = stream["url"] if not stream["url"].startswith("/") else api_base + stream["url"]
        media = (f'<video src="{esc(src)}" autoplay muted loop playsinline '
                 f'style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover"></video>')
        live = ("#34D399", stream.get("label") or "Live")
    elif stream.get("url") and stream.get("kind") in ("mjpeg", "image"):
        src = stream["url"] if not stream["url"].startswith("/") else api_base + stream["url"]
        media = f'<img src="{esc(src)}" alt="" style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover">'
        live = ("#34D399", stream.get("label") or "Live")
    else:
        media = ('<div style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px;'
                 'background:radial-gradient(circle at 50% 45%,rgba(56,189,248,.08),transparent 70%),'
                 'repeating-linear-gradient(0deg,rgba(255,255,255,.035) 0 1px,transparent 1px 3px),#05080F">'
                 '<svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="#4B5B75" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
                 '<path d="M3 7h12l4-3v14l-4-3H3zM2 2l20 20"/></svg>'
                 '<span class="zx-mono" style="font-size:10px;letter-spacing:.14em;color:#6B7A93">NO LIVE FEED</span></div>')
        live = None
    clip = (f'<span class="zx-mono" style="position:absolute;left:10px;top:30px;font-size:9px;color:#FCD34D;background:rgba(0,0,0,.6);'
            f'padding:2px 6px;border-radius:6px">▶ {esc(cam["clip"])}{"" if cam.get("media_url") else " · not ingested"}</span>') if cam.get("clip") else ""
    demo = ""
    if stream.get("label"):
        # Presentation footage must never read as evidence from this camera.
        demo = (f'<span class="zx-mono" style="position:absolute;right:8px;top:30px;font-size:9px;letter-spacing:.08em;color:#060814;'
                f'background:#FBBF24;padding:2px 7px;border-radius:6px;font-weight:700">{esc(stream["label"].upper())} · NOT EVIDENCE</span>')
    if alert:
        status = (f'<span class="zx-row" style="gap:6px;font-size:11px;font-weight:600;color:#FFE4EA;background:rgba(255,77,109,.85);'
                  f'padding:3px 9px;border-radius:999px"><span class="zx-live" style="width:7px;height:7px;border-radius:50%;background:#fff"></span>'
                  f'{esc(humanize(last.get("event_type")))}</span>')
    elif live:
        status = (f'<span class="zx-row" style="gap:6px;font-size:11px;color:#D1FAE5;background:rgba(6,40,30,.8);padding:3px 9px;border-radius:999px">'
                  f'<span style="width:7px;height:7px;border-radius:50%;background:{live[0]}"></span>{live[1]}</span>')
    else:
        status = (f'<span class="zx-row" style="gap:6px;font-size:11px;color:#B4BED3;background:rgba(10,14,26,.85);padding:3px 9px;border-radius:999px">'
                  f'<span style="width:7px;height:7px;border-radius:50%;background:{sev["c"]}"></span>Last: {esc(humanize(last.get("event_type")))}</span>')
    return (
        f'<div style="position:relative;aspect-ratio:16/10;border-radius:12px;overflow:hidden;border:1.5px solid {border};box-shadow:{shadow};margin-bottom:12px">'
        f'{media}'
        f'<div style="position:absolute;left:0;right:0;top:0;padding:7px 10px;display:flex;justify-content:space-between;gap:8px;'
        f'background:linear-gradient(180deg,rgba(0,0,0,.75),transparent)"><span style="font-size:12px;font-weight:700;color:#fff">{esc(cam.get("device_id"))}'
        f'<span style="font-weight:400;color:#C7D0E0"> &nbsp;{esc(cam.get("zone"))}</span></span>'
        f'<span class="zx-mono" style="font-size:11px;color:#E6EBF5">{esc(fmt_time(last.get("occurred_at"), True))}</span></div>'
        f'<div style="position:absolute;left:8px;bottom:8px">{status}</div>{clip}{demo}</div>'
    )
