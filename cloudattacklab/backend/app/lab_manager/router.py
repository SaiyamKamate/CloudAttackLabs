"""The "Labs" endpoints from docs/api-contract.md."""
from __future__ import annotations

from functools import lru_cache
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_session
from .adapter import LabAdapter
from .api_schemas import LabCreate, LabDetail, LabOut
from .docker_adapter import DockerComposeAdapter
from .service import LabManager
from .target import HttpTargetClient, TargetClient

router = APIRouter(prefix="/labs", tags=["labs"])


@lru_cache
def get_adapter() -> LabAdapter:
    settings = get_settings()
    return DockerComposeAdapter(
        settings.lab_compose_file,
        docker_bin=settings.docker_bin,
        lab_control_token=settings.lab_control_token,
        start_timeout_seconds=settings.lab_start_timeout_seconds,
    )


@lru_cache
def get_target_clients() -> dict[str, TargetClient]:
    settings = get_settings()
    return {name: HttpTargetClient(url, settings.lab_control_token) for name, url in settings.lab_targets.items()}


def get_manager(
    session: Session = Depends(get_session),
    adapter: LabAdapter = Depends(get_adapter),
    target_clients: dict[str, TargetClient] = Depends(get_target_clients),
) -> LabManager:
    return LabManager(session, adapter, target_clients)


# Plain `def` endpoints: FastAPI runs them in a thread pool, so a slow
# `docker compose up` does not block other requests.
@router.post("", response_model=LabOut, status_code=201)
def create_lab(body: LabCreate, manager: LabManager = Depends(get_manager)):
    return manager.create(body.name, body.target)


@router.get("", response_model=list[LabOut])
def list_labs(manager: LabManager = Depends(get_manager)):
    return manager.list()


@router.get("/{lab_id}", response_model=LabDetail)
def get_lab(lab_id: UUID, manager: LabManager = Depends(get_manager)):
    lab, containers, target_health = manager.details(lab_id)
    return LabDetail(
        **LabOut.model_validate(lab).model_dump(), containers=containers, target_health=target_health
    )


@router.post("/{lab_id}/start", response_model=LabOut)
def start_lab(lab_id: UUID, manager: LabManager = Depends(get_manager)):
    return manager.start(lab_id)


@router.post("/{lab_id}/stop", response_model=LabOut)
def stop_lab(lab_id: UUID, manager: LabManager = Depends(get_manager)):
    return manager.stop(lab_id)


@router.post("/{lab_id}/reset", response_model=LabOut)
def reset_lab(lab_id: UUID, manager: LabManager = Depends(get_manager)):
    return manager.reset(lab_id)
