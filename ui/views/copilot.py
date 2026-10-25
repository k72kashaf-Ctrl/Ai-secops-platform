"""Copilot page — AI-first, evidence-grounded operations assistant.

Every answer is rendered as a structured card (FACT / INFERENCE / CONFLICT /
MISSING) with its evidence ids. State changes are only ever *proposed*: the
backend returns a pending action plus a request id, and nothing is applied
until the operator clicks Confirm (POST /assistant/confirm).
"""
from __future__ import annotations

import re

from ui import assistant_bar, components, theme
from ui.common import *  # noqa: F401,F403

SUGGESTIONS = [
    "What's critical right now?",
    "What changed in the last hour?",
    "Show sensor conflicts",
    "Explain INC-69B8C5C3465420",
]
_INC_LINE = re.compile(r"^(INC-[0-9A-Z]+):\s*(.*)$")
_SEV_TAIL = re.compile(r"\s+—\s+(critical|high|medium|low)\s*/\s*([a-z_]+)\s*/\s*confidence\s+([0-9.]+)\s*$")
_USER_AVATAR = ":material/person:"
_AI_AVATAR = ":material/auto_awesome:"


# ------------------------------------------------------------------ helpers
def _session_id() -> str:
    # One conversation per browser session so follow-ups ("this incident") resolve.
    if "assistant_session_id" not in st.session_state:
        st.session_state["assistant_session_id"] = f"ui-{uuid.uuid4().hex[:12]}"
    return st.session_state["assistant_session_id"]


def _clip(text: Any, limit: int = 260) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _line_text(text: str) -> tuple[str, str]:
    """Return (trusted html body, ref) for one structured line."""
    ref = ""
    match = _INC_LINE.match(text)
    if match:
        ref, text = match.group(1), match.group(2)
    tail = _SEV_TAIL.search(text)
    if tail:
        body = theme.esc(_clip(text[: tail.start()]))
        body += (f' <span style="white-space:nowrap">{theme.sev_pill(tail.group(1))} '
                 f'<span class="zx-muted" style="font-size:.72rem">{theme.esc(theme.humanize(tail.group(2)))} · '
                 f'conf <span class="zx-mono">{theme.esc(tail.group(3))}</span></span></span>')
        return body, ref
    return theme.esc(_clip(text)), ref


def _lines(kind: str, items: list[Any], limit: int) -> list[str]:
    out = []
    for raw in items[:limit]:
        body, ref = _line_text(theme.detime(raw))
        out.append(
            f'<div class="zx-line" title="{theme.esc(raw)}">{theme.kind_badge(kind)}'
            f'<span style="font-size:.82rem;line-height:1.45;color:#DCE3F0">{body}</span>'
            f'{theme.ev_link(ref) if ref else "<span></span>"}</div>'
        )
    if len(items) > limit:
        out.append(f'<div class="zx-muted" style="font-size:.7rem;padding:4px 0 2px 94px">+{len(items) - limit} more {kind.lower()} line(s)</div>')
    return out


def _headline(result: dict[str, Any]) -> str:
    answer = str(result.get("answer") or "No answer returned.")
    lines = [line for line in answer.split("\n") if line.strip()]
    facts = set(result.get("facts") or [])
    if len(lines) > 1 and set(lines) <= facts:
        n = len(lines)
        return f"{n} incident{'s' if n != 1 else ''} matched — strongest first. Each line below is a stored fact."
    if result.get("intent") == "evidence" and result.get("evidence_event_ids"):
        ids = ", ".join(result.get("incident_ids") or []) or "the requested incident"
        return f"Evidence for {ids}: {len(result['evidence_event_ids'])} stored event(s), listed below."
    return "<br>".join(theme.esc(_clip(line, 420)) for line in lines[:4]) if len(lines) > 1 else theme.esc(_clip(answer, 600))


