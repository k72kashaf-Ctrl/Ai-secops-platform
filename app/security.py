"""Small, dependency-free request controls for local and demo deployments."""
from __future__ import annotations

import json
import threading
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import HTTPException, Request

from app.database import Database
from app.utils import now_iso


class SlidingWindowLimiter:
    """Per-client sliding window. ``cost`` lets one call consume several units
    (e.g. one /ingest batch consumes one unit per record)."""

    def __init__(self, limit: int, window_seconds: int = 60):
        self.limit = max(0, limit)
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[tuple[float, int]]] = defaultdict(deque)
        self._used: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()

    def allow(self, key: str, cost: int = 1) -> bool:
        if self.limit == 0:
            return True
        cost = max(1, cost)
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0][0] < cutoff:
                self._used[key] -= hits.popleft()[1]
            # A single batch larger than the whole budget is allowed on an idle
            # window so oversized-but-legitimate uploads are not rejected forever.
            if self._used[key] and self._used[key] + cost > self.limit:
                return False
            hits.append((now, cost))
            self._used[key] += cost
            return True


def client_key(request: Request) -> str:
    return request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or (
        request.client.host if request.client else "unknown"
    )


def record_audit(
    db: Database,
    request: Request,
    action: str,
    target: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    actor = request.headers.get("X-Actor", client_key(request))
    db.execute(
        "INSERT INTO audit_log(action, actor, target, details, created_at) VALUES(?,?,?,?,?)",
        (action, actor, target, json.dumps(details or {}, sort_keys=True, default=str), now_iso()),
    )


def require_ingest_rate_limit(request: Request, records: int) -> None:
    """Meter /ingest by record count (exempt from the per-request write limit)."""
    limiter: SlidingWindowLimiter = request.app.state.ingest_rate_limiter
    if not limiter.allow(client_key(request), cost=records):
        raise HTTPException(
            status_code=429,
            detail=(
                f"Ingest limit of {limiter.limit} records/minute exceeded; retry later "
                "(configure ZENITH_INGEST_RECORDS_PER_MINUTE)"
            ),
        )


def require_rate_limit(request: Request) -> None:
    limiter: SlidingWindowLimiter = request.app.state.rate_limiter
    if not limiter.allow(client_key(request)):
        raise HTTPException(status_code=429, detail="Rate limit exceeded; retry later")
