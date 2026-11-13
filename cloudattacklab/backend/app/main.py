"""CloudAttackLab backend: one FastAPI app, one module per feature.

Run locally (from backend/):  uvicorn app.main:app --port 8000
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .db import get_session
from .errors import ApiError, register_error_handlers
from .lab_manager.router import router as labs_router

API_PREFIX = "/api/v1"

health_router = APIRouter(tags=["health"])


@health_router.get("/health")
def health(session: Session = Depends(get_session)) -> dict[str, str]:
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise ApiError(503, "database_unavailable", "The backend cannot reach its database.") from exc
    return {"status": "ok", "database": "ok"}


def create_app() -> FastAPI:
    app = FastAPI(title="CloudAttackLab", version="0.3.0")
    register_error_handlers(app)
    app.include_router(health_router, prefix=API_PREFIX)
    app.include_router(labs_router, prefix=API_PREFIX)
    return app


app = create_app()
