"""The "Labs" endpoints from docs/api-contract.md."""
from __future__ import annotations

from functools import lru_cache
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..config import FALCO_SERVICE, Settings, get_settings
from ..db import get_session
from .adapter import LabAdapter
from ..telemetry.loki import LokiSource
from ..telemetry.source import TelemetrySourceError
from .api_schemas import FalcoStatusOut, LabCreate, LabDetail, LabOut, TargetUrlOut, TelemetryStatusOut
from .docker_adapter import DockerComposeAdapter
from .service import LabManager, active_run_in_lab
from .target import HttpTargetClient, TargetClient
from .target_guard import GuardedTargetClient, TargetGuard

router = APIRouter(prefix="/labs", tags=["labs"])


@lru_cache
def get_adapter() -> LabAdapter:
    settings = get_settings()
    return DockerComposeAdapter(
        settings.lab_compose_file,
        docker_bin=settings.docker_bin,
        lab_control_token=settings.lab_control_token,
        start_timeout_seconds=settings.lab_start_timeout_seconds,
        falco_enabled=settings.falco_enabled,
    )


def get_target_guard(
    settings: Settings = Depends(get_settings), adapter: LabAdapter = Depends(get_adapter)
) -> TargetGuard:
    """Checks that each configured lab target URL points into the lab (target_guard.py)."""
    return TargetGuard(settings.lab_targets, settings.lab_compose_file, adapter)


@lru_cache
def get_target_clients() -> dict[str, TargetClient]:
    settings = get_settings()
    guard = TargetGuard(settings.lab_targets, settings.lab_compose_file, get_adapter())
    # Every request first checks that the URL is inside the lab.
    return {
        name: GuardedTargetClient(HttpTargetClient(url, settings.lab_control_token), name, guard)
        for name, url in settings.lab_targets.items()
    }


def get_manager(
    session: Session = Depends(get_session),
    adapter: LabAdapter = Depends(get_adapter),
    target_clients: dict[str, TargetClient] = Depends(get_target_clients),
    guard: TargetGuard = Depends(get_target_guard),
    settings: Settings = Depends(get_settings),
) -> LabManager:
    container_services = tuple(settings.container_targets.values())
    # Lab target names are also their Compose service names (shopcloud).
    expected = tuple(settings.lab_targets) + container_services + ((FALCO_SERVICE,) if settings.falco_enabled else ())
    return LabManager(
        session, adapter, target_clients, guard=guard,
        expected_services=expected, container_services=container_services,
    )


# Plain `def` endpoints: FastAPI runs them in a thread pool, so a slow
# `docker compose up` does not block other requests.
@router.post("", response_model=LabOut, status_code=201)
def create_lab(body: LabCreate, manager: LabManager = Depends(get_manager)):
    return manager.create(body.name, body.target)


@router.get("", response_model=list[LabOut])
def list_labs(manager: LabManager = Depends(get_manager)):
    return manager.list()


def get_telemetry_status(settings: Settings = Depends(get_settings)) -> TelemetryStatusOut:
    """The active telemetry source; for Loki, a quick check that it answers."""
    if settings.telemetry_source != "loki":
        return TelemetryStatusOut(source=settings.telemetry_source, loki_url=None, reachable=None, error=None)
    try:
        LokiSource(settings.loki_url, settings.telemetry_service, timeout_seconds=2).check()
    except TelemetrySourceError as exc:
        return TelemetryStatusOut(source="loki", loki_url=settings.loki_url, reachable=False, error=str(exc))
    return TelemetryStatusOut(source="loki", loki_url=settings.loki_url, reachable=True, error=None)


@router.get("/{lab_id}", response_model=LabDetail)
def get_lab(
    lab_id: UUID,
    manager: LabManager = Depends(get_manager),
    telemetry: TelemetryStatusOut = Depends(get_telemetry_status),
    settings: Settings = Depends(get_settings),
):
    lab, containers, target_health = manager.details(lab_id)
    url_check = manager.target_url_check(lab)
    return LabDetail(
        **LabOut.model_validate(lab).model_dump(),
        status_detail=manager.status_detail,
        active_run=active_run_in_lab(manager.session, lab.id),
        containers=containers,
        target_health=target_health,
        target_url=TargetUrlOut(url=url_check.url, allowed=url_check.allowed, error=url_check.error),
        telemetry=telemetry,
        falco=falco_status(settings.falco_enabled, containers),
    )


def falco_status(enabled: bool, containers) -> FalcoStatusOut:
    falco = next((c for c in containers if c.service == FALCO_SERVICE), None)
    state = falco.state if falco else None
    health = falco.health if falco else None
    return FalcoStatusOut(
        enabled=enabled,
        state=state,
        health=health,
        healthy=bool(enabled and state == "running" and health == "healthy"),
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
