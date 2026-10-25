"""Zenith SecOps — Streamlit operations dashboard.

Launch:
    streamlit run ui/dashboard.py

The UI contains no business logic: every screen consumes the FastAPI backend.
Pages live in ``ui/views``; the shared look lives in ``ui/theme.py``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui import assistant_bar, theme  # noqa: E402
from ui.api_client import ApiConnectionError, ApiError, ZenithAPI  # noqa: E402
from ui.common import cached_health  # noqa: E402
from ui.views import briefing, command, copilot, events, incidents, ingest, sitemap, system, trust  # noqa: E402

st.set_page_config(
    page_title="Zenith SecOps",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)
theme.inject_css()

NAV = {
    ":material/radar: Command": command,
    ":material/crisis_alert: Incidents": incidents,
    ":material/map: Map": sitemap,
    ":material/verified_user: Trust": trust,
    ":material/auto_awesome: Copilot": copilot,
    ":material/description: Briefing": briefing,
    ":material/view_list: Events": events,
    ":material/upload: Ingest": ingest,
    ":material/monitor_heart: System": system,
}
# Other pages jump here (e.g. "open in investigation") by setting this key.
if st.session_state.get("zx_goto") in NAV:
    st.session_state["navigation"] = st.session_state.pop("zx_goto")

with st.sidebar:
    theme.show(
        '<div style="display:flex;flex-direction:column;align-items:center;gap:6px;margin:8px 0 16px">'
        '<span style="width:46px;height:46px;border-radius:14px;background:conic-gradient(from 210deg,#22D3EE,#818CF8,#E879F9,#22D3EE);'
        'display:flex;align-items:center;justify-content:center;box-shadow:0 0 26px rgba(129,140,248,.55)">'
        '<span style="width:40px;height:40px;border-radius:11px;background:#070A14;display:flex;align-items:center;justify-content:center">'
        '<span class="zx-grad" style="font-family:var(--zx-head);font-weight:700;font-size:21px">Z</span></span></span>'
        '<span style="font-family:var(--zx-head);font-weight:700;letter-spacing:.18em;font-size:.62rem;color:#C7D2FE">ZENITH</span></div>'
    )
    if "api_url" not in st.session_state:
        st.session_state["api_url"] = os.getenv("ZENITH_API_URL", "http://localhost:8000")
    page = st.radio("Workspace", list(NAV), key="navigation")

    api_url = st.session_state["api_url"]
    try:
        health = cached_health(api_url)
        online = health.get("status") == "ok" and health.get("database", {}).get("database_connected")
        st.session_state["zx_status"] = "online" if online else "offline"
        dot, label = ("#34D399", "API online") if online else ("#FBBF24", "Degraded")
    except (ApiConnectionError, ApiError):
        st.session_state["zx_status"] = "offline"
        dot, label = "#FF4D6D", "API offline"
    theme.show(
        f'<div title="{label} · deterministic · offline · no paid API" style="display:flex;flex-direction:column;align-items:center;gap:4px;'
        f'margin:14px 0 8px;font-size:.6rem;color:#9FB3C8"><span class="zx-live" style="width:9px;height:9px;border-radius:50%;'
        f'background:{dot};box-shadow:0 0 10px {dot}"></span>{label}</div>'
    )
    with st.popover(":material/settings:", use_container_width=True):
        st.text_input("API base URL", key="api_url")
        theme.show(
            f'<div class="zx-muted" style="font-size:.72rem">Docs <code>{theme.esc(api_url)}/docs</code><br>'
            f'Mobile <code>{theme.esc(api_url)}/mobile</code></div>'
        )
        if st.toggle("Compact layout", key="compact_mode", help="Single-column layout for phones and small screens."):
            theme.show(
                "<style>.block-container{max-width:760px}[data-testid=\"stHorizontalBlock\"]{flex-direction:column}"
                "[data-testid=\"stHorizontalBlock\"]>div{width:100%!important;flex:1 1 100%!important}</style>"
            )

api = ZenithAPI(st.session_state["api_url"])
assistant_bar.render(api, st.session_state["api_url"], page)
NAV[page].render(api, st.session_state["api_url"])
