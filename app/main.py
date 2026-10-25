"""FastAPI application factory and entry point.

Run with:
    uvicorn app.main:app --reload --port 8000
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import briefing as briefing_api
from app.api import assistant as assistant_api
from app.api import cameras as cameras_api
from app.api import events as events_api
from app.api import incidents as incidents_api
from app.api import mobile as mobile_api
from app.api import quality as quality_api
from app.api import reasoning as reasoning_api
from app.api import situation as situation_api
from app.api import system as system_api
from app.config import Settings, load_settings
from app.database import Database
from app.services.analysis import AnalysisWorker
from app.services.sample import seed_sample
from app.security import SlidingWindowLimiter, require_rate_limit
from app.utils import now_iso


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def create_app(
    db_path: str | Path | None = None,
    settings: Settings | None = None,
    *,
    auto_seed: bool = False,
) -> FastAPI:
    settings = settings or load_settings()
    if db_path is not None:
        settings = settings.derive(db_path=Path(db_path))
    database = Database(settings.db_path)
    database.init_schema()
    worker = AnalysisWorker(database, settings) if settings.analysis_mode == "deferred" else None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        database.init_schema()  # idempotent; re-opens the keep-alive after a restart
        yield
        if worker is not None:
            worker.stop()
        database.close()

    application = FastAPI(
        lifespan=lifespan,
        title="Zenith AI-Native SecOps Platform",
        version=__version__,
        description=(
            "Local-first security operations MVP: multi-source event ingestion, "
            "deterministic data-quality checks, explainable incident correlation, "
            "and evidence-backed operational briefings. No external AI or GPU required."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        swagger_ui_parameters={
            "defaultModelsExpandDepth": -1,
            "defaultModelExpandDepth": 2,
            "docExpansion": "list",
            "tryItOutEnabled": True,
            "syntaxHighlight": {"theme": "monokai"},
        },
    )
    application.state.db = database
    application.state.settings = settings
    application.state.started_at = now_iso()
    application.state.rate_limiter = SlidingWindowLimiter(settings.rate_limit_per_minute)
    application.state.ingest_rate_limiter = SlidingWindowLimiter(settings.ingest_records_per_minute)
    application.state.analysis_worker = worker
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.middleware("http")
    async def rate_limit_requests(request: Request, call_next):
        # /ingest is metered per record inside the endpoint instead.
        if request.method in {"POST", "PATCH", "PUT", "DELETE"} and request.url.path != "/ingest":
            try:
                require_rate_limit(request)
            except HTTPException as exc:
                return JSONResponse(status_code=429, content={"detail": str(exc.detail)})
        return await call_next(request)

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "detail": "Request validation failed",
                "errors": [
                    {
                        "field": ".".join(str(part) for part in error.get("loc", ())),
                        "message": error.get("msg", "invalid value"),
                        "type": error.get("type", "value_error"),
                    }
                    for error in exc.errors()
                ],
            },
        )

    application.include_router(events_api.router)
    application.include_router(incidents_api.router)
    application.include_router(quality_api.router)
    application.include_router(reasoning_api.router)
    application.include_router(briefing_api.router)
    application.include_router(assistant_api.router)
    application.include_router(system_api.router)
    application.include_router(mobile_api.router)
    application.include_router(situation_api.router)
    application.include_router(cameras_api.router)

    # Local camera clips, served read-only from exactly one directory
    # (StaticFiles rejects paths that resolve outside it).
    media_dir = Path(settings.media_dir)
    try:
        media_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    application.mount(
        "/media",
        StaticFiles(directory=str(media_dir), check_dir=False, follow_symlink=False),
        name="media",
    )

    @application.get("/", tags=["system"])
    def root():
        return {
            "name": "Zenith AI-Native SecOps Platform",
            "version": __version__,
            "status": "ok",
            "docs": "/docs",
            "openapi": "/openapi.json",
            "health": "/health",
            "dashboard": settings.api_base_url.replace(":8000", ":8501"),
        }

    if auto_seed:
        try:
            stats = database.stats()
            if stats["valid_events"] == 0 and not stats["rejected_events"] and settings.sample_path.exists():
                seed_sample(database, settings, reset=False)
        except Exception as exc:  # never prevent the API from starting
            application.state.seed_error = f"{type(exc).__name__}: {exc}"

    return application


app = create_app(auto_seed=_env_bool("ZENITH_AUTO_SEED", True))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=os.getenv("ZENITH_HOST", "127.0.0.1"),
        port=int(os.getenv("ZENITH_PORT", "8000")),
        reload=False,
    )
