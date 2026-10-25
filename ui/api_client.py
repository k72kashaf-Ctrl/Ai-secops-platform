"""Thin HTTP client used by the Streamlit dashboard."""
from __future__ import annotations

import os
import threading
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import requests
import requests.adapters


class ApiConnectionError(RuntimeError):
    pass


class ApiError(RuntimeError):
    def __init__(self, status_code: int, payload: Any):
        self.status_code = status_code
        self.payload = payload
        detail = payload.get("detail") if isinstance(payload, dict) else str(payload)
        super().__init__(f"API {status_code}: {detail}")


_SESSIONS: dict[str, requests.Session] = {}
_SESSIONS_LOCK = threading.Lock()


def _fast_loopback(url: str) -> str:
    """Use 127.0.0.1 for localhost: on Windows "localhost" tries IPv6 ::1 first and
    waits ~2 s per new connection when the API only listens on IPv4."""
    parts = urlsplit(url)
    if parts.hostname == "localhost":
        netloc = "127.0.0.1" + (f":{parts.port}" if parts.port else "")
        return urlunsplit(parts._replace(netloc=netloc))
    return url


def _shared_session(base_url: str) -> requests.Session:
    """One pooled keep-alive session per backend, reused across reruns and pages."""
    with _SESSIONS_LOCK:
        session = _SESSIONS.get(base_url)
        if session is None:
            session = requests.Session()
            adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=16)
            session.mount("http://", adapter)
            session.mount("https://", adapter)
            admin_key = os.getenv("ZENITH_ADMIN_API_KEY")
            if admin_key:
                session.headers.update({"X-Admin-Key": admin_key})
            _SESSIONS[base_url] = session
        return session


class ZenithAPI:
    def __init__(self, base_url: str | None = None, timeout: float = 30.0):
        raw = (base_url or os.getenv("ZENITH_API_URL", "http://localhost:8000")).rstrip("/")
        self.base_url = _fast_loopback(raw)
        self.timeout = timeout
        self.session = _shared_session(self.base_url)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        allow_status: tuple[int, ...] = (),
        timeout: float | None = None,
    ) -> tuple[int, Any]:
        url = f"{self.base_url}{path}"
        try:
            response = self.session.request(
                method,
                url,
                params={key: value for key, value in (params or {}).items() if value not in (None, "")},
                json=json,
                timeout=timeout or self.timeout,
            )
        except requests.RequestException as exc:
            raise ApiConnectionError(
                f"Cannot reach the API at {self.base_url}. Start it with: "
                "uvicorn app.main:app --port 8000. Original error: " + str(exc)
            ) from exc
        try:
            payload = response.json()
        except ValueError:
            payload = response.text
        if response.status_code >= 400 and response.status_code not in allow_status:
            raise ApiError(response.status_code, payload)
        return response.status_code, payload

    # ------------------------------------------------------------------ reads
    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health", timeout=5.0)[1]

    def root(self) -> dict[str, Any]:
        return self._request("GET", "/")[1]

    def overview(self) -> dict[str, Any]:
        return self._request("GET", "/overview")[1]

    def situation(self) -> dict[str, Any]:
        return self._request("GET", "/situation")[1]

    def cameras(self) -> dict[str, Any]:
        return self._request("GET", "/cameras")[1]

    def event_facets(self) -> dict[str, Any]:
        return self._request("GET", "/events/facets")[1]

    def incident_facets(self) -> dict[str, Any]:
        return self._request("GET", "/incidents/facets")[1]

    def events(self, **filters: Any) -> dict[str, Any]:
        return self._request("GET", "/events", params=filters)[1]

    def event(self, event_id: str) -> dict[str, Any]:
        return self._request("GET", f"/events/{quote(event_id, safe='')}")[1]

    def incidents(self, **filters: Any) -> dict[str, Any]:
        return self._request("GET", "/incidents", params=filters)[1]

    def incident(self, incident_id: str) -> dict[str, Any]:
        return self._request("GET", f"/incidents/{quote(incident_id, safe='')}")[1]

    def incident_reasoning(self, incident_id: str) -> dict[str, Any]:
        return self._request("GET", f"/incidents/{quote(incident_id, safe='')}/reasoning")[1]

    def quality_issues(self, **filters: Any) -> dict[str, Any]:
        return self._request("GET", "/quality-issues", params=filters)[1]

    def quality_summary(self) -> dict[str, Any]:
        return self._request("GET", "/quality-issues/summary")[1]

    def briefing(self) -> dict[str, Any]:
        return self._request("GET", "/briefing")[1]

    def assistant_query(self, query: str, session_id: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"query": query}
        if session_id:
            body["session_id"] = session_id
        return self._request("POST", "/assistant/query", json=body)[1]

    def assistant_confirm(self, request_id: str, confirmed: bool = True) -> dict[str, Any]:
        return self._request(
            "POST",
            "/assistant/confirm",
            json={"request_id": request_id, "confirmed": confirmed},
        )[1]

    def assistant_history(self, limit: int = 50) -> dict[str, Any]:
        return self._request("GET", "/assistant/history", params={"limit": limit})[1]

    # ----------------------------------------------------------------- writes
    def ingest(self, records: list[Any] | dict[str, Any] | Any) -> tuple[int, Any]:
        body = records if isinstance(records, (list, dict)) else {"events": records}
        # Dashboard uploads are small: analyse synchronously so incidents show at once.
        return self._request(
            "POST", "/ingest", params={"analyze": "sync"}, json=body, allow_status=(422,)
        )

    def update_incident_status(
        self, incident_id: str, status: str, assigned_to: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"status": status}
        if assigned_to:
            body["assigned_to"] = assigned_to
        return self._request(
            "PATCH", f"/incidents/{quote(incident_id, safe='')}", json=body
        )[1]

    def analyze(self) -> dict[str, Any]:
        return self._request("POST", "/quality/analyze")[1]

    def recompute(self) -> dict[str, Any]:
        return self._request("POST", "/admin/recompute")[1]

    def reset(self) -> dict[str, Any]:
        return self._request("POST", "/admin/reset")[1]

    def seed_sample(self, reset: bool = True) -> dict[str, Any]:
        return self._request("POST", "/admin/seed-sample", params={"reset": str(reset).lower()})[1]
