"""Global Zenith assistant bar: text + voice questions from the top of every page.

Type or hold the mic; the answer appears in a dock under the bar (and is read aloud
when asked by voice or when "Speak" is on). Proposed changes show the same
Confirm / Cancel card as the Copilot page — nothing changes without a click.
The conversation is shared with the Copilot page (same session id and turns).
"""
from __future__ import annotations

from typing import Any

import streamlit as st

from ui import components, theme
from ui.api_client import ZenithAPI
from ui.voice import voice_input
from ui.views import copilot

COPILOT_PAGE = ":material/auto_awesome: Copilot"


def _ask(api: ZenithAPI, api_url: str, question: str, via: str) -> None:
    turns: list[dict[str, Any]] = st.session_state.setdefault("cop_turns", [])
    try:
        result = api.assistant_query(question, session_id=copilot._session_id())
    except Exception as exc:  # connection / API errors are shown, never swallowed
        st.session_state["zxtop_error"] = str(exc)
        return
    st.session_state["assistant_result"] = result
    st.session_state["assistant_last_query"] = question
    turns.append({"q": question, "r": result, "via": via, "decision": None})
    st.session_state["zxtop_turn"] = len(turns) - 1
    st.session_state["zxtop_open"] = True
    request_speech(result, via)


def request_speech(result: dict[str, Any], via: str) -> None:
    """Queue the answer for the top bar's speaker (voice questions, or when Speak is on)."""
    if via == "voice" or st.session_state.get("zxtop_speak"):
        st.session_state["zxtop_say"] = (str(result.get("answer") or ""), result.get("request_id"))


def render(api: ZenithAPI, api_url: str, page: str) -> None:
    on_copilot = page == COPILOT_PAGE
    say_text, say_id = st.session_state.get("zxtop_say") or ("", None)
    with st.container(key="zxtop_bar"):
        c_in, c_go, c_mic, c_spk = st.columns([1, 0.11, 0.15, 0.15], vertical_alignment="center")
        with c_in:
            typed = st.text_input("Ask Zenith", key="zxtop_q", label_visibility="collapsed",
                                  placeholder="✦  Ask Zenith anything — “what's critical right now?”, “explain INC-…”, “acknowledge INC-…”")
        go = c_go.button("Ask", key="zxtop_go", type="primary", use_container_width=True)
        with c_mic:
            heard = voice_input(compact=True, key="zx_top_voice", speak_text=say_text, speak_id=say_id, auto_speak=True)
        c_spk.toggle("🔊 Speak", key="zxtop_speak", value=False, help="Read every answer aloud (voice questions are always answered aloud).")

    question, via = None, "text"
    if heard and heard.get("ts") != st.session_state.get("zxtop_voice_ts"):
        st.session_state["zxtop_voice_ts"] = heard.get("ts")
        question, via = (heard.get("transcript") or "").strip(), "voice"
    elif (go or (typed and typed != st.session_state.get("zxtop_last_typed"))) and typed and typed.strip():
        question = typed.strip()
    if typed:
        st.session_state["zxtop_last_typed"] = typed

    if question:
        if on_copilot:
            # The Copilot page renders after this bar in the same run and answers it there.
            st.session_state["zx_copilot_prefill"] = question
            st.session_state["zx_copilot_via"] = via
        else:
            with st.spinner("Zenith is reading the evidence…"):
                _ask(api, api_url, question, via)
            st.rerun()

    error = st.session_state.pop("zxtop_error", None)
    if error:
        st.error(f"Assistant unavailable: {error}")
    if not on_copilot:
        _dock(api, api_url)


def _dock(api: ZenithAPI, api_url: str) -> None:
    turns = st.session_state.get("cop_turns") or []
    idx = st.session_state.get("zxtop_turn")
    if idx is None or idx >= len(turns) or not st.session_state.get("zxtop_open"):
        return
    turn = turns[idx]
    with st.container(key="zxai_topdock"):
        head_l, head_r1, head_r2 = st.columns([1, 0.2, 0.08], vertical_alignment="center")
        via = {"voice": "🎙 via voice", "text": "⌨ typed"}.get(turn.get("via"), "")
        head_l.html(f'<div class="zx-row" style="gap:10px"><span class="zx-eyebrow">YOU ASKED</span>'
                    f'<span style="font-size:.9rem;color:#EEF2FA">“{theme.esc(turn["q"])}”</span>'
                    f'<span class="zx-muted" style="font-size:.7rem">{via}</span></div>')
        if head_r1.button("Open in Copilot →", key="zxtop_open_cop", use_container_width=True):
            st.session_state["zxtop_open"] = False
            components.goto(COPILOT_PAGE)
        if head_r2.button("✕", key="zxtop_close", help="Hide this answer", use_container_width=True):
            st.session_state["zxtop_open"] = False
            st.rerun()
        theme.show(copilot._answer_card(turn["r"]))
        if (turn["r"].get("pending_action") or {}) or turn.get("decision"):
            copilot._confirm_card(api, api_url, turn, idx)