def _answer_card(result: dict[str, Any]) -> str:
    conf = result.get("confidence")
    has_conf = isinstance(conf, (int, float)) and bool(result.get("incident_ids"))
    ring = theme.ring(float(conf), 58, 6, "#67E8F9", "#A78BFA", f"{conf:.2f}", gid=f"zxcop-{result.get('request_id')}") if has_conf else ""
    intent = theme.humanize(result.get("intent") or "answer")
    head_text = _headline(result)
    # _headline escapes everything except the <br> joins it adds itself.
    head = (
        f'<div class="zx-row" style="gap:14px;align-items:flex-start;margin-bottom:10px">'
        f'<div style="flex:1;min-width:0"><div class="zx-eyebrow zx-grad" style="margin-bottom:6px">ZENITH · {theme.esc(intent.upper())}</div>'
        f'<div style="font-family:var(--zx-head);font-size:1.02rem;font-weight:500;line-height:1.45;color:#EEF2FA">{head_text}</div></div>'
        f'{ring}</div>'
    )
    rows = (
        _lines("FACT", result.get("facts") or [], 6)
        + _lines("INFERENCE", result.get("inferences") or [], 3)
        + _lines("CONFLICT", list(dict.fromkeys(result.get("conflicts") or [])), 4)
        + _lines("MISSING", result.get("missing_information") or [], 4)
    )
    body = "".join(rows)
    evidence = result.get("evidence_event_ids") or []
    ev_html = ""
    if evidence:
        chips = " ".join(theme.ev_link(e) for e in evidence[:18])
        more = f' <span class="zx-muted" style="font-size:.7rem">+{len(evidence) - 18} more</span>' if len(evidence) > 18 else ""
        ev_html = (f'<div style="margin-top:10px;display:flex;flex-wrap:wrap;gap:5px;align-items:center">'
                   f'<span class="zx-mono" style="font-size:.6rem;letter-spacing:.1em;color:#6B7790;margin-right:4px">EVIDENCE</span>{chips}{more}</div>')
    recs = result.get("recommended_actions") or []
    rec_html = ""
    if recs:
        rec_html = (
            '<div style="margin-top:12px;border-radius:14px;padding:12px 14px;background:linear-gradient(135deg,rgba(129,140,248,.10),rgba(232,121,249,.05));'
            'border:1px solid rgba(165,180,252,.2)"><div style="font-size:.8rem;font-weight:600;margin-bottom:6px">Zenith proposes '
            '<span style="font-weight:400;color:#A5B4FC">· you decide</span></div>'
            + "".join(f'<div style="display:flex;gap:8px;font-size:.8rem;color:#C7D0E0;line-height:1.4;margin-bottom:3px">'
                      f'<span style="color:#A5B4FC">→</span>{theme.esc(_clip(r, 220))}</div>' for r in recs[:4])
            + "</div>"
        )
    meta = [f"provider {theme.esc(result.get('provider') or 'deterministic')}",
            "grounded" if result.get("grounded") else "not grounded"]
    if result.get("used_session_context"):
        meta.append("used conversation context")
    if result.get("confidence_basis"):
        meta.append(theme.esc(result["confidence_basis"]))
    foot = (f'<div class="zx-muted" style="font-size:.66rem;margin-top:10px;display:flex;flex-wrap:wrap;gap:4px 10px">'
            f'<span>{" · ".join(meta)}</span><span class="zx-mono" style="margin-left:auto">{theme.esc(result.get("request_id"))}</span></div>')
    return head + body + ev_html + rec_html + foot


def _pending_label(pending: dict[str, Any]) -> str:
    inc = theme.esc(pending.get("incident_id") or "incident")
    if pending.get("action") == "assign_incident":
        change = f'assign → <b>{theme.esc(pending.get("assigned_to"))}</b>'
    else:
        current = theme.esc(theme.humanize(pending.get("current_status") or "current"))
        change = f'{current} → <b>{theme.esc(theme.humanize(pending.get("status")))}</b>'
    return f'<span class="zx-mono" style="color:#FCD34D">{inc}</span> · {change}'


def _decide(api: ZenithAPI, api_url: str, turn: dict[str, Any], confirmed: bool) -> None:
    request_id = turn["r"]["request_id"]
    try:
        decision = api.assistant_confirm(request_id, confirmed)
    except ApiError as exc:
        if exc.status_code == 409:
            turn["decision"] = {"status": "already_decided", "text": "Already decided — no further change was made."}
            st.rerun()
        api_error(exc, api_url)
        return
    except Exception as exc:  # connection errors etc.
        api_error(exc, api_url)
        return
    outcome = decision.get("status")
    text = {
        "confirmed": "Confirmed and applied · audit entry written.",
        "cancelled": "Cancelled — nothing was changed.",
        "not_found": "The incident no longer exists; nothing was changed.",
    }.get(outcome, f"Result: {outcome}")
    turn["decision"] = {"status": outcome, "text": text}
    st.cache_data.clear()
    st.rerun()


