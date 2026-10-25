"""Zenith premium theme: global CSS plus small HTML component builders.

Every builder returns an HTML string; ``show()`` renders it. Builders emit
single-line HTML so Streamlit's markdown parser never turns indentation into a
code block. All text passes through ``esc`` before it reaches the page.
"""
from __future__ import annotations

import html
import math
import re
from datetime import datetime
from typing import Any, Iterable

import streamlit as st

# ----------------------------------------------------------------- palette
SEV = {
    "critical": {"c": "#FF4D6D", "light": "#FFB3C1", "deep": "#8A0F2A", "glow": "rgba(255,77,109,.85)", "halo": "rgba(255,77,109,.36)", "ink": "#FFFFFF"},
    "high": {"c": "#FBBF24", "light": "#FEF3C7", "deep": "#92400E", "glow": "rgba(251,191,36,.7)", "halo": "rgba(251,191,36,.24)", "ink": "#1A1205"},
    "medium": {"c": "#818CF8", "light": "#E0E7FF", "deep": "#3730A3", "glow": "rgba(129,140,248,.55)", "halo": "rgba(129,140,248,.16)", "ink": "#FFFFFF"},
    "low": {"c": "#34D399", "light": "#D1FAE5", "deep": "#065F46", "glow": "rgba(52,211,153,.5)", "halo": "rgba(52,211,153,.14)", "ink": "#04150F"},
    "unknown": {"c": "#94A3B8", "light": "#E2E8F0", "deep": "#334155", "glow": "rgba(148,163,184,.4)", "halo": "rgba(148,163,184,.12)", "ink": "#0B1020"},
}
TONE = {
    "critical": ("#FF8FA6", "rgba(255,77,109,.12)", "rgba(255,77,109,.40)"),
    "warning": ("#FCD34D", "rgba(251,191,36,.10)", "rgba(251,191,36,.35)"),
    "info": ("#C4B5FD", "rgba(167,139,250,.10)", "rgba(167,139,250,.35)"),
    "ok": ("#6EE7B7", "rgba(52,211,153,.10)", "rgba(52,211,153,.35)"),
    "neutral": ("#CBD5E1", "rgba(148,163,184,.08)", "rgba(148,163,184,.28)"),
}
KIND = {
    "FACT": ("#5EEAD4", "rgba(45,212,191,.10)", "rgba(45,212,191,.35)"),
    "INFERENCE": ("#C4B5FD", "rgba(167,139,250,.12)", "rgba(167,139,250,.40)"),
    "CONFLICT": ("#FCD34D", "rgba(251,191,36,.10)", "rgba(251,191,36,.40)"),
    "MISSING": ("#FDA4AF", "rgba(251,113,133,.10)", "rgba(251,113,133,.35)"),
    "ACTION": ("#A5B4FC", "rgba(129,140,248,.12)", "rgba(129,140,248,.40)"),
}
GROUP_COLORS = {
    "Cameras & ANPR": ("#38BDF8", "#BAE6FD"),
    "Access control": ("#A78BFA", "#DDD6FE"),
    "Perimeter & radar": ("#34D399", "#A7F3D0"),
    "Alarms & reports": ("#FB7185", "#FECDD3"),
    "Telemetry": ("#64748B", "#CBD5E1"),
    "Other": ("#F59E0B", "#FDE68A"),
}
STATUS_TONE = {
    "open": "critical", "acknowledged": "info", "investigating": "warning",
    "false_positive": "neutral", "resolved": "ok", "closed": "neutral",
}
SEV_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}

ICONS = {
    "command": "M12 3v2M12 19v2M3 12h2M19 12h2M12 8a4 4 0 1 1 0 8 4 4 0 0 1 0-8z",
    "incident": "M12 3l9 16H3zM12 10v4",
    "map": "M9 4l6 2 6-2v14l-6 2-6-2-6 2V6zM9 4v14M15 6v14",
    "trust": "M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6z",
    "spark": "M12 3l2.2 5.8L20 11l-5.8 2.2L12 19l-2.2-5.8L4 11l5.8-2.2z",
    "brief": "M6 3h9l4 4v14H6zM9 12h7M9 16h5",
    "events": "M4 6h16M4 12h16M4 18h10",
    "bell": "M12 2v10M12 22a2 2 0 1 0 0-4 2 2 0 0 0 0 4z",
    "upload": "M12 16V4M6 10l6-6 6 6M4 20h16",
    "pulse": "M3 12h4l3-8 4 16 3-8h4",
}


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def show(markup: str) -> None:
    # st.markdown, not st.html: st.html sanitises away the inline SVG (icons, rings, charts).
    st.markdown(markup, unsafe_allow_html=True)


def sev_of(value: Any) -> str:
    value = str(value or "unknown").lower()
    return value if value in SEV else "unknown"


# ------------------------------------------------------------------ format
def parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def fmt_time(value: Any, seconds: bool = False) -> str:
    dt = parse_ts(value)
    if not dt:
        return "—"
    return dt.strftime("%H:%M:%S" if seconds else "%H:%M")


def fmt_dt(value: Any) -> str:
    dt = parse_ts(value)
    return dt.strftime("%d %b %H:%M") if dt else "—"


_ISO_IN_TEXT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?")


def detime(text: Any) -> str:
    """Rewrite raw ISO timestamps inside backend prose as '12 Mar 09:20'."""
    return _ISO_IN_TEXT.sub(lambda m: fmt_dt(m.group(0)), "" if text is None else str(text))


def humanize(value: Any) -> str:
    return str(value or "").replace("_", " ").strip().capitalize()


def pct_delta(last: int, prev: int) -> tuple[str, str]:
    """Return (label, tone) for a last-vs-previous comparison."""
    if prev == 0 and last == 0:
        return "no change", "neutral"
    if prev == 0:
        return f"+{last} new", "warning"
    change = round((last - prev) / prev * 100)
    arrow = "↑" if change > 0 else ("↓" if change < 0 else "→")
    return f"{arrow} {abs(change)}%", ("warning" if change > 0 else "ok")


