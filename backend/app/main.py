"""CloudAttackLab backend: one FastAPI app, one module per feature.

Run locally (from backend/):  uvicorn app.main:app --port 8000
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_session
from .lab_manager.router import get_adapter
from .startup import run_startup_checks
from .errors import ApiError, register_error_handlers
from .incidents.router import router as incidents_router
from .lab_manager.router import router as labs_router
from .observability.router import router as observability_router
from .reports.router import router as reports_router
from .response_manager.router import router as responses_router
from .scenario_engine.router import router as scenarios_router
from .targets.router import router as targets_router

API_PREFIX = "/api/v1"

health_router = APIRouter(tags=["health"])


@health_router.get("/health")
def health(session: Session = Depends(get_session)) -> dict[str, str]:
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise ApiError(503, "database_unavailable", "The backend cannot reach its database.") from exc
    return {"status": "ok", "database": "ok"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Settings and the adapter come through the app's dependency overrides, if any (tests).
    settings = app.dependency_overrides.get(get_settings, get_settings)()
    adapter = app.dependency_overrides.get(get_adapter, get_adapter)()
    run_startup_checks(settings, adapter)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="CloudAttackLab", version="0.3.0", lifespan=lifespan)
    register_error_handlers(app)
    app.include_router(health_router, prefix=API_PREFIX)
    app.include_router(labs_router, prefix=API_PREFIX)
    app.include_router(scenarios_router, prefix=API_PREFIX)
    app.include_router(observability_router, prefix=API_PREFIX)
    app.include_router(incidents_router, prefix=API_PREFIX)
    app.include_router(responses_router, prefix=API_PREFIX)
    app.include_router(reports_router, prefix=API_PREFIX)
    app.include_router(targets_router, prefix=API_PREFIX)
    return app


app = create_app()
