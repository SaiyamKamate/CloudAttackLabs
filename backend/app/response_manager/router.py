"""The "Response actions" endpoints and GET /audit from docs/api-contract.md."""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..config import Settings, get_settings
from ..lab_manager.adapter import LabAdapter
from ..lab_manager.router import get_adapter, get_target_clients
from ..lab_manager.target import TargetClient
from ..models import AuditLog
from ..scenario_engine.engine import ScenarioEngine
from ..scenario_engine.router import get_scenario_engine
from .api_schemas import AuditOut, ResponseOut
from .service import ResponseManager
from ..targets.resolve import get_client_factory
from ..targets.service import ClientFactory

router = APIRouter(tags=["responses"])


def get_response_manager(
    session: Session = Depends(get_session),
    engine: ScenarioEngine = Depends(get_scenario_engine),
    target_clients: dict[str, TargetClient] = Depends(get_target_clients),
    adapter: LabAdapter = Depends(get_adapter),
    settings: Settings = Depends(get_settings),
    client_factory: ClientFactory = Depends(get_client_factory),
) -> ResponseManager:
    return ResponseManager(
        session, engine, target_clients, adapter=adapter, container_targets=settings.container_targets,
        settings=settings, client_factory=client_factory,
    )


# These endpoints declare no request body on purpose: the action, its parameters
# and the target all come from the scenario and the lab configuration.
@router.post("/incidents/{incident_id}/responses", response_model=ResponseOut, status_code=201)
def request_response(incident_id: UUID, manager: ResponseManager = Depends(get_response_manager)):
    return manager.request(incident_id)


@router.post("/responses/{response_id}/approve", response_model=ResponseOut)
def approve_response(response_id: UUID, manager: ResponseManager = Depends(get_response_manager)):
    return manager.approve(response_id)


@router.post("/responses/{response_id}/reject", response_model=ResponseOut)
def reject_response(response_id: UUID, manager: ResponseManager = Depends(get_response_manager)):
    return manager.reject(response_id)


@router.get("/responses/{response_id}", response_model=ResponseOut)
def get_response(response_id: UUID, manager: ResponseManager = Depends(get_response_manager)):
    return manager.get(response_id)


@router.get("/audit", response_model=list[AuditOut])
def list_audit(
    session: Session = Depends(get_session),
    object_type: Optional[str] = None,
    object_id: Optional[str] = None,
    action: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=1000),
):
    """The audit trail, newest first."""
    query = select(AuditLog)
    if object_type is not None:
        query = query.where(AuditLog.object_type == object_type)
    if object_id is not None:
        query = query.where(AuditLog.object_id == object_id)
    if action is not None:
        query = query.where(AuditLog.action == action)
    return list(session.scalars(query.order_by(AuditLog.timestamp.desc(), AuditLog.id.desc()).limit(limit)))
