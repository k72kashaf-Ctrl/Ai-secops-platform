"""Shared helpers for the Zenith dashboard pages (no business logic)."""
from __future__ import annotations

import html
import io
import json
import os
import sys
import uuid
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.api_client import ApiConnectionError, ApiError, ZenithAPI  # noqa: E402
from ui.voice import voice_input  # noqa: E402

SEVERITY_ORDER = ["critical", "high", "medium", "low"]
# Full incident lifecycle; merged with whatever /incidents/facets reports so
# the UI keeps working against older backends that only know three states.
INCIDENT_STATUSES = ["open", "acknowledged", "investigating", "false_positive", "resolved", "closed"]


# --------------------------------------------------------------------- helpers
def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def badge(severity: str | None) -> str:
    value = (severity or "unknown").lower()
    css = value if value in {"low", "medium", "high", "critical"} else "neutral"
    return f'<span class="badge b-{css}">{esc(value)}</span>'


def status_label(value: Any) -> str:
    return "All" if value == "All" else str(value or "unknown").replace("_", " ").capitalize()


def kpi(label: str, value: Any, sub: str = "", accent: str = "#38bdf8") -> None:
    st.markdown(
        f"""
        <div class="zx-kpi">
          <div class="zx-accent" style="background:{accent}"></div>
          <div class="zx-kpi-label">{esc(label)}</div>
          <div class="zx-kpi-value">{esc(value)}</div>
          <div class="zx-kpi-sub">{esc(sub)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def hero(title: str, subtitle: str) -> None:
    st.markdown(
        f'<div class="zx-hero"><div class="zx-hero-title">{esc(title)}</div>'
        f'<div class="zx-hero-sub">{esc(subtitle)}</div></div>',
        unsafe_allow_html=True,
    )


def api_error(exc: Exception, base_url: str) -> None:
    if isinstance(exc, ApiConnectionError):
        st.error(str(exc))
        st.code(
            f"cd source_code\nuvicorn app.main:app --port 8000\n"
            f"streamlit run ui/dashboard.py\n\nCurrent API URL: {base_url}",
            language="powershell",
        )
    elif isinstance(exc, ApiError):
        st.error(f"Backend returned HTTP {exc.status_code}.")
        with st.expander("Error details"):
            st.json(exc.payload)
    else:
        st.exception(exc)


@st.cache_data(ttl=5, show_spinner=False)
def cached_health(base_url: str) -> dict[str, Any]:
    return ZenithAPI(base_url).health()


def pagination(state_key: str, page: int, pages: int, total: int) -> None:
    left, middle, right = st.columns([1, 2, 1])
    if left.button("← Previous", key=f"{state_key}_prev", disabled=page <= 1):
        st.session_state[state_key] = page - 1
        st.rerun()
    middle.markdown(
        f"<div style='text-align:center;color:#92a4c0;padding-top:.5rem'>"
        f"Page <b style='color:#e6edf7'>{page}</b> of {max(pages, 1)} · {total} records</div>",
        unsafe_allow_html=True,
    )
    if right.button("Next →", key=f"{state_key}_next", disabled=page >= pages):
        st.session_state[state_key] = page + 1
        st.rerun()


def records_frame(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> None:
    if not rows:
        st.info("No records match the current filters.")
        return
    frame = pd.DataFrame(
        [{label: row.get(key) for key, label in columns} for row in rows]
    )
    st.dataframe(frame, width="stretch", hide_index=True)


def show_ingest_result(status_code: int, payload: Any, api: ZenithAPI) -> None:
    if not isinstance(payload, dict):
        st.error(f"Unexpected response ({status_code}): {payload}")
        return
    accepted = payload.get("accepted", 0)
    rejected = payload.get("rejected", 0)
    duplicate_ids = payload.get("duplicate_ids", 0)
    duplicate_content = payload.get("duplicate_content", 0)
    if status_code == 422 or (rejected and not accepted):
        st.error(
            f"Ingestion rejected: {rejected} record(s) failed validation. "
            "Raw records and errors were preserved."
        )
    elif rejected:
        st.warning(
            f"Partial ingestion: {accepted} accepted, {rejected} rejected, "
            f"{duplicate_ids} duplicate ID(s), {duplicate_content} duplicate-content record(s)."
        )
    else:
        analysis_note = (
            "Correlation and quality analysis is queued and will refresh shortly."
            if payload.get("analysis_pending")
            else "Correlation and quality analysis ran automatically."
        )
        st.success(
            f"Ingestion complete: {accepted} accepted, {duplicate_content} duplicate-content, "
            f"{duplicate_ids} duplicate ID(s). {analysis_note}"
        )
    cols = st.columns(5)
    cols[0].metric("Total", payload.get("total", 0))
    cols[1].metric("Accepted", accepted)
    cols[2].metric("Rejected", rejected)
    cols[3].metric("Duplicate IDs", duplicate_ids)
    cols[4].metric("Duplicate content", duplicate_content)
    errors = payload.get("errors") or []
    if errors:
        with st.expander("Rejection / validation details", expanded=status_code == 422):
            st.dataframe(pd.DataFrame(errors), width="stretch", hide_index=True)
    st.caption(f"Batch ID: {payload.get('batch_id')}")
    st.cache_data.clear()


def parse_upload(uploaded: Any) -> tuple[list[Any] | None, str | None]:
    raw = uploaded.getvalue()
    name = uploaded.name.lower()
    try:
        if name.endswith(".json"):
            payload = json.loads(raw.decode("utf-8-sig"))
            if isinstance(payload, list):
                return payload, None
            if isinstance(payload, dict) and isinstance(payload.get("events"), list):
                return payload["events"], None
            if isinstance(payload, dict) and isinstance(payload.get("event"), dict):
                return [payload["event"]], None
            if isinstance(payload, dict):
                return [payload], None
            return None, "JSON must be an event object, an array, or {'events': [...]}"
        if name.endswith(".csv"):
            frame = pd.read_csv(io.BytesIO(raw))
            if frame.empty:
                return [], None
            frame = frame.astype(object).where(pd.notna(frame), None)
            records = frame.to_dict(orient="records")
            cleaned = []
            for record in records:
                item = {key: value for key, value in record.items() if value is not None}
                payload_value = item.get("payload")
                if isinstance(payload_value, str):
                    try:
                        item["payload"] = json.loads(payload_value)
                    except ValueError:
                        item["payload"] = {"raw": payload_value}
                cleaned.append(item)
            return cleaned, None
        return None, "Only .json and .csv files are supported."
    except Exception as exc:
        return None, f"Could not parse file: {type(exc).__name__}: {exc}"


def safe_json(value: Any, limit: int = 20000) -> str:
    text = json.dumps(value, indent=2, default=str)
    return text[:limit] + ("\n... (truncated)" if len(text) > limit else "")


def datetime_to_param(value: Any, end_of_day: bool = False) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.combine(value, time.max if end_of_day else time.min)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()

