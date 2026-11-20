"""Target Management endpoints (plan section 6.1)."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_session
from ..models import Target, TargetAuthorization
from ..scenario_engine.engine import ScenarioEngine
from ..scenario_engine.router import get_scenario_engine
from .api_schemas import AuthorizationCreate, AuthorizationOut, TargetCreate, TargetDetail, TargetOut
from .resolve import get_client_factory
from .service import ClientFactory, TargetService, effective_status
from .url_policy import UrlRefused, check_pinned

router = APIRouter(prefix="/targets", tags=["targets"])


def get_target_service(
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    engine: ScenarioEngine = Depends(get_scenario_engine),
    client_factory: ClientFactory = Depends(get_client_factory),
) -> TargetService:
    # Only application scenarios can run against a registered target.
    kinds = {s.id: s.target for s in engine.list() if s.type == "application"}
    reserved = tuple(settings.lab_targets) + tuple(settings.container_targets)
    return TargetService(
        session, settings, client_factory=client_factory, scenario_kinds=kinds, reserved_names=reserved
    )


def authorization_out(row: TargetAuthorization) -> AuthorizationOut:
    return AuthorizationOut(
        id=row.id,
        scenario_ids=list(row.scenario_ids),
        status=effective_status(row),
        authorized_by=row.authorized_by,
        acknowledgement=row.acknowledgement,
        max_runs=row.max_runs,
        runs_used=row.runs_used,
        created_at=row.created_at,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
    )


def target_out(target: Target, service: TargetService) -> TargetOut:
    active = service.active_authorization(target)
    return TargetOut(
        id=target.id,
        name=target.name,
        kind=target.kind,
        base_url=target.base_url,
        resolved_ip=target.resolved_ip,
        port=target.port,
        status=target.status,
        instance_id=target.instance_id,
        app_version=target.app_version,
        capabilities=target.capabilities,
        verified_at=target.verified_at,
        telemetry_verified_at=target.telemetry_verified_at,
        lab_id=target.lab_id,
        created_at=target.created_at,
        active_authorization=authorization_out(active) if active else None,
    )


def target_detail(target: Target, service: TargetService) -> TargetDetail:
    try:
        check_pinned(
            target.base_url, target.resolved_ip, target.port,
            allowed_cidrs=service.settings.target_allowed_cidrs,
            allowed_ports=service.settings.target_allowed_ports,
        )
        allowed, error = True, None
    except UrlRefused as exc:
        allowed, error = False, str(exc)
    return TargetDetail(
        **target_out(target, service).model_dump(),
        authorizations=[authorization_out(a) for a in reversed(target.authorizations)],
        url_allowed=allowed,
        url_error=error,
    )


# Plain `def` endpoints: FastAPI runs them in a thread pool, so a slow target
# does not block other requests.
@router.get("", response_model=list[TargetOut])
def list_targets(service: TargetService = Depends(get_target_service)):
    return [target_out(t, service) for t in service.list()]


@router.post("", response_model=TargetDetail, status_code=201)
def register_target(body: TargetCreate, service: TargetService = Depends(get_target_service)):
    target = service.register(
        name=body.name, kind=body.kind, base_url=body.base_url,
        integration_key=body.integration_key, control_token=body.control_token,
    )
    return target_detail(target, service)


@router.get("/{target_id}", response_model=TargetDetail)
def get_target(target_id: UUID, service: TargetService = Depends(get_target_service)):
    return target_detail(service.get(target_id), service)


@router.post("/{target_id}/verify", response_model=TargetDetail)
def verify_target(target_id: UUID, service: TargetService = Depends(get_target_service)):
    return target_detail(service.verify(target_id), service)


@router.post("/{target_id}/verify-telemetry", response_model=TargetDetail)
def verify_target_telemetry(target_id: UUID, service: TargetService = Depends(get_target_service)):
    return target_detail(service.verify_telemetry(target_id), service)


@router.post("/{target_id}/authorizations", response_model=AuthorizationOut, status_code=201)
def authorize_target(target_id: UUID, body: AuthorizationCreate, service: TargetService = Depends(get_target_service)):
    row = service.authorize(
        target_id,
        scenario_ids=list(body.scenario_ids),
        duration_minutes=body.duration_minutes,
        authorized_by=body.authorized_by,
        acknowledgement=body.acknowledgement,
        max_runs=body.max_runs,
    )
    return authorization_out(row)


@router.post("/{target_id}/authorizations/{authorization_id}/revoke", response_model=AuthorizationOut)
def revoke_authorization(
    target_id: UUID, authorization_id: UUID, service: TargetService = Depends(get_target_service)
):
    return authorization_out(service.revoke(target_id, authorization_id))
