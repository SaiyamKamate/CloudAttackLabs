"""The "Scenarios and runs" endpoints from docs/api-contract.md."""
from __future__ import annotations

from functools import lru_cache
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_session
from ..lab_manager.router import get_target_clients
from ..lab_manager.target import TargetClient
from ..schemas.scenario import ScenarioDefinition
from .api_schemas import RunCreate, RunOut, ScenarioSummary
from .engine import ScenarioEngine
from .service import ScenarioService

router = APIRouter(tags=["scenarios"])


@lru_cache
def get_scenario_engine() -> ScenarioEngine:
    settings = get_settings()
    return ScenarioEngine(settings.scenarios_dir, set(settings.lab_targets))


def get_service(
    session: Session = Depends(get_session),
    engine: ScenarioEngine = Depends(get_scenario_engine),
    target_clients: dict[str, TargetClient] = Depends(get_target_clients),
    settings: Settings = Depends(get_settings),
) -> ScenarioService:
    return ScenarioService(session, engine, target_clients, settings)


@router.get("/scenarios", response_model=list[ScenarioSummary])
def list_scenarios(service: ScenarioService = Depends(get_service)):
    return [ScenarioSummary.from_definition(s) for s in service.list_scenarios()]


@router.get("/scenarios/{scenario_id}", response_model=ScenarioDefinition)
def get_scenario(scenario_id: str, service: ScenarioService = Depends(get_service)):
    return service.get_scenario(scenario_id)


@router.post("/labs/{lab_id}/runs", response_model=RunOut, status_code=201)
def start_run(lab_id: UUID, body: RunCreate, service: ScenarioService = Depends(get_service)):
    return service.start_run(lab_id, body.scenario_id)


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(run_id: UUID, service: ScenarioService = Depends(get_service)):
    return service.get_run(run_id)


@router.post("/runs/{run_id}/cleanup", response_model=RunOut)
def cleanup_run(run_id: UUID, service: ScenarioService = Depends(get_service)):
    return service.cleanup_run(run_id)
