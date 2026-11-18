"""GET /runs/{run_id}/report (api-contract.md)."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_session
from ..errors import ApiError
from ..models import ScenarioRun
from ..scenario_engine.engine import ScenarioEngine
from ..scenario_engine.router import get_scenario_engine
from .service import build_report

router = APIRouter(tags=["reports"])


@router.get("/runs/{run_id}/report")
def get_run_report(
    run_id: UUID,
    session: Session = Depends(get_session),
    engine: ScenarioEngine = Depends(get_scenario_engine),
) -> dict[str, Any]:
    run = session.get(ScenarioRun, run_id)
    if run is None:
        raise ApiError(404, "run_not_found", f"No run with id {run_id}.")
    return build_report(session, engine, run)