def _confirm_card(api: ZenithAPI, api_url: str, turn: dict[str, Any], idx: int) -> None:
    result = turn["r"]
    pending = result.get("pending_action") or {}
    decision = turn.get("decision")
    if decision:
        tone = "ok" if decision["status"] == "confirmed" else "neutral"
        theme.show(f'<div style="margin-top:8px">{theme.chip(decision["text"], tone)}</div>')
        return
    with st.container(key=f"zxg_confirm_{idx}"):
        theme.show(
            '<div style="margin:-16px -18px 0;padding:14px 18px;border-radius:20px 20px 0 0;'
            'background:linear-gradient(180deg,rgba(251,191,36,.16),rgba(251,191,36,.02))">'
            f'<div class="zx-row" style="justify-content:space-between">{theme.chip("NEEDS CONFIRMATION", "warning")}'
            f'<span class="zx-mono" style="font-size:.66rem;color:#8B97B0">{theme.esc(result.get("request_id"))}</span></div>'
            f'<div style="font-family:var(--zx-head);font-size:1.08rem;font-weight:500;margin-top:10px">{_pending_label(pending)}</div>'
            f'<div class="zx-muted" style="margin-top:4px">{theme.esc(pending.get("description") or "This request changes incident state.")}</div>'
            '<div class="zx-muted" style="font-size:.7rem;margin-top:6px">Nothing changes until you confirm. The decision is recorded in the audit log.</div></div>'
        )
        a, b = st.columns(2)
        if a.button("Confirm change", key=f"cop_confirm_{result['request_id']}", type="primary", use_container_width=True):
            _decide(api, api_url, turn, True)
        if b.button("Cancel", key=f"cop_cancel_{result['request_id']}", use_container_width=True):
            _decide(api, api_url, turn, False)


def _queue_suggestion() -> None:
    choice = st.session_state.get("cop_suggest")
    if choice:
        st.session_state["cop_queue"] = choice
    st.session_state["cop_suggest"] = None


def _history_table(api: ZenithAPI, session_id: str, mine: bool) -> None:
    params: dict[str, Any] = {"limit": 25}
    if mine:
        params["session_id"] = session_id
    try:
        items = api._request("GET", "/assistant/history", params=params)[1].get("items", [])
    except Exception as exc:
        st.warning(f"History unavailable: {exc}")
        return
    if not items:
        theme.show(theme.empty("No questions asked yet in this scope."))
        return
    rows = []
    for item in items:
        resp = item.get("response") or {}
        if item.get("decision"):
            state = item["decision"]
        elif item.get("pending_action"):
            state = "awaiting"
        else:
            state = "read-only"
        rows.append({
            "at": item.get("created_at"), "query": item.get("query"), "intent": resp.get("intent"),
            "incidents": len(resp.get("incident_ids") or []), "state": state, "id": item.get("history_id"),
        })
    tones = {"confirmed": "ok", "cancelled": "neutral", "awaiting": "warning", "read-only": "info"}
    theme.show(theme.html_table(rows, [
        ("at", "When"), ("query", "Question"), ("intent", "Intent"), ("incidents", "Incidents"),
        ("state", "Action"), ("id", "Request"),
    ], {
        "at": lambda v, r: f'<span class="zx-mono" style="font-size:.72rem">{theme.esc(theme.fmt_dt(v))}</span>',
        "query": lambda v, r: theme.esc(_clip(v, 90)),
        "intent": lambda v, r: f'<span class="zx-muted">{theme.esc(theme.humanize(v) or "—")}</span>',
        "incidents": lambda v, r: f'<span class="zx-mono">{v}</span>',
        "state": lambda v, r: theme.chip(theme.humanize(v), tones.get(v, "neutral")),
        "id": lambda v, r: f'<span class="zx-mono" style="font-size:.66rem;color:#8B97B0">{theme.esc(v)}</span>',
    }))


