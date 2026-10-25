"""Post-ingestion analysis orchestration.

Quality checks and correlation are full recomputes over persisted evidence.
Running them inside every ``POST /ingest`` makes bulk loading quadratic, so the
API inserts first and lets :class:`AnalysisWorker` recompute in the background:

* ``mark_dirty()`` is called after each accepted batch;
* the worker waits until ingestion has been quiet for ``debounce_seconds``,
  but never longer than ``max_delay_seconds`` after the first pending batch;
* batches that arrive while a recompute is running trigger exactly one more.

``run_full_analysis`` is serialised per database file so the worker,
``/admin/recompute`` and the sample loader never overwrite each other.
"""
from __future__ import annotations

import threading
import time
from typing import Any

from app.config import Settings
from app.database import Database
from app.utils import now_iso

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _analysis_lock(db: Database) -> threading.Lock:
    key = str(db.path.resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def run_full_analysis(db: Database, settings: Settings) -> dict[str, Any]:
    """Recompute quality issues, incidents and uncorroborated-event issues.

    Order matters: correlation reads sensor conflicts produced by the quality
    pass, and the uncorroborated pass reads single-event incidents.
    """
    from app.services.correlation import run_correlation
    from app.services.data_quality import add_uncorrelated_issues, run_quality_analysis

    with _analysis_lock(db):
        started = time.perf_counter()
        quality = run_quality_analysis(db, settings, include_uncorrelated=False)
        incidents = run_correlation(db, settings)
        quality += add_uncorrelated_issues(db, settings)
        return {
            "quality_issues": quality,
            "incidents": incidents,
            "duration_seconds": round(time.perf_counter() - started, 3),
        }


class AnalysisWorker:
    """Debounced background recompute for one database."""

    def __init__(
        self,
        db: Database,
        settings: Settings,
        *,
        debounce_seconds: float | None = None,
        max_delay_seconds: float | None = None,
    ):
        self.db = db
        self.settings = settings
        self.debounce_seconds = (
            settings.analysis_debounce_seconds if debounce_seconds is None else debounce_seconds
        )
        self.max_delay_seconds = (
            settings.analysis_max_delay_seconds if max_delay_seconds is None else max_delay_seconds
        )
        self._cond = threading.Condition()
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._requested = 0  # generation of the latest mark_dirty()
        self._completed = 0  # generation covered by the last finished run
        self._first_pending_at: float | None = None
        self._last_mark_at = 0.0
        self._running = False
        self.runs = 0
        self.last_started_at: str | None = None
        self.last_completed_at: str | None = None
        self.last_duration_seconds: float | None = None
        self.last_result: dict[str, Any] | None = None
        self.last_error: str | None = None

    # ---------------------------------------------------------------- control
    def mark_dirty(self) -> None:
        with self._cond:
            now = time.monotonic()
            self._requested += 1
            self._last_mark_at = now
            if self._first_pending_at is None:
                self._first_pending_at = now
            if self._stopping:
                return  # stop() flushes pending generations
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._loop, name="zenith-analysis", daemon=True
                )
                self._thread.start()
            self._cond.notify_all()

    def stop(self, timeout: float = 30.0, *, flush: bool = True) -> None:
        """Stop the thread; with ``flush`` analyse any batches still pending so
        a restart never serves stale incidents. The worker can be reused."""
        with self._cond:
            self._stopping = True
            self._cond.notify_all()
            thread = self._thread
        if thread is not None:
            thread.join(timeout)
        with self._cond:
            pending = self._completed < self._requested
            generation = self._requested
        if flush and pending:
            try:
                self.last_result = run_full_analysis(self.db, self.settings)
                self.last_error = None
            except Exception as exc:  # pragma: no cover - shutdown best effort
                self.last_error = f"{type(exc).__name__}: {exc}"
            with self._cond:
                self._completed = max(self._completed, generation)
                self.last_completed_at = now_iso()
                self.runs += 1
        with self._cond:
            self._stopping = False
            self._thread = None
            self._first_pending_at = None if self._completed >= self._requested else self._first_pending_at
            self._cond.notify_all()

    def wait_idle(self, timeout: float = 120.0) -> bool:
        """Block until every marked batch has been analysed (tests/admin)."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while self._completed < self._requested or self._running:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cond.wait(remaining)
        return True

    @property
    def pending(self) -> bool:
        with self._cond:
            return self._completed < self._requested or self._running

    def status(self) -> dict[str, Any]:
        with self._cond:
            return {
                "mode": "deferred",
                "analysis_pending": self._completed < self._requested or self._running,
                "running": self._running,
                "runs": self.runs,
                "last_started_at": self.last_started_at,
                "last_completed_at": self.last_completed_at,
                "last_duration_seconds": self.last_duration_seconds,
                "last_result": self.last_result,
                "last_error": self.last_error,
                "debounce_seconds": self.debounce_seconds,
                "max_delay_seconds": self.max_delay_seconds,
            }

    # ------------------------------------------------------------------- loop
    def _due_in(self, now: float) -> float:
        quiet_at = self._last_mark_at + self.debounce_seconds
        forced_at = (self._first_pending_at or now) + self.max_delay_seconds
        return max(0.0, min(quiet_at, forced_at) - now)

    def _loop(self) -> None:
        while True:
            with self._cond:
                while not self._stopping:
                    if self._completed < self._requested:
                        delay = self._due_in(time.monotonic())
                        if delay <= 0:
                            break
                        self._cond.wait(delay)
                    else:
                        self._cond.wait()
                if self._stopping:
                    return
                generation = self._requested
                self._first_pending_at = None
                self._running = True
                self.last_started_at = now_iso()
            try:
                result = run_full_analysis(self.db, self.settings)
                error = None
            except Exception as exc:  # keep the worker alive; report via /health
                result, error = None, f"{type(exc).__name__}: {exc}"
            with self._cond:
                self._running = False
                self._completed = generation
                self.runs += 1
                self.last_completed_at = now_iso()
                self.last_error = error
                if result is not None:
                    self.last_result = result
                    self.last_duration_seconds = result["duration_seconds"]
                if self._completed < self._requested and self._first_pending_at is None:
                    self._first_pending_at = time.monotonic()
                self._cond.notify_all()
