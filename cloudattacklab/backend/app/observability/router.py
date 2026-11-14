"""GET /events, /events/{id}, /alerts, /alerts/{id} (api-contract.md)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..errors import ApiError
from ..models import Alert, Event
from .api_schemas import AlertDetail, AlertOut, EventDetail, EventOut

router = APIRouter(tags=["observability"])


@router.get("/events", response_model=list[EventOut])
def list_events(
    session: Session = Depends(get_session),
    run_id: Optional[UUID] = None,
    event_type: Optional[str] = None,
    since: Optional[datetime] = None,
    limit: int = Query(default=100, ge=1, le=1000),
):
    query = select(Event)
    if run_id is not None:
        query = query.where(Event.run_id == run_id)
    if event_type is not None:
        query = query.where(Event.event_type == event_type)
    if since is not None:
        query = query.where(Event.timestamp >= since)
    query = query.order_by(Event.timestamp).limit(limit)
    return list(session.scalars(query))


@router.get("/events/{event_id}", response_model=EventDetail)
def get_event(event_id: UUID, session: Session = Depends(get_session)):
    event = session.get(Event, event_id)
    if event is None:
        raise ApiError(404, "event_not_found", f"No event with id {event_id}.")
    return event


@router.get("/alerts", response_model=list[AlertOut])
def list_alerts(
    session: Session = Depends(get_session),
    run_id: Optional[UUID] = None,
    rule_id: Optional[str] = None,
):
    query = select(Alert)
    if run_id is not None:
        query = query.where(Alert.run_id == run_id)
    if rule_id is not None:
        query = query.where(Alert.rule_id == rule_id)
    query = query.order_by(Alert.created_at)
    return list(session.scalars(query))


@router.get("/alerts/{alert_id}", response_model=AlertDetail)
def get_alert(alert_id: UUID, session: Session = Depends(get_session)):
    alert = session.get(Alert, alert_id)
    if alert is None:
        raise ApiError(404, "alert_not_found", f"No alert with id {alert_id}.")
    # Return the contributing events oldest-first.
    events = sorted(alert.events, key=lambda event: event.timestamp)
    return AlertDetail(
        **AlertOut.model_validate(alert).model_dump(),
        events=[EventOut.model_validate(event) for event in events],
    )
