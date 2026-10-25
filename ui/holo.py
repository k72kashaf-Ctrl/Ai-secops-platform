"""Futuristic HUD components: 3D holographic site map, threat-posture core, agent ticker.

Pure HTML/CSS/SVG (CSS 3D transforms + SVG SMIL), no JavaScript, so it renders
through st.markdown. Every animation is disabled under prefers-reduced-motion.
"""
from __future__ import annotations

import math
from typing import Any

from ui.theme import SEV, esc, fmt_time, sev_of

TILT, SPIN = 56, -8  # plane rotation (deg); standing elements counter-rotate to face the viewer

CSS = """
<style>
@keyframes zh-ripple{0%{transform:translate(-50%,-50%) scale(.35);opacity:.9}100%{transform:translate(-50%,-50%) scale(1.9);opacity:0}}
@keyframes zh-spin{to{transform:translate(-50%,-50%) rotate(360deg)}}
@keyframes zh-spin-r{to{transform:translate(-50%,-50%) rotate(-360deg)}}
@keyframes zh-gyro{to{transform:rotateY(360deg)}}
@keyframes zh-gyro2{to{transform:rotateX(70deg) rotateY(360deg)}}
@keyframes zh-scan{0%{transform:translateY(-100%)}100%{transform:translateY(100%)}}
@keyframes zh-beam{0%,100%{opacity:.85}50%{opacity:1}}
@keyframes zh-float{0%,100%{transform:translate(-50%,0)}50%{transform:translate(-50%,-6px)}}
@keyframes zh-marquee{from{transform:translateX(0)}to{transform:translateX(-50%)}}
@keyframes zh-flicker{0%,100%{opacity:1}92%{opacity:1}93%{opacity:.55}94%{opacity:1}}
@keyframes zh-dash{to{stroke-dashoffset:-60}}
.zh-stage{position:relative;border-radius:20px;overflow:hidden;perspective:1300px;perspective-origin:50% 10%;
 background:radial-gradient(ellipse 80% 70% at 50% 60%,#0B1E34 0%,#06101D 45%,#03060C 100%);border:1px solid rgba(125,211,252,.18);
 box-shadow:inset 0 0 120px rgba(0,0,0,.85),0 30px 80px -40px rgba(34,211,238,.45)}
.zh-plane{position:absolute;left:50%;top:57%;width:86%;transform-style:preserve-3d;
 background:radial-gradient(ellipse 50% 50% at 50% 50%,rgba(34,211,238,.13),transparent 70%);border-radius:28px;
 box-shadow:0 0 0 1px rgba(103,232,249,.25),0 0 60px rgba(34,211,238,.18),inset 0 0 80px rgba(34,211,238,.10)}
.zh-pin{position:absolute;width:0;height:0;transform-style:preserve-3d}
.zh-ripple{position:absolute;left:0;top:0;border-radius:50%;animation:zh-ripple 2.6s ease-out infinite}
.zh-up{position:absolute;left:0;top:0;width:0;height:0;transform-style:preserve-3d;transform-origin:0 0}
.zh-beam{position:absolute;left:-3px;width:6px;border-radius:3px;animation:zh-beam 2.4s ease-in-out infinite}
.zh-tag{position:absolute;left:0;transform:translate(-50%,0);white-space:nowrap;animation:zh-float 4s ease-in-out infinite}
.zh-gyro{position:absolute;border-radius:50%;animation:zh-gyro 7s linear infinite}
.zh-gyro2{position:absolute;border-radius:50%;animation:zh-gyro2 9s linear infinite}
.zh-sweep{position:absolute;border-radius:50%;background:conic-gradient(from 0deg,rgba(34,211,238,.28),rgba(34,211,238,0) 60deg,transparent);animation:zh-spin 7s linear infinite}
.zh-scanlines{position:absolute;inset:0;pointer-events:none;background:repeating-linear-gradient(0deg,rgba(255,255,255,.025) 0 1px,transparent 1px 3px)}
.zh-scanbar{position:absolute;left:0;right:0;height:35%;pointer-events:none;background:linear-gradient(180deg,transparent,rgba(34,211,238,.07),transparent);animation:zh-scan 6s linear infinite}
.zh-bracket{position:absolute;width:26px;height:26px;border-color:rgba(103,232,249,.75);border-style:solid;pointer-events:none}
.zh-hudtxt{position:absolute;font-family:var(--zx-mono);font-size:10px;letter-spacing:.14em;color:#67E8F9;text-shadow:0 0 8px rgba(34,211,238,.7)}
.zh-flicker{animation:zh-flicker 5s linear infinite}
.zh-flow{stroke-dasharray:4 10;animation:zh-dash 2.2s linear infinite}
.zh-ring{position:absolute;left:50%;top:50%;border-radius:50%}
.zh-ring.a{animation:zh-spin 18s linear infinite}
.zh-ring.b{animation:zh-spin-r 11s linear infinite}
.zh-ring.c{animation:zh-spin 6s linear infinite}
.zh-ticker{position:relative;overflow:hidden;height:38px;border-radius:12px;border:1px solid rgba(125,211,252,.18);background:linear-gradient(90deg,rgba(8,16,30,.9),rgba(14,12,34,.9));
 -webkit-mask-image:linear-gradient(90deg,#000 0,#000 94%,transparent);mask-image:linear-gradient(90deg,#000 0,#000 94%,transparent);margin-bottom:14px}
.zh-track{position:absolute;top:0;left:0;display:inline-flex;align-items:center;height:38px;gap:42px;white-space:nowrap;animation:zh-marquee 70s linear infinite;padding-left:20px}
.zh-ticker:hover .zh-track{animation-play-state:paused}
.zx-hud{position:relative}
.zx-hud::before,.zx-hud::after{content:"";position:absolute;width:16px;height:16px;border-color:rgba(103,232,249,.7);border-style:solid;pointer-events:none;z-index:2}
.zx-hud::before{left:8px;top:8px;border-width:2px 0 0 2px;border-radius:6px 0 0 0}
.zx-hud::after{right:8px;bottom:8px;border-width:0 2px 2px 0;border-radius:0 0 6px 0}
@media (prefers-reduced-motion:reduce){.zh-ripple,.zh-beam,.zh-tag,.zh-gyro,.zh-gyro2,.zh-sweep,.zh-scanbar,.zh-flicker,.zh-flow,.zh-ring.a,.zh-ring.b,.zh-ring.c,.zh-track{animation:none!important}}
</style>
"""


