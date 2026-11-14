"""GET /incidents and /incidents/{incident_id} (api-contract.md), minimal version."""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..errors import ApiError
from ..models import Incident
from .api_schemas import IncidentDetail, IncidentOut

router = APIRouter(tags=["incidents"])


@router.get("/incidents", response_model=list[IncidentOut])
def list_incidents(session: Session = Depends(get_session), run_id: Optional[UUID] = None):
    query = select(Incident)
    if run_id is not None:
        query = query.where(Incident.run_id == run_id)
    return list(session.scalars(query.order_by(Incident.created_at)))


@router.get("/incidents/{incident_id}", response_model=IncidentDetail)
def get_incident(incident_id: UUID, session: Session = Depends(get_session)):
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise ApiError(404, "incident_not_found", f"No incident with id {incident_id}.")
    return IncidentDetail(
        **IncidentOut.model_validate(incident).model_dump(),
        alerts=sorted(incident.alerts, key=lambda alert: alert.created_at),
        responses=sorted(incident.responses, key=lambda response: response.requested_at),
    )