# --------------------------------------------------------------------- page
def render(api: ZenithAPI, api_url: str) -> None:
    session_id = _session_id()
    turns: list[dict[str, Any]] = st.session_state.setdefault("cop_turns", [])

    theme.page_header("Zenith", "Copilot",
                      "Grounded answers over stored events and incidents. Changes are proposed — you confirm.",
                      status=st.session_state.get("zx_status"))

    def ask(question: str, via: str = "text") -> None:
        try:
            result = api.assistant_query(question, session_id=session_id)
        except Exception as exc:
            api_error(exc, api_url)
            return
        st.session_state["assistant_result"] = result
        st.session_state["assistant_last_query"] = question
        turns.append({"q": question, "r": result, "via": via, "decision": None})

    typed = st.chat_input("Ask Zenith — incidents, evidence, conflicts, or “acknowledge INC-…”", key="cop_chat")
    main, side = st.columns([1, 0.36], gap="medium")

    # ---- side rail: voice (secondary input) + guardrails
    with side:
        with st.container(key="zxg_voice"):
            theme.show(theme.section("Voice", "top bar · every page")
                       + '<div class="zx-muted" style="font-size:.74rem;line-height:1.5">Hold <b>🎙 Voice</b> in the assistant bar at the top '
                         'of any page and speak; the answer is shown and read aloud. Turn on <b>🔊 Speak</b> to hear typed answers too. '
                         'Speech runs in your browser (Web Speech API; Chrome/Edge for input). Changes still need Confirm.</div>')
        with st.container(key="zxg_guard"):
            theme.show(
                theme.section("How Zenith answers")
                + "".join(
                    f'<div style="display:flex;gap:8px;align-items:center;margin-bottom:6px">{theme.kind_badge(k)}'
                    f'<span style="font-size:.76rem;color:#C7D0E0">{theme.esc(t)}</span></div>'
                    for k, t in [("FACT", "read from stored events"), ("INFERENCE", "reasoned from facts"),
                                 ("CONFLICT", "sensors disagree"), ("MISSING", "what we don't know"),
                                 ("ACTION", "proposed · needs confirm")])
                + f'<div class="zx-muted" style="font-size:.68rem;margin-top:8px">session <span class="zx-mono">{theme.esc(session_id)}</span>'
                  f' · {len(turns)} turn(s)</div>'
            )
            if turns and st.button("Clear conversation", key="cop_clear", use_container_width=True):
                st.session_state["cop_turns"] = []
                st.session_state.pop("assistant_result", None)
                st.rerun()

    # ---- collect the next question (one per run)
    question, via = None, "text"
    prefill = st.session_state.pop("zx_copilot_prefill", None)
    if prefill and str(prefill).strip():
        question, via = str(prefill).strip(), st.session_state.pop("zx_copilot_via", "handoff")
    elif typed and typed.strip():
        question = typed.strip()
    elif st.session_state.get("cop_queue"):
        question = st.session_state.pop("cop_queue")
        via = st.session_state.pop("cop_queue_via", "suggestion")

    with main:
        with st.container(key="zxai_cop_head"):
            theme.show(
                f'<div class="zx-row" style="gap:16px;flex-wrap:wrap">{theme.orb(48)}'
                '<div style="flex:1;min-width:220px"><div class="zx-eyebrow">ZENITH COPILOT</div>'
                '<div style="font-family:var(--zx-head);font-size:1.08rem;font-weight:500;margin-top:2px">'
                'Ask what matters — every answer cites the events behind it.</div></div>'
                f'{theme.chip("provider: deterministic · grounded", "ok")}</div>'
            )
            st.pills("Suggestions", SUGGESTIONS, key="cop_suggest", on_change=_queue_suggestion,
                     label_visibility="collapsed")

        if question:
            with st.spinner("Zenith is reading the evidence…"):
                ask(question, via)
            if turns and (via == "voice" or st.session_state.get("zxtop_speak")):
                # Re-run so the top bar's speaker receives this answer and reads it aloud.
                assistant_bar.request_speech(turns[-1]["r"], via)
                st.rerun()

        if not turns:
            theme.show(theme.empty("Start with a suggestion above or type a question below. "
                                   "Try “acknowledge INC-…” to see a confirm-before-change proposal."))
        for idx, turn in enumerate(turns):
            with st.chat_message("user", avatar=_USER_AVATAR):
                via_tag = {"voice": " · via voice", "handoff": " · from Ask bar"}.get(turn.get("via"), "")
                theme.show(f'<div style="font-size:.92rem">{theme.esc(turn["q"])}'
                           f'<span class="zx-muted" style="font-size:.66rem">{via_tag}</span></div>')
            with st.chat_message("assistant", avatar=_AI_AVATAR):
                result = turn["r"]
                with st.container(key=f"zxai_ans_{idx}"):
                    theme.show(_answer_card(result))
                    if idx == len(turns) - 1 and result.get("incident_ids") and not result.get("requires_confirmation"):
                        ids = result["incident_ids"][:3]
                        cols = st.columns(len(ids))
                        for col, inc_id in zip(cols, ids):
                            if col.button(f"Investigate {inc_id[:10]}…", key=f"cop_open_{idx}_{inc_id}", use_container_width=True):
                                components.goto(":material/crisis_alert: Incidents", inv_incident=inc_id)
                if result.get("requires_confirmation"):
                    _confirm_card(api, api_url, turn, idx)

        with st.expander("Conversation history", expanded=False):
            mine = st.toggle("This session only", value=True, key="cop_hist_mine")
            _history_table(api, session_id, mine)