def _project(zones: list[dict[str, Any]], w: float, h: float, pad: float, top: float, bottom: float):
    pts = [z for z in zones if z.get("latitude") is not None and z.get("longitude") is not None]
    if not pts:
        return [], [], None
    lats = sorted(z["latitude"] for z in pts)
    lons = sorted(z["longitude"] for z in pts)
    mlat, mlon = lats[len(lats) // 2], lons[len(lons) // 2]
    kx = math.cos(math.radians(mlat))

    def dist(z: dict[str, Any]) -> float:
        return math.hypot((z["longitude"] - mlon) * kx, z["latitude"] - mlat)

    ds = sorted(dist(z) for z in pts)
    med = ds[len(ds) // 2] or 1e-6
    on = [z for z in pts if dist(z) <= med * 5] or pts
    off = [(z, dist(z) * 111.3) for z in pts if z not in on]
    x0, x1 = min(z["longitude"] for z in on), max(z["longitude"] for z in on)
    y0, y1 = min(z["latitude"] for z in on), max(z["latitude"] for z in on)
    spanx, spany = max((x1 - x0) * kx, 1e-4), max(y1 - y0, 1e-4)
    # Schematic: fill the plane on both axes (not to scale), keep orientation (north up).
    sx, sy = (w - 2 * pad) / spanx, (h - top - bottom) / spany

    def xy(z: dict[str, Any]) -> tuple[float, float]:
        return pad + (z["longitude"] - x0) * kx * sx, top + (y1 - z["latitude"]) * sy

    return [(z, *xy(z)) for z in on], off, (spany * 111.3, spanx * 111.3)


def holo_map(zones: list[dict[str, Any]], selected: str | None = None, hot_only: bool = False,
             callouts: list[dict[str, Any]] | None = None, height: int = 540, analysed: int | None = None) -> str:
    """3D holographic site picture: zones are light pillars on a tilted hex-grid plane."""
    W, H = 1000.0, 640.0
    placed, off, span = _project(zones, W, H, 90, 70, 170)
    if not placed:
        return ('<div class="zh-stage" style="height:220px;display:flex;align-items:center;justify-content:center;color:#8B97B0">'
                'No zone coordinates yet — ingest events with latitude/longitude to light up the holo-map.</div>')
    cx, cy = W / 2, H - 55  # analysis core sits on the near edge; streams flow toward the viewer
    max_events = max(z.get("events", 0) for z, _, _ in placed) or 1
    callout_by_zone = {c.get("zone"): c for c in (callouts or [])}
    counter = f"rotateZ({-SPIN}deg) rotateX({-TILT}deg)"

    flows, packets, pins = [], [], []
    for i, (z, x, y) in enumerate(placed):
        sev = sev_of(z.get("worst_severity") or "unknown")
        c = SEV[sev]
        mx, my = (x + cx) / 2 + (y - cy) * 0.18, (y + cy) / 2 - (x - cx) * 0.18
        path = f"M{x:.0f} {y:.0f} Q{mx:.0f} {my:.0f} {cx:.0f} {cy:.0f}"
        flows.append(f'<path d="{path}" stroke="{c["c"]}" stroke-opacity=".22" stroke-width="5" fill="none"/>'
                     f'<path class="zh-flow" d="{path}" stroke="{c["c"]}" stroke-opacity=".85" stroke-width="1.6" fill="none"/>')
        dur = 2.2 + (i % 4) * 0.55
        packets.append(f'<circle r="4.5" fill="{c["light"]}" style="filter:drop-shadow(0 0 6px {c["c"]})">'
                       f'<animateMotion dur="{dur:.2f}s" repeatCount="indefinite" path="{path}"/></circle>')
        if hot_only and sev not in ("critical", "high"):
            continue
        events = z.get("events", 0)
        beam_h = round(40 + 120 * math.sqrt(events / max_events))
        ring_d = round(54 + 10 * min(z.get("incidents", 0), 10))
        is_sel = z.get("zone") == selected
        tag = ""
        co = callout_by_zone.get(z.get("zone"))
        if co:
            cc = SEV[sev_of(co.get("severity"))]
            side = -118 if x < cx else 118  # card beside the beam, away from the centre line
            tag = (f'<div class="zh-tag" style="top:{-beam_h - 112}px;left:{side}px;padding:8px 12px;border-radius:12px;min-width:190px;'
                   f'background:linear-gradient(135deg,rgba(70,12,30,.92),rgba(18,6,22,.92));border:1px solid {cc["c"]};'
                   f'box-shadow:0 0 26px -4px {cc["glow"]},inset 0 0 18px rgba(255,77,109,.15)">'
                   f'<div style="font-family:var(--zx-mono);font-size:10px;color:{cc["light"]};display:flex;justify-content:space-between;gap:12px">'
                   f'<span>▲ {esc(str(co.get("severity", "")).upper())} · {esc(fmt_time(co.get("at")))}</span><span>{esc(co.get("conf"))}</span></div>'
                   f'<div style="font-family:var(--zx-head);font-size:14px;font-weight:600;color:#fff">{esc(co.get("title"))}</div>'
                   f'<div style="font-size:10.5px;color:#F5C2CF">{esc(co.get("sub"))}</div></div>')
        label_top = -beam_h - 34
        sel_ring = (f'<span class="zh-ripple" style="width:{ring_d + 40}px;height:{ring_d + 40}px;border:2px dashed #E0F2FE;'
                    f'animation:none;transform:translate(-50%,-50%);opacity:.9"></span>') if is_sel else ""
        pins.append(
            f'<div class="zh-pin" style="left:{x / W * 100:.2f}%;top:{y / H * 100:.2f}%">'
            # ground: glow disc, solid ring, two ripples
            f'<span style="position:absolute;left:0;top:0;width:{ring_d * 2.4:.0f}px;height:{ring_d * 2.4:.0f}px;transform:translate(-50%,-50%);'
            f'border-radius:50%;background:radial-gradient(circle,{c["halo"]} 0%,transparent 65%)"></span>'
            f'<span style="position:absolute;left:0;top:0;width:{ring_d}px;height:{ring_d}px;transform:translate(-50%,-50%);border-radius:50%;'
            f'border:2px solid {c["c"]};box-shadow:0 0 18px {c["glow"]},inset 0 0 14px {c["glow"]}"></span>'
            f'<span class="zh-ripple" style="width:{ring_d}px;height:{ring_d}px;border:1.5px solid {c["c"]}"></span>'
            f'<span class="zh-ripple" style="width:{ring_d}px;height:{ring_d}px;border:1.5px solid {c["c"]};animation-delay:1.3s"></span>{sel_ring}'
            # standing hologram: beam, crystal, label, tag
            f'<div class="zh-up" style="transform:{counter}">'
            f'<div class="zh-beam" style="top:{-beam_h}px;height:{beam_h}px;background:linear-gradient(0deg,{c["c"]},{c["light"]} 70%,transparent);'
            f'box-shadow:0 0 16px {c["glow"]},0 0 40px {c["halo"]}"></div>'
            f'<div style="position:absolute;left:-9px;top:{-beam_h - 9}px;width:18px;height:18px;transform:rotate(45deg);border-radius:4px;'
            f'background:linear-gradient(135deg,#fff,{c["light"]} 40%,{c["c"]});box-shadow:0 0 22px {c["glow"]},0 0 50px {c["halo"]}"></div>'
            f'<div class="zh-tag" style="top:{label_top}px;animation:none;display:flex;align-items:center;gap:6px;padding:3px 9px;border-radius:999px;'
            f'background:rgba(4,10,20,.82);border:1px solid {c["c"]}88;font-size:11px;color:#E6F6FF;font-weight:500">'
            f'<span style="font-family:var(--zx-head);font-weight:700;color:{c["light"]}">{z.get("incidents", 0)}</span>{esc(z.get("zone"))}</div>'
            f'{tag}</div></div>'
        )

    core = (
        f'<div class="zh-pin" style="left:{cx / W * 100:.2f}%;top:{cy / H * 100:.2f}%">'
        f'<span style="position:absolute;left:0;top:0;width:150px;height:150px;transform:translate(-50%,-50%);border-radius:50%;'
        f'background:radial-gradient(circle,rgba(129,140,248,.35),transparent 65%)"></span>'
        f'<span class="zh-ripple" style="width:90px;height:90px;border:2px solid #A5B4FC;animation-duration:3.2s"></span>'
        f'<div class="zh-up" style="transform:{counter}">'
        f'<div style="position:absolute;left:-2px;top:-56px;width:4px;height:56px;background:linear-gradient(0deg,#818CF8,#E879F9 70%,transparent);'
        f'box-shadow:0 0 20px rgba(167,139,250,.9)"></div>'
        f'<div style="position:absolute;left:-30px;top:-116px;width:60px;height:60px;transform-style:preserve-3d">'
        f'<span class="zh-gyro" style="inset:0;border:2px solid rgba(103,232,249,.85);box-shadow:0 0 16px rgba(34,211,238,.7)"></span>'
        f'<span class="zh-gyro2" style="inset:6px;border:2px solid rgba(232,121,249,.8);box-shadow:0 0 16px rgba(232,121,249,.6)"></span>'
        f'<span style="position:absolute;inset:22px;border-radius:50%;background:radial-gradient(circle at 35% 30%,#fff,#A5B4FC 45%,#4338CA);'
        f'box-shadow:0 0 30px rgba(129,140,248,.95)"></span></div>'
        f'<div class="zh-tag zh-flicker" style="top:-140px;font-family:var(--zx-mono);font-size:10px;letter-spacing:.16em;color:#C7D2FE;'
        f'text-shadow:0 0 8px rgba(129,140,248,.9)">ZENITH CORE{f" · {analysed} EVENTS" if analysed is not None else ""}</div></div></div>'
    )

    hexes = ('<pattern id="zh-hex" width="34" height="58.9" patternUnits="userSpaceOnUse" patternTransform="scale(1)">'
             '<path d="M17 0 L34 9.8 L34 29.4 L17 39.3 L0 29.4 L0 9.8 Z M17 39.3 L17 58.9" fill="none" stroke="rgba(103,232,249,.11)" stroke-width="1"/></pattern>')
    rings = "".join(f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{r}" fill="none" stroke="rgba(103,232,249,{0.22 - r / 3000:.2f})" stroke-width="1" '
                    f'stroke-dasharray="{"2 6" if r % 200 else "none"}"/>' for r in (120, 240, 360, 480, 600))
    ticks = "".join(f'<line x1="{cx + 600 * math.cos(math.radians(a)):.0f}" y1="{cy + 600 * math.sin(math.radians(a)):.0f}" '
                    f'x2="{cx + 570 * math.cos(math.radians(a)):.0f}" y2="{cy + 570 * math.sin(math.radians(a)):.0f}" stroke="rgba(103,232,249,.35)"/>'
                    for a in range(0, 360, 10))
    plane = (
        f'<div class="zh-plane" style="height:{H:.0f}px;transform:translate(-50%,-50%) rotateX({TILT}deg) rotateZ({SPIN}deg)">'
        f'<svg aria-hidden="true" viewBox="0 0 {W:.0f} {H:.0f}" preserveAspectRatio="none" width="100%" height="100%" style="position:absolute;inset:0;overflow:visible">'
        f'<defs>{hexes}</defs><rect width="{W:.0f}" height="{H:.0f}" rx="28" fill="url(#zh-hex)"/>{rings}{ticks}'
        f'<line x1="0" y1="{cy:.0f}" x2="{W:.0f}" y2="{cy:.0f}" stroke="rgba(103,232,249,.12)"/>'
        f'<line x1="{cx:.0f}" y1="0" x2="{cx:.0f}" y2="{H:.0f}" stroke="rgba(103,232,249,.12)"/>'
        f'{"".join(flows)}{"".join(packets)}</svg>'
        f'<div class="zh-sweep" style="left:50%;top:{cy / H * 100:.2f}%;width:1300px;height:1300px"></div>'
        f'{core}{"".join(pins)}</div>'
    )
    off_html = "".join(
        f'<span style="display:inline-flex;gap:6px;align-items:center;margin-right:6px;padding:4px 10px;border-radius:999px;'
        f'border:1px dashed rgba(165,180,252,.5);background:rgba(8,10,24,.8);font-size:10.5px;color:#C7D2FE">↗ {esc(z.get("zone"))} · '
        f'{z.get("incidents", 0)} incident(s) · off-grid ≈{km:.0f} km</span>' for z, km in off)
    legend = "".join(
        f'<span style="display:flex;align-items:center;gap:5px"><span style="width:8px;height:14px;border-radius:2px;'
        f'background:linear-gradient(0deg,{SEV[k]["c"]},{SEV[k]["light"]});box-shadow:0 0 8px {SEV[k]["c"]}"></span>{k}</span>'
        for k in ("critical", "high", "medium"))
    brackets = "".join(
        f'<span class="zh-bracket" style="{pos};border-width:{bw};border-radius:{br}"></span>'
        for pos, bw, br in (("left:14px;top:14px", "2px 0 0 2px", "8px 0 0 0"), ("right:14px;top:14px", "2px 2px 0 0", "0 8px 0 0"),
                            ("left:14px;bottom:14px", "0 0 2px 2px", "0 0 0 8px"), ("right:14px;bottom:14px", "0 2px 2px 0", "0 0 8px 0")))
    span_txt = f"N–S {span[0]:.1f} KM · E–W {span[1]:.1f} KM" if span else ""
    return (
        f'<div class="zh-stage" style="height:{height}px">{plane}'
        f'<div class="zh-scanlines"></div><div class="zh-scanbar"></div>{brackets}'
        f'<span class="zh-hudtxt zh-flicker" style="left:48px;top:22px">◢ TACTICAL HOLO-VIEW · SCHEMATIC</span>'
        f'<span class="zh-hudtxt" style="right:48px;top:22px">ZONES {len(placed)} · STREAMS {len(placed)} · NORTH ↑</span>'
        f'<div style="position:absolute;left:46px;bottom:62px">{off_html}</div>'
        f'<div style="position:absolute;left:46px;right:46px;bottom:20px;display:flex;align-items:center;gap:16px;flex-wrap:wrap;'
        f'font-size:10.5px;color:#9FB3C8"><span class="zx-mono" style="letter-spacing:.1em;color:#67E8F9">BEAM = EVENTS · NUMBER = OPEN INCIDENTS · '
        f'STREAMS → CORE</span><span style="margin-left:auto;display:flex;gap:12px">{legend}</span>'
        f'<span class="zx-mono" style="color:#67E8F9;letter-spacing:.08em">{span_txt}</span></div></div>'
    )


def posture(by_severity: dict[str, int], corroborated_critical: int) -> tuple[str, str, str]:
    """Deterministic posture label from open incident counts → (level, severity key, reason)."""
    crit, high = by_severity.get("critical", 0), by_severity.get("high", 0)
    if corroborated_critical:
        return "CRITICAL", "critical", f"{corroborated_critical} corroborated critical incident(s) open"
    if crit:
        return "SEVERE", "critical", f"{crit} critical incident(s) awaiting corroboration"
    if high:
        return "ELEVATED", "high", f"{high} high-severity incident(s) open"
    if sum(by_severity.values()):
        return "GUARDED", "medium", "only medium/low incidents open"
    return "NOMINAL", "low", "no open incidents"


def threat_core(by_severity: dict[str, int], corroborated_critical: int, open_total: int) -> str:
    """Arc-reactor style gauge: outer ring split by open-incident severity mix, centre = posture."""
    level, key, reason = posture(by_severity, corroborated_critical)
    c = SEV[key]
    total = max(1, sum(by_severity.get(k, 0) for k in ("critical", "high", "medium", "low")))
    stops, acc = [], 0.0
    for k in ("critical", "high", "medium", "low"):
        share = by_severity.get(k, 0) / total * 360
        if share:
            stops.append(f"{SEV[k]['c']} {acc:.1f}deg {acc + share - 2:.1f}deg, transparent {acc + share - 2:.1f}deg {acc + share:.1f}deg")
            acc += share
    conic = ", ".join(stops) or "rgba(255,255,255,.08) 0deg 360deg"
    size = 230
    mask = "radial-gradient(circle,transparent 62%,#000 63%,#000 70%,transparent 71%)"
    bars = "".join(
        f'<div style="display:grid;grid-template-columns:62px 1fr 26px;gap:8px;align-items:center;font-size:11px;color:#C7D0E0">'
        f'<span>{k.capitalize()}</span><span style="height:6px;border-radius:4px;background:rgba(255,255,255,.06);overflow:hidden">'
        f'<span style="display:block;height:6px;width:{by_severity.get(k, 0) / total * 100:.1f}%;background:linear-gradient(90deg,{SEV[k]["c"]},{SEV[k]["light"]});'
        f'box-shadow:0 0 10px {SEV[k]["c"]}"></span></span><span class="zx-mono" style="text-align:right">{by_severity.get(k, 0)}</span></div>'
        for k in ("critical", "high", "medium", "low"))
    return (
        f'<div class="zx-glass zx-hud" style="padding:18px;display:flex;flex-direction:column;align-items:center;gap:14px">'
        f'<div class="zx-row" style="width:100%;justify-content:space-between"><span class="zx-eyebrow">THREAT POSTURE</span>'
        f'<span class="zx-mono" style="font-size:10px;color:#8B97B0">{open_total} OPEN</span></div>'
        f'<div style="position:relative;width:{size}px;height:{size}px">'
        f'<span style="position:absolute;inset:0;border-radius:50%;background:radial-gradient(circle,{c["halo"]} 0%,transparent 70%)"></span>'
        f'<span class="zh-ring a" style="width:{size}px;height:{size}px;transform:translate(-50%,-50%);background:conic-gradient({conic});'
        f'-webkit-mask:{mask};mask:{mask};filter:drop-shadow(0 0 8px {c["c"]})"></span>'
        f'<span class="zh-ring b" style="width:{size - 46}px;height:{size - 46}px;transform:translate(-50%,-50%);border:1.5px dashed rgba(103,232,249,.55)"></span>'
        f'<span class="zh-ring c" style="width:{size - 74}px;height:{size - 74}px;transform:translate(-50%,-50%);'
        f'background:conic-gradient(from 0deg,{c["c"]},transparent 30%,transparent 50%,{c["light"]} 55%,transparent 80%);'
        f'-webkit-mask:radial-gradient(circle,transparent 66%,#000 67%);mask:radial-gradient(circle,transparent 66%,#000 67%)"></span>'
        f'<span style="position:absolute;left:50%;top:50%;width:{size - 104}px;height:{size - 104}px;transform:translate(-50%,-50%);border-radius:50%;'
        f'background:radial-gradient(circle at 40% 35%,rgba(255,255,255,.18),rgba(10,12,28,.95) 60%);border:1px solid {c["c"]}88;'
        f'box-shadow:0 0 40px {c["glow"]},inset 0 0 30px {c["halo"]};display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px">'
        f'<span style="font-family:var(--zx-head);font-size:40px;font-weight:700;line-height:1;color:#fff;text-shadow:0 0 18px {c["c"]}">'
        f'{by_severity.get("critical", 0)}</span><span class="zx-mono" style="font-size:9px;letter-spacing:.14em;color:{c["light"]}">CRITICAL OPEN</span></span></div>'
        f'<div style="text-align:center"><div class="zh-flicker" style="font-family:var(--zx-head);font-size:22px;font-weight:700;letter-spacing:.22em;'
        f'color:{c["light"]};text-shadow:0 0 16px {c["c"]}">{level}</div>'
        f'<div class="zx-muted" style="font-size:.74rem;margin-top:2px">{esc(reason)}</div></div>'
        f'<div style="width:100%;display:flex;flex-direction:column;gap:6px">{bars}</div></div>'
    )


def ticker(items: list[dict[str, Any]]) -> str:
    """Endless agent-activity marquee (content duplicated for a seamless loop; pauses on hover)."""
    if not items:
        return ""
    cells = "".join(
        f'<span style="display:inline-flex;align-items:center;gap:8px;font-size:12px;color:#D6E2F0">'
        f'<span style="width:7px;height:7px;border-radius:50%;background:{SEV[sev_of(i.get("severity"))]["c"]};'
        f'box-shadow:0 0 10px {SEV[sev_of(i.get("severity"))]["c"]}"></span>'
        f'<span class="zx-mono" style="color:#67E8F9;font-size:11px">{esc(fmt_time(i.get("at")))}</span>{esc(i.get("text"))}</span>'
        for i in items)
    return (f'<div class="zh-ticker"><span class="zx-mono" style="position:absolute;left:0;top:0;bottom:0;z-index:2;display:flex;align-items:center;'
            f'padding:0 12px;font-size:10px;letter-spacing:.14em;color:#060814;background:linear-gradient(90deg,#22D3EE,#818CF8)">AGENT LIVE</span>'
            f'<div class="zh-track" style="padding-left:120px">{cells}{cells}</div></div>')