# ------------------------------------------------------------------- CSS
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');
:root{--zx-bg:#04060C;--zx-text:#EEF2FA;--zx-muted:#8B97B0;--zx-soft:#C7D0E0;--zx-line:rgba(255,255,255,.07);--zx-cyan:#22D3EE;--zx-indigo:#818CF8;--zx-fuchsia:#E879F9;--zx-mono:'JetBrains Mono',ui-monospace,monospace;--zx-head:'Space Grotesk',Inter,sans-serif}
html,body,.stApp{font-family:Inter,system-ui,sans-serif;color:var(--zx-text)}
.stApp{background:radial-gradient(1200px 620px at 18% -12%,#0B1730 0%,transparent 60%),radial-gradient(900px 600px at 100% 0%,#1A1033 0%,transparent 55%),radial-gradient(900px 500px at 40% 110%,rgba(236,72,153,.08) 0%,transparent 60%),var(--zx-bg)}
.stApp::before{content:"";position:fixed;inset:0;pointer-events:none;background-image:linear-gradient(rgba(125,211,252,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(125,211,252,.025) 1px,transparent 1px);background-size:48px 48px;mask-image:radial-gradient(ellipse at 50% 0%,#000 30%,transparent 75%);z-index:0}
header[data-testid="stHeader"]{background:transparent;height:0;overflow:visible}
[data-testid="stExpandSidebarButton"],[data-testid="stSidebarCollapsedControl"]{display:flex!important;visibility:visible!important;position:fixed!important;top:14px;left:14px;z-index:1000001;border-radius:12px;background:rgba(16,20,36,.9);border:1px solid rgba(125,211,252,.35);box-shadow:0 0 20px -6px rgba(34,211,238,.7);color:#E0F2FE}
[data-testid="stToolbarActions"],[data-testid="stMainMenu"],[data-testid="stDecoration"],[data-testid="stStatusWidget"],#MainMenu,footer,.stDeployButton,[data-testid="stAppDeployButton"]{display:none!important}
.block-container{padding-top:1.4rem;padding-bottom:3rem;max-width:1560px}
h1,h2,h3,h4{font-family:var(--zx-head);letter-spacing:-.015em;color:var(--zx-text)}
h3{font-size:1.05rem;font-weight:600}
p,li,label,.stMarkdown{color:var(--zx-text)}
a{color:#7DD3FC}
code{color:#7DD3FC;background:rgba(125,211,252,.08);border-radius:6px}
/* sidebar = glass nav rail */
[data-testid="stSidebar"]{background:linear-gradient(180deg,rgba(8,12,24,.92),rgba(5,7,14,.96));border-right:1px solid var(--zx-line);min-width:236px!important;max-width:236px!important}
[data-testid="stSidebar"] .block-container,[data-testid="stSidebarContent"]{padding-top:.4rem}
[data-testid="stSidebar"] [role="radiogroup"]{gap:4px}
[data-testid="stSidebar"] [role="radiogroup"] label{margin:0;padding:9px 12px;border-radius:12px;border:1px solid transparent;transition:background .15s,border-color .15s;width:100%}
[data-testid="stSidebar"] [role="radiogroup"] label:hover{background:rgba(255,255,255,.04)}
[data-testid="stSidebar"] [role="radiogroup"] label>span:first-child{position:absolute;opacity:0;width:1px;height:1px;overflow:hidden}
[data-testid="stSidebar"] [role="radiogroup"] label>div>div:first-child:not([data-testid]){display:none}
[data-testid="stSidebar"] [role="radiogroup"] label p{font-size:.9rem;font-weight:500;color:#9AA6BF}
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked){background:linear-gradient(100deg,rgba(34,211,238,.16),rgba(129,140,248,.16));border-color:rgba(125,211,252,.35);box-shadow:0 0 24px -10px rgba(34,211,238,.7)}
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) p{color:#F0F9FF;font-weight:600}
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p{font-size:.7rem;letter-spacing:.12em;text-transform:uppercase;color:#5F6B85}
/* icon rail navigation (design option 4) */
[data-testid="stSidebar"]{min-width:108px!important;max-width:108px!important}
[data-testid="stSidebarContent"]{padding:0 8px}
[data-testid="stSidebar"] [role="radiogroup"] label{padding:9px 4px!important;justify-content:center;position:relative}
[data-testid="stSidebar"] [role="radiogroup"] label p{display:flex;flex-direction:column;align-items:center;gap:5px;font-size:.66rem!important;text-align:center;line-height:1.1}
[data-testid="stSidebar"] [role="radiogroup"] label p>span:first-child{font-size:24px!important;margin:0!important;line-height:1}
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked)::before{content:"";position:absolute;left:-8px;top:10px;bottom:10px;width:3px;border-radius:3px;background:linear-gradient(#22D3EE,#E879F9);box-shadow:0 0 10px #22D3EE}
[data-testid="stSidebar"] [data-testid="stWidgetLabel"]{display:none}
/* numbered mode strip */
[class*="st-key-zxmode"] button{min-height:58px!important;justify-content:flex-start!important;text-align:left;padding:8px 14px!important;border-radius:14px!important;
 background:linear-gradient(180deg,rgba(22,28,46,.75),rgba(10,14,26,.85))!important;border:1px solid rgba(255,255,255,.08)!important}
[class*="st-key-zxmode"] button p{font-size:.84rem!important;font-weight:600}
[class*="st-key-zxmodeon"] button{border:1.5px solid #22D3EE!important;background:linear-gradient(180deg,rgba(14,60,84,.55),rgba(10,20,40,.9))!important;box-shadow:0 0 26px -6px rgba(34,211,238,.9),inset 0 0 18px rgba(34,211,238,.15)!important}
[class*="st-key-zxmodeon"] button p{color:#E0F7FF!important}
/* operator action bar */
.st-key-zxbar{background:linear-gradient(180deg,rgba(16,20,36,.92),rgba(8,10,20,.95));border:1px solid rgba(125,211,252,.18);border-radius:18px;padding:10px 14px;box-shadow:0 -10px 40px -20px rgba(34,211,238,.4)}
.st-key-zxbar button{min-height:44px!important;white-space:nowrap}
.st-key-zxbar_ack button{border:1.5px solid #FF4D6D!important;background:linear-gradient(180deg,rgba(120,16,40,.55),rgba(60,8,22,.75))!important;color:#FFE4EA!important;box-shadow:0 0 22px -6px rgba(255,77,109,.9)!important;font-weight:700!important}
/* glass surfaces */
.zx-glass{position:relative;background:linear-gradient(180deg,rgba(22,28,46,.78),rgba(10,14,26,.84)) padding-box,linear-gradient(140deg,rgba(125,211,252,.28),rgba(255,255,255,.05) 40%,rgba(192,132,252,.22)) border-box;border:1px solid transparent;border-radius:20px;box-shadow:0 1px 0 rgba(255,255,255,.04) inset,0 22px 50px -24px rgba(0,0,0,.85);padding:16px 18px;margin-bottom:14px;overflow:hidden}
.zx-ai{background:linear-gradient(180deg,rgba(24,22,48,.92),rgba(12,12,30,.92)) padding-box,linear-gradient(100deg,#22D3EE,#818CF8 45%,#E879F9) border-box;border:1px solid transparent}
.zx-grad{background:linear-gradient(100deg,#67E8F9,#A5B4FC 50%,#F0ABFC);-webkit-background-clip:text;background-clip:text;color:transparent}
.zx-mono{font-family:var(--zx-mono)}
.zx-muted{color:var(--zx-muted);font-size:.82rem}
.zx-eyebrow{font-family:var(--zx-mono);font-size:.66rem;letter-spacing:.14em;text-transform:uppercase;color:#A5B4FC}
.zx-h,h3.zx-h{font-family:var(--zx-head)!important;font-weight:600!important;font-size:1.02rem!important;margin:0!important;padding:0!important;line-height:1.3!important}
.zx-row{display:flex;align-items:center;gap:10px}
.zx-chip{display:inline-flex;align-items:center;gap:6px;height:26px;padding:0 10px;border-radius:999px;font-size:.74rem;font-weight:500;white-space:nowrap}
.zx-dot{width:6px;height:6px;border-radius:50%;display:inline-block}
.zx-kind{display:inline-block;min-width:74px;text-align:center;font-family:var(--zx-mono);font-size:.6rem;font-weight:600;letter-spacing:.06em;border-radius:6px;padding:3px 6px}
.zx-ev{font-family:var(--zx-mono);font-size:.68rem;padding:2px 7px;border-radius:6px;background:rgba(125,211,252,.08);color:#7DD3FC;white-space:nowrap}
.zx-line{display:grid;grid-template-columns:84px 1fr auto;gap:10px;align-items:center;padding:6px 0;border-bottom:1px solid rgba(255,255,255,.04)}
.zx-line:last-child{border-bottom:0}
.zx-kpi{position:relative;padding:14px 16px 12px;min-height:170px}
.zx-kpi-v{font-family:var(--zx-head);font-size:2.25rem;font-weight:600;line-height:1;letter-spacing:-.02em}
.zx-table{width:100%;border-collapse:separate;border-spacing:0 6px;font-size:.82rem}
.zx-table th{font-family:var(--zx-mono);font-size:.62rem;letter-spacing:.1em;text-transform:uppercase;color:#6B7790;text-align:left;font-weight:500;padding:0 10px}
.zx-table td{background:rgba(255,255,255,.025);padding:9px 10px;border-top:1px solid rgba(255,255,255,.04);border-bottom:1px solid rgba(255,255,255,.04)}
.zx-table td:first-child{border-left:1px solid rgba(255,255,255,.04);border-radius:10px 0 0 10px}
.zx-table td:last-child{border-right:1px solid rgba(255,255,255,.04);border-radius:0 10px 10px 0}
/* instant feedback while a page loads (Streamlit's own status widget is hidden) */
[data-testid="stApp"][data-test-script-state="running"]::after{content:"";position:fixed;top:0;left:0;right:0;height:3px;z-index:1000000;pointer-events:none;background:linear-gradient(90deg,transparent,#22D3EE,#818CF8,#E879F9,transparent);background-size:45% 100%;background-repeat:no-repeat;animation:zx-load .8s linear infinite;box-shadow:0 0 12px rgba(129,140,248,.8)}
@keyframes zx-load{from{background-position:-45% 0}to{background-position:145% 0}}
[data-stale="true"]{transition:opacity .12s ease-in}
/* motion */
@keyframes zx-sweep{to{transform:translate(-50%,-50%) rotate(360deg)}}
@keyframes zx-ping{0%{transform:translate(-50%,-50%) scale(.9);opacity:.7}100%{transform:translate(-50%,-50%) scale(2.6);opacity:0}}
@keyframes zx-spin{to{transform:rotate(360deg)}}
@keyframes zx-flow{to{stroke-dashoffset:-40}}
@keyframes zx-shine{0%{background-position:-200% 0}100%{background-position:200% 0}}
@keyframes zx-blink{50%{opacity:.35}}
.zx-sweep{position:absolute;border-radius:50%;background:conic-gradient(from 0deg,rgba(34,211,238,.20),rgba(34,211,238,0) 70deg,transparent 360deg);animation:zx-sweep 9s linear infinite;pointer-events:none}
.zx-ping{position:absolute;left:50%;top:50%;border-radius:50%;animation:zx-ping 2.4s ease-out infinite}
.zx-orb{animation:zx-spin 6s linear infinite}
.zx-flow{stroke-dasharray:6 14;animation:zx-flow 2.8s linear infinite}
.zx-shine{background-image:linear-gradient(100deg,transparent 30%,rgba(255,255,255,.16) 50%,transparent 70%);background-size:200% 100%;animation:zx-shine 3.5s ease-in-out infinite}
.zx-live{animation:zx-blink 1.6s ease-in-out infinite}
@media (prefers-reduced-motion:reduce){.zx-sweep,.zx-ping,.zx-orb,.zx-flow,.zx-shine,.zx-live{animation:none}.zx-sweep{display:none}}
/* any st.container(key="zxg_...") becomes a glass card; "zxai_..." an AI-gradient card */
[class*="st-key-zxg"],[class*="st-key-zxai"]{background:linear-gradient(180deg,rgba(22,28,46,.78),rgba(10,14,26,.84)) padding-box,linear-gradient(140deg,rgba(125,211,252,.28),rgba(255,255,255,.05) 40%,rgba(192,132,252,.22)) border-box;border:1px solid transparent;border-radius:20px;padding:16px 18px;box-shadow:0 22px 50px -24px rgba(0,0,0,.85)}
[class*="st-key-zxai"]{background:linear-gradient(180deg,rgba(24,22,48,.92),rgba(12,12,30,.92)) padding-box,linear-gradient(100deg,#22D3EE,#818CF8 45%,#E879F9) border-box}
[class*="st-key-zxg"] .zx-glass,[class*="st-key-zxai"] .zx-glass{background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.06);box-shadow:none}
/* global assistant bar (sticky, every page) */
[data-testid="stLayoutWrapper"]:has(> .st-key-zxtop_bar){position:sticky;top:6px;z-index:999}
.st-key-zxtop_bar{margin:-.4rem 0 10px;padding:10px 12px;border-radius:18px;
 background:linear-gradient(180deg,rgba(14,14,34,.94),rgba(8,10,22,.94)) padding-box,linear-gradient(100deg,#22D3EE,#818CF8 45%,#E879F9) border-box;
 border:1px solid transparent;box-shadow:0 14px 40px -18px rgba(129,140,248,.75);backdrop-filter:blur(10px)}
.st-key-zxtop_q [data-testid="stTextInputRootElement"]{background:rgba(255,255,255,.04)!important;border:1px solid rgba(165,180,252,.28)!important;border-radius:999px!important}
.st-key-zxtop_q input{height:42px;font-size:.95rem;background:transparent!important}
.st-key-zxtop_bar iframe{display:block}
.st-key-zxtop_speak label p{font-size:.8rem!important;white-space:nowrap}
.st-key-zxai_topdock{margin-bottom:14px}
/* streamlit widgets */
.stButton>button,.stDownloadButton>button,.stFormSubmitButton>button{border-radius:12px;border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.03);color:#DCE3F0;font-weight:500;min-height:40px;transition:all .15s}
.stButton>button:hover,.stDownloadButton>button:hover,.stFormSubmitButton>button:hover{border-color:rgba(125,211,252,.55);color:#F0F9FF;background:rgba(125,211,252,.06)}
.stButton>button[kind="primary"],.stFormSubmitButton>button[kind="primaryFormSubmit"],.stButton>button[data-testid="stBaseButton-primary"],.stFormSubmitButton>button[data-testid="stBaseButton-primaryFormSubmit"]{background:linear-gradient(100deg,#22D3EE,#818CF8);color:#060814;border:0;font-weight:700;box-shadow:0 10px 30px -12px rgba(99,102,241,.9)}
.stButton>button[kind="primary"]:hover,.stButton>button[data-testid="stBaseButton-primary"]:hover{filter:brightness(1.08);color:#060814}
[data-testid="stTextInputRootElement"],.stTextInput input,.stTextArea textarea,.stNumberInput input,.stDateInput input,[data-baseweb="select"]>div{background:rgba(10,14,26,.85)!important;border-radius:12px!important;border-color:rgba(255,255,255,.10)!important;color:var(--zx-text)!important}
.stTextInput input:focus,.stTextArea textarea:focus{border-color:rgba(125,211,252,.6)!important;box-shadow:0 0 0 3px rgba(34,211,238,.12)!important}
.stTabs [data-baseweb="tab-list"]{gap:4px;padding:4px;border-radius:14px;background:rgba(255,255,255,.035);border:1px solid rgba(255,255,255,.06)}
.stTabs [data-baseweb="tab"]{height:34px;padding:0 14px;border-radius:10px;color:#8B97B0;background:transparent}
.stTabs [aria-selected="true"]{background:linear-gradient(100deg,rgba(34,211,238,.22),rgba(129,140,248,.22));color:#F0F9FF!important;box-shadow:inset 0 0 0 1px rgba(125,211,252,.35)}
.stTabs [data-baseweb="tab-highlight"],.stTabs [data-baseweb="tab-border"]{display:none}
[data-testid="stExpander"]{border:1px solid rgba(255,255,255,.07);border-radius:16px;background:rgba(10,14,26,.55)}
[data-testid="stDataFrame"]{border:1px solid rgba(255,255,255,.07);border-radius:14px;overflow:hidden}
div[data-testid="stMetric"]{background:linear-gradient(180deg,rgba(22,28,46,.78),rgba(10,14,26,.84));border:1px solid rgba(255,255,255,.08);border-radius:16px;padding:12px 14px}
div[data-testid="stMetricValue"]{font-family:var(--zx-head)}
[data-testid="stAlert"]{border-radius:14px;border:1px solid rgba(255,255,255,.08)}
[data-testid="stChatMessage"]{background:rgba(16,20,36,.6);border:1px solid rgba(255,255,255,.06);border-radius:16px}
[data-testid="stFileUploaderDropzone"]{background:rgba(10,14,26,.6);border:1px dashed rgba(125,211,252,.35);border-radius:16px}
[data-testid="stSegmentedControl"] button,[data-testid="stPills"] button,[data-testid="stButtonGroup"] button{border-radius:999px!important}
@media (max-width:768px){.block-container{padding:1rem .75rem 2.5rem}[data-testid="stHorizontalBlock"]{flex-direction:column;gap:.5rem}[data-testid="stHorizontalBlock"]>div{width:100%!important;flex:1 1 100%!important}.zx-kpi-v{font-size:1.7rem}.zx-hide-sm{display:none!important}.stButton>button{width:100%;min-height:44px}}
</style>
"""


def inject_css() -> None:
    from ui import holo  # local import: holo builds on this module

    st.markdown(CSS + holo.CSS, unsafe_allow_html=True)


# ------------------------------------------------------------- primitives
def chip(label: Any, tone: str = "neutral", dot: bool = True) -> str:
    fg, bg, bd = TONE.get(tone, TONE["neutral"])
    d = f'<span class="zx-dot" style="background:{fg};box-shadow:0 0 8px {fg}"></span>' if dot else ""
    return f'<span class="zx-chip" style="color:{fg};background:{bg};border:1px solid {bd}">{d}{esc(label)}</span>'


def sev_pill(severity: Any, solid: bool = False) -> str:
    s = sev_of(severity)
    c = SEV[s]
    if solid:
        return (f'<span class="zx-mono" style="font-size:.62rem;font-weight:700;letter-spacing:.08em;color:#060814;'
                f'background:linear-gradient(90deg,{c["c"]},{c["light"]});border-radius:999px;padding:3px 10px">{esc(s.upper())}</span>')
    return (f'<span class="zx-mono" style="font-size:.62rem;font-weight:600;letter-spacing:.08em;color:{c["c"]};'
            f'background:{c["halo"]};border:1px solid {c["c"]}66;border-radius:999px;padding:2px 8px">{esc(s.upper())}</span>')


def status_pill(status: Any) -> str:
    value = str(status or "unknown")
    return chip(humanize(value), STATUS_TONE.get(value, "neutral"))


def kind_badge(kind: str) -> str:
    fg, bg, bd = KIND.get(kind, KIND["FACT"])
    return f'<span class="zx-kind" style="color:{fg};background:{bg};border:1px solid {bd}">{esc(kind)}</span>'


def ev_link(event_id: Any) -> str:
    return f'<span class="zx-ev">{esc(event_id)}</span>'


def glass(inner: str, style: str = "", ai: bool = False) -> str:
    cls = "zx-glass zx-ai" if ai else "zx-glass"
    return f'<div class="{cls}" style="{style}">{inner}</div>'


def section(title: str, sub: str = "", right: str = "") -> str:
    s = f'<span class="zx-muted" style="font-size:.74rem">{esc(sub)}</span>' if sub else ""
    return (f'<div class="zx-row" style="margin-bottom:10px;align-items:baseline"><h3 class="zx-h">{esc(title)}</h3>{s}'
            f'<span style="margin-left:auto">{right}</span></div>')


def empty(text: str) -> str:
    return glass(f'<div class="zx-muted" style="text-align:center;padding:18px 0">{esc(text)}</div>')


def sparkline(series: Iterable[float], color: str, gid: str, height: int = 34) -> str:
    values = [float(v or 0) for v in series] or [0.0]
    if len(values) == 1:
        values = values * 2
    top = max(values) or 1.0
    pts = [(i * 200 / (len(values) - 1), (height - 3) - v / top * (height - 7)) for i, v in enumerate(values)]
    line = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts)
    area = line + f" L200 {height} L0 {height} Z"
    return (f'<svg aria-hidden="true" width="100%" height="{height}" viewBox="0 0 200 {height}" preserveAspectRatio="none" style="display:block">'
            f'<defs><linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{color}" stop-opacity=".45"/>'
            f'<stop offset="1" stop-color="{color}" stop-opacity="0"/></linearGradient></defs>'
            f'<path d="{area}" fill="url(#{gid})"/><path d="{line}" fill="none" stroke="{color}" stroke-width="2" '
            f'stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/></svg>')


def kpi_card(label: str, value: Any, sub: str, delta: str, delta_tone: str, series: Iterable[float],
             color: str, light: str, icon: str, gid: str) -> str:
    fg, bg, _ = TONE.get(delta_tone, TONE["neutral"])
    return (
        f'<div class="zx-glass zx-kpi zx-hud">'
        f'<span aria-hidden="true" style="position:absolute;right:-40px;top:-40px;width:140px;height:140px;border-radius:50%;background:{color}33;filter:blur(30px)"></span>'
        f'<div class="zx-row" style="position:relative"><span style="width:30px;height:30px;border-radius:10px;background:linear-gradient(135deg,{light},{color});'
        f'display:flex;align-items:center;justify-content:center;box-shadow:0 6px 18px -6px {color}">'
        f'<svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#060814" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="{ICONS.get(icon, icon)}"/></svg></span>'
        f'<span style="font-size:.8rem;color:#B4BED3;font-weight:500">{esc(label)}</span></div>'
        f'<div class="zx-row" style="position:relative;justify-content:space-between;align-items:flex-end;margin:10px 0 6px">'
        f'<span class="zx-kpi-v">{esc(value)}</span>'
        f'<span class="zx-mono" style="font-size:.7rem;font-weight:600;color:{fg};background:{bg};border-radius:999px;padding:3px 8px">{esc(delta)}</span></div>'
        f'<div style="position:relative">{sparkline(series, color, gid)}</div>'
        f'<div class="zx-muted" style="position:relative;font-size:.72rem;margin-top:6px">{esc(sub)}</div></div>'
    )


def ring(value: float, size: int = 70, stroke: int = 7, c1: str = "#22D3EE", c2: str = "#818CF8",
         label: str = "", gid: str = "zx-ring", sub: str = "") -> str:
    value = max(0.0, min(1.0, float(value or 0)))
    r = (size - stroke) / 2 - 1
    circ = 2 * math.pi * r
    center = size / 2
    inner = f'<span style="font-family:var(--zx-head);font-size:{max(12, size // 5)}px;font-weight:700">{esc(label)}</span>'
    if sub:
        inner += f'<span style="font-size:9px;letter-spacing:.1em;color:#8B97B0">{esc(sub)}</span>'
    return (
        f'<div role="img" aria-label="{esc(label)} {esc(sub)}" style="position:relative;width:{size}px;height:{size}px;flex:0 0 {size}px">'
        f'<svg aria-hidden="true" width="{size}" height="{size}" viewBox="0 0 {size} {size}" style="filter:drop-shadow(0 0 6px {c1}88)">'
        f'<defs><linearGradient id="{gid}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{c1}"/><stop offset="1" stop-color="{c2}"/></linearGradient></defs>'
        f'<circle cx="{center}" cy="{center}" r="{r:.1f}" fill="none" stroke="rgba(255,255,255,.07)" stroke-width="{stroke}"/>'
        f'<circle cx="{center}" cy="{center}" r="{r:.1f}" fill="none" stroke="url(#{gid})" stroke-width="{stroke}" stroke-linecap="round" '
        f'stroke-dasharray="{circ * value:.1f} {circ:.1f}" transform="rotate(-90 {center} {center})"/></svg>'
        f'<span style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center">{inner}</span></div>'
    )


def orb(size: int = 52) -> str:
    return (
        f'<span aria-hidden="true" style="position:relative;width:{size}px;height:{size}px;flex:0 0 {size}px;display:inline-block">'
        '<span class="zx-orb" style="position:absolute;inset:0;border-radius:50%;background:conic-gradient(from 0deg,#22D3EE,#818CF8,#E879F9,#F59E0B,#22D3EE);filter:blur(1px)"></span>'
        '<span style="position:absolute;inset:4px;border-radius:50%;background:radial-gradient(circle at 35% 30%,#312E81,#0B0B1E 70%)"></span>'
        '<span style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center">'
        f'<svg width="{size // 2.6:.0f}" height="{size // 2.6:.0f}" viewBox="0 0 24 24" fill="none" stroke="#E0E7FF" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="{ICONS["spark"]}"/></svg></span></span>'
    )


# -------------------------------------------------------------- page chrome
def page_header(title: str, accent: str, subtitle: str, latest: Any = None, status: str | None = None) -> None:
    clock = ""
    if latest:
        dt = parse_ts(latest)
        day = dt.strftime("%a %d %b").upper() if dt else ""
        clock = (f'<div class="zx-hide-sm" style="display:flex;flex-direction:column;align-items:flex-end;gap:1px">'
                 f'<span class="zx-mono" style="font-size:.62rem;color:#8B97B0;letter-spacing:.08em">LATEST EVENT · {esc(day)}</span>'
                 f'<span style="font-family:var(--zx-head);font-size:1.55rem;font-weight:600">{esc(fmt_time(latest))}'
                 f'<span style="font-size:.75rem;color:#8B97B0;font-weight:500"> UTC</span></span></div>')
    pill = ""
    if status:
        tone = "ok" if status == "online" else "critical"
        fg, bg, bd = TONE[tone]
        text = "Agent watching" if status == "online" else "API offline"
        sub = "deterministic · offline · local" if status == "online" else "start uvicorn on :8000"
        pill = (f'<div class="zx-hide-sm" style="display:flex;align-items:center;gap:10px;height:46px;padding:0 14px;border-radius:14px;background:{bg};border:1px solid {bd}">'
                f'<span style="position:relative;width:10px;height:10px"><span class="zx-ping" style="width:10px;height:10px;background:{fg}"></span>'
                f'<span style="position:absolute;inset:0;border-radius:50%;background:{fg};box-shadow:0 0 10px {fg}"></span></span>'
                f'<span style="display:flex;flex-direction:column"><span style="font-size:.78rem;font-weight:600;color:{fg}">{text}</span>'
                f'<span style="font-size:.64rem;color:#8B97B0">{sub}</span></span></div>')
    show(
        f'<div class="zx-row" style="gap:18px;margin:0 0 16px;flex-wrap:wrap">'
        f'<div style="flex:1;min-width:260px"><div style="font-family:var(--zx-head);font-size:1.75rem;font-weight:600;letter-spacing:-.02em;line-height:1.15">'
        f'{esc(title)} <span class="zx-grad">{esc(accent)}</span></div>'
        f'<div class="zx-muted" style="margin-top:4px">{esc(subtitle)}</div></div>{pill}{clock}</div>'
    )


def agent_brief(headline: str, chips: list[dict[str, str]], eyebrow: str) -> str:
    """``headline`` must already be escaped / trusted HTML."""
    chip_html = "".join(chip(c.get("label"), c.get("tone", "neutral")) for c in chips)
    return glass(
        f'<div class="zx-row" style="gap:18px;flex-wrap:wrap">{orb(52)}'
        f'<div style="flex:1;min-width:260px;display:flex;flex-direction:column;gap:6px">'
        f'<span class="zx-eyebrow">{esc(eyebrow)}</span>'
        f'<span style="font-family:var(--zx-head);font-size:1.12rem;font-weight:500;line-height:1.35">{headline}</span></div>'
        f'<div class="zx-row" style="flex-wrap:wrap;gap:8px;justify-content:flex-end;max-width:420px">{chip_html}</div></div>',
        style="padding:14px 18px;box-shadow:0 20px 60px -30px rgba(168,85,247,.6)", ai=True,
    )


def feed(items: list[dict[str, Any]]) -> str:
    rows = []
    for item in items:
        c = SEV[sev_of(item.get("severity"))]["c"]
        rows.append(
            f'<div style="display:grid;grid-template-columns:44px 10px 1fr;gap:8px;align-items:start;padding:6px 0;font-size:.78rem;color:#C7D0E0">'
            f'<span class="zx-mono" style="color:#8B97B0;font-size:.7rem">{esc(fmt_time(item.get("at")))}</span>'
            f'<span style="width:7px;height:7px;margin-top:5px;border-radius:50%;background:{c};box-shadow:0 0 8px {c}"></span>'
            f'<span>{esc(item.get("text"))} <span class="zx-ev" style="font-size:.6rem">{esc(str(item.get("ref") or "")[:14])}</span></span></div>'
        )
    head = ('<div class="zx-row" style="margin-bottom:4px"><span class="zx-live" style="width:7px;height:7px;border-radius:50%;background:#E879F9;'
            'box-shadow:0 0 8px #E879F9"></span><h3 class="zx-h" style="font-size:.9rem">Agent activity</h3>'
            '<span class="zx-muted" style="font-size:.7rem">event time · newest first</span></div>')
    return glass(head + ("".join(rows) or '<div class="zx-muted">No agent actions yet.</div>'))


def bars(buckets: list[dict[str, Any]], groups: list[str], height: int = 140) -> str:
    totals = [sum((b.get("counts") or {}).values()) for b in buckets] or [0]
    top = max(totals) or 1
    scale = (height - 10) / top
    cols = []
    for b in buckets:
        segs = []
        for g in groups:
            v = (b.get("counts") or {}).get(g, 0)
            if v:
                c, c2 = GROUP_COLORS.get(g, GROUP_COLORS["Other"])
                segs.append(f'<div style="height:{max(2, round(v * scale))}px;background:linear-gradient(180deg,{c2},{c});border-radius:3px;box-shadow:0 0 10px -2px {c}"></div>')
        total = sum((b.get("counts") or {}).values())
        cols.append(f'<div title="{esc(fmt_time(b.get("start")))} · {total} events" style="flex:1;display:flex;flex-direction:column-reverse;gap:2px;height:100%;min-width:3px">{"".join(segs)}</div>')
    peak_i = totals.index(max(totals)) if buckets else 0
    labels = ""
    if buckets:
        idx = sorted({0, len(buckets) // 3, 2 * len(buckets) // 3, len(buckets) - 1})
        labels = "".join(f'<span>{esc(fmt_time(buckets[i].get("start")))}</span>' for i in idx)
    legend = "".join(
        f'<span class="zx-chip" style="background:rgba(255,255,255,.04);color:#C7D0E0;height:22px"><span style="width:8px;height:8px;border-radius:3px;'
        f'background:{GROUP_COLORS.get(g, GROUP_COLORS["Other"])[0]}"></span>{esc(g)}</span>' for g in groups)
    peak = (f'<span class="zx-mono" style="position:absolute;right:0;top:-2px;font-size:.62rem;color:#FBBF24">peak {max(totals)} at '
            f'{esc(fmt_time(buckets[peak_i].get("start")))}</span>') if buckets else ""
    return (
        f'<div style="position:relative;height:{height}px;margin-top:6px">'
        f'<div aria-hidden="true" style="position:absolute;inset:0;background:linear-gradient(to bottom,rgba(255,255,255,.06) 1px,transparent 1px) 0 0/100% 50%"></div>'
        f'<div style="position:absolute;inset:14px 0 0 0;display:flex;align-items:flex-end;gap:4px">{"".join(cols)}</div>{peak}</div>'
        f'<div class="zx-mono" style="display:flex;justify-content:space-between;font-size:.62rem;color:#5F6B85;margin:6px 0 10px">{labels}</div>'
        f'<div class="zx-row" style="flex-wrap:wrap;gap:6px">{legend}</div>'
    )


def site_map(zones: list[dict[str, Any]], selected: str | None = None, hot_only: bool = False,
             callouts: list[dict[str, Any]] | None = None, height: int = 440) -> str:
    """Offline 2D site picture projected from average zone coordinates."""
    pts = [z for z in zones if z.get("latitude") is not None and z.get("longitude") is not None]
    if not pts:
        return empty("No zone coordinates yet — ingest events with latitude/longitude to draw the site picture.")
    lats = sorted(z["latitude"] for z in pts)
    lons = sorted(z["longitude"] for z in pts)
    mlat, mlon = lats[len(lats) // 2], lons[len(lons) // 2]
    kx = math.cos(math.radians(mlat))
    dist = lambda z: math.hypot((z["longitude"] - mlon) * kx, z["latitude"] - mlat)  # noqa: E731
    ds = sorted(dist(z) for z in pts)
    med = ds[len(ds) // 2] or 1e-6
    on = [z for z in pts if dist(z) <= med * 5] or pts
    off = [z for z in pts if z not in on]
    width = 1000
    pad = 80
    x0, x1 = min(z["longitude"] for z in on), max(z["longitude"] for z in on)
    y0, y1 = min(z["latitude"] for z in on), max(z["latitude"] for z in on)
    spanx = max((x1 - x0) * kx, 1e-4)
    spany = max(y1 - y0, 1e-4)
    # Schematic projection: fit height, then stretch x (up to 3x) so zones use the width.
    sy = (height - 2 * pad - 30) / spany
    sx = min((width - 2 * pad - 260) / spanx, sy * 3)
    s = sy
    ox = (width - 260 - spanx * sx) / 2
    oy = (height - 30 - spany * sy) / 2

    def xy(z: dict[str, Any]) -> tuple[float, float]:
        return ox + (z["longitude"] - x0) * kx * sx, oy + (y1 - z["latitude"]) * sy

    cx, cy = width / 2, (height - 30) / 2
    max_events = max(z.get("events", 0) for z in on) or 1
    halos, pins, links = [], [], []
    ordered = sorted(on, key=lambda z: xy(z)[0])
    for a, b in zip(ordered, ordered[1:]):
        (ax, ay), (bx, by) = xy(a), xy(b)
        mx, my = (ax + bx) / 2, (ay + by) / 2 - 24
        links.append(f"M{ax:.0f} {ay:.0f} Q{mx:.0f} {my:.0f} {bx:.0f} {by:.0f}")
    for z in on:
        sev = sev_of(z.get("worst_severity") or "unknown")
        if hot_only and sev not in ("critical", "high"):
            continue
        c = SEV[sev]
        x, y = xy(z)
        d = round(26 + 22 * math.sqrt(z.get("events", 0) / max_events))
        halos.append(f'<span aria-hidden="true" style="position:absolute;left:{x / width * 100:.2f}%;top:{y:.0f}px;width:{d * 4}px;height:{d * 4}px;'
                     f'transform:translate(-50%,-50%);border-radius:50%;background:radial-gradient(circle,{c["halo"]} 0%,transparent 68%)"></span>')
        ping = f'<span class="zx-ping" style="width:{d}px;height:{d}px;border:2px solid {c["c"]}"></span>' if sev == "critical" else ""
        sel = (f'<span style="position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);width:{d + 14}px;height:{d + 14}px;border-radius:50%;'
               f'border:1.5px dashed #E0F2FE"></span>') if z.get("zone") == selected else ""
        pins.append(
            f'<span title="{esc(z.get("zone"))}: {z.get("incidents", 0)} open incidents, {z.get("events", 0)} events" '
            f'style="position:absolute;left:{x / width * 100:.2f}%;top:{y:.0f}px;width:0;height:0">{ping}{sel}'
            f'<span style="position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);width:{d}px;height:{d}px;border-radius:50%;'
            f'background:radial-gradient(circle at 35% 30%,{c["light"]},{c["c"]} 55%,{c["deep"]});box-shadow:0 0 22px {c["glow"]},inset 0 -4px 8px rgba(0,0,0,.35);'
            f'display:flex;align-items:center;justify-content:center;font-family:var(--zx-head);font-size:13px;font-weight:700;color:{c["ink"]}">{z.get("incidents", 0)}</span>'
            f'<span style="position:absolute;left:50%;top:{d / 2 + 8:.0f}px;transform:translateX(-50%);white-space:nowrap;font-size:11px;font-weight:500;color:#DCE3F0;'
            f'background:rgba(6,10,20,.72);border:1px solid rgba(255,255,255,.08);padding:2px 8px;border-radius:999px">{esc(z.get("zone"))}</span></span>'
        )
    callout_html, leaders = [], []
    for i, co in enumerate((callouts or [])[:2]):
        z = next((zz for zz in on if zz.get("zone") == co.get("zone")), None)
        if not z:
            continue
        x, y = xy(z)
        top = 62 + i * 96
        c = SEV[sev_of(co.get("severity"))]
        leaders.append(f'<path d="M{x:.0f} {y:.0f} C {x + 80:.0f} {y:.0f}, {width - 330:.0f} {top + 34}, {width - 290:.0f} {top + 34}" '
                       f'fill="none" stroke="{c["c"]}" stroke-opacity=".7" stroke-width="1.2" stroke-dasharray="3 4"/>')
        callout_html.append(
            f'<div style="position:absolute;left:{(width - 290) / width * 100:.2f}%;top:{top:.0f}px;width:240px;max-width:27%;border-radius:14px;padding:10px 12px 10px 14px;'
            f'background:linear-gradient(135deg,rgba(76,16,36,.92),rgba(20,8,20,.92));border:1px solid {c["c"]}88;box-shadow:0 10px 30px -8px {c["glow"]}">'
            f'<div class="zx-mono" style="display:flex;justify-content:space-between;font-size:10px;color:{c["light"]}"><span>● {esc(str(co.get("severity", "")).upper())} · {esc(fmt_time(co.get("at")))}</span>'
            f'<span>{esc(co.get("conf"))}</span></div>'
            f'<div style="font-family:var(--zx-head);font-size:14px;font-weight:600;margin-top:2px">{esc(co.get("title"))}</div>'
            f'<div style="font-size:11px;color:#E6B8C4">{esc(co.get("sub"))}</div></div>'
        )
    off_html = "".join(
        f'<div style="display:inline-flex;align-items:center;gap:6px;margin-right:6px;background:rgba(6,10,20,.75);border:1px dashed rgba(165,180,252,.35);'
        f'border-radius:10px;padding:5px 10px;font-size:11px;color:#C7D2FE">↗ {esc(z.get("zone"))} · {z.get("incidents", 0)} incident(s) · off-map '
        f'≈{dist(z) * 111.3:.0f} km</div>' for z in off)
    rings_svg = "".join(f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{r}" />' for r in (90, 180, 270, 360, 450))
    return (
        f'<div style="position:relative;height:{height}px;border-radius:16px;overflow:hidden;background:radial-gradient(ellipse 70% 80% at 50% 48%,#0C1A2E 0%,#070C18 60%,#04070F 100%);'
        f'border:1px solid rgba(125,211,252,.12);box-shadow:inset 0 0 80px rgba(0,0,0,.7)">'
        f'<svg aria-hidden="true" viewBox="0 0 {width} {height}" preserveAspectRatio="none" width="100%" height="100%" style="position:absolute;inset:0">'
        f'<defs><pattern id="zx-grid" width="32" height="32" patternUnits="userSpaceOnUse"><path d="M32 0H0V32" fill="none" stroke="rgba(125,211,252,.06)"/></pattern>'
        f'<linearGradient id="zx-road" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#22D3EE" stop-opacity=".1"/><stop offset=".5" stop-color="#818CF8" stop-opacity=".6"/>'
        f'<stop offset="1" stop-color="#E879F9" stop-opacity=".1"/></linearGradient></defs>'
        f'<rect width="100%" height="100%" fill="url(#zx-grid)"/><g fill="none" stroke="rgba(125,211,252,.09)">{rings_svg}</g>'
        f'<g fill="none" stroke="rgba(167,139,250,.07)"><path d="M40 {height - 110} C 260 {height - 160}, 380 {height - 60}, 560 {height - 120} S 860 {height - 170}, 980 {height - 130}"/>'
        f'<path d="M80 60 C 300 110, 420 30, 600 80 S 860 70, 990 120"/></g>'
        f'<path d="{" ".join(links)}" fill="none" stroke="rgba(129,140,248,.16)" stroke-width="5" stroke-linecap="round"/>'
        f'<path class="zx-flow" d="{" ".join(links)}" fill="none" stroke="url(#zx-road)" stroke-width="1.6" stroke-linecap="round"/>{"".join(leaders)}</svg>'
        f'<div aria-hidden="true" class="zx-sweep" style="left:50%;top:{cy:.0f}px;width:900px;height:900px;transform:translate(-50%,-50%)"></div>'
        f'{"".join(halos)}{"".join(pins)}{"".join(callout_html)}'
        f'<div style="position:absolute;left:14px;top:14px">{off_html}</div>'
        f'<span class="zx-mono" style="position:absolute;right:14px;top:14px;width:38px;height:38px;border-radius:50%;border:1px solid rgba(125,211,252,.3);'
        f'background:rgba(6,10,20,.6);display:flex;align-items:center;justify-content:center;font-size:11px;color:#7DD3FC">N↑</span>'
        f'<div style="position:absolute;left:14px;bottom:12px;right:14px;display:flex;gap:14px;align-items:center;flex-wrap:wrap;background:rgba(6,10,20,.78);'
        f'border:1px solid rgba(255,255,255,.08);border-radius:12px;padding:8px 14px;font-size:11px;color:#B4BED3">'
        f'<span>schematic · not to scale · bubble = events · number = open incidents · glow = worst severity</span><span style="margin-left:auto;display:flex;gap:12px;align-items:center">'
        + "".join(f'<span style="display:flex;align-items:center;gap:5px"><span style="width:9px;height:9px;border-radius:50%;background:{SEV[k]["c"]};'
                  f'box-shadow:0 0 8px {SEV[k]["c"]}"></span>{k}</span>' for k in ("critical", "high", "medium"))
        + f'</span></div>'
        f'<span class="zx-mono" style="position:absolute;right:14px;bottom:56px;font-size:10px;color:#7DD3FC">N–S {spany * 111.3:.1f} km · E–W {spanx * 111.3:.1f} km</span></div>'
    )


def evidence_chain(events: list[dict[str, Any]]) -> str:
    """Horizontal event-time chain for an incident's evidence."""
    if not events:
        return ""
    times = [parse_ts(e.get("occurred_at")) for e in events]
    valid = [t for t in times if t]
    if not valid:
        return ""
    t0, t1 = min(valid), max(valid)
    span = max((t1 - t0).total_seconds(), 1)
    nodes = []
    for e, t in zip(events[:12], times[:12]):
        pos = 6 + 88 * (((t - t0).total_seconds() / span) if t else 0)
        c = SEV[sev_of(e.get("severity"))]
        abbr = esc(str(e.get("source_type") or "?")[:3].upper())
        nodes.append(
            f'<span title="{esc(e.get("event_id"))} · {esc(e.get("event_type"))}" style="position:absolute;left:{pos:.1f}%;top:0;transform:translateX(-50%);'
            f'display:flex;flex-direction:column;align-items:center;gap:4px"><span style="width:28px;height:28px;border-radius:50%;'
            f'background:radial-gradient(circle at 35% 30%,{c["light"]},{c["c"]});box-shadow:0 0 14px {c["c"]};display:flex;align-items:center;justify-content:center;'
            f'font-family:var(--zx-mono);font-size:8px;font-weight:700;color:#060814">{abbr}</span>'
            f'<span class="zx-mono" style="font-size:9px;color:#B4BED3;white-space:nowrap">{esc(fmt_time(e.get("occurred_at"), True))}</span></span>'
        )
    mins = span / 60
    label = f"{span:.0f} seconds" if span < 120 else f"{mins:.0f} minutes"
    return (
        f'<div style="border-radius:14px;padding:12px 14px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06);margin:10px 0">'
        f'<div style="display:flex;justify-content:space-between;font-size:11px;color:#8B97B0;margin-bottom:10px"><span>Evidence chain · {label} · event time</span>'
        f'<span class="zx-mono">{esc(fmt_time(t0.isoformat(), True))} → {esc(fmt_time(t1.isoformat(), True))}</span></div>'
        f'<div style="position:relative;height:54px"><span aria-hidden="true" style="position:absolute;left:4%;right:4%;top:13px;height:2px;'
        f'background:linear-gradient(90deg,#FF4D6D,#FBBF24,#A78BFA);box-shadow:0 0 10px rgba(167,139,250,.6)"></span>{"".join(nodes)}</div></div>'
    )


def incident_card(inc: dict[str, Any], active: bool = False) -> str:
    sev = sev_of(inc.get("severity"))
    c = SEV[sev]
    sources = inc.get("sources") or []
    if isinstance(sources, str):
        sources = [sources]
    border = "rgba(125,211,252,.5)" if active else "rgba(255,255,255,.06)"
    bg = "linear-gradient(100deg,rgba(34,211,238,.10),rgba(129,140,248,.08))" if active else "rgba(255,255,255,.02)"
    conf = inc.get("confidence")
    conf_t = f"{float(conf):.2f}" if conf is not None else "—"
    return (
        f'<div style="display:flex;align-items:center;gap:10px;background:{bg};border:1px solid {border};border-radius:12px;padding:10px 12px;margin-bottom:6px">'
        f'<span style="width:4px;align-self:stretch;border-radius:4px;background:linear-gradient({c["c"]},{c["light"]});box-shadow:0 0 10px {c["c"]}"></span>'
        f'<span style="flex:1;min-width:0;display:flex;flex-direction:column;gap:2px"><span style="font-size:.84rem;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'
        f'{esc(inc.get("title"))}</span><span class="zx-mono" style="font-size:.62rem;color:{c["c"]}">{esc(sev.upper())} · {esc(" · ".join(sources))}</span></span>'
        f'<span style="display:flex;flex-direction:column;align-items:flex-end;gap:2px"><span style="font-family:var(--zx-head);font-size:.95rem;font-weight:600">{conf_t}</span>'
        f'<span class="zx-mono" style="font-size:.62rem;color:#8B97B0">{esc(fmt_time(inc.get("created_at")))}</span></span></div>'
    )


def html_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]], render: dict[str, Any] | None = None) -> str:
    """Small styled table; ``render`` maps a key to a callable returning trusted HTML."""
    render = render or {}
    head = "".join(f"<th>{esc(label)}</th>" for _, label in columns)
    body = []
    for row in rows:
        cells = []
        for key, _ in columns:
            fn = render.get(key)
            cells.append(f"<td>{fn(row.get(key), row) if fn else esc(row.get(key))}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f'<table class="zx-table"><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'
